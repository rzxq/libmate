"""
Разовый скрипт миграции.

Зачем он нужен: SQLAlchemy (`Base.metadata.create_all`, вызывается в
`database.init_db()`) умеет создавать только ОТСУТСТВУЮЩИЕ таблицы, но не
умеет добавлять новые колонки в уже существующую таблицу. Если в базе уже
были книги ДО того, как в модель Book добавили average_rating, ratings_count,
is_ebook, is_audiobook, for_sale, marketplaces, buy_link — таблица "books" в
файле базы физически не содержит этих колонок, и любой запрос к ней (поиск
по библиотеке, избранное, добавление книги) будет падать с ошибкой вида
"no such column: books.average_rating".

Этот скрипт добавляет недостающие колонки через ALTER TABLE, ничего не удаляя
и не трогая существующие строки — книги, коллекции и т.п. остаются на месте.

ЗАПУСК (один раз, после обновления кода, ДО перезапуска бота):
    python migrate.py

На Railway это можно сделать через Railway CLI (после `railway login` и
`railway link` в папке проекта):
    railway run python migrate.py

Если у тебя нет Railway CLI под рукой — самый простой альтернативный путь:
удалить старый файл базы (Railway -> Volumes) и просто заново запустить бота,
он создаст таблицу с нуля со всеми колонками. Но тогда старые книги пропадут,
так что используй этот скрипт, если данные жалко терять.
"""
import sqlite3

from config import DATABASE_PATH

# колонка -> SQL-тип для ALTER TABLE ... ADD COLUMN
NEW_COLUMNS = {
    "average_rating": "REAL",
    "ratings_count": "INTEGER",
    "is_ebook": "BOOLEAN",
    "is_audiobook": "BOOLEAN",
    "for_sale": "BOOLEAN",
    "marketplaces": "TEXT",
    "buy_link": "TEXT",
}


def main() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(books)")
        existing_columns = {row[1] for row in cur.fetchall()}

        if not existing_columns:
            print(
                f"Таблица 'books' не найдена в файле {DATABASE_PATH!r} — "
                "миграция не нужна, бот создаст таблицу сам при первом запуске."
            )
            return

        added = []
        for column, sql_type in NEW_COLUMNS.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE books ADD COLUMN {column} {sql_type}")
                added.append(column)

        conn.commit()
        if added:
            print(f"Готово. Добавлены колонки: {', '.join(added)}")
        else:
            print("Все нужные колонки уже на месте, миграция не нужна.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
