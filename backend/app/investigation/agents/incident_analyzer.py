"""Incident Analyzer Agent for formulating investigation plans."""

import logging
import time
from datetime import datetime
from typing import Any

from app.investigation.state import InvestigationState

logger = logging.getLogger("opspilot.investigation.agents.analyzer")


def _parse_timestamp(val: Any, default_time: float | None = None) -> float:
    fallback = default_time if default_time is not None else time.time()
    if val is None:
        return fallback
    if isinstance(val, (int, float)):
        if val != val or val == float("inf") or val == float("-inf"):
            return fallback
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return fallback
        try:
            num = float(val)
            if num != num or num == float("inf") or num == float("-inf"):
                return fallback
            return num
        except ValueError:
            pass
        try:
            # Handle ISO string
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return fallback
    return fallback


async def run_incident_analyzer(state: InvestigationState) -> dict[str, Any]:
    """
    Analyze incident context, trigger event, and establish the investigation plan.
    """
    ctx = state.get("incident_context", {})
    service = state.get("affected_service") or ctx.get("service_name") or "unknown"
    severity = state.get("incident_severity") or ctx.get("severity") or "HIGH"
    incident_type = ctx.get("incident_type") or "unknown"

    now = time.time()
    raw_triggered = ctx.get("triggered_at") or ctx.get("detected_at")
    trigger_ts = _parse_timestamp(raw_triggered, default_time=now)

    # Future-dated trigger timestamps must not exceed current wall-clock time
    if trigger_ts > now:
        logger.warning(
            "Incident trigger timestamp %.0f is in the future; clamping to current time (%.0f)",
            trigger_ts,
            now,
        )
        trigger_ts = now

    # 15 minutes before incident, up to 5 minutes after (strictly capped at current time)
    start_ts = trigger_ts - 900.0
    end_ts = min(trigger_ts + 300.0, now)

    # Invariance guarantees
    if end_ts > now:
        end_ts = now
    if start_ts > end_ts:
        start_ts = end_ts

    # Determine potential failure hypotheses based on incident type
    hypotheses_to_test = [
        "Elevated request error rate due to internal unhandled exception",
        "Upstream traffic surge causing downstream resource saturation",
        "Database connection pool exhaustion or query lock contention",
    ]
    if "payment" in incident_type.lower() or "payment" in service.lower():
        hypotheses_to_test.append("Downstream payment gateway decline or timeout spike")
    if "latency" in incident_type.lower():
        hypotheses_to_test.append(
            "Downstream dependency call latency propagation or thread pool starvation"
        )

    plan = {
        "target_service": service,
        "incident_type": incident_type,
        "severity": severity,
        "time_window": {
            "start": start_ts,
            "end": end_ts,
            "duration_minutes": round((end_ts - start_ts) / 60.0, 1),
            "trigger_timestamp": trigger_ts,
        },
        "hypotheses_to_test": hypotheses_to_test,
        "telemetry_targets": {
            "prometheus": ["error_rate", "p95_latency", "request_rate", "db_pool"],
            "loki": ["error_logs", "exception_traces"],
            "tempo": ["failed_spans", "bottleneck_spans"],
            "postgres": ["historical_recurrence", "past_rca"],
        },
    }

    logger.info(
        "Incident analyzer generated plan for service %s (window: %.0f - %.0f)",
        service,
        start_ts,
        end_ts,
    )

    return {
        "investigation_plan": plan,
        "affected_service": service,
        "incident_severity": severity,
        "agent_statuses": {"incident_analyzer": "COMPLETED"},
    }
