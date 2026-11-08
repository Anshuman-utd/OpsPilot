from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import ServiceUnavailableRule
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
async def test_probe_down_target_detected():
    """Target exists in Prometheus scrape targets, but up == 0."""
    rule = ServiceUnavailableRule(
        expected_services=("api-gateway", "order-service", "payment-service")
    )
    mock_prom = AsyncMock()
    mock_prom.query_instant.return_value = PrometheusQueryResult(
        success=True,
        query="up",
        samples=[
            PrometheusSample(labels={"service": "api-gateway"}, value=1.0),
            PrometheusSample(labels={"service": "order-service"}, value=1.0),
            PrometheusSample(labels={"service": "payment-service"}, value=0.0),
        ],
    )

    breaches, healthy_fps = await rule.evaluate(mock_prom, Settings())
    assert len(breaches) == 1
    assert breaches[0].service_name == "payment-service"
    assert breaches[0].incident_type == "service_unavailable"
    assert breaches[0].severity == IncidentSeverity.CRITICAL
    assert len(healthy_fps) == 2


@pytest.mark.asyncio
async def test_target_disappears_from_discovery_detected():
    """Target completely drops out of Prometheus service discovery / configuration."""
    rule = ServiceUnavailableRule(
        expected_services=("api-gateway", "order-service", "payment-service")
    )
    mock_prom = AsyncMock()
    # payment-service target completely disappeared from Prometheus!
    mock_prom.query_instant.return_value = PrometheusQueryResult(
        success=True,
        query="up",
        samples=[
            PrometheusSample(labels={"service": "api-gateway"}, value=1.0),
            PrometheusSample(labels={"service": "order-service"}, value=1.0),
        ],
    )

    breaches, healthy_fps = await rule.evaluate(mock_prom, Settings())
    assert len(breaches) == 1
    assert breaches[0].service_name == "payment-service"
    assert breaches[0].incident_type == "service_missing"
    assert breaches[0].severity == IncidentSeverity.CRITICAL
    assert "missing entirely from Prometheus" in breaches[0].description
    assert len(healthy_fps) == 2


@pytest.mark.asyncio
async def test_prometheus_unavailable_does_not_resolve_active_incidents(sqlite_session):
    """When Prometheus is down, ServiceUnavailableRule does NOT return healthy fingerprints."""
    mock_prom = AsyncMock()
    mock_prom.query_instant.return_value = PrometheusQueryResult(
        success=False,
        query="up",
        samples=[],
        error="Connection refused: http://localhost:9090",
    )

    # Seed an active incident
    rule = ServiceUnavailableRule()
    fp = rule.generate_fingerprint("payment-service")
    inc = IncidentModel(
        title="Existing Service Outage",
        service_name="payment-service",
        severity="CRITICAL",
        status=IncidentStatus.OPEN.value,
        incident_type="service_unavailable",
        fingerprint=fp,
    )
    sqlite_session.add(inc)
    await sqlite_session.commit()

    engine = DetectionEngine(rules=[rule], prometheus_client=mock_prom)
    res = await engine.evaluate_all(sqlite_session)

    # Must NOT create new breaches and must NOT auto-resolve active incident
    assert res.breaches_detected == 0
    assert res.incidents_resolved == 0

    # Verify incident remains OPEN
    stmt = sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
    current_inc = (await sqlite_session.execute(stmt)).scalar_one()
    assert current_inc.status == IncidentStatus.OPEN.value
