from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
import services
from chat_cleanup import clean, track
from config import FREE_BOOK_LIMIT
from keyboards import MAIN_MENU, MENU_TEXTS, book_actions_kb, book_choices_kb
from states import AddBook, CheckBook

router = Router()

# Временное хранилище найденных вариантов книг на время выбора пользователем
_search_cache: dict[int, list[services.BookInfo]] = {}

# Фильтр: сообщение НЕ является нажатием кнопки главного меню.
# Вешаем его на все "жду свободный текст" хендлеры, чтобы кнопка меню
# никогда не воспринималась как ответ на вопрос — даже если бот почему-то
# застрял в старом состоянии.
NOT_MENU_BUTTON = ~F.text.in_(MENU_TEXTS)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    await db.get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Я помогу тебе не покупать одну и ту же книгу дважды 📚\n\n"
        "— «➕ Добавить книгу» — заношу книгу в твою библиотеку и проверяю, "
        "есть ли у неё цикл/серия и какая это часть.\n"
        "— «🔎 Проверить книгу» — узнать, есть ли она уже у тебя, прежде чем покупать.\n"
        "— «📚 Моя библиотека» / «⭐ Избранное» / «🗂 Коллекции» / «✍️ Заметки об авторах».\n\n"
        f"Бесплатно — до {FREE_BOOK_LIMIT} книг без определения циклов. "
        "«💎 Подписка» снимает лимит и включает ИИ-проверку циклов/серий.",
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

    _search_cache[message.from_user.id] = results
    lines = [f"{i + 1}. {r.title} — {r.author}" for i, r in enumerate(results)]
    sent = await message.answer(
        "Нашёл несколько вариантов, выбери нужный:\n" + "\n".join(lines),
        reply_markup=book_choices_kb(len(results), "addpick"),
    )
    track(message.chat.id, sent.message_id)
    await state.set_state(AddBook.waiting_choice)


async def _finalize_add_book(bot, chat_id: int, user: db.User, chosen: services.BookInfo) -> Message:
    """Общая логика подтверждения и сохранения книги — используется и при
    единственном найденном варианте (авто), и при ручном выборе из списка."""
    pro = db.is_pro(user)

    if not pro and await db.count_books(user.id) >= FREE_BOOK_LIMIT:
        sent = await bot.send_message(
            chat_id,
            f"На бесплатном тарифе можно хранить до {FREE_BOOK_LIMIT} книг, лимит достигнут.\n"
            "Оформи 💎 Подписку — снимет лимит и включит определение циклов через ИИ.",
        )
        track(chat_id, sent.message_id)
        return sent

    if pro:
        series = await services.check_series_info(chosen.title, chosen.author)
    else:
        series = services.SeriesInfo(is_series=False)

    book = await db.add_book(
        user_id=user.id,
        title=chosen.title,
        author=chosen.author,
        cover_url=chosen.cover_url,
        description=chosen.description,
        series_name=series.series_name,
        series_part=series.part_number,
        series_total=series.total_parts,
    )

    text = f"✅ Нашёл и добавил: «{book.title}» — {book.author}\n"
    if pro:
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
    else:
        text += "\n💎 Хочешь узнать, часть ли это цикла и какая по счёту? Оформи подписку."

    sent = await bot.send_message(chat_id, text, reply_markup=book_actions_kb(book.id))
    track(chat_id, sent.message_id)
    return sent


@router.callback_query(AddBook.waiting_choice, F.data.startswith("addpick:"))
async def add_book_pick(callback: CallbackQuery, state: FSMContext) -> None:
    _, raw_idx = callback.data.split(":")
    await callback.answer()

    if raw_idx == "cancel":
        await state.clear()
        await callback.message.edit_text("Добавление отменено.")
        _search_cache.pop(callback.from_user.id, None)
        return

    idx = int(raw_idx)
    results = _search_cache.get(callback.from_user.id, [])
    if idx >= len(results):
        await callback.message.edit_text("Что-то пошло не так, попробуй ещё раз.")
        await state.clear()
        return

    chosen = results[idx]
    user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(f"Добавляю «{chosen.title}»… ⏳")

    await _finalize_add_book(callback.bot, callback.message.chat.id, user, chosen)
    await state.clear()
    _search_cache.pop(callback.from_user.id, None)


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
    text = f"❌ У тебя её нет: «{chosen.title}» — {chosen.author}\nМожно покупать!\n"

    if db.is_pro(user):
        series = await services.check_series_info(chosen.title, chosen.author)
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
    else:
        text += "\n💎 С подпиской я бы сразу сказал, часть ли это цикла и какая по счёту."

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

    for b in books[:20]:
        star = "⭐ " if b.is_favorite else ""
        part = f" (часть {b.series_part} из {b.series_total})" if b.series_part else ""
        series = f"\n📖 Цикл: {b.series_name}{part}" if b.series_name else ""
        sent = await message.answer(
            f"{star}«{b.title}» — {b.author}{series}",
            reply_markup=book_actions_kb(b.id),
        )
        track(message.chat.id, sent.message_id)
    if len(books) > 20:
        sent = await message.answer(f"…и ещё {len(books) - 20}. Уточни поиск через «🔎 Проверить книгу».")
        track(message.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("fav:"))
async def toggle_fav(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    book = await db.toggle_favorite(book_id)
    await callback.answer("Готово ⭐" if book and book.is_favorite else "Убрано из избранного")


@router.callback_query(F.data.startswith("del:"))
async def delete_book_cb(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    ok = await db.delete_book(book_id)
    await callback.answer("Удалено" if ok else "Не найдено")
    if ok:
        await callback.message.edit_text("🗑 Книга удалена из библиотеки.")
