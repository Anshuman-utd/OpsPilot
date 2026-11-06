import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from common.fault_injection import create_fault_router, fault_manager


@pytest.fixture
def fault_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_fault_router())
    return app


@pytest.fixture
async def fault_client(fault_app: FastAPI):
    transport = ASGITransport(app=fault_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_fault_endpoints_forbidden_by_default(fault_client: AsyncClient):
    """Test that fault injection endpoints return HTTP 403 when FAULT_INJECTION_ENABLED is false."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=False):
        response = await fault_client.get("/debug/faults")
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_faults_when_enabled(fault_client: AsyncClient):
    """Test that all 6 default fault scenarios are listed when enabled."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        response = await fault_client.get("/debug/faults")
        assert response.status_code == 200
        faults = response.json()
        fault_names = {f["name"] for f in faults}

        expected_faults = {
            "payment_failure_spike",
            "downstream_latency_spike",
            "payment_service_unavailability",
            "db_pool_exhaustion",
            "cascading_timeout",
            "order_service_error_spike",
        }
        assert expected_faults.issubset(fault_names)


@pytest.mark.asyncio
async def test_enable_and_disable_fault(fault_client: AsyncClient):
    """Test enabling a fault with parameters and then explicitly disabling it."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        # Enable fault
        enable_resp = await fault_client.post(
            "/debug/faults/downstream_latency_spike/enable",
            json={"duration_seconds": 30, "params": {"delay_seconds": 3.5}},
        )
        assert enable_resp.status_code == 200
        data = enable_resp.json()
        assert data["enabled"] is True
        assert data["params"]["delay_seconds"] == 3.5
        assert fault_manager.is_active("downstream_latency_spike") is True

        # Disable fault
        disable_resp = await fault_client.post("/debug/faults/downstream_latency_spike/disable")
        assert disable_resp.status_code == 200
        assert disable_resp.json()["enabled"] is False
        assert fault_manager.is_active("downstream_latency_spike") is False


@pytest.mark.asyncio
async def test_fault_expiration_ttl():
    """Test that an enabled fault automatically deactivates once TTL passes."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        # Enable with 1 second duration
        fault_manager.enable("payment_failure_spike", 1, {"failure_rate": 0.9})
        assert fault_manager.is_active("payment_failure_spike") is True

        # Wait past expiration
        time.sleep(1.1)
        assert fault_manager.is_active("payment_failure_spike") is False


@pytest.mark.asyncio
async def test_reset_all_faults(fault_client: AsyncClient):
    """Test that reset endpoint disables all active faults at once."""
    with patch("common.fault_injection.is_fault_injection_globally_enabled", return_value=True):
        fault_manager.enable("payment_failure_spike", 60, {})
        fault_manager.enable("order_service_error_spike", 60, {})
        assert fault_manager.is_active("payment_failure_spike") is True
        assert fault_manager.is_active("order_service_error_spike") is True

        reset_resp = await fault_client.post("/debug/faults/reset")
        assert reset_resp.status_code == 200
        assert fault_manager.is_active("payment_failure_spike") is False
        assert fault_manager.is_active("order_service_error_spike") is False
