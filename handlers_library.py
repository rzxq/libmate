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
    collections_kb,
    library_card_kb,
    paginated_list_kb,
    search_card_kb,
)
from states import AddBook, CheckBook

router = Router()

# Временное хранилище результатов поиска и текущей страницы, на время выбора
# пользователем. Формат: {tg_id: {"results": [...], "page": 0}}
_search_cache: dict[int, dict] = {}

# То же самое для списка книг библиотеки/избранного, чтобы постранично
# листать без повторных походов в БД на каждый клик.
# Формат: {tg_id: {"books": [...], "page": 0, "header": "..."}}
_library_cache: dict[int, dict] = {}

# Фильтр: сообщение НЕ является нажатием кнопки главного меню.
# Вешаем его на все "жду свободный текст" хендлеры, чтобы кнопка меню
# никогда не воспринималась как ответ на вопрос — даже если бот почему-то
# застрял в старом состоянии.
NOT_MENU_BUTTON = ~F.text.in_(MENU_TEXTS)


def _list_text(items, page: int, header: str) -> str:
    """Текст страницы списка: заголовок + пронумерованные строки + номер страницы."""
    start = page * PAGE_SIZE
    chunk = items[start:start + PAGE_SIZE]
    lines = [f"{start + i + 1}. {it.title} — {it.author}" for i, it in enumerate(chunk)]
    total_pages = max(1, (len(items) - 1) // PAGE_SIZE + 1)
    return f"{header}\n\n" + "\n".join(lines) + f"\n\nСтраница {page + 1} из {total_pages}."


def _book_card_text(b) -> str:
    """Полная карточка книги: описание, рейтинг, доступность, покупка.
    Работает и с BookInfo (результат поиска), и с db.Book (книга в библиотеке) —
    у обоих есть нужные атрибуты."""
    lines = [f"📖 <b>{b.title}</b>", f"👤 {b.author}"]

    series_name = getattr(b, "series_name", None)
    if series_name:
        part = getattr(b, "series_part", None)
        total = getattr(b, "series_total", None)
        part_str = f" (часть {part} из {total})" if part and total else (f" (часть {part})" if part else "")
        lines.append(f"🔗 Цикл: {series_name}{part_str}")

    lines.append(services.format_rating(getattr(b, "average_rating", None), getattr(b, "ratings_count", None)))
    lines.append(services.format_availability(b))
    lines.append(services.format_purchase(b))

    description = getattr(b, "description", None)
    if description:
        short = description if len(description) <= 700 else description[:700].rstrip() + "…"
        lines.append(f"\n📝 {short}")
    else:
        lines.append("\n📝 Описание не найдено.")

    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    await db.get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Я помогу тебе не покупать одну и ту же книгу дважды 📚\n\n"
        "— «➕ Добавить книгу» — заношу книгу в твою библиотеку, проверяю, "
        "есть ли у неё цикл/серия и какая это часть, а заодно смотрю рейтинг, "
        "доступность в электронном виде и можно ли её купить.\n"
        "— «🔎 Проверить книгу» — узнать, есть ли она уже у тебя, прежде чем покупать.\n"
        "— «📚 Моя библиотека» / «⭐ Избранное» / «🗂 Коллекции» / «✍️ Заметки об авторах».",
        reply_markup=MAIN_MENU,
    )


