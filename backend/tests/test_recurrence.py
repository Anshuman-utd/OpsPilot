from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import PaymentFailureSpikeRule
from app.services.prometheus import PrometheusQueryResult, PrometheusSample


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite async database session fixture with partial unique index."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolved_incident_recurrence_creates_new_incident_option_b(sqlite_session):
    """
    Test the complete recurrence lifecycle (Option B):
    1. Incident #1 created upon metric breach.
    2. Incident #1 resolved.
    3. Metric breaches again with the same rule and service.
    4. DetectionEngine creates Incident #2 (distinct ID, status=OPEN),
       preserving Incident #1 (status=RESOLVED).
    5. Partial unique constraint prevents duplicate active incidents.
    """
    mock_prom = AsyncMock()
    rule = PaymentFailureSpikeRule()
    fp = rule.generate_fingerprint("payment-service")

    settings = Settings(
        MIN_PAYMENT_SAMPLES=5,
        PAYMENT_FAILURE_CRITICAL_THRESHOLD=50.0,
        PAYMENT_FAILURE_HIGH_THRESHOLD=20.0,
    )
    engine = DetectionEngine(rules=[rule], prometheus_client=mock_prom)

    # 1. First breach: 80% failures with 10 attempts
    mock_prom.query_instant.side_effect = [
        PrometheusQueryResult(
            success=True, query="ratio", samples=[PrometheusSample(labels={}, value=80.0)]
        ),
        PrometheusQueryResult(
            success=True, query="samples", samples=[PrometheusSample(labels={}, value=10.0)]
        ),
    ]

    res1 = await engine.evaluate_all(sqlite_session, settings=settings)
    assert res1.incidents_created == 1
    assert res1.incidents_updated == 0

    # Verify Incident #1 is in DB
    stmt = sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
    incidents = (await sqlite_session.execute(stmt)).scalars().all()
    assert len(incidents) == 1
    inc1 = incidents[0]
    assert inc1.status == IncidentStatus.OPEN.value
    assert inc1.occurrence_count == 1
    inc1_id = inc1.id

    # 2. Resolve Incident #1
    inc1.status = IncidentStatus.RESOLVED.value
    inc1.resolved_at = datetime.now(UTC)
    await sqlite_session.commit()

    # 3. Second breach occurs later for the same service and rule
    mock_prom.query_instant.side_effect = [
        PrometheusQueryResult(
            success=True, query="ratio", samples=[PrometheusSample(labels={}, value=75.0)]
        ),
        PrometheusQueryResult(
            success=True, query="samples", samples=[PrometheusSample(labels={}, value=10.0)]
        ),
    ]

    res2 = await engine.evaluate_all(sqlite_session, settings=settings)
    assert res2.incidents_created == 1
    assert res2.incidents_updated == 0

    # 4. Verify DB now has 2 incidents with the same fingerprint: one RESOLVED, one OPEN!
    stmt2 = (
        sa.select(IncidentModel)
        .where(IncidentModel.fingerprint == fp)
        .order_by(IncidentModel.created_at.asc())
    )
    all_incidents = (await sqlite_session.execute(stmt2)).scalars().all()
    assert len(all_incidents) == 2

    # Original incident is preserved
    assert all_incidents[0].id == inc1_id
    assert all_incidents[0].status == IncidentStatus.RESOLVED.value

    # New recurrent incident has distinct ID and is OPEN
    assert all_incidents[1].id != inc1_id
    assert all_incidents[1].status == IncidentStatus.OPEN.value
    assert all_incidents[1].occurrence_count == 1
    assert all_incidents[1].triggered_value == 75.0

    # 5. Verify partial unique constraint: 2nd ACTIVE incident with same fingerprint fails
    with pytest.raises(sa.exc.IntegrityError):
        duplicate_active = IncidentModel(
            title="Duplicate Active Incident",
            service_name="payment-service",
            severity="HIGH",
            status=IncidentStatus.OPEN.value,
            incident_type="payment_failure",
            fingerprint=fp,
        )
        sqlite_session.add(duplicate_active)
        await sqlite_session.commit()
