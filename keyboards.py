from typing import Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Сколько элементов показываем на одной странице списка (поиск / библиотека / избранное).
PAGE_SIZE = 5

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить книгу"), KeyboardButton(text="🔎 Проверить книгу")],
        [KeyboardButton(text="📚 Моя библиотека"), KeyboardButton(text="⭐ Избранное")],
        [KeyboardButton(text="🗂 Коллекции"), KeyboardButton(text="✍️ Заметки об авторах")],
    ],
    resize_keyboard=True,
)

# Тексты всех кнопок главного меню. Используем как "стоп-слова" в FSM-хендлерах,
# ожидающих свободный текст (название книги, имя автора и т.п.) — если человек
# вместо ответа нажал кнопку меню, это НЕ должно восприниматься как ответ.
MENU_TEXTS = {btn.text for row in MAIN_MENU.keyboard for btn in row}


def paginated_list_kb(total_count: int, page: int, prefix: str, page_size: int = PAGE_SIZE) -> InlineKeyboardMarkup:
    """
    Универсальная клавиатура для постраничного списка (поиск книг, библиотека,
    избранное). На странице — до page_size кнопок с номерами элементов, затем
    навигация и отмена.

    Callback-схема:
      {prefix}:show:<abs_index>  — открыть карточку элемента (описание, рейтинг и т.д.)
      {prefix}:page:<page_num>   — переключить страницу
      {prefix}:cancel            — отмена / закрыть список
    """
    start = page * page_size
    end = min(start + page_size, total_count)

    buttons = [
        [InlineKeyboardButton(text=f"№{i + 1}", callback_data=f"{prefix}:show:{i}")]
        for i in range(start, end)
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред. страница", callback_data=f"{prefix}:page:{page - 1}"))
    if end < total_count:
        nav_row.append(InlineKeyboardButton(text="Следующая страница ➡️", callback_data=f"{prefix}:page:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def search_card_kb(idx: int, prefix: str) -> InlineKeyboardMarkup:
    """Карточка одного результата поиска (перед добавлением в библиотеку)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить в библиотеку", callback_data=f"{prefix}:add:{idx}")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{prefix}:back")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}:cancel")],
        ]
    )


def library_card_kb(book_id: int, buy_link: Optional[str] = None) -> InlineKeyboardMarkup:
    """Карточка книги из библиотеки/избранного."""
    rows = [
        [
            InlineKeyboardButton(text="⭐ Избранное вкл/выкл", callback_data=f"fav:{book_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{book_id}"),
        ],
        [InlineKeyboardButton(text="🗂 В коллекцию", callback_data=f"tocol:{book_id}")],
    ]
    if buy_link:
        rows.append([InlineKeyboardButton(text="🛒 Купить", url=buy_link)])
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="lib:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def collections_kb(collections, book_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"setcol:{book_id}:{c.id}")]
        for c in collections
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="setcol:cancel")])
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