# ---------- Добавление книги ----------

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
        # Важно: сбрасываем состояние, иначе бот навсегда "застрянет" в режиме
        # ожидания названия книги и будет перехватывать вообще все сообщения,
        # включая нажатия кнопок меню.
        await state.clear()
        return

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)

    if len(results) == 1:
        # Один явный вариант — не мучаем пользователя выбором, добавляем сразу.
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
    """Общая логика подтверждения и сохранения книги — используется и при
    единственном найденном варианте (авто), и при ручном выборе из списка."""
    series = await services.check_series_info(chosen.title, chosen.author)

    book = await db.add_book(
        user_id=user.id,
        title=chosen.title,
        author=chosen.author,
        cover_url=chosen.cover_url,
        description=chosen.description,
        series_name=series.series_name,
        series_part=series.part_number,
        series_total=series.total_parts,
        average_rating=chosen.average_rating,
        ratings_count=chosen.ratings_count,
        is_ebook=chosen.is_ebook,
        for_sale=chosen.for_sale,
        buy_link=chosen.buy_link,
    )

    text = f"✅ Нашёл и добавил: «{book.title}» — {book.author}\n"
    if series.is_series and series.series_name:
        text += f"\n📖 Это часть цикла «{series.series_name}»"
        if series.part_number and series.total_parts:
            text += f", часть {series.part_number} из {series.total_parts}."
            owned = await db.get_series_books(user.id, series.series_name)
            owned_parts = sorted(b.series_part for b in owned if b.series_part)
            missing = [p for p in range(1, series.total_parts + 1) if p not in owned_parts]
            if missing:
                text += f"\n⚠️ У тебя пока нет частей: {', '.join(map(str, missing))}."
        elif series.part_number:
            text += f", часть {series.part_number}."
        else:
            text += "."
    else:
        text += "\nПохоже, отдельная книга, не часть цикла."
    if series.note:
        text += f"\n\nℹ️ {series.note}"

    text += "\n\n" + services.format_rating(chosen.average_rating, chosen.ratings_count)
    text += "\n" + services.format_availability(chosen)
    text += "\n" + services.format_purchase(chosen)

    sent = await bot.send_message(chat_id, text, reply_markup=library_card_kb(book.id, buy_link=book.buy_link))
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
        await callback.message.edit_text("Добавление отменено.")
        return

    if not data:
        await callback.message.edit_text("Список устарел, начни заново через «➕ Добавить книгу».")
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
        text = _list_text(results, page, "Нашёл несколько вариантов, выбери нужный:")
        kb = paginated_list_kb(len(results), page, "addpick")
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "show":
        idx = int(rest[0])
        if idx >= len(results):
            await callback.message.edit_text("Что-то пошло не так, попробуй ещё раз.")
            return
        chosen = results[idx]
        await callback.message.edit_text(_book_card_text(chosen), reply_markup=search_card_kb(idx, "addpick"))
        return

    if action == "add":
        idx = int(rest[0])
        if idx >= len(results):
            await callback.message.edit_text("Что-то пошло не так, попробуй ещё раз.")
            return
        chosen = results[idx]
        user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
        await callback.message.edit_text(f"Добавляю «{chosen.title}»… ⏳")
        await _finalize_add_book(callback.bot, callback.message.chat.id, user, chosen)
        await state.clear()
        _search_cache.pop(user_id, None)
        return


# ---------- Проверка "есть ли у меня книга" ----------

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
    series = await services.check_series_info(chosen.title, chosen.author)

    text = f"❌ У тебя её нет: «{chosen.title}» — {chosen.author}\n"
    if series.is_series and series.series_name:
        text += f"\n📖 Входит в цикл «{series.series_name}»"
        if series.part_number and series.total_parts:
            text += f", часть {series.part_number} из {series.total_parts}."
        elif series.part_number:
            text += f", часть {series.part_number}."
        else:
            text += "."
    if series.note:
        text += f"\n\nℹ️ {series.note}"

    text += "\n\n" + services.format_rating(chosen.average_rating, chosen.ratings_count)
    text += "\n" + services.format_availability(chosen)
    text += "\n" + services.format_purchase(chosen)

    sent = await message.answer(text)
    track(message.chat.id, sent.message_id)


# ---------- Библиотека и избранное ----------

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
        await callback.message.edit_text("Список закрыт. Открой «📚 Моя библиотека» или «⭐ Избранное» заново.")
        return

    if not data:
        await callback.message.edit_text("Список устарел, открой «📚 Моя библиотека» заново.")
        return

    books, header = data["books"], data["header"]

    if action == "page":
        data["page"] = int(rest[0])
        text = _list_text(books, data["page"], header)
        kb = paginated_list_kb(len(books), data["page"], "lib")
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "back":
        text = _list_text(books, data["page"], header)
        kb = paginated_list_kb(len(books), data["page"], "lib")
        await callback.message.edit_text(text, reply_markup=kb)
        return

    if action == "show":
        idx = int(rest[0])
        if idx >= len(books):
            await callback.message.edit_text("Что-то пошло не так, открой список заново.")
            return
        book = books[idx]
        star = "⭐ В избранном\n\n" if book.is_favorite else ""
        await callback.message.edit_text(
            star + _book_card_text(book),
            reply_markup=library_card_kb(book.id, buy_link=book.buy_link),
        )
        return


@router.callback_query(F.data.startswith("fav:"))
async def toggle_fav(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    book = await db.toggle_favorite(book_id)
    await callback.answer("Готово ⭐" if book and book.is_favorite else "Убрано из избранного")
    if book:
        star = "⭐ В избранном\n\n" if book.is_favorite else ""
        try:
            await callback.message.edit_text(
                star + _book_card_text(book),
                reply_markup=library_card_kb(book.id, buy_link=book.buy_link),
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("del:"))
async def delete_book_cb(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    ok = await db.delete_book(book_id)
    await callback.answer("Удалено" if ok else "Не найдено")
    if ok:
        await callback.message.edit_text("🗑 Книга удалена из библиотеки.", reply_markup=None)
