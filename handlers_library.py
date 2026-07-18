from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
import services
from chat_cleanup import clean, track
from keyboards import (
    MAIN_MENU,
    MENU_TEXTS,
    PAGE_SIZE,
    library_card_kb,
    paginated_list_kb,
    search_card_kb,
)
from states import AddBook, CheckBook

router = Router()

_search_cache: dict[int, dict] = {}
_library_cache: dict[int, dict] = {}
_context_cache: dict[str, services.BookContext] = {}

NOT_MENU_BUTTON = ~F.text.in_(MENU_TEXTS)


async def _get_context(title: str, author: str) -> services.BookContext:
    key = f"{title.strip().lower()}|{author.strip().lower()}"
    cached = _context_cache.get(key)
    if cached is not None:
        return cached
    context = await services.check_book_context(title, author)
    _context_cache[key] = context
    return context


def _list_text(items, page: int, header: str) -> str:
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]
    lines = [f"{start + i + 1}. {it.title} — {it.author}" for i, it in enumerate(chunk)]
    total_pages = max(1, (len(items) - 1) // PAGE_SIZE + 1)
    return f"{header}\n\n" + "\n".join(lines) + f"\n\nСтраница {page + 1} из {total_pages}."


def _book_card_text(b) -> str:
    lines = []
    lines.append(f"📖 <b>{b.title}</b>")
    lines.append(f"👤 {b.author}")

    description = getattr(b, "description", None)
    if description:
        short = description if len(description) <= 700 else description[:700].rstrip() + "…"
        lines.append(f"\n📝 {short}")

    return "\n".join(lines)


async def _send_with_cover(bot, chat_id: int, text: str, cover_url: Optional[str] = None, reply_markup=None) -> Message:
    if cover_url:
        try:
            if len(text) <= 1024:
                return await bot.send_photo(chat_id, photo=cover_url, caption=text, reply_markup=reply_markup)
            await bot.send_photo(chat_id, photo=cover_url)
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            pass
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def _delete_and_send(callback: CallbackQuery, text: str, reply_markup=None) -> Message:
    """Удаляет текущее сообщение (даже фото) и отправляет новый текст."""
    try:
        await callback.message.delete()
    except Exception:
        pass
    sent = await callback.bot.send_message(callback.message.chat.id, text, reply_markup=reply_markup)
    track(callback.message.chat.id, sent.message_id)
    return sent


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    await db.get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Я помогу тебе не покупать одну и ту же книгу дважды 📚\n\n"
        "— «➕ Добавить книгу» — заношу книгу в твою библиотеку.\n"
        "— «🔎 Проверить книгу» — узнать, есть ли она уже у тебя, прежде чем покупать.\n"
        "— «📚 Моя библиотека» / «⭐ Избранное» / «🗂 Коллекции» / «✍️ Заметки об авторах».",
        reply_markup=MAIN_MENU,
    )


@router.message(F.text == "➕ Добавить книгу")
@router.message(Command("add"))
async def add_book_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)
    sent = await message.answer("Напиши название книги (и, если знаешь, автора через запятую).")
    track(message.chat.id, sent.message_id)
    await state.set_state(AddBook.waiting_query)


@router.message(AddBook.waiting_query, NOT_MENU_BUTTON)
async def add_book_query(message: Message, state: FSMContext) -> None:
    track(message.chat.id, message.message_id)
    query = message.text.strip()
    results = await services.search_book(query)

    if not results:
        sent = await message.answer(
            "Не нашёл такую книгу в базе. Проверь написание или укажи автора, например:\n"
            "«Аленький цветочек, Аксаков»."
        )
        track(message.chat.id, sent.message_id)
        await state.clear()
        return

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)

    if len(results) == 1:
        await state.clear()
        await _finalize_add_book(message.bot, message.chat.id, user, results[0])
        return

    _search_cache[message.from_user.id] = {"results": results, "page": 0}
    text = _list_text(results, 0, "Нашёл несколько вариантов, выбери нужный:")
    kb = paginated_list_kb(len(results), 0, "addpick")
    sent = await message.answer(text, reply_markup=kb)
    track(message.chat.id, sent.message_id)
    await state.set_state(AddBook.waiting_choice)


