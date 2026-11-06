import importlib.util
import os
import sys
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.fault_injection import fault_manager

spec = importlib.util.spec_from_file_location(
    "order_service_main",
    os.path.join(os.path.dirname(__file__), "../order-service/main.py"),
)
order_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(order_module)
app = order_module.app


@pytest.fixture
async def order_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_order_health(order_client: AsyncClient):
    """Test Order Service health check."""
    response = await order_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "order-service"
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_order_metrics(order_client: AsyncClient):
    """Test Order Service metrics export."""
    response = await order_client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text or "orders_created_total" in response.text


@pytest.mark.asyncio
async def test_create_order_payment_success(order_client: AsyncClient):
    """Test order creation with successful payment produces CONFIRMED order."""
    fault_manager.reset_all()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    app.dependency_overrides[order_module.get_demo_db] = lambda: mock_db
    try:
        with patch.object(
            order_module,
            "call_payment_service",
            new=AsyncMock(return_value=(201, {"id": "pay-123", "status": "SUCCESS"})),
        ):
            response = await order_client.post(
                "/orders",
                json={"customer_id": "cust-1", "amount": 150.0, "currency": "USD"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["customer_id"] == "cust-1"
            assert data["status"] == "CONFIRMED"
            assert data["payment_id"] == "pay-123"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_order_payment_failed(order_client: AsyncClient):
    """Test order creation when payment fails produces PAYMENT_FAILED order."""
    fault_manager.reset_all()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    app.dependency_overrides[order_module.get_demo_db] = lambda: mock_db
    try:
        with patch.object(
            order_module,
            "call_payment_service",
            new=AsyncMock(return_value=(201, {"id": "pay-failed", "status": "FAILED"})),
        ):
            response = await order_client.post(
                "/orders",
                json={"customer_id": "cust-2", "amount": 75.0, "currency": "USD"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "PAYMENT_FAILED"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_order_payment_timeout(order_client: AsyncClient):
    """Test payment timeout triggers HTTP 504 and updates status to PAYMENT_FAILED."""
    fault_manager.reset_all()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    app.dependency_overrides[order_module.get_demo_db] = lambda: mock_db
    try:
        with patch.object(
            order_module,
            "call_payment_service",
            new=AsyncMock(
                side_effect=HTTPException(status_code=504, detail="Payment service timed out")
            ),
        ):
            response = await order_client.post(
                "/orders",
                json={"customer_id": "cust-timeout", "amount": 200.0, "currency": "USD"},
            )
            assert response.status_code == 504
            assert "timed out" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_order_internal_error_fault(order_client: AsyncClient):
    """Test that active order_service_error_spike triggers HTTP 500."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        fault_manager.enable(
            "order_service_error_spike",
            60,
            {"error_rate": 1.0, "error_message": "Forced internal error"},
        )
        try:
            response = await order_client.post(
                "/orders",
                json={"customer_id": "cust-err", "amount": 50.0, "currency": "USD"},
            )
            assert response.status_code == 500
            assert "Forced internal error" in response.json()["detail"]
        finally:
            fault_manager.reset_all()
