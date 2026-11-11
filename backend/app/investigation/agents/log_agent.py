"""Log Investigator Agent for querying and correlating Loki application logs."""

import logging
from typing import Any

from app.investigation.state import InvestigationState
from app.investigation.tools.loki_tools import LokiInvestigationTool

logger = logging.getLogger("opspilot.investigation.agents.log")


async def run_log_agent(
    state: InvestigationState,
    tool: LokiInvestigationTool | None = None,
) -> dict[str, Any]:
    """Execute Loki log queries to uncover errors, exceptions, and extract trace IDs."""
    loki_tool = tool or LokiInvestigationTool()
    service = state.get("affected_service", "order-service")
    plan = state.get("investigation_plan", {})
    tw = plan.get("time_window", {})
    start = float(tw.get("start", 0.0))
    end = float(tw.get("end", 0.0))

    try:
        err_res = await loki_tool.search_error_logs(service, start, end, limit=30)

        # Keyword search for known failure triggers if few logs found
        kw_res = None
        if len(err_res.logs) < 5:
            kw_res = await loki_tool.search_logs_by_keyword(
                service, "timeout", start, end, limit=15
            )

        combined_trace_ids = set(err_res.extracted_trace_ids)
        if kw_res:
            combined_trace_ids.update(kw_res.extracted_trace_ids)

        findings = {
            "service": service,
            "total_error_logs": len(err_res.logs),
            "extracted_trace_ids": sorted(combined_trace_ids),
            "error_logs": err_res.to_dict(),
            "keyword_logs": kw_res.to_dict() if kw_res else None,
            "summary": err_res.summary_text,
        }

        return {
            "logs_findings": findings,
            "agent_statuses": {"log_agent": "COMPLETED"},
        }

    except Exception as exc:
        logger.error("Log investigator agent failed: %s", exc)
        return {
            "logs_findings": {
                "service": service,
                "error": str(exc),
                "extracted_trace_ids": [],
                "summary": f"Log retrieval failed: {exc}",
            },
            "agent_statuses": {"log_agent": "FAILED"},
            "agent_errors": {"log_agent": str(exc)},
        }
