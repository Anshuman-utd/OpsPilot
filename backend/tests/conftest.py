from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.main import app
from tests.fake_redis import FakeRedis


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an asynchronous HTTP test client bound to the FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def sqlite_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async database session fixture for isolated unit testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_redis_remediation(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> FakeRedis | None:
    """Provide isolated FakeRedis client for remediation distributed locking tests unless marked live_redis."""
    if "live_redis" in request.keywords:
        return None
    fake = FakeRedis()
    monkeypatch.setattr("app.remediation.distributed_lock.get_redis_client", lambda: fake)
    return fake
