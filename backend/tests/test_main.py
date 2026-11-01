import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.main import create_application


def test_create_application() -> None:
    """Test that the application factory creates a valid FastAPI app instance."""
    app = create_application()
    assert app.title == get_settings().APP_NAME
    assert app.version == get_settings().APP_VERSION
    assert app.docs_url == "/docs"


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient) -> None:
    """Test GET / returns 200 OK with correct API metadata."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "OpsPilot"
    assert data["status"] == "operational"
    assert "version" in data
    assert "environment" in data
    assert data["documentation"]["swagger"] == "/docs"
