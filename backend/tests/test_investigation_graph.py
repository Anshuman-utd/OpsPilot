"""Unit tests for LangGraph multi-agent orchestration and fault resilience."""

import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.investigation.graph import build_investigation_graph, run_investigation_workflow
from app.investigation.state import ConfidenceLevel, InvestigationState
from app.investigation.tools.loki_tools import LogEntry, LogSearchResult
from app.investigation.tools.prometheus_tools import (
    MetricDataPoint,
    MetricInvestigationResult,
    MetricSeriesData,
)
from app.investigation.tools.tempo_tools import SpanSummary, TraceDetailResult


@pytest.mark.asyncio
async def test_investigation_graph_compilation():
    """Verify graph compiles without syntax or topology errors."""
    graph = build_investigation_graph()
    assert graph is not None


@pytest.mark.asyncio
async def test_full_investigation_workflow_execution():
    """Verify end-to-end execution of the multi-agent investigation workflow."""
    now = time.time()
    inc_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())

    initial_state: InvestigationState = {
        "investigation_id": inv_id,
        "incident_id": inc_id,
        "affected_service": "order-service",
        "incident_severity": "CRITICAL",
        "incident_context": {
            "title": "High HTTP 5xx error rate on order-service",
            "service_name": "order-service",
            "severity": "CRITICAL",
            "incident_type": "high_http_5xx_rate",
            "triggered_at": now - 300,
            "fingerprint": "fp-orders-5xx",
            "evidence": {"observed_error_ratio": 0.25},
        },
        "investigation_started_at": str(now),
        "investigation_status": "RUNNING",
        "agent_statuses": {},
        "agent_errors": {},
    }

    # Mock tool responses so tests run fast, reliably, and deterministically
    mock_metric_res = MetricInvestigationResult(
        tool_name="get_service_error_rate",
        query='sum(rate(http_requests_total{service="order-service", status_code=~"5.."}[2m]))',
        service="order-service",
        success=True,
        series=[
            MetricSeriesData(
                labels={"service": "order-service"},
                points=[MetricDataPoint(timestamp=now, value=0.25)],
                min_value=0.01,
                max_value=0.25,
                mean_value=0.15,
                latest_value=0.25,
            )
        ],
        overall_trend="increasing",
        summary_text="order-service error rate peaked at 0.25 (trend: increasing)",
        deep_link="http://localhost:9090/graph",
    )

    mock_log_res = LogSearchResult(
        tool_name="search_error_logs",
        query='{service="order-service"}',
        service="order-service",
        success=True,
        total_found=2,
        extracted_trace_ids=["4bf92f3577b34da6a3ce929d0e0e4736"],
        logs=[
            LogEntry(
                timestamp=now,
                raw_text="2026-11-09 [ERROR] Database pool exhausted trace_id=4bf92f3577b34da6a3ce929d0e0e4736",
                level="ERROR",
                service="order-service",
                trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            )
        ],
        summary_text="Retrieved 2 error logs",
        deep_link="http://localhost:3001/explore",
    )

    mock_trace_res = TraceDetailResult(
        tool_name="get_trace_details",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        success=True,
        root_span=SpanSummary(
            span_id="s1",
            parent_span_id=None,
            service_name="order-service",
            operation_name="POST /orders",
            duration_ms=1850.0,
            start_time_unix_nano=int(now * 1e9),
            is_error=True,
        ),
        bottleneck_span=SpanSummary(
            span_id="s2",
            parent_span_id="s1",
            service_name="order-service",
            operation_name="db:checkout",
            duration_ms=1700.0,
            start_time_unix_nano=int(now * 1e9),
            is_error=True,
            status_message="Connection acquisition timeout",
        ),
        failed_spans=[],
        total_duration_ms=1850.0,
        services_involved=["order-service"],
        summary_text="Trace bottleneck on order-service::db:checkout (1700ms)",
        deep_link="http://localhost:3001/explore",
    )

    with (
        patch(
            "app.investigation.agents.metrics_agent.PrometheusInvestigationTool.get_service_error_rate",
            new_callable=AsyncMock,
        ) as m_err,
        patch(
            "app.investigation.agents.metrics_agent.PrometheusInvestigationTool.get_service_p95_latency",
            new_callable=AsyncMock,
        ) as m_lat,
        patch(
            "app.investigation.agents.metrics_agent.PrometheusInvestigationTool.get_service_request_rate",
            new_callable=AsyncMock,
        ) as m_req,
        patch(
            "app.investigation.agents.metrics_agent.PrometheusInvestigationTool.get_db_pool_saturation",
            new_callable=AsyncMock,
        ) as m_db,
        patch(
            "app.investigation.agents.log_agent.LokiInvestigationTool.search_error_logs",
            new_callable=AsyncMock,
        ) as m_log,
        patch(
            "app.investigation.agents.trace_agent.TempoInvestigationTool.get_trace_details",
            new_callable=AsyncMock,
        ) as m_trace,
        patch(
            "app.investigation.agents.historical_agent.run_historical_agent", new_callable=AsyncMock
        ) as m_hist,
    ):
        m_err.return_value = mock_metric_res
        m_lat.return_value = mock_metric_res
        m_req.return_value = mock_metric_res
        m_db.return_value = mock_metric_res
        m_log.return_value = mock_log_res
        m_trace.return_value = mock_trace_res
        m_hist.return_value = {
            "historical_findings": {
                "service": "order-service",
                "past_incidents_count": 1,
                "recurrence_count": 1,
                "summary": "1 past incident",
            },
            "agent_statuses": {"historical_agent": "COMPLETED"},
        }

        final_state = await run_investigation_workflow(initial_state)

        # Assert all agent nodes finished
        statuses = final_state["agent_statuses"]
        assert statuses.get("incident_analyzer") == "COMPLETED"
        assert statuses.get("metrics_agent") == "COMPLETED"
        assert statuses.get("log_agent") == "COMPLETED"
        assert statuses.get("trace_agent") == "COMPLETED"
        assert statuses.get("aggregator") == "COMPLETED"
        assert statuses.get("rca_agent") == "COMPLETED"
        assert statuses.get("validator_agent") == "COMPLETED"

        # Assert facts and evidence were aggregated
        assert len(final_state["observed_facts"]) >= 1
        assert len(final_state["evidence_references"]) >= 1

        # Assert final report was generated and validated
        report = final_state["final_report"]
        assert report is not None
        assert "executive_summary" in report
        assert len(report["hypotheses"]) >= 1
        assert report["confidence_level"] in [
            ConfidenceLevel.LOW.value,
            ConfidenceLevel.MEDIUM.value,
            ConfidenceLevel.HIGH.value,
        ]
        assert final_state["investigation_status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_investigation_fault_resilience_on_telemetry_outage():
    """Verify that when telemetry services fail/time out, the graph does not crash and completes with error notes."""
    now = time.time()
    inc_id = str(uuid.uuid4())
    inv_id = str(uuid.uuid4())

    initial_state: InvestigationState = {
        "investigation_id": inv_id,
        "incident_id": inc_id,
        "affected_service": "payment-service",
        "incident_severity": "HIGH",
        "incident_context": {
            "title": "Payment Failure Spike",
            "service_name": "payment-service",
            "severity": "HIGH",
            "incident_type": "payment_failure",
            "triggered_at": now,
        },
        "investigation_started_at": str(now),
        "investigation_status": "RUNNING",
        "agent_statuses": {},
        "agent_errors": {},
    }

    # Simulate Prometheus, Loki, and Tempo throwing network errors
    with (
        patch(
            "app.investigation.agents.metrics_agent.PrometheusInvestigationTool.get_service_error_rate",
            side_effect=RuntimeError("Prometheus connection refused"),
        ),
        patch(
            "app.investigation.agents.log_agent.LokiInvestigationTool.search_error_logs",
            side_effect=RuntimeError("Loki 502 Bad Gateway"),
        ),
        patch(
            "app.investigation.agents.trace_agent.TempoInvestigationTool.search_recent_traces",
            side_effect=RuntimeError("Tempo connection timeout"),
        ),
        patch(
            "app.investigation.agents.historical_agent.run_historical_agent", new_callable=AsyncMock
        ) as m_hist,
    ):
        m_hist.return_value = {
            "historical_findings": {"past_incidents_count": 0, "summary": "No history"},
            "agent_statuses": {"historical_agent": "COMPLETED"},
        }

        final_state = await run_investigation_workflow(initial_state)

        # Graph MUST NOT raise an unhandled exception and must finish
        assert final_state["investigation_status"] == "COMPLETED"
        report = final_state["final_report"]
        assert report is not None
        assert len(report["hypotheses"]) >= 1
        # Confidence must be LOW due to complete telemetry outage
        assert report["confidence_level"] == ConfidenceLevel.LOW.value
        # Errors recorded
        errors = final_state.get("agent_errors", {})
        assert "metrics_agent" in errors
        assert "log_agent" in errors
        assert "trace_agent" in errors
