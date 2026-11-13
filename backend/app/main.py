from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.reports import router as reports_router
from app.api.services import router as services_router
from app.core.auth import authorize_workspace
from app.core.config import get_settings
from app.investigation.router import router as investigations_router
from app.remediation.router import router as remediation_router


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
        docs_url=None if settings.auth_required else "/docs",
        redoc_url=None if settings.auth_required else "/redoc",
        openapi_url=None if settings.auth_required else "/openapi.json",
        lifespan=lifespan,
    )

    # Allow cross-origin requests from frontend development server
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.PUBLIC_ORIGIN.rstrip("/")] if settings.auth_required else [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def prevent_workspace_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Register API routers
    application.include_router(health_router)
    application.include_router(auth_router)
    protected = [Depends(authorize_workspace)] if settings.auth_required else []
    application.include_router(incidents_router, dependencies=protected)
    application.include_router(investigations_router, dependencies=protected)
    application.include_router(remediation_router, dependencies=protected)
    application.include_router(services_router, dependencies=protected)
    application.include_router(demo_router, dependencies=protected)
    application.include_router(reports_router, dependencies=protected)

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
