from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
from config import COMMUNITY_CHAT_ID
from keyboards import collections_kb, sentiment_kb
from states import AddToCollection, AuthorNoteFSM, NewCollection, ShareReview

router = Router()


# ---------- Коллекции ----------


@router.message(F.text == "🗂 Коллекции")
@router.message(Command("collections"))
async def collections_menu(message: Message) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    cols = await db.get_collections(user.id)
    if not cols:
        await message.answer("У тебя пока нет коллекций. Напиши /newcollection, чтобы создать первую.")
        return
    lines = [f"• {c.name}" for c in cols]
    await message.answer(
        "Твои коллекции:\n" + "\n".join(lines) + "\n\nЧтобы создать новую — /newcollection"
    )


@router.message(Command("newcollection"))
async def new_collection_start(message: Message, state: FSMContext) -> None:
    await state.set_state(NewCollection.waiting_name)
    await message.answer("Как назвать коллекцию? (например «Хочу прочитать» или «Фэнтези»)")


@router.message(NewCollection.waiting_name)
async def new_collection_name(message: Message, state: FSMContext) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    col = await db.create_collection(user.id, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Коллекция «{col.name}» создана.")


@router.callback_query(F.data.startswith("tocol:"))
async def to_collection_start(callback: CallbackQuery, state: FSMContext) -> None:
    book_id = int(callback.data.split(":")[1])
    user = await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    cols = await db.get_collections(user.id)
    if not cols:
        await callback.answer("Сначала создай коллекцию через /newcollection", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("В какую коллекцию добавить?", reply_markup=collections_kb(cols, book_id))


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
    await state.set_state(AuthorNoteFSM.waiting_author)
    await message.answer(
        "Имя автора? (Напиши имя, чтобы посмотреть заметки о нём, "
        "или чтобы добавить новую заметку.)"
    )


@router.message(AuthorNoteFSM.waiting_author)
async def author_note_author(message: Message, state: FSMContext) -> None:
    author = message.text.strip()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    existing = await db.get_author_notes(user.id, author)

    if existing:
        lines = []
        for n in existing:
            emoji = {"like": "👍", "dislike": "👎", "neutral": "😐"}.get(n.sentiment, "")
            note_text = f" — {n.note}" if n.note else ""
            lines.append(f"{emoji} {n.author_name}{note_text}")
        await message.answer("Твои заметки об этом авторе:\n" + "\n".join(lines))

    await state.update_data(author=author)
    await state.set_state(AuthorNoteFSM.waiting_sentiment)
    await message.answer("Хочешь добавить новую заметку? Отметь отношение:", reply_markup=sentiment_kb())


@router.callback_query(AuthorNoteFSM.waiting_sentiment, F.data.startswith("sent:"))
async def author_note_sentiment(callback: CallbackQuery, state: FSMContext) -> None:
    sentiment = callback.data.split(":")[1]
    await state.update_data(sentiment=sentiment)
    await callback.answer()
    await callback.message.answer("Напиши короткий комментарий (или «-», если не нужен).")
    await state.set_state(AuthorNoteFSM.waiting_text)


@router.message(AuthorNoteFSM.waiting_text)
async def author_note_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    note_text = None if message.text.strip() == "-" else message.text.strip()
    await db.add_author_note(user.id, data["author"], data["sentiment"], note_text)
    await state.clear()
    await message.answer(f"✅ Заметка про «{data['author']}» сохранена.")


# ---------- Поделиться отзывом (мини-версия "буктока") ----------


@router.message(F.text == "💬 Поделиться отзывом")
@router.message(Command("share"))
async def share_start(message: Message, state: FSMContext) -> None:
    if not COMMUNITY_CHAT_ID:
        await message.answer(
            "Функция «поделиться» пока не настроена: нужно указать COMMUNITY_CHAT_ID "
            "(ID канала/чата, куда будут падать отзывы) в переменных окружения."
        )
        return
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username)
    books = await db.get_library(user.id)
    if not books:
        await message.answer("Сначала добавь хотя бы одну книгу в библиотеку.")
        return
    await state.set_state(ShareReview.waiting_book_choice)
    lines = [f"{i + 1}. {b.title}" for i, b in enumerate(books[:15])]
    await state.update_data(books=[b.id for b in books[:15]], titles=[b.title for b in books[:15]])
    await message.answer("О какой книге отзыв? Напиши номер:\n" + "\n".join(lines))


@router.message(ShareReview.waiting_book_choice)
async def share_pick_book(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        idx = int(message.text.strip()) - 1
        title = data["titles"][idx]
    except (ValueError, IndexError):
        await message.answer("Напиши просто номер из списка.")
        return
    await state.update_data(chosen_title=title)
    await state.set_state(ShareReview.waiting_text)
    await message.answer(f"Напиши, что думаешь о книге «{title}» — отправлю в общий чат.")


@router.message(ShareReview.waiting_text)
async def share_publish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    title = data["chosen_title"]
    username = message.from_user.username or message.from_user.full_name

    await bot.send_message(
        COMMUNITY_CHAT_ID,
        f"📖 Отзыв на «{title}» от @{username}:\n\n{message.text}",
    )
    await state.clear()
    await message.answer("✅ Отправлено в общий чат!")
