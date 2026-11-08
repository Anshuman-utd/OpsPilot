from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import (
    HighHttp5xxErrorRateRule,
    HighOrderFailureRateRule,
    HighP95LatencyRule,
    PaymentFailureSpikeRule,
    ServiceUnavailableRule,
)
from app.services.prometheus import PrometheusClient, PrometheusQueryResult, PrometheusSample


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite async database session fixture for isolated tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_prometheus():
    """Mock PrometheusClient for deterministic unit tests."""
    client = AsyncMock(spec=PrometheusClient)
    client.base_url = "http://localhost:9090"
    client.timeout = 5.0
    return client


@pytest.fixture
def test_settings():
    """Deterministic settings for rule tests."""
    return Settings(
        HTTP_5XX_HIGH_THRESHOLD=0.05,
        HTTP_5XX_CRITICAL_THRESHOLD=0.20,
        P95_LATENCY_HIGH_SECONDS=1.0,
        P95_LATENCY_CRITICAL_SECONDS=3.0,
        PAYMENT_FAILURE_HIGH_THRESHOLD=20.0,
        PAYMENT_FAILURE_CRITICAL_THRESHOLD=50.0,
        ORDER_FAILURE_HIGH_THRESHOLD=20.0,
        ORDER_FAILURE_CRITICAL_THRESHOLD=50.0,
        MIN_TRAFFIC_REQUESTS=5,
        MIN_PAYMENT_SAMPLES=5,
    )


class TestDetectionRules:
    """Unit tests for individual detection rule logic."""

    @pytest.mark.asyncio
    async def test_http_5xx_rule_healthy(self, mock_prometheus, test_settings):
        rule = HighHttp5xxErrorRateRule()
        # 1% error rate (below 5% high threshold)
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[PrometheusSample(labels={"service": "api-gateway"}, value=0.01)],
            ),
            PrometheusQueryResult(
                success=True,
                query="volume",
                samples=[PrometheusSample(labels={"service": "api-gateway"}, value=1.0)],
            ),
        ]

        breaches, healthy_fps = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 0
        assert len(healthy_fps) == 1

    @pytest.mark.asyncio
    async def test_http_5xx_rule_breach_high(self, mock_prometheus, test_settings):
        rule = HighHttp5xxErrorRateRule()
        # 8% error rate (breaches high 5% threshold)
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[PrometheusSample(labels={"service": "order-service"}, value=0.08)],
            ),
            PrometheusQueryResult(
                success=True,
                query="volume",
                samples=[PrometheusSample(labels={"service": "order-service"}, value=0.5)],
            ),
        ]

        breaches, _ = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 1
        assert breaches[0].severity == IncidentSeverity.HIGH
        assert breaches[0].service_name == "order-service"
        assert breaches[0].observed_value == 8.0

    @pytest.mark.asyncio
    async def test_http_5xx_rule_breach_critical(self, mock_prometheus, test_settings):
        rule = HighHttp5xxErrorRateRule()
        # 25% error rate (breaches critical 20% threshold)
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[PrometheusSample(labels={"service": "order-service"}, value=0.25)],
            ),
            PrometheusQueryResult(
                success=True,
                query="volume",
                samples=[PrometheusSample(labels={"service": "order-service"}, value=2.0)],
            ),
        ]

        breaches, _ = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 1
        assert breaches[0].severity == IncidentSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_p95_latency_rule(self, mock_prometheus, test_settings):
        rule = HighP95LatencyRule()
        # 3.5s latency (critical breach)
        mock_prometheus.query_instant.return_value = PrometheusQueryResult(
            success=True,
            query="p95",
            samples=[PrometheusSample(labels={"service": "payment-service"}, value=3.5)],
        )

        breaches, _ = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 1
        assert breaches[0].severity == IncidentSeverity.CRITICAL
        assert breaches[0].observed_value == 3.5

    @pytest.mark.asyncio
    async def test_payment_failure_spike_min_sample_guard(self, mock_prometheus, test_settings):
        rule = PaymentFailureSpikeRule()
        # 100% failure rate, but only 2 transactions (< min 5 required)
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[PrometheusSample(labels={}, value=100.0)],
            ),
            PrometheusQueryResult(
                success=True,
                query="samples",
                samples=[PrometheusSample(labels={}, value=2.0)],
            ),
        ]

        breaches, _ = await rule.evaluate(mock_prometheus, test_settings)
        # Should not breach due to minimum sample threshold guard
        assert len(breaches) == 0

    @pytest.mark.asyncio
    async def test_service_unavailable_rule(self, mock_prometheus, test_settings):
        rule = ServiceUnavailableRule()
        mock_prometheus.query_instant.return_value = PrometheusQueryResult(
            success=True,
            query="up",
            samples=[
                PrometheusSample(labels={"service": "api-gateway"}, value=1.0),
                PrometheusSample(labels={"service": "payment-service"}, value=0.0),
            ],
        )

        breaches, healthy_fps = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 1
        assert breaches[0].service_name == "payment-service"
        assert breaches[0].severity == IncidentSeverity.CRITICAL
        assert len(healthy_fps) == 1


    @pytest.mark.asyncio
    async def test_order_failure_rule(self, mock_prometheus, test_settings):
        rule = HighOrderFailureRateRule()
        # 60% order failure rate with 10 total orders (critical breach)
        mock_prometheus.query_instant.side_effect = [
            PrometheusQueryResult(
                success=True,
                query="ratio",
                samples=[PrometheusSample(labels={}, value=60.0)],
            ),
            PrometheusQueryResult(
                success=True,
                query="samples",
                samples=[PrometheusSample(labels={}, value=10.0)],
            ),
        ]

        breaches, _ = await rule.evaluate(mock_prometheus, test_settings)
        assert len(breaches) == 1
        assert breaches[0].severity == IncidentSeverity.CRITICAL
        assert breaches[0].service_name == "order-service"


class TestDetectionEngineResilience:
    """Tests resilience against Prometheus errors, empty sets, and network outages."""

    @pytest.mark.asyncio
    async def test_empty_prometheus_response_does_not_create_incident(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        mock_prometheus.query_instant.return_value = PrometheusQueryResult(
            success=True, query="test", samples=[]
        )
        engine = DetectionEngine(prometheus_client=mock_prometheus)

        res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res.breaches_detected == 0
        assert res.incidents_created == 0

    @pytest.mark.asyncio
    async def test_prometheus_timeout_does_not_create_or_resolve_incidents(
        self, sqlite_session, mock_prometheus, test_settings
    ):
        # Create an existing active incident
        incident = IncidentModel(
            title="Existing Active Incident",
            service_name="payment-service",
            severity="HIGH",
            status=IncidentStatus.OPEN.value,
            incident_type="payment_failure",
            fingerprint="test-fp-123",
            triggered_value=25.0,
            threshold_value=20.0,
        )
        sqlite_session.add(incident)
        await sqlite_session.commit()

        # Simulate Prometheus error
        mock_prometheus.query_instant.return_value = PrometheusQueryResult(
            success=False, query="query", error="Connection timeout to Prometheus"
        )
        engine = DetectionEngine(prometheus_client=mock_prometheus)

        res = await engine.evaluate_all(sqlite_session, settings=test_settings)
        assert res.breaches_detected == 0
        assert res.incidents_created == 0
        assert res.incidents_resolved == 0

        # Existing incident must remain untouched and OPEN
        await sqlite_session.refresh(incident)
        assert incident.status == IncidentStatus.OPEN.value
