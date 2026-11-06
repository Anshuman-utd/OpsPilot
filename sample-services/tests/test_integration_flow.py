import importlib.util
import os
import sys
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.fault_injection import fault_manager

# Load Gateway
gw_spec = importlib.util.spec_from_file_location(
    "gw_main", os.path.join(os.path.dirname(__file__), "../api-gateway/main.py")
)
gw_module = importlib.util.module_from_spec(gw_spec)
gw_spec.loader.exec_module(gw_module)
gw_app = gw_module.app

# Load Order Service
order_spec = importlib.util.spec_from_file_location(
    "ord_main", os.path.join(os.path.dirname(__file__), "../order-service/main.py")
)
order_module = importlib.util.module_from_spec(order_spec)
order_spec.loader.exec_module(order_module)
order_app = order_module.app

# Load Payment Service
pay_spec = importlib.util.spec_from_file_location(
    "pay_main", os.path.join(os.path.dirname(__file__), "../payment-service/main.py")
)
pay_module = importlib.util.module_from_spec(pay_spec)
pay_spec.loader.exec_module(pay_module)
pay_app = pay_module.app


@pytest.fixture
async def e2e_clients() -> AsyncGenerator[tuple[AsyncClient, AsyncClient, AsyncClient], None]:
    """Provide clients bound to the respective ASGI microservice applications."""
    gw_client = AsyncClient(transport=ASGITransport(app=gw_app), base_url="http://api-gateway:8001")
    order_client = AsyncClient(
        transport=ASGITransport(app=order_app), base_url="http://order-service:8002"
    )
    payment_client = AsyncClient(
        transport=ASGITransport(app=pay_app), base_url="http://payment-service:8003"
    )

    async with gw_client, order_client, payment_client:
        yield gw_client, order_client, payment_client


@pytest.mark.asyncio
async def test_end_to_end_order_and_payment_flow(e2e_clients):
    """
    Test complete request chain:
    Gateway -> Order Service -> Payment Service
    Verifying that status becomes CONFIRMED with payment_id and telemetry updates.
    """
    fault_manager.reset_all()
    gw_client, order_client, payment_client = e2e_clients

    # 1. Mock database persistence in Order and Payment services
    mock_order_db = AsyncMock()
    mock_order_db.add = MagicMock()
    mock_order_db.commit = AsyncMock()
    mock_order_db.refresh = AsyncMock()

    mock_pay_db = AsyncMock()
    mock_pay_db.add = MagicMock()
    mock_pay_db.commit = AsyncMock()
    mock_pay_db.refresh = AsyncMock()

    order_app.dependency_overrides[order_module.get_demo_db] = lambda: mock_order_db
    pay_app.dependency_overrides[pay_module.get_demo_db] = lambda: mock_pay_db

    # 2. Intercept Order Service's call to Payment Service and forward to payment_client
    async def mock_call_payment(order_id: str, amount: float, currency: str):
        resp = await payment_client.post(
            "/payments",
            json={"order_id": order_id, "amount": amount, "currency": currency},
        )
        return resp.status_code, resp.json() if resp.status_code == 201 else None

    # 3. Intercept Gateway's call to Order Service and forward to order_client
    async def mock_forward_order(payload: dict):
        resp = await order_client.post("/orders", json=payload)
        return order_module.OrderResponse(**resp.json())

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(order_module, "call_payment_service", mock_call_payment)
        mp.setattr(gw_module, "forward_order_creation", mock_forward_order)

        response = await gw_client.post(
            "/api/orders",
            json={"customer_id": "cust-e2e-100", "amount": 199.99, "currency": "USD"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["customer_id"] == "cust-e2e-100"
        assert data["status"] == "CONFIRMED"
        assert data["amount"] == 199.99
        assert data["payment_id"] is not None

    order_app.dependency_overrides.clear()
    pay_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_end_to_end_payment_failure_cascade(e2e_clients):
    """
    Test payment failure propagation:
    When Payment Service declines a payment (via simulated fault),
    Order Service marks order as PAYMENT_FAILED and Gateway reports the result.
    """
    gw_client, order_client, payment_client = e2e_clients

    mock_order_db = AsyncMock()
    mock_order_db.add = MagicMock()
    mock_order_db.commit = AsyncMock()
    mock_order_db.refresh = AsyncMock()

    mock_pay_db = AsyncMock()
    mock_pay_db.add = MagicMock()
    mock_pay_db.commit = AsyncMock()
    mock_pay_db.refresh = AsyncMock()

    order_app.dependency_overrides[order_module.get_demo_db] = lambda: mock_order_db
    pay_app.dependency_overrides[pay_module.get_demo_db] = lambda: mock_pay_db

    # Enable payment failure spike
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("common.fault_injection.is_fault_injection_globally_enabled", lambda: True)
        fault_manager.enable(
            "payment_failure_spike", 60, {"failure_rate": 1.0, "reason": "STOLEN_CARD"}
        )

        async def mock_call_payment(order_id: str, amount: float, currency: str):
            resp = await payment_client.post(
                "/payments",
                json={"order_id": order_id, "amount": amount, "currency": currency},
            )
            return resp.status_code, resp.json()

        async def mock_forward_order(payload: dict):
            resp = await order_client.post("/orders", json=payload)
            return order_module.OrderResponse(**resp.json())

        mp.setattr(order_module, "call_payment_service", mock_call_payment)
        mp.setattr(gw_module, "forward_order_creation", mock_forward_order)

        response = await gw_client.post(
            "/api/orders",
            json={"customer_id": "cust-declined", "amount": 500.0, "currency": "USD"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PAYMENT_FAILED"

        fault_manager.reset_all()

    order_app.dependency_overrides.clear()
    pay_app.dependency_overrides.clear()
