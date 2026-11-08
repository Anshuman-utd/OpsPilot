from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import (
    HighHttp5xxErrorRateRule,
    HighP95LatencyRule,
)
from app.services.prometheus import PrometheusClient, PrometheusQueryResult, PrometheusSample


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite async database session fixture."""
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
        HTTP_5XX_HIGH_THRESHOLD=0.05,
        HTTP_5XX_CRITICAL_THRESHOLD=0.20,
        P95_LATENCY_HIGH_SECONDS=1.0,
        P95_LATENCY_CRITICAL_SECONDS=3.0,
    )


class TestIncidentDeduplication:
    """Verifies that repetitive metric breaches update existing incidents without duplication."""

    @pytest.mark.asyncio
    async def test_repeated_breach_updates_same_incident(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        # 10% 5xx error rate on order-service
        ratio_result = PrometheusQueryResult(
            success=True,
            query="ratio",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=0.10)],
        )
        volume_result = PrometheusQueryResult(
            success=True,
            query="volume",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=1.0)],
        )

        mock_prometheus.query_instant.side_effect = [
            ratio_result,
            volume_result,
            ratio_result,
            volume_result,
            ratio_result,
            volume_result,
        ]

        engine = DetectionEngine(
            rules=[HighHttp5xxErrorRateRule()], prometheus_client=mock_prometheus
        )

        # 1st evaluation: Creates initial incident
        res1 = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res1.incidents_created == 1
        assert res1.incidents_updated == 0

        stmt = select(IncidentModel)
        incidents = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents) == 1
        first_id = incidents[0].id
        assert incidents[0].occurrence_count == 1
        assert incidents[0].status == IncidentStatus.OPEN.value

        # 2nd evaluation: Updates existing incident, does NOT create duplicate
        res2 = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res2.incidents_created == 0
        assert res2.incidents_updated == 1

        incidents_after_2 = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents_after_2) == 1
        assert incidents_after_2[0].id == first_id  # ID preserved
        assert incidents_after_2[0].occurrence_count == 2

        # 3rd evaluation: Continues updating existing incident
        res3 = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res3.incidents_created == 0
        assert res3.incidents_updated == 1

        incidents_after_3 = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents_after_3) == 1
        assert incidents_after_3[0].occurrence_count == 3

    @pytest.mark.asyncio
    async def test_different_services_produce_separate_incidents(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        # 10% 5xx error rate on order-service and payment-service
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[
                    PrometheusSample(labels={"service": "order-service"}, value=0.10),
                    PrometheusSample(labels={"service": "payment-service"}, value=0.15),
                ],
            ),
            PrometheusQueryResult(
                success=True,
                query="volume",
                samples=[
                    PrometheusSample(labels={"service": "order-service"}, value=1.0),
                    PrometheusSample(labels={"service": "payment-service"}, value=1.0),
                ],
            ),
        ]

        engine = DetectionEngine(
            rules=[HighHttp5xxErrorRateRule()], prometheus_client=mock_prometheus
        )

        res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res.incidents_created == 2

        stmt = select(IncidentModel)
        incidents = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents) == 2
        services = {inc.service_name for inc in incidents}
        assert services == {"order-service", "payment-service"}
        # Distinct fingerprints
        assert incidents[0].fingerprint != incidents[1].fingerprint

    @pytest.mark.asyncio
    async def test_different_rules_produce_separate_incidents(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        # Rule 1 (5xx) breaches on order-service
        ratio_result = PrometheusQueryResult(
            success=True,
            query="ratio",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=0.10)],
        )
        volume_result = PrometheusQueryResult(
            success=True,
            query="volume",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=1.0)],
        )
        # Rule 2 (latency) also breaches on order-service
        p95_result = PrometheusQueryResult(
            success=True,
            query="p95",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=2.5)],
        )

        mock_prometheus.query_instant.side_effect = [ratio_result, volume_result, p95_result]

        engine = DetectionEngine(
            rules=[HighHttp5xxErrorRateRule(), HighP95LatencyRule()],
            prometheus_client=mock_prometheus,
        )

        res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res.incidents_created == 2

        stmt = select(IncidentModel)
        incidents = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(incidents) == 2
        types = {inc.incident_type for inc in incidents}
        assert types == {"http_5xx_rate", "p95_latency"}

    @pytest.mark.asyncio
    async def test_resolved_incident_recurrence_creates_new_incident(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        ratio_result = PrometheusQueryResult(
            success=True,
            query="ratio",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=0.10)],
        )
        volume_result = PrometheusQueryResult(
            success=True,
            query="volume",
            samples=[PrometheusSample(labels={"service": "order-service"}, value=1.0)],
        )

        mock_prometheus.query_instant.side_effect = [
            ratio_result,
            volume_result,
            ratio_result,
            volume_result,
        ]

        engine = DetectionEngine(
            rules=[HighHttp5xxErrorRateRule()], prometheus_client=mock_prometheus
        )

        # 1. First breach creates incident
        await engine.evaluate_all(sqlite_session, settings=test_settings)
        stmt = select(IncidentModel)
        first_inc = (await sqlite_session.execute(stmt)).scalars().first()
        assert first_inc.status == IncidentStatus.OPEN.value

        # 2. Mark first incident as RESOLVED
        first_inc.status = IncidentStatus.RESOLVED.value
        first_inc.fingerprint = f"{first_inc.fingerprint}_resolved_{first_inc.id[:8]}"
        await sqlite_session.commit()

        # 3. Next breach: Because previous incident is RESOLVED (and active status is not matching),
        # a new incident is created
        res2 = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res2.incidents_created == 1

        all_incidents = (await sqlite_session.execute(stmt)).scalars().all()
        assert len(all_incidents) == 2
