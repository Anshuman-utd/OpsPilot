import importlib.util
import os
import sys
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.fault_injection import fault_manager

spec = importlib.util.spec_from_file_location(
    "payment_service_main",
    os.path.join(os.path.dirname(__file__), "../payment-service/main.py"),
)
payment_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payment_module)
app = payment_module.app


@pytest.fixture
async def payment_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_payment_health(payment_client: AsyncClient):
    """Test Payment Service health check."""
    response = await payment_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "payment-service"
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_payment_metrics(payment_client: AsyncClient):
    """Test Payment Service Prometheus metrics export."""
    response = await payment_client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text or "payments_processed_total" in response.text


@pytest.mark.asyncio
async def test_process_payment_success(payment_client: AsyncClient):
    """Test successful payment processing."""
    fault_manager.reset_all()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    app.dependency_overrides[payment_module.get_demo_db] = lambda: mock_db
    try:
        response = await payment_client.post(
            "/payments",
            json={"order_id": "order-1234", "amount": 99.95, "currency": "USD"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["order_id"] == "order-1234"
        assert data["status"] == "SUCCESS"
        assert data["amount"] == 99.95
        assert "id" in data
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_process_payment_unavailability_fault(payment_client: AsyncClient):
    """Test that active unavailability fault triggers HTTP 503."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        fault_manager.enable(
            "payment_service_unavailability",
            60,
            {"status_code": 503, "error_message": "Service Down"},
        )
        try:
            response = await payment_client.post(
                "/payments",
                json={"order_id": "order-fail", "amount": 10.0, "currency": "USD"},
            )
            assert response.status_code == 503
            assert "Service Down" in response.json()["detail"]
        finally:
            fault_manager.reset_all()


@pytest.mark.asyncio
async def test_process_payment_failure_spike_fault(payment_client: AsyncClient):
    """Test simulated payment failure spike generates FAILED payment status."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        fault_manager.enable(
            "payment_failure_spike", 60, {"failure_rate": 1.0, "reason": "CARD_EXPIRED"}
        )

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        app.dependency_overrides[payment_module.get_demo_db] = lambda: mock_db
        try:
            response = await payment_client.post(
                "/payments",
                json={"order_id": "order-spike", "amount": 50.0, "currency": "USD"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "FAILED"
            assert data["failure_reason"] == "CARD_EXPIRED"
        finally:
            app.dependency_overrides.clear()
            fault_manager.reset_all()
