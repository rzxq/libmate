import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
COMMUNITY_CHAT_ID = os.getenv("COMMUNITY_CHAT_ID", "") or None
DATABASE_PATH = os.getenv("DATABASE_PATH", "bookbot.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь его в переменные окружения (Railway Variables "
        "или файл .env локально)."
    )
