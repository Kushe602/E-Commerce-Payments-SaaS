"""Async SQLAlchemy 2.0 engine, session factory, and declarative base."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# SQLite has no row-level locking; ``SELECT ... FOR UPDATE`` is a Postgres-only
# safeguard we apply when fulfilling orders to serialise concurrent stock writes.
SUPPORTS_ROW_LOCKING = settings.database_url.startswith("postgresql")


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
