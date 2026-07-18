"""
Слой работы с базой данных.

Используем SQLite через SQLAlchemy (async) — этого более чем достаточно
для личной библиотеки одного или нескольких пользователей и не требует
отдельной БД-инстанции на Railway (экономит ресурсы/деньги).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
    or_,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import DATABASE_PATH

engine = create_async_engine(f"sqlite+aiosqlite:///{DATABASE_PATH}")
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)

    books: Mapped[list["Book"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    collections: Mapped[list["Collection"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    author_notes: Mapped[list["AuthorNote"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))

    owner: Mapped[User] = relationship(back_populates="collections")
    books: Mapped[list["Book"]] = relationship(back_populates="collection")


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    collection_id: Mapped[Optional[int]] = mapped_column(ForeignKey("collections.id"), nullable=True)

    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(500))
    cover_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    series_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    series_part: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    series_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    average_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_ebook: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_audiobook: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    for_sale: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    marketplaces: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    buy_link: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)

    owner: Mapped[User] = relationship(back_populates="books")
    collection: Mapped[Optional[Collection]] = relationship(back_populates="books")


class AuthorNote(Base):
    __tablename__ = "author_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str] = mapped_column(String(500))
    sentiment: Mapped[str] = mapped_column(String(20))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner: Mapped[User] = relationship(back_populates="author_notes")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_or_create_user(tg_id: int, username: Optional[str]) -> User:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if user:
            return user
        user = User(tg_id=tg_id, username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def count_books(user_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(select(Book).where(Book.user_id == user_id))
        return len(result.scalars().all())


async def add_book(
    user_id: int,
    title: str,
    author: str,
    cover_url: Optional[str] = None,
    description: Optional[str] = None,
    series_name: Optional[str] = None,
    series_part: Optional[int] = None,
    series_total: Optional[int] = None,
    average_rating: Optional[float] = None,
    ratings_count: Optional[int] = None,
    is_ebook: Optional[bool] = None,
    is_audiobook: Optional[bool] = None,
    for_sale: Optional[bool] = None,
    marketplaces: Optional[str] = None,
    buy_link: Optional[str] = None,
) -> Book:
    async with async_session() as session:
        book = Book(
            user_id=user_id,
            title=title,
            author=author,
            cover_url=cover_url,
            description=description,
            series_name=series_name,
            series_part=series_part,
            series_total=series_total,
            average_rating=average_rating,
            ratings_count=ratings_count,
            is_ebook=is_ebook,
            is_audiobook=is_audiobook,
            for_sale=for_sale,
            marketplaces=marketplaces,
            buy_link=buy_link,
        )
        session.add(book)
        await session.commit()
        await session.refresh(book)
        return book


async def find_books_by_title(user_id: int, query: str) -> Sequence[Book]:
    like = f"%{query}%"
    async with async_session() as session:
        result = await session.execute(
            select(Book).where(
                Book.user_id == user_id,
                or_(
                    func.lower(Book.title).like(func.lower(like)),
                    func.lower(Book.author).like(func.lower(like)),
                ),
            )
        )
        return result.scalars().all()


async def get_library(user_id: int, only_favorites: bool = False) -> Sequence[Book]:
    async with async_session() as session:
        stmt = select(Book).where(Book.user_id == user_id).order_by(Book.added_at.desc())
        if only_favorites:
            stmt = stmt.where(Book.is_favorite == True)
        result = await session.execute(stmt)
        return result.scalars().all()


async def get_series_books(user_id: int, series_name: str) -> Sequence[Book]:
    async with async_session() as session:
        result = await session.execute(
            select(Book).where(Book.user_id == user_id, Book.series_name == series_name)
        )
        return result.scalars().all()


async def get_book(book_id: int) -> Optional[Book]:
    async with async_session() as session:
        return await session.get(Book, book_id)


async def toggle_favorite(book_id: int) -> Optional[Book]:
    async with async_session() as session:
        book = await session.get(Book, book_id)
        if not book:
            return None
        book.is_favorite = not book.is_favorite
        await session.commit()
        await session.refresh(book)
        return book


async def delete_book(book_id: int) -> bool:
    async with async_session() as session:
        book = await session.get(Book, book_id)
        if not book:
            return False
        await session.delete(book)
        await session.commit()
        return True


async def create_collection(user_id: int, name: str) -> Collection:
    async with async_session() as session:
        col = Collection(user_id=user_id, name=name)
        session.add(col)
        await session.commit()
        await session.refresh(col)
        return col


async def get_collections(user_id: int) -> Sequence[Collection]:
    async with async_session() as session:
        result = await session.execute(select(Collection).where(Collection.user_id == user_id))
        return result.scalars().all()


async def assign_book_to_collection(book_id: int, collection_id: int) -> None:
    async with async_session() as session:
        book = await session.get(Book, book_id)
        if book:
            book.collection_id = collection_id
            await session.commit()


async def add_author_note(user_id: int, author_name: str, sentiment: str, note: Optional[str]) -> AuthorNote:
    async with async_session() as session:
        rec = AuthorNote(user_id=user_id, author_name=author_name, sentiment=sentiment, note=note)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        return rec


async def get_author_notes(user_id: int, author_name: Optional[str] = None) -> Sequence[AuthorNote]:
    async with async_session() as session:
        stmt = select(AuthorNote).where(AuthorNote.user_id == user_id)
        if author_name:
            stmt = stmt.where(AuthorNote.author_name.ilike(f"%{author_name}%"))
        result = await session.execute(stmt)
        return result.scalars().all()
