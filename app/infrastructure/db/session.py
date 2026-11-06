from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import app.infrastructure.db.ids  # noqa: F401  (registra el generador de ids en before_flush)
from app.core.config import get_database_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_database_settings().sqlalchemy_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por solicitud."""
    async with get_sessionmaker()() as session:
        yield session
