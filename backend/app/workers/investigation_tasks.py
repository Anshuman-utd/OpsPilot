"""Celery background tasks for asynchronous autonomous incident investigation."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app
from app.core.database import get_session_factory
from app.investigation.graph import run_investigation_workflow
from app.investigation.models import (
    AgentStatus,
    InvestigationAgentRunModel,
    InvestigationModel,
    InvestigationStatus,
)
from app.investigation.state import InvestigationState
from app.models.incident import IncidentModel

logger = logging.getLogger("opspilot.workers.investigation")

AGENT_NAMES = [
    "incident_analyzer",
    "metrics_agent",
    "log_agent",
    "trace_agent",
    "historical_agent",
    "aggregator",
    "rca_agent",
    "validator_agent",
]


async def _execute_investigation(
    investigation_id: str,
    session_factory: Any = None,
) -> dict[str, Any]:
    """Execute LangGraph investigation asynchronously and persist runs and final report."""
    factory = session_factory or get_session_factory()
    now = datetime.now(UTC)

    async with factory() as session:
        stmt = (
            select(InvestigationModel)
            .options(
                selectinload(InvestigationModel.incident),
                selectinload(InvestigationModel.agent_runs),
            )
            .where(InvestigationModel.id == investigation_id)
        )
        investigation = (await session.execute(stmt)).scalar_one_or_none()

        if not investigation:
            logger.error("Investigation '%s' not found for execution", investigation_id)
            return {"status": "error", "error": f"Investigation {investigation_id} not found"}

        incident: IncidentModel = investigation.incident
        if not incident:
            logger.error("Parent incident missing for investigation '%s'", investigation_id)
            investigation.status = InvestigationStatus.FAILED.value
            investigation.error_message = "Parent incident record missing"
            await session.commit()
            return {"status": "error", "error": "Parent incident missing"}

        # Mark investigation as RUNNING
        investigation.status = InvestigationStatus.RUNNING.value
        investigation.started_at = now
        investigation.updated_at = now

        # Initialize agent run records if they don't already exist
        existing_runs = {r.agent_name: r for r in investigation.agent_runs}
        for ag_name in AGENT_NAMES:
            if ag_name not in existing_runs:
                new_run = InvestigationAgentRunModel(
                    id=str(uuid.uuid4()),
                    investigation_id=investigation.id,
                    agent_name=ag_name,
                    status=AgentStatus.PENDING.value,
                    created_at=now,
                )
                investigation.agent_runs.append(new_run)
                existing_runs[ag_name] = new_run

        await session.commit()

        # Build initial investigation state
        initial_state: InvestigationState = {
            "investigation_id": str(investigation.id),
            "incident_id": str(incident.id),
            "affected_service": incident.service_name,
            "incident_severity": incident.severity,
            "incident_context": {
                "id": str(incident.id),
                "title": incident.title,
                "service_name": incident.service_name,
                "severity": incident.severity,
                "status": incident.status,
                "incident_type": incident.incident_type,
                "triggered_at": incident.triggered_at.isoformat()
                if incident.triggered_at
                else None,
                "fingerprint": incident.fingerprint,
                "evidence": incident.evidence or {},
            },
            "investigation_started_at": now.isoformat(),
            "investigation_status": "RUNNING",
            "agent_statuses": {},
            "agent_errors": {},
        }

    # Execute workflow outside DB transaction so long queries don't hold connection locks
    try:
        final_state = await run_investigation_workflow(initial_state)
    except Exception as exc:
        logger.exception("Investigation graph execution encountered fatal exception: %s", exc)
        async with factory() as session:
            inv = await session.get(InvestigationModel, investigation_id)
            if inv:
                inv.status = InvestigationStatus.FAILED.value
                inv.error_message = str(exc)
                inv.completed_at = datetime.now(UTC)
                inv.updated_at = datetime.now(UTC)
                await session.commit()
        return {"status": "failed", "error": str(exc)}

    # Persist completed results and individual agent outputs
    async with factory() as session:
        stmt = (
            select(InvestigationModel)
            .options(selectinload(InvestigationModel.agent_runs))
            .where(InvestigationModel.id == investigation_id)
        )
        inv = (await session.execute(stmt)).scalar_one()

        completed_at = datetime.now(UTC)
        duration_ms = (completed_at - (inv.started_at or now)).total_seconds() * 1000.0

        final_rep = final_state.get("final_report", {})
        inv.status = final_state.get("investigation_status", InvestigationStatus.COMPLETED.value)
        inv.completed_at = completed_at
        inv.total_duration_ms = duration_ms
        inv.summary = final_rep.get("executive_summary") or "Investigation completed."
        inv.final_report = final_rep
        inv.error_message = None
        inv.updated_at = completed_at

        # Map agent findings
        agent_findings_map = {
            "incident_analyzer": final_state.get("investigation_plan"),
            "metrics_agent": final_state.get("metrics_findings"),
            "log_agent": final_state.get("logs_findings"),
            "trace_agent": final_state.get("traces_findings"),
            "historical_agent": final_state.get("historical_findings"),
            "aggregator": {
                "observed_facts": final_state.get("observed_facts"),
                "evidence_references": final_state.get("evidence_references"),
            },
            "rca_agent": final_state.get("synthesis_result"),
            "validator_agent": final_state.get("validation_result"),
        }

        agent_statuses = final_state.get("agent_statuses", {})
        agent_errors = final_state.get("agent_errors", {})

        for run_rec in inv.agent_runs:
            ag_name = run_rec.agent_name
            run_rec.status = agent_statuses.get(ag_name, AgentStatus.COMPLETED.value)
            run_rec.findings = agent_findings_map.get(ag_name)
            run_rec.error_message = agent_errors.get(ag_name)
            run_rec.duration_ms = duration_ms / len(AGENT_NAMES)
            run_rec.completed_at = completed_at

        await session.commit()
        logger.info(
            "Successfully persisted investigation %s in %.1fms (status: %s)",
            investigation_id,
            duration_ms,
            inv.status,
        )

    return {
        "investigation_id": investigation_id,
        "status": inv.status,
        "duration_ms": duration_ms,
        "summary": inv.summary,
    }


@celery_app.task(
    name="app.workers.investigation_tasks.run_investigation_task",
    bind=True,
    max_retries=1,
)
def run_investigation_task(self, investigation_id: str) -> dict[str, Any]:
    """Celery task running autonomous investigation in background."""
    logger.info("Celery task picked up investigation '%s'", investigation_id)
    try:
        return asyncio.run(_execute_investigation(investigation_id))
    except Exception as exc:
        logger.exception("Investigation Celery task failed: %s", exc)
        raise self.retry(exc=exc, countdown=5) from exc
