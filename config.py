import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bookbot.db")

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь его в переменные окружения (Railway Variables "
        "или файл .env локально)."
    )
