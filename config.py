import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bookbot.db")

# Google Books работает и без ключа, но анонимные запросы с общих IP
# облачных хостингов (Railway, Heroku и т.п.) часто попадают под жёсткие
# лимиты и мгновенно возвращают ошибку/пустой ответ. Ключ снимает это
# ограничение. Бесплатный, получить: см. README, раздел "Google Books API".
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь его в переменные окружения (Railway Variables "
        "или файл .env локально)."
    )
