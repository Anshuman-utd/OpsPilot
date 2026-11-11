"""Historical Context Investigator Agent for correlating past incidents and recurring failure modes."""

import logging
from typing import Any

from app.core.database import get_session_factory
from app.investigation.state import InvestigationState
from app.investigation.tools.incident_tools import IncidentHistoryTool

logger = logging.getLogger("opspilot.investigation.agents.historical")


async def run_historical_agent(
    state: InvestigationState,
    tool: IncidentHistoryTool | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Retrieve historical incidents for the service and check for recurring fingerprints."""
    hist_tool = tool or IncidentHistoryTool()
    service = state.get("affected_service", "order-service")
    ctx = state.get("incident_context", {})
    fingerprint = ctx.get("fingerprint") or ""
    current_inc_id = state.get("incident_id")

    try:
        # Helper to run queries inside a session
        async def _query(sess: Any) -> dict[str, Any]:
            by_svc = await hist_tool.get_historical_incidents(
                session=sess,
                service_name=service,
                lookback_days=14,
                limit=5,
                exclude_incident_id=current_inc_id,
            )

            by_fp = None
            if fingerprint:
                by_fp = await hist_tool.get_similar_incidents_by_fingerprint(
                    session=sess,
                    fingerprint=fingerprint,
                    limit=5,
                    exclude_incident_id=current_inc_id,
                )

            past_rca_summaries = [
                inc.previous_rca_summary for inc in by_svc.incidents if inc.previous_rca_summary
            ]

            summary = (
                f"Historical analysis: found {by_svc.total_found} past incident(s) for service '{service}'. "
                f"Fingerprint recurrence count: {by_fp.total_found if by_fp else 0}."
            )

            return {
                "service": service,
                "fingerprint": fingerprint,
                "past_incidents_count": by_svc.total_found,
                "recurrence_count": by_fp.total_found if by_fp else 0,
                "past_incidents": by_svc.to_dict()["incidents"],
                "recurring_incidents": by_fp.to_dict()["incidents"] if by_fp else [],
                "previous_rca_summaries": past_rca_summaries,
                "summary": summary,
            }

        if session:
            findings = await _query(session)
        else:
            session_maker = get_session_factory()
            async with session_maker() as sess:
                findings = await _query(sess)

        return {
            "historical_findings": findings,
            "agent_statuses": {"historical_agent": "COMPLETED"},
        }

    except Exception as exc:
        logger.error("Historical context investigator agent failed: %s", exc)
        return {
            "historical_findings": {
                "service": service,
                "error": str(exc),
                "past_incidents_count": 0,
                "recurrence_count": 0,
                "summary": f"Historical retrieval failed: {exc}",
            },
            "agent_statuses": {"historical_agent": "FAILED"},
            "agent_errors": {"historical_agent": str(exc)},
        }
