"""Metrics Investigator Agent for querying and correlating Prometheus telemetry."""

import logging
from typing import Any

from app.investigation.state import InvestigationState
from app.investigation.tools.prometheus_tools import PrometheusInvestigationTool

logger = logging.getLogger("opspilot.investigation.agents.metrics")


async def run_metrics_agent(
    state: InvestigationState,
    tool: PrometheusInvestigationTool | None = None,
) -> dict[str, Any]:
    """Execute Prometheus metric queries across golden signals and saturation indicators."""
    metric_tool = tool or PrometheusInvestigationTool()
    service = state.get("affected_service", "order-service")
    plan = state.get("investigation_plan", {})
    tw = plan.get("time_window", {})
    start = float(tw.get("start", 0.0))
    end = float(tw.get("end", 0.0))

    try:
        err_res = await metric_tool.get_service_error_rate(service, start, end)
        lat_res = await metric_tool.get_service_p95_latency(service, start, end)
        req_res = await metric_tool.get_service_request_rate(service, start, end)
        db_res = await metric_tool.get_db_pool_saturation(service, start, end)

        pay_res = None
        if "payment" in service.lower() or "order" in service.lower():
            pay_res = await metric_tool.get_payment_failure_ratio(start, end)

        downstream_res = None
        if "order" in service.lower():
            downstream_res = await metric_tool.get_downstream_latency(
                service, "payment-service", start, end
            )

        summaries = [
            err_res.summary_text,
            lat_res.summary_text,
            req_res.summary_text,
            db_res.summary_text,
        ]
        if pay_res and pay_res.success:
            summaries.append(pay_res.summary_text)
        if downstream_res and downstream_res.success and downstream_res.series:
            summaries.append(downstream_res.summary_text)

        findings = {
            "service": service,
            "error_rate": err_res.to_dict(),
            "p95_latency": lat_res.to_dict(),
            "request_rate": req_res.to_dict(),
            "db_pool_saturation": db_res.to_dict(),
            "payment_failure": pay_res.to_dict() if pay_res else None,
            "downstream_latency": downstream_res.to_dict() if downstream_res else None,
            "summary": " | ".join(s for s in summaries if s),
        }

        return {
            "metrics_findings": findings,
            "agent_statuses": {"metrics_agent": "COMPLETED"},
        }

    except Exception as exc:
        logger.error("Metrics investigator agent failed: %s", exc)
        return {
            "metrics_findings": {
                "service": service,
                "error": str(exc),
                "summary": f"Metrics retrieval failed: {exc}",
            },
            "agent_statuses": {"metrics_agent": "FAILED"},
            "agent_errors": {"metrics_agent": str(exc)},
        }
