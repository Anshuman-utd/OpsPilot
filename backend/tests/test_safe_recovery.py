from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import (
    HighHttp5xxErrorRateRule,
    HighP95LatencyRule,
    PaymentFailureSpikeRule,
)
from app.services.prometheus import PrometheusQueryResult, PrometheusSample


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite async database session fixture."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_low_sample_volume_does_not_auto_resolve_incident(sqlite_session):
    """
    If traffic stops or drops below MIN_PAYMENT_SAMPLES,
    an existing active incident MUST NOT be automatically resolved.
    """
    rule = PaymentFailureSpikeRule()
    fp = rule.generate_fingerprint("payment-service")
    settings = Settings(MIN_PAYMENT_SAMPLES=10)

    # Active incident in DB
    inc = IncidentModel(
        title="Payment Failure Spike",
        service_name="payment-service",
        severity="HIGH",
        status=IncidentStatus.OPEN.value,
        incident_type="payment_failure",
        fingerprint=fp,
    )
    sqlite_session.add(inc)
    await sqlite_session.commit()

    mock_prom = AsyncMock()
    # 0% failures, but only 2 attempts (less than MIN_PAYMENT_SAMPLES=10)
    mock_prom.query_instant.side_effect = [
        PrometheusQueryResult(
            success=True, query="ratio", samples=[PrometheusSample(labels={}, value=0.0)]
        ),
        PrometheusQueryResult(
            success=True, query="samples", samples=[PrometheusSample(labels={}, value=2.0)]
        ),
    ]

    engine = DetectionEngine(rules=[rule], prometheus_client=mock_prom)
    res = await engine.evaluate_all(sqlite_session, settings=settings)

    # Must NOT resolve!
    assert res.incidents_resolved == 0
    refreshed_inc = (
        await sqlite_session.execute(
            sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
        )
    ).scalar_one()
    assert refreshed_inc.status == IncidentStatus.OPEN.value


@pytest.mark.asyncio
async def test_nan_latency_does_not_auto_resolve(sqlite_session):
    """Histogram returning NaN during zero traffic must not resolve incident."""
    rule = HighP95LatencyRule()
    fp = rule.generate_fingerprint("order-service")

    inc = IncidentModel(
        title="High P95 Latency",
        service_name="order-service",
        severity="HIGH",
        status=IncidentStatus.OPEN.value,
        incident_type="p95_latency",
        fingerprint=fp,
    )
    sqlite_session.add(inc)
    await sqlite_session.commit()

    mock_prom = AsyncMock()
    mock_prom.query_instant.return_value = PrometheusQueryResult(
        success=True,
        query="p95",
        samples=[PrometheusSample(labels={"service": "order-service"}, value=float("nan"))],
    )

    engine = DetectionEngine(rules=[rule], prometheus_client=mock_prom)
    res = await engine.evaluate_all(sqlite_session)

    assert res.incidents_resolved == 0
    refreshed_inc = (
        await sqlite_session.execute(
            sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
        )
    ).scalar_one()
    assert refreshed_inc.status == IncidentStatus.OPEN.value


@pytest.mark.asyncio
async def test_flapping_dampening_requires_consecutive_healthy_cycles(sqlite_session):
    """
    When CONSECUTIVE_HEALTHY_CYCLES_FOR_RECOVERY=2:
    - Cycle 1 healthy: incident remains OPEN, healthy_cycles becomes 1.
    - Cycle 2 healthy: incident is RESOLVED!
    """
    rule = HighHttp5xxErrorRateRule()
    fp = rule.generate_fingerprint("api-gateway")
    settings = Settings(CONSECUTIVE_HEALTHY_CYCLES_FOR_RECOVERY=2)

    inc = IncidentModel(
        title="Elevated HTTP 5xx error rate",
        service_name="api-gateway",
        severity="HIGH",
        status=IncidentStatus.OPEN.value,
        incident_type="http_5xx_rate",
        fingerprint=fp,
    )
    sqlite_session.add(inc)
    await sqlite_session.commit()

    mock_prom = AsyncMock()
    # Healthy traffic: 0% errors, 1.0 req/s volume
    healthy_side_effect = [
        PrometheusQueryResult(
            success=True,
            query="ratio",
            samples=[PrometheusSample(labels={"service": "api-gateway"}, value=0.0)],
        ),
        PrometheusQueryResult(
            success=True,
            query="volume",
            samples=[PrometheusSample(labels={"service": "api-gateway"}, value=1.0)],
        ),
    ]

    engine = DetectionEngine(rules=[rule], prometheus_client=mock_prom)

    # --- Cycle 1 ---
    mock_prom.query_instant.side_effect = list(healthy_side_effect)
    res1 = await engine.evaluate_all(sqlite_session, settings=settings)
    assert res1.incidents_resolved == 0

    inc_c1 = (
        await sqlite_session.execute(
            sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
        )
    ).scalar_one()
    assert inc_c1.status == IncidentStatus.OPEN.value
    assert inc_c1.metadata_json.get("healthy_cycles") == 1

    # --- Cycle 2 ---
    mock_prom.query_instant.side_effect = list(healthy_side_effect)
    res2 = await engine.evaluate_all(sqlite_session, settings=settings)
    assert res2.incidents_resolved == 1

    inc_c2 = (
        await sqlite_session.execute(
            sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
        )
    ).scalar_one()
    assert inc_c2.status == IncidentStatus.RESOLVED.value
    assert inc_c2.resolved_at is not None
