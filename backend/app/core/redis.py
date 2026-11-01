import time
from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.core.config import get_settings

_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


def get_redis_pool() -> ConnectionPool:
    """Return or initialize the singleton Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _redis_pool


def get_redis_client() -> Redis:
    """Return or initialize the singleton Redis async client."""
    global _redis_client
    if _redis_client is None:
        pool = get_redis_pool()
        _redis_client = Redis(connection_pool=pool)
    return _redis_client


async def ping_redis() -> tuple[bool, dict[str, Any] | None]:
    """
    Execute a PING command against Redis to verify connectivity and measure latency.
    Returns (True, details) if healthy, (False, details) otherwise.
    """
    start_time = time.perf_counter()
    try:
        client = get_redis_client()
        pong = await client.ping()
        if pong:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return True, {"latency_ms": latency_ms}
        return False, {"error": "Redis PING returned unexpected response"}
    except Exception as exc:
        return False, {"error": str(exc)}


async def close_redis() -> None:
    """Cleanly close the Redis connection pool on application shutdown."""
    global _redis_client, _redis_pool
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None
