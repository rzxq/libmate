import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from config import BOT_TOKEN
import database as db
import handlers_library
import handlers_extra
import handlers_subscription

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REMINDER_CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # раз в 6 часов — не грузит Railway


async def subscription_reminder_loop(bot: Bot) -> None:
    """Лёгкий фоновый цикл: раз в несколько часов напоминает о скором окончании подписки.
    Никаких доп. библиотек (типа APScheduler) не нужно — просто спящая корутина."""
    while True:
        try:
            for user in await db.get_users_expiring_soon(hours=24):
                await bot.send_message(
                    user.tg_id,
                    "⏳ Подписка заканчивается в течение суток. Продли её через «💎 Подписка», "
                    "чтобы не потерять доступ к безлимитной библиотеке и проверке циклов.",
                )
                await db.mark_expiry_notified(user.id)
        except Exception:
            logging.exception("Ошибка в цикле напоминаний о подписке")
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


async def main() -> None:
    await db.init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(handlers_library.router)
    dp.include_router(handlers_extra.router)
    dp.include_router(handlers_subscription.router)

    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        """Ловит любое необработанное исключение в хендлерах: пишет в лог и
        отвечает пользователю вместо того, чтобы просто "зависнуть" без ответа."""
        logger.exception("Необработанная ошибка при апдейте %s", event.update, exc_info=event.exception)
        try:
            chat = None
            if event.update.message:
                chat = event.update.message.chat.id
            elif event.update.callback_query and event.update.callback_query.message:
                chat = event.update.callback_query.message.chat.id
            if chat:
                await bot.send_message(chat, "⚠️ Что-то пошло не так, попробуй ещё раз чуть позже.")
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке пользователю")
        return True

    asyncio.create_task(subscription_reminder_loop(bot))

    # На случай, если Railway/Telegram оставили старый вебхук — снимаем его
    # перед запуском long polling, иначе бот не будет получать апдейты.
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
