import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

# Ensure common package is discoverable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from fastapi import FastAPI, HTTPException, status

from common.fault_injection import create_fault_router
from common.logging_config import configure_logging
from common.metrics import (
    DOWNSTREAM_REQUEST_DURATION_SECONDS,
    DOWNSTREAM_REQUESTS_TOTAL,
    PrometheusMiddleware,
    metrics_endpoint,
)
from common.schemas import OrderCreateRequest, OrderResponse, ServiceHealthResponse
from common.telemetry import setup_telemetry

SERVICE_NAME = "api-gateway"
logger = configure_logging(SERVICE_NAME)

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8003")
GATEWAY_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT_SECONDS", "5.0"))


async def forward_order_creation(payload: dict) -> OrderResponse:
    """Forward order creation to Order Service with telemetry and error handling."""
    order_url = f"{ORDER_SERVICE_URL.rstrip('/')}/orders"
    start_time = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
            resp = await client.post(order_url, json=payload)
            duration = time.perf_counter() - start_time

            DOWNSTREAM_REQUESTS_TOTAL.labels(
                service=SERVICE_NAME,
                target="order-service",
                status_code=str(resp.status_code),
            ).inc()
            DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
                service=SERVICE_NAME, target="order-service"
            ).observe(duration)

            if resp.status_code == 201:
                return OrderResponse(**resp.json())

            # Translate downstream error
            error_detail = (
                resp.json().get("detail", resp.text)
                if resp.headers.get("content-type") == "application/json"
                else resp.text
            )
            logger.error(
                "Order service returned error: code=%d, detail=%s",
                resp.status_code,
                error_detail,
            )
            raise HTTPException(status_code=resp.status_code, detail=error_detail)

    except httpx.TimeoutException as exc:
        duration = time.perf_counter() - start_time
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME, target="order-service", status_code="timeout"
        ).inc()
        DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME, target="order-service"
        ).observe(duration)
        logger.error("Order service call timed out after %.2fs", duration)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Order service timed out after {GATEWAY_TIMEOUT}s",
        ) from exc

    except httpx.RequestError as exc:
        duration = time.perf_counter() - start_time
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            service=SERVICE_NAME, target="order-service", status_code="error"
        ).inc()
        DOWNSTREAM_REQUEST_DURATION_SECONDS.labels(
            service=SERVICE_NAME, target="order-service"
        ).observe(duration)
        logger.error("Failed to connect to order service: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to order service: {exc}",
        ) from exc


async def fetch_downstream_health() -> dict[str, dict]:
    """Fetch health checks from Order and Payment services."""
    services_status = {
        "api-gateway": {"status": "healthy"},
        "order-service": {"status": "unknown"},
        "payment-service": {"status": "unknown"},
    }

    async with httpx.AsyncClient(timeout=2.0) as client:
        # Check Order Service
        try:
            resp = await client.get(f"{ORDER_SERVICE_URL.rstrip('/')}/health")
            services_status["order-service"] = (
                resp.json() if resp.status_code == 200 else {"status": "degraded"}
            )
        except Exception as exc:
            services_status["order-service"] = {"status": "unreachable", "error": str(exc)}

        # Check Payment Service
        try:
            resp = await client.get(f"{PAYMENT_SERVICE_URL.rstrip('/')}/health")
            services_status["payment-service"] = (
                resp.json() if resp.status_code == 200 else {"status": "degraded"}
            )
        except Exception as exc:
            services_status["payment-service"] = {"status": "unreachable", "error": str(exc)}

    return services_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gateway startup and shutdown lifecycle."""
    logger.info("Initializing api-gateway...")
    yield
    logger.info("Shutting down api-gateway...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpsPilot API Gateway",
        description="Public entry point, routing, and health aggregation for microservices",
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
        )

    @app.get("/services/health", tags=["Health"])
    async def services_health():
        """Aggregates health checks across all downstream microservices."""
        services_status = await fetch_downstream_health()
        all_healthy = all(v.get("status") == "healthy" for v in services_status.values())

        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": datetime.now(UTC).isoformat(),
            "services": services_status,
        }

    @app.post(
        "/api/orders",
        response_model=OrderResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Orders"],
    )
    async def create_order(request: OrderCreateRequest) -> OrderResponse:
        """Route order creation to the Order Service."""
        return await forward_order_creation(request.model_dump())

    @app.get("/api/orders/{order_id}", response_model=OrderResponse, tags=["Orders"])
    async def get_order(order_id: str) -> OrderResponse:
        """Route order lookup to the Order Service."""
        order_url = f"{ORDER_SERVICE_URL.rstrip('/')}/orders/{order_id}"
        try:
            async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
                resp = await client.get(order_url)
                if resp.status_code == 200:
                    return OrderResponse(**resp.json())
                if resp.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to reach order service: {exc}",
            ) from exc

    return app


app = create_app()
