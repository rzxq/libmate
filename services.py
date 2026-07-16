"""
Внешние источники данных о книгах.

1) Google Books API — бесплатный, без ключа для базового поиска, хорошо
   индексирует русскоязычные издания и переводы (можно ограничивать
   langRestrict=ru). Используем для поиска обложки/описания/автора.

2) Anthropic API (с включённым веб-поиском) — используем, чтобы выяснить
   ГЛАВНОЕ, что нужно заказчице: входит ли книга в цикл, какая это часть
   и сколько всего частей. Обычные книжные API (в т.ч. Google Books) это
   почти никогда не знают надёжно, а вот модель с доступом к вебу может
   собрать эту информацию с сайтов вроде LiveLib/Fantlab/издательств.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import httpx
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"


@dataclass
class BookInfo:
    title: str
    author: str
    cover_url: Optional[str] = None
    description: Optional[str] = None


@dataclass
class SeriesInfo:
    is_series: bool
    series_name: Optional[str] = None
    part_number: Optional[int] = None
    total_parts: Optional[int] = None
    note: Optional[str] = None
    confidence: str = "low"  # low | medium | high


async def search_book(query: str, prefer_russian: bool = True) -> list[BookInfo]:
    """Ищет книги через Google Books API. Возвращает до 5 вариантов."""
    params = {"q": query, "maxResults": 5}
    if prefer_russian:
        params["langRestrict"] = "ru"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(GOOGLE_BOOKS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("items", [])
    if not items and prefer_russian:
        # Если по-русски ничего не нашлось — ищем без ограничения языка
        return await search_book(query, prefer_russian=False)

    results = []
    for item in items:
        info = item.get("volumeInfo", {})
        title = info.get("title", "Без названия")
        authors = ", ".join(info.get("authors", ["Неизвестен"]))
        cover = info.get("imageLinks", {}).get("thumbnail")
        description = info.get("description")
        results.append(BookInfo(title=title, author=authors, cover_url=cover, description=description))
    return results


async def check_series_info(title: str, author: str) -> SeriesInfo:
    """
    Спрашивает у Claude (с веб-поиском), входит ли книга в цикл/серию,
    и если да — какая это часть и сколько всего частей.
    """
    if not ANTHROPIC_API_KEY:
        return SeriesInfo(is_series=False, note="ANTHROPIC_API_KEY не настроен", confidence="low")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = (
        "Ты помогаешь читателю разобраться, является ли книга частью серии/цикла. "
        "Используй веб-поиск, чтобы найти точную информацию (например, на LiveLib, "
        "Fantlab, сайтах издательств, Goodreads). Отвечай ТОЛЬКО валидным JSON без "
        "пояснений и без markdown-разметки, строго в формате:\n"
        '{"is_series": true/false, "series_name": "строка или null", '
        '"part_number": число или null, "total_parts": число или null, '
        '"note": "короткая заметка на русском, например про качество перевода '
        'или про то, что часть не указана издателем"}'
    )

    user_prompt = f'Книга: "{title}". Автор: {author}. Входит ли она в цикл/серию?'

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )

    # Собираем весь текстовый вывод (могут быть text-блоки вперемешку с tool_use)
    text_parts = [block.text for block in message.content if block.type == "text"]
    raw = "\n".join(text_parts).strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return SeriesInfo(is_series=False, note="Не удалось разобрать ответ ИИ", confidence="low")

    return SeriesInfo(
        is_series=bool(parsed.get("is_series")),
        series_name=parsed.get("series_name"),
        part_number=parsed.get("part_number"),
        total_parts=parsed.get("total_parts"),
        note=parsed.get("note"),
        confidence="medium",
    )
