from aiogram.fsm.state import State, StatesGroup


class AddBook(StatesGroup):
    waiting_query = State()
    waiting_choice = State()


class CheckBook(StatesGroup):
    waiting_query = State()


class NewCollection(StatesGroup):
    waiting_name = State()


class AddToCollection(StatesGroup):
    waiting_book_choice = State()
    waiting_collection_choice = State()


class AuthorNoteFSM(StatesGroup):
    waiting_author = State()
    waiting_sentiment = State()
    waiting_text = State()


class ShareReview(StatesGroup):
    waiting_book_choice = State()
    waiting_text = State()
