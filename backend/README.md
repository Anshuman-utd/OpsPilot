# OpsPilot Backend

FastAPI asynchronous backend for the OpsPilot platform.

## Features
- **FastAPI**: Asynchronous web framework with automatic OpenAPI documentation.
- **SQLAlchemy 2.0 (async)**: Async PostgreSQL ORM with `asyncpg`.
- **Alembic**: Database migrations management.
- **Redis (asyncio)**: Fast caching and health checks.
- **Pydantic v2**: Type-safe data validation and centralized settings.

## Quickstart

```bash
# Sync dependencies
uv sync --extra dev

# Run development server
uv run uvicorn app.main:app --reload --port 8000
```
