"""Database engine and session factories (async for FastAPI, sync for Celery)."""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — registers all ORM models with the mapper registry
from app.core.config import settings

# Async engine for FastAPI. Pool sizes are env-driven (see Settings.DB_*) so they
# can stay within free/managed Postgres connection caps and scale up on demand.
# pool_pre_ping recovers transparently from connections dropped by the provider.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_ENV == "development",
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    connect_args={"timeout": 10, "command_timeout": 10},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Sync engine for Celery workers and Alembic migrations (psycopg2 driver).
# Kept small by default; raise SYNC_DB_* on a larger plan with more worker concurrency.
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    pool_pre_ping=True,
    pool_size=settings.SYNC_DB_POOL_SIZE,
    max_overflow=settings.SYNC_DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
)

SyncSessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)


async def get_async_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
