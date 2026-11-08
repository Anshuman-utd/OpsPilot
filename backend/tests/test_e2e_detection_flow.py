from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import (
    IncidentEventType,
    IncidentModel,
    IncidentSeverity,
    IncidentStatus,
)
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import PaymentFailureSpikeRule
from app.services.prometheus import PrometheusClient, PrometheusQueryResult, PrometheusSample


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_prometheus():
    client = AsyncMock(spec=PrometheusClient)
    client.base_url = "http://localhost:9090"
    client.timeout = 5.0
    return client


@pytest.fixture
def test_settings():
    return Settings(
        PAYMENT_FAILURE_HIGH_THRESHOLD=20.0,
        PAYMENT_FAILURE_CRITICAL_THRESHOLD=50.0,
        MIN_PAYMENT_SAMPLES=5,
    )


class TestEndToEndDetectionWorkflow:
    """
    End-to-end scenario testing the full lifecycle:
    Normal -> Fault Injected -> Incident Created -> Deduplicated -> Recovered -> Auto-resolved.
    """

    @pytest.mark.asyncio
    async def test_complete_detection_to_resolution_lifecycle(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        engine = DetectionEngine(
            rules=[PaymentFailureSpikeRule()],
            prometheus_client=mock_prometheus,
        )

        # ------------------------------------------------------------------
        # Step 1: Normal State (0% failure rate)
        # ------------------------------------------------------------------
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True, query="ratio", samples=[PrometheusSample(labels={}, value=0.0)]
            ),
            PrometheusQueryResult(
                success=True, query="samples", samples=[PrometheusSample(labels={}, value=25.0)]
            ),
        ]

        normal_res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert normal_res.breaches_detected == 0
        assert normal_res.incidents_created == 0

        stmt = select(IncidentModel)
        assert len((await sqlite_session.execute(stmt)).scalars().all()) == 0

        # ------------------------------------------------------------------
        # Step 2: Fault Condition (65% payment failure spike -> CRITICAL)
        # ------------------------------------------------------------------
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True, query="ratio", samples=[PrometheusSample(labels={}, value=65.0)]
            ),
            PrometheusQueryResult(
                success=True, query="samples", samples=[PrometheusSample(labels={}, value=40.0)]
            ),
        ]

        breach_res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert breach_res.breaches_detected == 1
        assert breach_res.incidents_created == 1

        incidents = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents) == 1
        incident = incidents[0]
        assert incident.service_name == "payment-service"
        assert incident.severity == IncidentSeverity.CRITICAL.value
        assert incident.status == IncidentStatus.OPEN.value
        assert incident.occurrence_count == 1
        assert incident.evidence["observed_failure_percentage"] == 65.0

        # ------------------------------------------------------------------
        # Step 3: Continued Fault (Deduplication pass)
        # ------------------------------------------------------------------
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True, query="ratio", samples=[PrometheusSample(labels={}, value=70.0)]
            ),
            PrometheusQueryResult(
                success=True, query="samples", samples=[PrometheusSample(labels={}, value=55.0)]
            ),
        ]

        dedup_res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert dedup_res.breaches_detected == 1
        assert dedup_res.incidents_created == 0  # No duplicate!
        assert dedup_res.incidents_updated == 1

        await sqlite_session.refresh(incident)
        assert incident.occurrence_count == 2
        assert incident.evidence["observed_failure_percentage"] == 70.0

        # ------------------------------------------------------------------
        # Step 4: Operator Acknowledges Incident
        # ------------------------------------------------------------------
        now = datetime.now(UTC)
        incident.status = IncidentStatus.ACKNOWLEDGED.value
        incident.acknowledged_at = now
        await sqlite_session.commit()

        # ------------------------------------------------------------------
        # Step 5: Service Recovers (Failure rate drops to 0%)
        # ------------------------------------------------------------------
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True, query="ratio", samples=[PrometheusSample(labels={}, value=0.0)]
            ),
            PrometheusQueryResult(
                success=True, query="samples", samples=[PrometheusSample(labels={}, value=30.0)]
            ),
        ]

        recovery_res = await engine.evaluate_all(
            sqlite_session, settings=test_settings, auto_resolve=True
        )
        assert recovery_res.breaches_detected == 0
        assert recovery_res.incidents_resolved == 1

        await sqlite_session.refresh(incident)
        assert incident.status == IncidentStatus.RESOLVED.value
        assert incident.resolved_at is not None

        # Verify audit events recorded in database
        await sqlite_session.refresh(incident, ["events"])
        event_types = [e.event_type for e in incident.events]
        assert IncidentEventType.INCIDENT_CREATED.value in event_types
        assert IncidentEventType.INCIDENT_UPDATED.value in event_types
        assert IncidentEventType.INCIDENT_RESOLVED.value in event_types
