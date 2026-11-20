"""Database session management and schema bootstrap."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from fsip_common.logging import get_logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

log = get_logger()

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(database_url: str) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
        )
        _sessionmaker = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


def get_sessionmaker(database_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        if database_url is None:
            raise RuntimeError("Database not initialized and no URL provided")
        get_engine(database_url)
    return _sessionmaker  # type: ignore[return-value]


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _sessionmaker is None:
        raise RuntimeError("Database sessionmaker is not configured")
    async with _sessionmaker() as session:
        yield session


async def init_db(database_url: str, max_retries: int = 10, retry_delay: float = 2.0) -> None:
    """Initialize database connection and ensure tables exist."""
    engine = get_engine(database_url)

    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            log.info("db.init_ok", database_url=database_url.split("@")[-1])
            return
        except Exception as e:
            log.warning("db.init_retry", attempt=attempt + 1, error=str(e))
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                log.error("db.init_failed", error=str(e))
                raise
