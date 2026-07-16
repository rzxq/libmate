from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
import services
from config import FREE_BOOK_LIMIT
from keyboards import MAIN_MENU, book_actions_kb, book_choices_kb
from states import AddBook, CheckBook

router = Router()

# Временное хранилище найденных вариантов книг на время выбора пользователем
_search_cache: dict[int, list[services.BookInfo]] = {}


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
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
    await state.set_state(AddBook.waiting_query)
    await message.answer("Напиши название книги (и, если знаешь, автора через запятую).")


@router.message(AddBook.waiting_query)
async def add_book_query(message: Message, state: FSMContext) -> None:
    query = message.text.strip()
    results = await services.search_book(query)
    if not results:
        await message.answer(
            "Не нашёл такую книгу в базе. Проверь написание или укажи автора, например:\n"
            "«Аленький цветочек, Аксаков»."
        )
        return

    _search_cache[message.from_user.id] = results
    lines = [f"{i + 1}. {r.title} — {r.author}" for i, r in enumerate(results)]
    await message.answer(
        "Нашёл несколько вариантов, выбери нужный:\n" + "\n".join(lines),
        reply_markup=book_choices_kb(len(results), "addpick"),
    )
    await state.set_state(AddBook.waiting_choice)


@router.callback_query(AddBook.waiting_choice, F.data.startswith("addpick:"))
async def add_book_pick(callback: CallbackQuery, state: FSMContext) -> None:
    _, raw_idx = callback.data.split(":")
    await callback.answer()

    if raw_idx == "cancel":
        await state.clear()
        await callback.message.edit_text("Добавление отменено.")
        return

    idx = int(raw_idx)
    results = _search_cache.get(callback.from_user.id, [])
    if idx >= len(results):
        await callback.message.edit_text("Что-то пошло не так, попробуй ещё раз.")
        await state.clear()
        return

    chosen = results[idx]
    user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    pro = db.is_pro(user)

    if not pro and await db.count_books(user.id) >= FREE_BOOK_LIMIT:
        await callback.message.edit_text(
            f"На бесплатном тарифе можно хранить до {FREE_BOOK_LIMIT} книг, лимит достигнут.\n"
            "Оформи 💎 Подписку — снимет лимит и включит определение циклов через ИИ."
        )
        await state.clear()
        _search_cache.pop(callback.from_user.id, None)
        return

    if pro:
        await callback.message.edit_text(f"Добавляю «{chosen.title}» и проверяю, есть ли у неё цикл… ⏳")
        series = await services.check_series_info(chosen.title, chosen.author)
    else:
        await callback.message.edit_text(f"Добавляю «{chosen.title}»…")
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

    text = f"✅ Добавлено: «{book.title}» — {book.author}\n"
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

    await callback.message.answer(text, reply_markup=book_actions_kb(book.id))
    await state.clear()
    _search_cache.pop(callback.from_user.id, None)


# ---------- Проверка "есть ли у меня книга" ----------


@router.message(F.text == "🔎 Проверить книгу")
@router.message(Command("check"))
async def check_book_start(message: Message, state: FSMContext) -> None:
    await state.set_state(CheckBook.waiting_query)
    await message.answer("Какую книгу проверить?")


@router.message(CheckBook.waiting_query)
async def check_book_query(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    query = message.text.strip()

    owned = await db.find_books_by_title(user.id, query)
    if owned:
        lines = []
        for b in owned:
            part = f" (часть {b.series_part})" if b.series_part else ""
            lines.append(f"• {b.title} — {b.author}{part}")
        await message.answer("✅ Уже есть в твоей библиотеке:\n" + "\n".join(lines))
        return

    await message.answer("В твоей библиотеке такой книги нет. Ищу информацию о ней… ⏳")
    results = await services.search_book(query)
    if not results:
        await message.answer("❌ Не нашёл эту книгу вообще. Можешь смело покупать — точно нет в базе.")
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

    await message.answer(text)


# ---------- Библиотека и избранное ----------


@router.message(F.text == "📚 Моя библиотека")
@router.message(Command("library"))
async def show_library(message: Message) -> None:
    await _show_books(message, only_favorites=False)


@router.message(F.text == "⭐ Избранное")
@router.message(Command("favorites"))
async def show_favorites(message: Message) -> None:
    await _show_books(message, only_favorites=True)


async def _show_books(message: Message, only_favorites: bool) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    books = await db.get_library(user.id, only_favorites=only_favorites)
    if not books:
        label = "избранных книг" if only_favorites else "книг в библиотеке"
        await message.answer(f"Пока нет {label}.")
        return

    for b in books[:20]:
        star = "⭐ " if b.is_favorite else ""
        part = f" (часть {b.series_part} из {b.series_total})" if b.series_part else ""
        series = f"\n📖 Цикл: {b.series_name}{part}" if b.series_name else ""
        await message.answer(
            f"{star}«{b.title}» — {b.author}{series}",
            reply_markup=book_actions_kb(b.id),
        )
    if len(books) > 20:
        await message.answer(f"…и ещё {len(books) - 20}. Уточни поиск через «🔎 Проверить книгу».")


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
