import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DemoBase(DeclarativeBase):
    """Base declarative class for Demo Application models."""

    pass


class OrderModel(DemoBase):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PaymentModel(DemoBase):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("idx_payments_order_status", "order_id", "status"),)


_demo_engine: AsyncEngine | None = None
_demo_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_demo_database_url() -> str:
    """Return the demo database URL from environment."""
    return os.getenv(
        "DEMO_DATABASE_URL",
        "postgresql+asyncpg://demo_user:demo_password@localhost:5434/demo_db",
    )


def get_demo_engine(database_url: str | None = None) -> AsyncEngine:
    """Create or return the singleton async SQLAlchemy engine for Demo PostgreSQL."""
    global _demo_engine
    if _demo_engine is None:
        url = database_url or get_demo_database_url()
        _demo_engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=2,
            pool_timeout=5.0,  # Bounded timeout so pool exhaustion faults fail predictably
        )
    return _demo_engine


def get_demo_sessionmaker(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Create or return the singleton sessionmaker."""
    global _demo_sessionmaker
    if _demo_sessionmaker is None:
        eng = engine or get_demo_engine()
        _demo_sessionmaker = async_sessionmaker(
            bind=eng,
            class_=AsyncSession,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _demo_sessionmaker


async def init_demo_db(engine: AsyncEngine | None = None) -> None:
    """Initialize tables in Demo PostgreSQL."""
    eng = engine or get_demo_engine()
    async with eng.begin() as conn:
        await conn.run_sync(DemoBase.metadata.create_all)


async def get_demo_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    session_factory = get_demo_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
