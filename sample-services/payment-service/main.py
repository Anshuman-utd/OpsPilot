import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

# Ensure common package is discoverable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.database import PaymentModel, get_demo_db, init_demo_db
from common.fault_injection import create_fault_router, fault_manager
from common.logging_config import configure_logging
from common.metrics import (
    PAYMENT_FAILURES_TOTAL,
    PAYMENTS_PROCESSED_TOTAL,
    PrometheusMiddleware,
    metrics_endpoint,
    record_pool_metrics,
)
from common.schemas import PaymentCreateRequest, PaymentResponse, ServiceHealthResponse
from common.telemetry import setup_telemetry

SERVICE_NAME = "payment-service"
logger = configure_logging(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Service startup and shutdown lifecycle."""
    logger.info("Initializing payment-service...")
    try:
        await init_demo_db()
        logger.info("Demo database tables verified.")
    except Exception as exc:
        logger.warning("Database initialization deferred (database may still be starting): %s", exc)
    yield
    logger.info("Shutting down payment-service...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpsPilot Payment Service",
        description="Simulated external payment processing microservice",
        version="0.1.0",
        lifespan=lifespan,
    )

    setup_telemetry(app, SERVICE_NAME)
    app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)

    # Register fault injection controls
    app.include_router(create_fault_router())

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        record_pool_metrics(SERVICE_NAME)
        return metrics_endpoint()

    @app.get("/health", response_model=ServiceHealthResponse, tags=["Health"])
    async def health():
        return ServiceHealthResponse(
            service=SERVICE_NAME,
            status="healthy",
            timestamp=datetime.now(UTC).isoformat(),
            database="connected",
        )

    @app.post(
        "/payments",
        response_model=PaymentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Payments"],
    )
    async def process_payment(
        request: PaymentCreateRequest, db: Annotated[AsyncSession, Depends(get_demo_db)]
    ) -> PaymentResponse:
        """Process a payment for an order, taking into account any active fault scenarios."""
        # 1. Fault Scenario: Payment Service Unavailability (503)
        if fault_manager.is_active("payment_service_unavailability"):
            params = fault_manager.get_params("payment_service_unavailability")
            status_code = int(params.get("status_code", 503))
            error_msg = params.get("error_message", "Payment service unavailable")
            logger.error("Fault triggered: %s", error_msg)
            raise HTTPException(status_code=status_code, detail=error_msg)

        # 2. Fault Scenarios: Downstream Latency Spike & Cascading Timeout
        await fault_manager.maybe_inject_latency("downstream_latency_spike")
        await fault_manager.maybe_inject_latency("cascading_timeout")

        # 3. Fault Scenario: Payment Failure Spike
        is_failure_spike = fault_manager.should_trigger_probabilistic_fault(
            "payment_failure_spike", rate_param="failure_rate"
        )

        payment_status = "SUCCESS"
        failure_reason = None

        if is_failure_spike:
            params = fault_manager.get_params("payment_failure_spike")
            payment_status = "FAILED"
            failure_reason = params.get("reason", "SIMULATED_PAYMENT_FAILURE")
            PAYMENT_FAILURES_TOTAL.labels(reason=failure_reason).inc()
            logger.warning(
                "Payment failed due to simulated fault",
                extra={"extra_fields": {"order_id": request.order_id, "reason": failure_reason}},
            )

        PAYMENTS_PROCESSED_TOTAL.labels(status=payment_status).inc()

        payment = PaymentModel(
            id=str(uuid.uuid4()),
            order_id=request.order_id,
            amount=request.amount,
            currency=request.currency,
            status=payment_status,
            failure_reason=failure_reason,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(payment)
        await db.commit()
        await db.refresh(payment)

        logger.info(
            "Payment processed: status=%s, id=%s",
            payment.status,
            payment.id,
            extra={
                "extra_fields": {
                    "payment_id": payment.id,
                    "order_id": payment.order_id,
                    "status": payment.status,
                }
            },
        )

        return PaymentResponse(
            id=payment.id,
            order_id=payment.order_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            failure_reason=payment.failure_reason,
            created_at=payment.created_at,
        )

    @app.get("/payments/{payment_id}", response_model=PaymentResponse, tags=["Payments"])
    async def get_payment(
        payment_id: str, db: Annotated[AsyncSession, Depends(get_demo_db)]
    ) -> PaymentResponse:
        """Retrieve a payment transaction by ID."""
        stmt = select(PaymentModel).where(PaymentModel.id == payment_id)
        result = await db.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            raise HTTPException(status_code=404, detail=f"Payment {payment_id} not found")

        return PaymentResponse(
            id=payment.id,
            order_id=payment.order_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            failure_reason=payment.failure_reason,
            created_at=payment.created_at,
        )

    return app


app = create_app()
