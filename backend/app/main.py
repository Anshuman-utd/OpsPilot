from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager to handle startup and shutdown events."""
    # Startup: Resources initialization (database pools, redis clients if any)
    yield
    # Shutdown: Cleanly close connection pools
    try:
        from app.core.database import close_db_engine

        await close_db_engine()
    except (ImportError, Exception):
        pass

    try:
        from app.core.redis import close_redis

        await close_redis()
    except (ImportError, Exception):
        pass


def create_application() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    application = FastAPI(
        title=settings.APP_NAME,
        description=(
            "OpsPilot API: Agentic AI-powered Site Reliability Engineering (SRE) platform. "
            "Autonomously investigates application incidents, identifies potential root causes, "
            "recommends remediation, and verifies service recovery."
        ),
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Allow cross-origin requests from frontend development server
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    application.include_router(health_router)

    @application.get(
        "/",
        tags=["Root"],
        summary="API Root Information",
        response_model=dict[str, Any],
    )
    async def root() -> dict[str, Any]:
        """Return basic API metadata and service status."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.APP_ENV,
            "status": "operational",
            "documentation": {
                "swagger": "/docs",
                "redoc": "/redoc",
                "openapi": "/openapi.json",
            },
        }

    return application


app = create_application()
