import time
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    pass


# Global engine and sessionmaker references
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_db_engine() -> AsyncEngine:
    """Return or initialize the singleton SQLAlchemy async engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return or initialize the singleton async sessionmaker."""
    global _session_factory
    if _session_factory is None:
        engine = get_db_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an asynchronous database session with clean lifecycle."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def ping_database() -> tuple[bool, dict[str, Any] | None]:
    """
    Execute a lightweight query against the database to check connectivity and measure latency.
    Returns (True, details) if healthy, (False, details) otherwise.
    """
    start_time = time.perf_counter()
    try:
        engine = get_db_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return True, {"latency_ms": latency_ms}
    except Exception as exc:
        return False, {"error": str(exc)}


async def close_db_engine() -> None:
    """Cleanly dispose of the database connection pool on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
