import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

# Ensure common package is discoverable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.database import OrderModel, get_demo_db, init_demo_db
from common.fault_injection import create_fault_router, fault_manager
from common.logging_config import configure_logging
from common.metrics import (
    DOWNSTREAM_REQUEST_DURATION_SECONDS,
    DOWNSTREAM_REQUESTS_TOTAL,
    ORDERS_CREATED_TOTAL,
    PrometheusMiddleware,
    metrics_endpoint,
)
from common.schemas import OrderCreateRequest, OrderResponse, ServiceHealthResponse
from common.telemetry import setup_telemetry

SERVICE_NAME = "order-service"
logger = configure_logging(SERVICE_NAME)

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8003")
HTTP_TIMEOUT = float(os.getenv("PAYMENT_SERVICE_TIMEOUT_SECONDS", "3.0"))


async def call_payment_service(
    order_id: str, amount: float, currency: str
) -> tuple[int, dict | None]:
    """Execute asynchronous HTTP call to Payment Service with telemetry and timeout handling."""
    payment_url = f"{PAYMENT_SERVICE_URL.rstrip('/')}/payments"
    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                payment_url,
                json={"order_id": order_id, "amount": amount, "currency": currency},
            )
            duration = time.perf_counter() - start_time
            DOWNSTREAM_REQUESTS_TOTAL.labels(
                service=SERVICE_NAME,
                target="payment-service",
                status_code=str(resp.status_code),
            ).inc()
            DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
                service=SERVICE_NAME, target="payment-service"
            ).observe(duration)

            if resp.status_code == 201:
                return resp.status_code, resp.json()
            return resp.status_code, None

    except httpx.TimeoutException as exc:
        duration = time.perf_counter() - start_time
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME, target="payment-service", status_code="timeout"
        ).inc()
        DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME, target="payment-service"
        ).observe(duration)
        logger.error("Payment service call timed out after %.2fs: %s", duration, exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Payment service timed out after {HTTP_TIMEOUT}s",
        ) from exc

    except httpx.RequestError as exc:
        duration = time.perf_counter() - start_time
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME, target="payment-service", status_code="error"
        ).inc()
        DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME, target="payment-service"
        ).observe(duration)
        logger.error("Payment service connection error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach payment service: {exc}",
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Service startup and shutdown lifecycle."""
    logger.info("Initializing order-service...")
    try:
        await init_demo_db()
        logger.info("Demo database tables verified.")
    except Exception as exc:
        logger.warning("Database initialization deferred: %s", exc)
    yield
    logger.info("Shutting down order-service...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpsPilot Order Service",
        description="Handles order creation, persistence, and payment orchestration",
        version="0.1.0",
        lifespan=lifespan,
    )

    setup_telemetry(app, SERVICE_NAME)
    app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)

    # Register fault injection controls
    app.include_router(create_fault_router())

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return metrics_endpoint()

    @app.get("/health", response_model=ServiceHealthResponse, tags=["Health"])
    async def health():
        return ServiceHealthResponse(
            service=SERVICE_NAME,
            status="healthy",
            timestamp=datetime.now(UTC).isoformat(),
            database="connected",
            dependencies={"payment_service_url": PAYMENT_SERVICE_URL},
        )

    @app.post(
        "/orders",
        response_model=OrderResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Orders"],
    )
    async def create_order(
        request: OrderCreateRequest, db: Annotated[AsyncSession, Depends(get_demo_db)]
    ) -> OrderResponse:
        """Create an order and initiate payment processing with Payment Service."""
        # 1. Fault Scenario: Order Service Internal Error Spike
        if fault_manager.should_trigger_probabilistic_fault(
            "order_service_error_spike", rate_param="error_rate"
        ):
            params = fault_manager.get_params("order_service_error_spike")
            error_msg = params.get("error_message", "Order service internal processing failure")
            logger.error("Fault triggered: %s", error_msg)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_msg)

        # 2. Persist initial order in PENDING status
        order_id = str(uuid.uuid4())
        order = OrderModel(
            id=order_id,
            customer_id=request.customer_id,
            amount=request.amount,
            currency=request.currency,
            status="PENDING",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)

        logger.info(
            "Order created with PENDING status: order_id=%s",
            order_id,
            extra={"extra_fields": {"order_id": order_id, "customer_id": order.customer_id}},
        )

        # 3. Call Payment Service with trace context and telemetry
        payment_id = None
        payment_status = "FAILED"
        try:
            status_code, pay_data = await call_payment_service(
                order.id, order.amount, order.currency
            )
            if status_code == 201 and pay_data:
                payment_id = pay_data.get("id")
                payment_status = pay_data.get("status", "FAILED")
        except HTTPException:
            order.status = "PAYMENT_FAILED"
            order.updated_at = datetime.now(UTC)
            await db.commit()
            ORDERS_CREATED_TOTAL.labels(status="PAYMENT_FAILED").inc()
            raise

        # 4. Update order final status based on payment result
        if payment_status == "SUCCESS":
            order.status = "CONFIRMED"
        else:
            order.status = "PAYMENT_FAILED"

        order.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(order)

        ORDERS_CREATED_TOTAL.labels(status=order.status).inc()

        logger.info(
            "Order %s finalized with status %s",
            order.id,
            order.status,
            extra={
                "extra_fields": {
                    "order_id": order.id,
                    "status": order.status,
                    "payment_id": payment_id,
                }
            },
        )

        return OrderResponse(
            id=order.id,
            customer_id=order.customer_id,
            amount=order.amount,
            currency=order.currency,
            status=order.status,
            created_at=order.created_at,
            updated_at=order.updated_at,
            payment_id=payment_id,
        )

    @app.get("/orders/{order_id}", response_model=OrderResponse, tags=["Orders"])
    async def get_order(
        order_id: str, db: Annotated[AsyncSession, Depends(get_demo_db)]
    ) -> OrderResponse:
        """Retrieve order by ID."""
        stmt = select(OrderModel).where(OrderModel.id == order_id)
        result = await db.execute(stmt)
        order = result.scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

        return OrderResponse(
            id=order.id,
            customer_id=order.customer_id,
            amount=order.amount,
            currency=order.currency,
            status=order.status,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )

    return app


app = create_app()
