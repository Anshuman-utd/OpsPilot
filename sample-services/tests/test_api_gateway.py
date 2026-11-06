import importlib.util
import os
import sys
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.schemas import OrderResponse

spec = importlib.util.spec_from_file_location(
    "gateway_service_main",
    os.path.join(os.path.dirname(__file__), "../api-gateway/main.py"),
)
gateway_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_module)
app = gateway_module.app


@pytest.fixture
async def gateway_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_gateway_health(gateway_client: AsyncClient):
    """Test API Gateway health check."""
    response = await gateway_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "api-gateway"
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_gateway_metrics(gateway_client: AsyncClient):
    """Test API Gateway Prometheus metrics export."""
    response = await gateway_client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


@pytest.mark.asyncio
async def test_gateway_services_health(gateway_client: AsyncClient):
    """Test aggregated downstream health endpoint."""
    mock_health = {
        "api-gateway": {"status": "healthy"},
        "order-service": {"status": "healthy"},
        "payment-service": {"status": "healthy"},
    }
    with patch.object(
        gateway_module, "fetch_downstream_health", new=AsyncMock(return_value=mock_health)
    ):
        response = await gateway_client.get("/services/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "services" in data
        assert data["services"]["order-service"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_gateway_create_order_forwarding(gateway_client: AsyncClient):
    """Test forwarding order creation from Gateway to Order Service."""
    mock_order = OrderResponse(
        id="ord-gw-1",
        customer_id="cust-gw",
        amount=250.0,
        currency="USD",
        status="CONFIRMED",
        created_at="2026-11-07T01:00:00Z",
        updated_at="2026-11-07T01:00:00Z",
        payment_id="pay-gw-1",
    )

    with patch.object(
        gateway_module, "forward_order_creation", new=AsyncMock(return_value=mock_order)
    ):
        response = await gateway_client.post(
            "/api/orders",
            json={"customer_id": "cust-gw", "amount": 250.0, "currency": "USD"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "ord-gw-1"
        assert data["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_gateway_create_order_timeout(gateway_client: AsyncClient):
    """Test gateway returns 504 when Order Service times out."""
    with patch.object(
        gateway_module,
        "forward_order_creation",
        new=AsyncMock(
            side_effect=HTTPException(status_code=504, detail="Order service timed out after 5.0s")
        ),
    ):
        response = await gateway_client.post(
            "/api/orders",
            json={"customer_id": "cust-gw", "amount": 250.0, "currency": "USD"},
        )
        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]


@pytest.mark.asyncio
async def test_gateway_create_order_connection_error(gateway_client: AsyncClient):
    """Test gateway returns 502 when Order Service connection fails."""
    with patch.object(
        gateway_module,
        "forward_order_creation",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=502, detail="Failed to connect to order service: Connection refused"
            )
        ),
    ):
        response = await gateway_client.post(
            "/api/orders",
            json={"customer_id": "cust-gw", "amount": 250.0, "currency": "USD"},
        )
        assert response.status_code == 502
        assert "Failed to connect" in response.json()["detail"]
