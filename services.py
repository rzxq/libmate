"""
Внешние источники данных о книгах.

1) Google Books API — бесплатный, без ключа для базового поиска, хорошо
   индексирует русскоязычные издания и переводы (можно ограничивать
   langRestrict=ru). Используем ТОЛЬКО для первичного поиска: название,
   автор, обложка, краткое описание. Google Books НЕ используется для
   рейтинга и покупки — у него почти никогда нет рейтинга для русскоязычных
   книг, а покупать через него никто из русскоязычной аудитории не привык.

2) Anthropic API — используем для двух вещей ОДНИМ запросом:
   а) входит ли книга в цикл, какая это часть и сколько их всего —
      обычные книжные API это почти никогда не знают надёжно;
   б) рейтинг, доступность (бумага/электронная/аудио) и возможность купить —
      здесь модель использует свои знания о LiveLib, Wildberries, Ozon,
      Литрес, MyBook, Bookmate, Читай-городу и т.п.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, GOOGLE_BOOKS_API_KEY

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"

GOOGLE_BOOKS_MAX_RESULTS = 20

CLAUDE_MODEL = "claude-3-5-haiku-latest"


@dataclass
class BookInfo:
    title: str
    author: str
    cover_url: Optional[str] = None
    description: Optional[str] = None


@dataclass
class BookContext:
    is_series: bool = False
    series_name: Optional[str] = None
    part_number: Optional[int] = None
    total_parts: Optional[int] = None
    series_note: Optional[str] = None

    average_rating: Optional[float] = None
    ratings_count: Optional[int] = None

    is_ebook: Optional[bool] = None
    is_audiobook: Optional[bool] = None

    for_sale: Optional[bool] = None
    marketplaces: Optional[str] = None
    buy_link: Optional[str] = None

    market_note: Optional[str] = None


def format_rating(rating: Optional[float], count: Optional[int] = None) -> str:
    if not rating:
        return "⭐ Рейтинг: нет данных"
    full = max(0, min(5, round(rating)))
    stars = "⭐" * full + "☆" * (5 - full)
    tail = f" ({count} оценок)" if count else ""
    return f"{stars} {rating:.1f} из 5{tail}"


def format_availability(info) -> str:
    is_ebook = getattr(info, "is_ebook", None)
    is_audio = getattr(info, "is_audiobook", None)

    if is_ebook is None and is_audio is None:
        return "📱 Доступность: нет данных"

    formats = []
    if is_ebook:
        formats.append("электронная (fb2/epub/pdf)")
    if is_audio:
        formats.append("аудиокнига")

    if not formats:
        return "📱 Доступность: похоже, есть только бумажная версия"
    return "📱 Доступность: " + ", ".join(formats)


def format_purchase(info) -> str:
    for_sale = getattr(info, "for_sale", None)
    marketplaces = getattr(info, "marketplaces", None)
    buy_link = getattr(info, "buy_link", None)

    if for_sale and marketplaces:
        return f"🛒 Покупка: можно купить — {marketplaces}"
    if for_sale and buy_link:
        return "🛒 Покупка: можно купить (ссылка ниже)"
    if for_sale is False:
        return "🛒 Покупка: сейчас нигде не продаётся (возможно, распродан тираж)"
    return "🛒 Покупка: нет данных"


async def _google_books_request(query: str, lang_restrict: Optional[str]) -> list[dict]:
    params = {"q": query, "maxResults": GOOGLE_BOOKS_MAX_RESULTS}
    if lang_restrict:
        params["langRestrict"] = lang_restrict
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
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


def _parse_volume(item: dict) -> BookInfo:
    info = item.get("volumeInfo", {})
    title = info.get("title", "Без названия")
    authors = ", ".join(info.get("authors", ["Неизвестен"]))
    cover = info.get("imageLinks", {}).get("thumbnail")
    description = info.get("description")
    return BookInfo(title=title, author=authors, cover_url=cover, description=description)


async def search_book(query: str, prefer_russian: bool = True) -> list[BookInfo]:
    items = await _google_books_request(query, "ru" if prefer_russian else None)
    if not items and prefer_russian:
        items = await _google_books_request(query, None)

    if not items:
        try:
            ai_result = await ai_find_book(query)
        except Exception:
            logger.exception("ИИ-фолбэк поиска книги упал для запроса %r", query)
            ai_result = None
        return [ai_result] if ai_result else []

    return [_parse_volume(item) for item in items]


async def ai_find_book(query: str) -> Optional[BookInfo]:
    if not ANTHROPIC_API_KEY:
        return None

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = (
        "Ты помогаешь найти точные библиографические данные книги для "
        "русскоязычного читателя. Отвечай ТОЛЬКО валидным JSON без markdown, "
        'в формате: {"found": true/false, "title": "строка", "author": "строка", '
        '"description": "1-2 предложения на русском или null"}'
    )
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Найди книгу: {query}"}],
        )
    except Exception as e:
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


async def check_book_context(title: str, author: str) -> BookContext:
    if not ANTHROPIC_API_KEY:
        return BookContext(market_note="ANTHROPIC_API_KEY не настроен")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = (
        "Ты помогаешь русскоязычному читателю разобраться с книгой. "
        "Для рейтинга и отзывов используй LiveLib (у него "
        "своя 5-звёздочная шкала оценок — используй именно её) и Fantlab. "
        "Для форматов и покупки — Wildberries, Ozon, Litres (ЛитРес), "
        "MyBook, Bookmate, «Читай-город», «Лабиринт». НЕ используй Google "
        "Books как источник рейтинга или покупки — русскоязычная аудитория "
        "им для этого не пользуется. Отвечай ТОЛЬКО валидным JSON без "
        "markdown-разметки и пояснений, строго в формате:\n"
        '{"is_series": true/false, "series_name": "строка или null", '
        '"part_number": число или null, "total_parts": число или null, '
        '"series_note": "строка или null", '
        '"average_rating": число от 0 до 5 или null, '
        '"ratings_count": число или null, '
        '"is_ebook": true/false/null, '
        '"is_audiobook": true/false/null, '
        '"for_sale": true/false/null, '
        '"marketplaces": "площадки через запятую, например \'Wildberries, Озон, ЛитРес\', или null", '
        '"buy_link": "прямая ссылка на карточку товара или null", '
        '"market_note": "короткая заметка на русском (например про цену или доступность) или null"}'
    )
    user_prompt = f'Книга: "{title}". Автор: {author}.'

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        logger.exception("Anthropic API упал в check_book_context для %r / %r", title, author)
        return BookContext(market_note=f"Ошибка ИИ: {e}")

    text_parts = [block.text for block in message.content if block.type == "text"]
    raw = "\n".join(text_parts).strip().replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Не разобрал JSON от ИИ в check_book_context: %r", raw)
        return BookContext(market_note="Не удалось разобрать ответ ИИ")

    return BookContext(
        is_series=bool(parsed.get("is_series")),
        series_name=parsed.get("series_name"),
        part_number=parsed.get("part_number"),
        total_parts=parsed.get("total_parts"),
        series_note=parsed.get("series_note"),
        average_rating=parsed.get("average_rating"),
        ratings_count=parsed.get("ratings_count"),
        is_ebook=parsed.get("is_ebook"),
        is_audiobook=parsed.get("is_audiobook"),
        for_sale=parsed.get("for_sale"),
        marketplaces=parsed.get("marketplaces"),
        buy_link=parsed.get("buy_link"),
        market_note=parsed.get("market_note"),
    )
