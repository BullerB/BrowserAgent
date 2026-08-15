"""Async engine / session plumbing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webflow.config import Settings, get_settings
from webflow.persistence.models import Base


class Database:
    """Owns the engine and hands out sessions.

    Schema is created with ``create_all``; SQLite plus additive JSON-blob state
    means a migration tool would be overhead at this stage.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._settings.ensure_dirs()
        self._engine: AsyncEngine = create_async_engine(
            self._settings.database_url, future=True
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._initialised = False

    async def create_schema(self) -> None:
        if self._initialised:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._initialised = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        await self.create_schema()
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()


_default: Database | None = None


def get_database() -> Database:
    global _default
    if _default is None:
        _default = Database()
    return _default
