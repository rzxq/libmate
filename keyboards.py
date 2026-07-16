from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить книгу"), KeyboardButton(text="🔎 Проверить книгу")],
        [KeyboardButton(text="📚 Моя библиотека"), KeyboardButton(text="⭐ Избранное")],
        [KeyboardButton(text="🗂 Коллекции"), KeyboardButton(text="✍️ Заметки об авторах")],
        [KeyboardButton(text="💎 Подписка")],
    ],
    resize_keyboard=True,
)


def book_choices_kb(count: int, prefix: str) -> InlineKeyboardMarkup:
    """Кнопки выбора одного из найденных вариантов книги (индексы 0..count-1)."""
    buttons = [
        [InlineKeyboardButton(text=f"Вариант {i + 1}", callback_data=f"{prefix}:{i}")]
        for i in range(count)
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def book_actions_kb(book_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Избранное вкл/выкл", callback_data=f"fav:{book_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{book_id}"),
            ],
            [InlineKeyboardButton(text="🗂 В коллекцию", callback_data=f"tocol:{book_id}")],
        ]
    )


def collections_kb(collections, book_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"setcol:{book_id}:{c.id}")]
        for c in collections
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="setcol:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def subscription_plans_kb(plans: dict) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{p['title']} — {p['price_rub']}₽", callback_data=f"buy:{key}")]
        for key, p in plans.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sentiment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Нравится", callback_data="sent:like"),
                InlineKeyboardButton(text="👎 Не нравится", callback_data="sent:dislike"),
                InlineKeyboardButton(text="😐 Нейтрально", callback_data="sent:neutral"),
            ]
        ]
    )