async def _finalize_add_book(bot, chat_id: int, user: db.User, chosen: services.BookInfo) -> Message:
    book = await db.add_book(
        user_id=user.id,
        title=chosen.title,
        author=chosen.author,
        cover_url=chosen.cover_url,
        description=chosen.description,
    )

    text = f"✅ Нашёл и добавил: «{book.title}» — {book.author}"
    sent = await _send_with_cover(bot, chat_id, text, book.cover_url, reply_markup=library_card_kb(book.id))
    track(chat_id, sent.message_id)
    return sent


@router.callback_query(AddBook.waiting_choice, F.data.startswith("addpick:"))
async def addpick_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, action, *rest = callback.data.split(":")
    user_id = callback.from_user.id
    data = _search_cache.get(user_id)

    if action == "cancel":
        await state.clear()
        _search_cache.pop(user_id, None)
        await _delete_and_send(callback, "Добавление отменено.")
        return

    if not data:
        await _delete_and_send(callback, "Список устарел, начни заново через «➕ Добавить книгу».")
        await state.clear()
        return

    results, page = data["results"], data["page"]

    if action == "page":
        data["page"] = int(rest[0])
        text = _list_text(results, data["page"], "Нашёл несколько вариантов, выбери нужный:")
        kb = paginated_list_kb(len(results), data["page"], "addpick")
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "back":
        await callback.message.delete()
        text = _list_text(results, page, "Нашёл несколько вариантов, выбери нужный:")
        kb = paginated_list_kb(len(results), page, "addpick")
        sent = await callback.bot.send_message(callback.message.chat.id, text, reply_markup=kb)
        track(callback.message.chat.id, sent.message_id)
        return

    if action == "show":
        idx = int(rest[0])
        if idx >= len(results):
            await _delete_and_send(callback, "Что-то пошло не так, попробуй ещё раз.")
            return
        chosen = results[idx]
        text = _book_card_text(chosen)
        await callback.message.delete()
        sent = await _send_with_cover(callback.bot, callback.message.chat.id, text, chosen.cover_url, reply_markup=search_card_kb(idx, "addpick"))
        track(callback.message.chat.id, sent.message_id)
        return

    if action == "add":
        idx = int(rest[0])
        if idx >= len(results):
            await _delete_and_send(callback, "Что-то пошло не так, попробуй ещё раз.")
            return
        chosen = results[idx]
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        await _delete_and_send(callback, f"Добавляю «{chosen.title}»… ⏳")
        await _finalize_add_book(callback.bot, callback.message.chat.id, user, chosen)
        await state.clear()
        _search_cache.pop(user_id, None)
        return


@router.message(F.text == "🔎 Проверить книгу")
@router.message(Command("check"))
async def check_book_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)
    sent = await message.answer("Какую книгу проверить?")
    track(message.chat.id, sent.message_id)
    await state.set_state(CheckBook.waiting_query)


@router.message(CheckBook.waiting_query, NOT_MENU_BUTTON)
async def check_book_query(message: Message, state: FSMContext) -> None:
    await state.clear()
    track(message.chat.id, message.message_id)
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    query = message.text.strip()

    owned = await db.find_books_by_title(user.id, query)
    if owned:
        lines = []
        for b in owned:
            part = f" (часть {b.series_part})" if b.series_part else ""
            lines.append(f"• {b.title} — {b.author}{part}")
        sent = await message.answer("✅ Уже есть в твоей библиотеке:\n" + "\n".join(lines))
        track(message.chat.id, sent.message_id)
        return

    searching = await message.answer("В твоей библиотеке такой книги нет. Ищу информацию о ней… ⏳")
    track(message.chat.id, searching.message_id)

    results = await services.search_book(query)
    if not results:
        sent = await message.answer("❌ Не нашёл эту книгу вообще. Можешь смело покупать — точно нет в базе.")
        track(message.chat.id, sent.message_id)
        return

    chosen = results[0]
    text = f"❌ У тебя её нет: «{chosen.title}» — {chosen.author}"
    sent = await _send_with_cover(message.bot, message.chat.id, text, chosen.cover_url)
    track(message.chat.id, sent.message_id)


