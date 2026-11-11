"""Distributed Trace Investigator Agent for querying and analyzing Tempo traces."""

import logging
from typing import Any

from app.investigation.state import InvestigationState
from app.investigation.tools.tempo_tools import TempoInvestigationTool, TraceDetailResult

logger = logging.getLogger("opspilot.investigation.agents.trace")


async def run_trace_agent(
    state: InvestigationState,
    tool: TempoInvestigationTool | None = None,
) -> dict[str, Any]:
    """Execute Tempo trace searches and detail inspections to isolate latency bottlenecks and failing spans."""
    tempo_tool = tool or TempoInvestigationTool()
    service = state.get("affected_service", "order-service")
    plan = state.get("investigation_plan", {})
    tw = plan.get("time_window", {})
    start = float(tw.get("start", 0.0))
    end = float(tw.get("end", 0.0))

    try:
        # Check if log agent or evidence provided known trace IDs
        candidate_trace_ids: list[str] = []
        logs_f = state.get("logs_findings", {})
        if logs_f and isinstance(logs_f, dict):
            candidate_trace_ids.extend(logs_f.get("extracted_trace_ids", []))

        # If no trace IDs from logs, search Tempo directly for error traces
        if not candidate_trace_ids:
            search_res = await tempo_tool.search_recent_traces(
                service, start, end, limit=5, error_only=True
            )
            if search_res.success and search_res.traces:
                candidate_trace_ids = [t.trace_id for t in search_res.traces]

        # If still no error traces, search any recent traces
        if not candidate_trace_ids:
            search_all = await tempo_tool.search_recent_traces(
                service, start, end, limit=3, error_only=False
            )
            if search_all.success and search_all.traces:
                candidate_trace_ids = [t.trace_id for t in search_all.traces]

        # Analyze up to 3 candidate traces
        analyzed_traces: list[TraceDetailResult] = []
        for tid in candidate_trace_ids[:3]:
            detail = await tempo_tool.get_trace_details(tid)
            if detail.success:
                analyzed_traces.append(detail)

        all_bottlenecks = [t.bottleneck_span for t in analyzed_traces if t.bottleneck_span]
        failing_spans_count = sum(len(t.failed_spans) for t in analyzed_traces)
        all_services = sorted(set(svc for t in analyzed_traces for svc in t.services_involved))

        primary_bottleneck = None
        if all_bottlenecks:
            top_b = max(all_bottlenecks, key=lambda s: s.duration_ms)
            primary_bottleneck = {
                "service": top_b.service_name,
                "operation": top_b.operation_name,
                "duration_ms": round(top_b.duration_ms, 2),
            }

        summary = (
            f"Analyzed {len(analyzed_traces)} trace(s) involving: {', '.join(all_services) or service}. "
            f"Found {failing_spans_count} failed span(s)."
        )
        if primary_bottleneck:
            summary += f" Major bottleneck: {primary_bottleneck['service']}::{primary_bottleneck['operation']} ({primary_bottleneck['duration_ms']}ms)."

        findings = {
            "service": service,
            "traces_analyzed_count": len(analyzed_traces),
            "traces": [t.to_dict() for t in analyzed_traces],
            "primary_bottleneck": primary_bottleneck,
            "services_involved": all_services,
            "failing_spans_count": failing_spans_count,
            "summary": summary,
        }

        return {
            "traces_findings": findings,
            "agent_statuses": {"trace_agent": "COMPLETED"},
        }

    except Exception as exc:
        logger.error("Trace investigator agent failed: %s", exc)
        return {
            "traces_findings": {
                "service": service,
                "error": str(exc),
                "traces": [],
                "summary": f"Trace retrieval failed: {exc}",
            },
            "agent_statuses": {"trace_agent": "FAILED"},
            "agent_errors": {"trace_agent": str(exc)},
        }
