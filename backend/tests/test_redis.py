from unittest.mock import AsyncMock, patch

import pytest

from app.core.redis import close_redis, get_redis_client, get_redis_pool, ping_redis


@pytest.mark.asyncio
async def test_ping_redis_success() -> None:
    """Test ping_redis returns True and latency when Redis responds with PONG."""
    mock_client = AsyncMock()
    mock_client.ping.return_value = True

    with patch("app.core.redis.get_redis_client", return_value=mock_client):
        is_healthy, details = await ping_redis()
        assert is_healthy is True
        assert details is not None
        assert "latency_ms" in details


@pytest.mark.asyncio
async def test_ping_redis_failure() -> None:
    """Test ping_redis returns False and error message when Redis client raises exception."""
    mock_client = AsyncMock()
    mock_client.ping.side_effect = ConnectionError("Redis server unavailable")

    with patch("app.core.redis.get_redis_client", return_value=mock_client):
        is_healthy, details = await ping_redis()
        assert is_healthy is False
        assert details is not None
        assert "Redis server unavailable" in details["error"]


@pytest.mark.asyncio
async def test_ping_redis_unexpected_response() -> None:
    """Test ping_redis returns False if ping response is falsy."""
    mock_client = AsyncMock()
    mock_client.ping.return_value = False

    with patch("app.core.redis.get_redis_client", return_value=mock_client):
        is_healthy, details = await ping_redis()
        assert is_healthy is False
        assert details is not None
        assert "unexpected response" in details["error"]


@pytest.mark.asyncio
async def test_redis_client_and_pool_lifecycle() -> None:
    """Test Redis pool, client retrieval, and clean disposal."""
    pool = get_redis_pool()
    assert pool is not None
    client = get_redis_client()
    assert client is not None

    await close_redis()
