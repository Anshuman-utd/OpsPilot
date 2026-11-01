from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    timestamp: str


class LivenessResponse(BaseModel):
    status: str
    timestamp: str


class ComponentStatus(BaseModel):
    status: str
    details: dict[str, Any] | None = None


class ReadinessResponse(BaseModel):
    status: str
    timestamp: str
    components: dict[str, ComponentStatus]


# Dependency check functions to be implemented / overridden
async def check_database_health() -> tuple[bool, dict[str, Any] | None]:
    """Check database health. Implemented in core.database."""
    try:
        from app.core.database import ping_database

        return await ping_database()
    except Exception as exc:
        return False, {"error": str(exc)}


async def check_redis_health() -> tuple[bool, dict[str, Any] | None]:
    """Check Redis health. Implemented in core.redis."""
    try:
        from app.core.redis import ping_redis

        return await ping_redis()
    except Exception as exc:
        return False, {"error": str(exc)}


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic application health check."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/health/live", response_model=LivenessResponse)
async def liveness_check() -> LivenessResponse:
    """Kubernetes-style liveness probe to verify the application process is running."""
    return LivenessResponse(
        status="live",
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Service Unavailable"}},
)
async def readiness_check(response: Response) -> ReadinessResponse:
    """
    Readiness probe to verify all backing infrastructure (PostgreSQL, Redis) is healthy.
    Returns 200 if all components are healthy, otherwise returns 503.
    """
    db_ok, db_details = await check_database_health()
    redis_ok, redis_details = await check_redis_health()

    components = {
        "database": ComponentStatus(
            status="healthy" if db_ok else "unhealthy",
            details=db_details,
        ),
        "redis": ComponentStatus(
            status="healthy" if redis_ok else "unhealthy",
            details=redis_details,
        ),
    }

    all_healthy = db_ok and redis_ok
    if not all_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if all_healthy else "unhealthy",
        timestamp=datetime.now(UTC).isoformat(),
        components=components,
    )
