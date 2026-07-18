"""
Внешние источники данных о книгах.

Используем ТОЛЬКО Google Books API — бесплатно.
Google Books даёт: название, автор, обложка, описание.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from config import GOOGLE_BOOKS_API_KEY

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_BOOKS_MAX_RESULTS = 20


@dataclass
class BookInfo:
    title: str
    author: str
    cover_url: Optional[str] = None
    description: Optional[str] = None


@dataclass
class BookContext:
    pass


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
    if cover:
        cover = cover.replace("http://", "https://").replace("zoom=1", "zoom=6")
    description = info.get("description")
    return BookInfo(title=title, author=authors, cover_url=cover, description=description)


async def search_book(query: str, prefer_russian: bool = True) -> list[BookInfo]:
    items = await _google_books_request(query, "ru" if prefer_russian else None)
    if not items and prefer_russian:
        items = await _google_books_request(query, None)
    return [_parse_volume(item) for item in items]


async def check_book_context(title: str, author: str) -> BookContext:
    return BookContext()
