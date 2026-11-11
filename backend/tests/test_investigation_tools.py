"""Unit tests for Phase 4 Telemetry Investigation Tools."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.models import InvestigationModel, InvestigationStatus
from app.investigation.tools.incident_tools import IncidentHistoryTool
from app.investigation.tools.loki_tools import LokiInvestigationTool
from app.investigation.tools.prometheus_tools import (
    PrometheusInvestigationTool,
)
from app.investigation.tools.tempo_tools import TempoInvestigationTool
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.services.prometheus import PrometheusQueryResult, PrometheusSeries

# =========================================================================
# 1. Prometheus Investigation Tool Tests
# =========================================================================


@pytest.mark.asyncio
async def test_prometheus_investigation_tool_metrics_and_trend():
    mock_client = AsyncMock()
    # 6 points increasing: 0.01 -> 0.05
    points = [
        (100.0, 0.01),
        (115.0, 0.01),
        (130.0, 0.02),
        (145.0, 0.04),
        (160.0, 0.05),
        (175.0, 0.06),
    ]
    mock_client.query_range.return_value = PrometheusQueryResult(
        success=True,
        query='sum(rate(http_requests_total{service="order-service", status_code=~"5.."}[2m]))',
        series=[PrometheusSeries(labels={"service": "order-service"}, values=points)],
    )

    tool = PrometheusInvestigationTool(client=mock_client)
    res = await tool.get_service_error_rate("order-service", 100.0, 175.0)

    assert res.success is True
    assert res.service == "order-service"
    assert res.overall_trend == "increasing"
    assert len(res.series) == 1
    assert res.series[0].min_value == 0.01
    assert res.series[0].max_value == 0.06
    assert res.series[0].latest_value == 0.06
    assert "order-service" in res.summary_text
    assert "graph?g0.expr=" in res.deep_link


@pytest.mark.asyncio
async def test_prometheus_investigation_tool_query_failure():
    mock_client = AsyncMock()
    mock_client.query_range.return_value = PrometheusQueryResult(
        success=False,
        query="bad query",
        error="connection refused",
    )

    tool = PrometheusInvestigationTool(client=mock_client)
    res = await tool.get_service_p95_latency("order-service", 100.0, 200.0)

    assert res.success is False
    assert res.error == "connection refused"
    assert res.series == []


@pytest.mark.asyncio
async def test_prometheus_compare_windows():
    mock_client = AsyncMock()

    # Baseline: mean = 0.02
    mock_client.query_range.side_effect = [
        # Current window: mean = 0.10 (+400% change)
        PrometheusQueryResult(
            success=True,
            query="test_metric",
            series=[PrometheusSeries(labels={}, values=[(200.0, 0.10)])],
        ),
        # Baseline window: mean = 0.02
        PrometheusQueryResult(
            success=True,
            query="test_metric",
            series=[PrometheusSeries(labels={}, values=[(100.0, 0.02)])],
        ),
    ]

    tool = PrometheusInvestigationTool(client=mock_client)
    res = await tool.compare_windows(
        "test_metric",
        current_window=(200.0, 250.0),
        baseline_window=(100.0, 150.0),
    )

    assert res.error is None
    assert res.is_anomaly is True
    assert res.percentage_change == pytest.approx(400.0, rel=1e-2)
    assert "Anomaly detected" in res.summary


@pytest.mark.asyncio
async def test_prometheus_downstream_latency_generated_promql():
    mock_client = AsyncMock()
    mock_client.query_range.return_value = PrometheusQueryResult(
        success=True,
        query="test",
        series=[
            PrometheusSeries(
                labels={"service": "order-service->payment-service"},
                values=[(100.0, 0.45), (150.0, 0.75)],
            )
        ],
    )
    tool = PrometheusInvestigationTool(client=mock_client)

    # 1. With target service
    res_with_target = await tool.get_downstream_latency(
        "order-service", "payment-service", 100.0, 200.0
    )
    expected_query_with_target = (
        'histogram_quantile(0.95, sum by (le) (rate(downstream_request_duration_seconds_bucket{service="order-service",target="payment-service"}[2m])))'
    )
    assert mock_client.query_range.call_args[1]["query"] == expected_query_with_target
    assert res_with_target.success is True
    assert len(res_with_target.series) == 1
    assert res_with_target.series[0].max_value == 0.75

    # 2. Without target service
    res_no_target = await tool.get_downstream_latency("order-service", None, 100.0, 200.0)
    expected_query_without_target = (
        'histogram_quantile(0.95, sum by (le) (rate(downstream_request_duration_seconds_bucket{service="order-service"}[2m])))'
    )
    assert mock_client.query_range.call_args[1]["query"] == expected_query_without_target
    assert res_no_target.success is True


@pytest.mark.asyncio
async def test_prometheus_downstream_latency_empty_series_handled_honestly():
    mock_client = AsyncMock()
    mock_client.query_range.return_value = PrometheusQueryResult(
        success=True,
        query="test",
        series=[],
    )
    tool = PrometheusInvestigationTool(client=mock_client)
    res = await tool.get_downstream_latency("order-service", "payment-service", 100.0, 200.0)
    assert res.success is True
    assert res.series == []
    assert "No time-series data points returned" in res.summary_text


# =========================================================================
# 2. Loki Investigation Tool Tests
# =========================================================================


@pytest.mark.asyncio
async def test_loki_investigation_tool_search_error_logs():
    mock_response = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"service": "order-service", "level": "ERROR"},
                    "values": [
                        [
                            "1670000000000000000",
                            "2026-11-09T12:00:00Z [ERROR] Failed to charge customer trace_id=4bf92f3577b34da6a3ce929d0e0e4736",
                        ],
                        [
                            "1670000005000000000",
                            "2026-11-09T12:00:05Z [ERROR] Database timeout occurred trace_id=8a3ce929d0e0e47364bf92f3577b34da",
                        ],
                    ],
                }
            ],
        },
    }

    tool = LokiInvestigationTool(base_url="http://localhost:3100")
    with patch.object(tool, "query_range", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_response

        res = await tool.search_error_logs("order-service", 1000.0, 2000.0)

        assert res.success is True
        assert res.service == "order-service"
        assert len(res.logs) == 2
        assert len(res.extracted_trace_ids) == 2
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in res.extracted_trace_ids
        assert res.logs[0].level == "ERROR"
        assert "explore?left=" in res.deep_link


@pytest.mark.asyncio
async def test_loki_investigation_tool_network_failure():
    tool = LokiInvestigationTool(base_url="http://localhost:3100")
    with patch.object(tool, "query_range", side_effect=httpx.ConnectError("Connection refused")):
        res = await tool.search_error_logs("payment-service", 100.0, 200.0)

        assert res.success is False
        assert "Connection refused" in (res.error or "")
        assert res.logs == []


# =========================================================================
# 3. Tempo Investigation Tool Tests
# =========================================================================


@pytest.mark.asyncio
async def test_tempo_investigation_tool_parse_spans_and_bottleneck():
    raw_trace_json = {
        "batches": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "order-service"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
                                "spanId": "span-root-1",
                                "parentSpanId": "",
                                "name": "POST /orders",
                                "startTimeUnixNano": "1670000000000000000",
                                "endTimeUnixNano": "1670000002000000000",  # 2000ms
                                "attributes": [
                                    {"key": "http.status_code", "value": {"intValue": 500}}
                                ],
                                "status": {"code": 2, "message": "Downstream payment failed"},
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "payment-service"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
                                "spanId": "span-child-2",
                                "parentSpanId": "span-root-1",
                                "name": "POST /payments/charge",
                                "startTimeUnixNano": "1670000000200000000",
                                "endTimeUnixNano": "1670000001800000000",  # 1600ms (bottleneck)
                                "attributes": [
                                    {"key": "http.status_code", "value": {"intValue": 503}}
                                ],
                                "status": {"code": 2, "message": "Gateway timeout"},
                            }
                        ]
                    }
                ],
            },
        ]
    }

    tool = TempoInvestigationTool(base_url="http://localhost:3200")
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = raw_trace_json
        mock_get.return_value = mock_resp

        res = await tool.get_trace_details("4bf92f3577b34da6a3ce929d0e0e4736")

        assert res.success is True
        assert res.root_span is not None
        assert res.root_span.service_name == "order-service"
        assert res.root_span.duration_ms == 2000.0
        assert res.bottleneck_span is not None
        assert res.bottleneck_span.service_name == "order-service"
        assert len(res.failed_spans) == 2
        assert sorted(res.services_involved) == ["order-service", "payment-service"]
        assert "explore?left=" in res.deep_link


@pytest.mark.asyncio
async def test_tempo_trace_not_found():
    tool = TempoInvestigationTool(base_url="http://localhost:3200")
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Trace not found"
        mock_get.return_value = mock_resp

        res = await tool.get_trace_details("nonexistent-trace-id")
        assert res.success is False
        assert "not found" in (res.error or "")


# =========================================================================
# 4. Incident History Tool Tests
# =========================================================================


@pytest.mark.asyncio
async def test_incident_history_tool(sqlite_session: AsyncSession):
    now = datetime.now(UTC)

    # 1. Past resolved incident
    inc1 = IncidentModel(
        id=str(uuid.uuid4()),
        fingerprint="fp-test-service-1",
        title="High latency on order-service",
        service_name="order-service",
        severity=IncidentSeverity.HIGH.value,
        status=IncidentStatus.RESOLVED.value,
        incident_type="high_p95_latency",
        source="detection_engine",
        triggered_at=now - timedelta(days=2),
        resolved_at=now - timedelta(days=2) + timedelta(hours=1),  # 60 min duration
        occurrence_count=3,
        evidence={},
    )
    sqlite_session.add(inc1)
    await sqlite_session.flush()

    # Add an investigation report to inc1
    inv1 = InvestigationModel(
        id=str(uuid.uuid4()),
        incident_id=inc1.id,
        status=InvestigationStatus.COMPLETED.value,
        trigger_type="automatic",
        summary="Database lock contention on order table caused thread starvation.",
    )
    sqlite_session.add(inv1)

    # 2. Current active incident to exclude
    inc_current = IncidentModel(
        id=str(uuid.uuid4()),
        fingerprint="fp-test-service-1",
        title="Current High latency on order-service",
        service_name="order-service",
        severity=IncidentSeverity.CRITICAL.value,
        status=IncidentStatus.OPEN.value,
        incident_type="high_p95_latency",
        source="detection_engine",
        triggered_at=now,
        occurrence_count=1,
        evidence={},
    )
    sqlite_session.add(inc_current)
    await sqlite_session.commit()

    tool = IncidentHistoryTool()

    # Search by service name excluding current incident
    history = await tool.get_historical_incidents(
        session=sqlite_session,
        service_name="order-service",
        lookback_days=7,
        exclude_incident_id=str(inc_current.id),
    )

    assert history.success is True
    assert history.total_found == 1
    item = history.incidents[0]
    assert item.incident_id == str(inc1.id)
    assert (
        item.previous_rca_summary
        == "Database lock contention on order table caused thread starvation."
    )
    assert item.duration_minutes == pytest.approx(60.0, rel=1e-1)

    # Search similar by fingerprint
    fp_search = await tool.get_similar_incidents_by_fingerprint(
        session=sqlite_session,
        fingerprint="fp-test-service-1",
        exclude_incident_id=str(inc_current.id),
    )
    assert fp_search.success is True
    assert fp_search.total_found == 1
    assert fp_search.incidents[0].incident_id == str(inc1.id)
