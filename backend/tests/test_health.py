from unittest.mock import patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient) -> None:
    """Test GET /health returns 200 OK and healthy status."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_liveness_endpoint(async_client: AsyncClient) -> None:
    """Test GET /health/live returns 200 OK and live status."""
    response = await async_client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "live"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_readiness_all_healthy(async_client: AsyncClient) -> None:
    """Test GET /health/ready returns 200 OK when both PostgreSQL and Redis are healthy."""
    with (
        patch(
            "app.api.health.check_database_health",
            return_value=(True, {"latency_ms": 1.25}),
        ),
        patch(
            "app.api.health.check_redis_health",
            return_value=(True, {"latency_ms": 0.45}),
        ),
    ):
        response = await async_client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["components"]["database"]["status"] == "healthy"
        assert data["components"]["database"]["details"]["latency_ms"] == 1.25
        assert data["components"]["redis"]["status"] == "healthy"
        assert data["components"]["redis"]["details"]["latency_ms"] == 0.45


@pytest.mark.asyncio
async def test_readiness_database_unhealthy(async_client: AsyncClient) -> None:
    """Test GET /health/ready returns 503 Service Unavailable when database fails."""
    with (
        patch(
            "app.api.health.check_database_health",
            return_value=(False, {"error": "Connection refused to PostgreSQL"}),
        ),
        patch(
            "app.api.health.check_redis_health",
            return_value=(True, {"latency_ms": 0.50}),
        ),
    ):
        response = await async_client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["components"]["database"]["status"] == "unhealthy"
        assert "Connection refused" in data["components"]["database"]["details"]["error"]
        assert data["components"]["redis"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_readiness_redis_unhealthy(async_client: AsyncClient) -> None:
    """Test GET /health/ready returns 503 Service Unavailable when Redis fails."""
    with (
        patch(
            "app.api.health.check_database_health",
            return_value=(True, {"latency_ms": 1.10}),
        ),
        patch(
            "app.api.health.check_redis_health",
            return_value=(False, {"error": "Redis connection timeout"}),
        ),
    ):
        response = await async_client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["components"]["database"]["status"] == "healthy"
        assert data["components"]["redis"]["status"] == "unhealthy"
        assert "timeout" in data["components"]["redis"]["details"]["error"]


@pytest.mark.asyncio
async def test_readiness_all_unhealthy(async_client: AsyncClient) -> None:
    """Test GET /health/ready returns 503 Service Unavailable when both dependencies fail."""
    with (
        patch(
            "app.api.health.check_database_health",
            return_value=(False, {"error": "DB down"}),
        ),
        patch(
            "app.api.health.check_redis_health",
            return_value=(False, {"error": "Redis down"}),
        ),
    ):
        response = await async_client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["components"]["database"]["status"] == "unhealthy"
        assert data["components"]["redis"]["status"] == "unhealthy"
