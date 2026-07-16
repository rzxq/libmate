import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_db
import handlers_library
import handlers_extra

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(handlers_library.router)
    dp.include_router(handlers_extra.router)

    # На случай, если Railway/Telegram оставили старый вебхук — снимаем его
    # перед запуском long polling, иначе бот не будет получать апдейты.
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
