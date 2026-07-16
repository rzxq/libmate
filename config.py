import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bookbot.db")

# Токен провайдера оплаты (ЮKassa), выдаётся через @BotFather -> Payments.
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")

# Сколько книг можно добавить бесплатно, до того как понадобится подписка
FREE_BOOK_LIMIT = int(os.getenv("FREE_BOOK_LIMIT", "15"))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь его в переменные окружения (Railway Variables "
        "или файл .env локально)."
    )
