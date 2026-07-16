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
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"

# ВАЖНО: используем актуальное имя модели. Проверить актуальный список
# моделей можно в доке Anthropic — если модель переименуют, поменяй строку
# здесь и в check_series_info ниже.
CLAUDE_MODEL = "claude-sonnet-5"


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


async def _google_books_request(query: str, lang_restrict: Optional[str]) -> list[dict]:
    """Один HTTP-запрос к Google Books. Возвращает [] при любой ошибке (лог пишем)."""
    params = {"q": query, "maxResults": 5}
    if lang_restrict:
        params["langRestrict"] = lang_restrict

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(GOOGLE_BOOKS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        return data.get("items", [])
    except httpx.HTTPStatusError as e:
        logger.warning("Google Books HTTP %s для запроса %r: %s", e.response.status_code, query, e)
        return []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Google Books ошибка запроса %r: %s", query, e)
        return []


async def search_book(query: str, prefer_russian: bool = True) -> list[BookInfo]:
    """
    Ищет книги через Google Books. Пробуем по очереди:
    1) точный запрос по названию (intitle:) с ограничением на русский язык,
    2) intitle: без ограничения языка,
    3) обычный полнотекстовый запрос без intitle,
    и только если вообще ничего не нашлось — идём в ИИ-фолбэк.
    """
    title_query = f"intitle:{query}"

    items = await _google_books_request(title_query, "ru" if prefer_russian else None)
    if not items and prefer_russian:
        items = await _google_books_request(title_query, None)
    if not items:
        items = await _google_books_request(query, None)

    if not items:
        try:
            ai_result = await ai_find_book(query)
        except Exception:
            logger.exception("ИИ-фолбэк поиска книги упал для запроса %r", query)
            ai_result = None
        return [ai_result] if ai_result else []

    results = []
    for item in items:
        info = item.get("volumeInfo", {})
        title = info.get("title", "Без названия")
        authors = ", ".join(info.get("authors", ["Неизвестен"]))
        cover = info.get("imageLinks", {}).get("thumbnail")
        description = info.get("description")
        results.append(BookInfo(title=title, author=authors, cover_url=cover, description=description))
    return results


async def ai_find_book(query: str) -> Optional[BookInfo]:
    """
    Фолбэк-поиск через Claude с веб-поиском для книг, которых нет в Google
    Books — там неплохо индексируются страницы LiveLib, Fantlab, Litres,
    издательств и т.п. Это надёжнее и безопаснее прямого скрапинга LiveLib
    (у которого нет публичного API и есть риск бана по IP/условиям
    использования) — модель сама находит и пересказывает нужные факты.
    """
    if not ANTHROPIC_API_KEY:
        return None

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = (
        "Ты помогаешь найти точные библиографические данные книги для "
        "русскоязычного читателя, используя веб-поиск (LiveLib, Fantlab, "
        "Litres, сайты издательств). Отвечай ТОЛЬКО валидным JSON без markdown, "
        'в формате: {"found": true/false, "title": "строка", "author": "строка", '
        '"description": "1-2 предложения на русском или null"}'
    )
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Найди книгу: {query}"}],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
    except Exception:
        logger.exception("Anthropic API упал в ai_find_book для запроса %r", query)
        return None

    text_parts = [b.text for b in message.content if b.type == "text"]
    raw = "\n".join(text_parts).strip().replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Не разобрал JSON от ИИ в ai_find_book: %r", raw)
        return None
    if not parsed.get("found"):
        return None
    return BookInfo(
        title=parsed.get("title", query),
        author=parsed.get("author", "Неизвестен"),
        description=parsed.get("description"),
    )


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

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
    except Exception:
        logger.exception("Anthropic API упал в check_series_info для %r / %r", title, author)
        return SeriesInfo(is_series=False, note="Не удалось проверить цикл (ошибка ИИ)", confidence="low")

    # Собираем весь текстовый вывод (могут быть text-блоки вперемешку с tool_use)
    text_parts = [block.text for block in message.content if block.type == "text"]
    raw = "\n".join(text_parts).strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Не разобрал JSON от ИИ в check_series_info: %r", raw)
        return SeriesInfo(is_series=False, note="Не удалось разобрать ответ ИИ", confidence="low")

    return SeriesInfo(
        is_series=bool(parsed.get("is_series")),
        series_name=parsed.get("series_name"),
        part_number=parsed.get("part_number"),
        total_parts=parsed.get("total_parts"),
        note=parsed.get("note"),
        confidence="medium",
    )