@router.message(F.text == "📚 Моя библиотека")
@router.message(Command("library"))
async def show_library(message: Message, state: FSMContext) -> None:
    await _show_books(message, state, only_favorites=False)


@router.message(F.text == "⭐ Избранное")
@router.message(Command("favorites"))
async def show_favorites(message: Message, state: FSMContext) -> None:
    await _show_books(message, state, only_favorites=True)


async def _show_books(message: Message, state: FSMContext, only_favorites: bool) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    books = await db.get_library(user.id, only_favorites=only_favorites)

    if not books:
        label = "избранных книг" if only_favorites else "книг в библиотеке"
        sent = await message.answer(f"Пока нет {label}.")
        track(message.chat.id, sent.message_id)
        return

    header = "⭐ Твоё избранное:" if only_favorites else "📚 Твоя библиотека:"
    books = list(books)
    _library_cache[message.from_user.id] = {"books": books, "page": 0, "header": header}

    text = _list_text(books, 0, header)
    kb = paginated_list_kb(len(books), 0, "lib")
    sent = await message.answer(text, reply_markup=kb)
    track(message.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("lib:"))
async def lib_cb(callback: CallbackQuery) -> None:
    await callback.answer()
    _, action, *rest = callback.data.split(":")
    user_id = callback.from_user.id
    data = _library_cache.get(user_id)

    if action == "cancel":
        _library_cache.pop(user_id, None)
        await _delete_and_send(callback, "Список закрыт. Открой «📚 Моя библиотека» или «⭐ Избранное» заново.")
        return

    if not data:
        await _delete_and_send(callback, "Список устарел, открой «📚 Моя библиотека» заново.")
        return

    books, header = data["books"], data["header"]

    if action == "page":
        data["page"] = int(rest[0])
        text = _list_text(books, data["page"], header)
        kb = paginated_list_kb(len(books), data["page"], "lib")
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "back":
        await callback.message.delete()
        text = _list_text(books, data["page"], header)
        kb = paginated_list_kb(len(books), data["page"], "lib")
        sent = await callback.bot.send_message(callback.message.chat.id, text, reply_markup=kb)
        track(callback.message.chat.id, sent.message_id)
        return

    if action == "show":
        idx = int(rest[0])
        if idx >= len(books):
            await _delete_and_send(callback, "Что-то пошло не так, открой список заново.")
            return
        book = books[idx]
        star = "⭐ В избранном\n\n" if book.is_favorite else ""
        text = star + _book_card_text(book)
        await callback.message.delete()
        sent = await _send_with_cover(callback.bot, callback.message.chat.id, text, book.cover_url, reply_markup=library_card_kb(book.id))
        track(callback.message.chat.id, sent.message_id)
        return


@router.callback_query(F.data.startswith("fav:"))
async def toggle_fav(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    book = await db.toggle_favorite(book_id)
    await callback.answer("Готово ⭐" if book and book.is_favorite else "Убрано из избранного")
    if book:
        star = "⭐ В избранном\n\n" if book.is_favorite else ""
        text = star + _book_card_text(book)
        try:
            await callback.message.delete()
            sent = await _send_with_cover(callback.bot, callback.message.chat.id, text, book.cover_url, reply_markup=library_card_kb(book.id))
            track(callback.message.chat.id, sent.message_id)
        except Exception:
            pass


@router.callback_query(F.data.startswith("del:"))
async def delete_book_cb(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    ok = await db.delete_book(book_id)
    await callback.answer("Удалено" if ok else "Не найдено")
    if ok:
        await _delete_and_send(callback, "🗑 Книга удалена из библиотеки.")
