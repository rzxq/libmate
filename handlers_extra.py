from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
from chat_cleanup import clean, track
from keyboards import MENU_TEXTS, collections_kb, sentiment_kb
from states import AuthorNoteFSM, NewCollection

router = Router()

NOT_MENU_BUTTON = ~F.text.in_(MENU_TEXTS)


# ---------- Коллекции ----------


@router.message(F.text == "🗂 Коллекции")
@router.message(Command("collections"))
async def collections_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)

    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    cols = await db.get_collections(user.id)
    if not cols:
        sent = await message.answer("У тебя пока нет коллекций. Напиши /newcollection, чтобы создать первую.")
        track(message.chat.id, sent.message_id)
        return
    lines = [f"• {c.name}" for c in cols]
    sent = await message.answer(
        "Твои коллекции:\n" + "\n".join(lines) + "\n\nЧтобы создать новую — /newcollection"
    )
    track(message.chat.id, sent.message_id)


@router.message(Command("newcollection"))
async def new_collection_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)
    sent = await message.answer("Как назвать коллекцию? (например «Хочу прочитать» или «Фэнтези»)")
    track(message.chat.id, sent.message_id)
    await state.set_state(NewCollection.waiting_name)


@router.message(NewCollection.waiting_name, NOT_MENU_BUTTON)
async def new_collection_name(message: Message, state: FSMContext) -> None:
    track(message.chat.id, message.message_id)
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    col = await db.create_collection(user.id, message.text.strip())
    await state.clear()
    sent = await message.answer(f"✅ Коллекция «{col.name}» создана.")
    track(message.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("tocol:"))
async def to_collection_start(callback: CallbackQuery) -> None:
    book_id = int(callback.data.split(":")[1])
    user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    cols = await db.get_collections(user.id)
    if not cols:
        await callback.answer("Сначала создай коллекцию через /newcollection", show_alert=True)
        return
    await callback.answer()
    sent = await callback.message.answer("В какую коллекцию добавить?", reply_markup=collections_kb(cols, book_id))
    track(callback.message.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("setcol:"))
async def set_collection(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    if parts[1] == "cancel":
        await callback.message.edit_text("Отменено.")
        return
    book_id, collection_id = int(parts[1]), int(parts[2])
    await db.assign_book_to_collection(book_id, collection_id)
    await callback.message.edit_text("✅ Книга добавлена в коллекцию.")


# ---------- Заметки об авторах ----------


@router.message(F.text == "✍️ Заметки об авторах")
@router.message(Command("authornote"))
async def author_note_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await clean(message.bot, message.chat.id)
    track(message.chat.id, message.message_id)
    sent = await message.answer(
        "Имя автора? (Напиши имя, чтобы посмотреть заметки о нём, "
        "или чтобы добавить новую заметку — я сначала гляну, нет ли его уже "
        "в твоей библиотеке.)"
    )
    track(message.chat.id, sent.message_id)
    await state.set_state(AuthorNoteFSM.waiting_author)


@router.message(AuthorNoteFSM.waiting_author, NOT_MENU_BUTTON)
async def author_note_author(message: Message, state: FSMContext) -> None:
    track(message.chat.id, message.message_id)
    typed = message.text.strip()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)

    # Пробуем найти этого автора среди уже добавленных книг пользователя —
    # чтобы не заставлять вручную подтверждать написание, если он там уже есть.
    books = await db.get_library(user.id)
    known_authors = {b.author for b in books}
    match = next(
        (a for a in known_authors if typed.lower() in a.lower() or a.lower() in typed.lower()),
        None,
    )
    author = match or typed
    if match:
        sent = await message.answer(f"📚 Нашёл в твоей библиотеке автора «{match}».")
        track(message.chat.id, sent.message_id)

    existing = await db.get_author_notes(user.id, author)
    if existing:
        lines = []
        for n in existing:
            emoji = {"like": "👍", "dislike": "👎", "neutral": "😐"}.get(n.sentiment, "")
            note_text = f" — {n.note}" if n.note else ""
            lines.append(f"{emoji} {n.author_name}{note_text}")
        sent = await message.answer("Твои заметки об этом авторе:\n" + "\n".join(lines))
        track(message.chat.id, sent.message_id)

    await state.update_data(author=author)
    sent = await message.answer("Хочешь добавить новую заметку? Отметь отношение:", reply_markup=sentiment_kb())
    track(message.chat.id, sent.message_id)
    await state.set_state(AuthorNoteFSM.waiting_sentiment)


@router.callback_query(AuthorNoteFSM.waiting_sentiment, F.data.startswith("sent:"))
async def author_note_sentiment(callback: CallbackQuery, state: FSMContext) -> None:
    sentiment = callback.data.split(":")[1]
    await state.update_data(sentiment=sentiment)
    await callback.answer()
    sent = await callback.message.answer("Напиши короткий комментарий (или «-», если не нужен).")
    track(callback.message.chat.id, sent.message_id)
    await state.set_state(AuthorNoteFSM.waiting_text)


@router.message(AuthorNoteFSM.waiting_text, NOT_MENU_BUTTON)
async def author_note_text(message: Message, state: FSMContext) -> None:
    track(message.chat.id, message.message_id)
    data = await state.get_data()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    note_text = None if message.text.strip() == "-" else message.text.strip()
    await db.add_author_note(user.id, data["author"], data["sentiment"], note_text)
    await state.clear()
    sent = await message.answer(f"✅ Заметка про «{data['author']}» сохранена.")
    track(message.chat.id, sent.message_id)
