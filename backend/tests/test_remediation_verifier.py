"""Tests for Phase 5 Recovery Verification Engine."""

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.investigation.tools.prometheus_tools import MetricInvestigationResult, MetricSeriesData
from app.remediation.verifier import RecoveryVerificationEngine
from app.services.prometheus import PrometheusQueryResult, PrometheusSample


@pytest.mark.asyncio
async def test_evaluate_observation_healthy_metric():
    mock_tool = AsyncMock()
    # 0.01 error rate (below 0.05 threshold)
    mock_tool.get_service_error_rate.return_value = MetricInvestigationResult(
        tool_name="get_service_error_rate",
        service="order-service",
        query="test",
        success=True,
        series=[MetricSeriesData(labels={"service": "order-service"}, points=[], latest_value=0.01)],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    obs = await verifier.evaluate_observation(
        service_name="order-service",
        verification_conditions={"metric": "http_error_rate", "threshold": 0.05},
    )

    assert obs["telemetry_available"] is True
    assert obs["healthy"] is True
    assert obs["observed_value"] == 0.01


@pytest.mark.asyncio
async def test_evaluate_observation_degraded_metric():
    mock_tool = AsyncMock()
    # 0.15 error rate (exceeds 0.05 threshold)
    mock_tool.get_service_error_rate.return_value = MetricInvestigationResult(
        tool_name="get_service_error_rate",
        service="order-service",
        query="test",
        success=True,
        series=[MetricSeriesData(labels={"service": "order-service"}, points=[], latest_value=0.15)],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    obs = await verifier.evaluate_observation(
        service_name="order-service",
        verification_conditions={"metric": "http_error_rate", "threshold": 0.05},
    )

    assert obs["telemetry_available"] is True
    assert obs["healthy"] is False
    assert obs["observed_value"] == 0.15


@pytest.mark.asyncio
async def test_evaluate_observation_missing_telemetry():
    mock_tool = AsyncMock()
    # Tool fails or returns empty series
    mock_tool.get_service_error_rate.return_value = MetricInvestigationResult(
        tool_name="get_service_error_rate",
        service="order-service",
        query="test",
        success=False,
        error="Prometheus connection refused",
        series=[],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    obs = await verifier.evaluate_observation(
        service_name="order-service",
        verification_conditions={"metric": "http_error_rate", "threshold": 0.05},
    )

    assert obs["telemetry_available"] is False
    assert obs["healthy"] is False  # Missing telemetry is NEVER healthy
    assert obs["observed_value"] is None


@pytest.mark.asyncio
async def test_evaluate_observation_payment_failure_ratio():
    mock_tool = AsyncMock()
    mock_tool.get_payment_failure_ratio.return_value = MetricInvestigationResult(
        tool_name="get_payment_failure_ratio",
        service="payment-service",
        query="test",
        success=True,
        series=[MetricSeriesData(labels={}, points=[], latest_value=2.5)],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    obs = await verifier.evaluate_observation(
        service_name="payment-service",
        verification_conditions={"metric": "payment_failure_ratio", "threshold": 5.0},
    )

    assert obs["telemetry_available"] is True
    assert obs["healthy"] is True
    assert obs["observed_value"] == 2.5


@pytest.mark.asyncio
async def test_evaluate_observation_db_pool_saturation():
    mock_tool = AsyncMock()
    mock_tool.get_db_pool_saturation.return_value = MetricInvestigationResult(
        tool_name="get_db_pool_saturation",
        service="order-service",
        query="test",
        success=True,
        series=[MetricSeriesData(labels={}, points=[], latest_value=35.0)],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    obs = await verifier.evaluate_observation(
        service_name="order-service",
        verification_conditions={"metric": "db_pool_saturation", "threshold": 50.0},
    )

    assert obs["telemetry_available"] is True
    assert obs["healthy"] is True
    assert obs["observed_value"] == 35.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("samples", "telemetry_available", "healthy"),
    [([], False, False), ([0.0], True, False), ([1.0], True, True)],
)
async def test_recovery_requires_healthy_target(samples, telemetry_available, healthy):
    tool = AsyncMock()
    tool.get_service_error_rate.return_value = MetricInvestigationResult(
        tool_name="get_service_error_rate", service="order-service", query="test", success=True,
        series=[MetricSeriesData(labels={}, points=[], latest_value=0.01)],
    )
    tool.client.query_instant.return_value = PrometheusQueryResult(
        success=True, query="up", samples=[PrometheusSample(labels={}, value=value) for value in samples],
    )
    verifier = RecoveryVerificationEngine(prometheus_tool=tool)
    verifier.settings = Settings(AUTH_REQUIRED=True)
    observation = await verifier.evaluate_observation("order-service", {"metric": "http_error_rate"})

    assert observation["telemetry_available"] is telemetry_available
    assert observation["healthy"] is healthy
