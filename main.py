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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await db.init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(handlers_library.router)
    dp.include_router(handlers_extra.router)

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

    # На случай, если Railway/Telegram оставили старый вебхук — снимаем его
    # перед запуском long polling, иначе бот не будет получать апдейты.
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
