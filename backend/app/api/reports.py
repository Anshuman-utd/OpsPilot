"""Download a workspace incident and its response history as Markdown."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db_session
from app.investigation.models import InvestigationModel
from app.models.incident import IncidentModel
from app.remediation.models import RemediationExecutionModel, RemediationPlanModel
from app.remediation.service import RemediationService

router = APIRouter(tags=["Reports"])


def text(value: Any) -> str:
    # Keep telemetry and LLM text literal in rendered Markdown.
    return "".join(f"\\{char}" if char in "\\`*_{}[]<>()#+-.!|>" else char for char in " ".join(str(value).split()))


@router.get("/api/v1/incidents/{incident_id}/report")
async def incident_report(
    incident_id: str, db: Annotated[AsyncSession, Depends(get_db_session)]
) -> Response:
    incident = await db.scalar(select(IncidentModel).options(selectinload(IncidentModel.events)).where(
        IncidentModel.id == incident_id
    ))
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    investigation = await db.scalar(select(InvestigationModel).where(
        InvestigationModel.incident_id == incident.id,
        InvestigationModel.final_report.is_not(None),
    ).order_by(InvestigationModel.created_at.desc()).limit(1))
    plans = await db.scalars(select(RemediationPlanModel).where(
        RemediationPlanModel.incident_id == incident.id,
    ).options(selectinload(RemediationPlanModel.executions).selectinload(
        RemediationExecutionModel.verification_runs
    )).order_by(RemediationPlanModel.created_at.desc()))
    lines = [
        f"# {text(incident.title)}", "",
        f"- Incident ID: {incident.id}",
        f"- Service: {text(incident.service_name)}",
        f"- Severity: {incident.severity}",
        f"- Status: {incident.status}",
        f"- Detected: {incident.triggered_at.isoformat()}",
        f"- Resolved: {incident.resolved_at.isoformat() if incident.resolved_at else 'Pending'}",
        "", "## Incident timeline", "",
    ]
    lines.extend(f"- {event.created_at.isoformat()} · {text(event.actor)} · {text(event.message)}" for event in incident.events)
    if investigation and investigation.final_report:
        report = investigation.final_report
        lines.extend(["", "## Investigation", ""])
        if mode := report.get("synthesis_mode"):
            lines.extend([f"Synthesis: {text(mode)}", ""])
        lines.extend([text(report.get("executive_summary", "")), "", "### Observed facts", ""])
        lines.extend(f"- {text(fact)}" for fact in report.get("observed_facts", []))
        lines.extend(["", "### Hypotheses", ""])
        for hypothesis in report.get("hypotheses", []):
            lines.append(f"- {text(hypothesis.get('title', 'Hypothesis'))} — {text(hypothesis.get('explanation', ''))}")
        lines.extend(["", "### Evidence references", ""])
        for evidence in report.get("evidence_sources", []):
            lines.append(f"- {text(evidence.get('evidence_id', ''))}: {text(evidence.get('observation', ''))}")
    lines.extend(["", "## Remediation", ""])
    service = RemediationService()
    for plan in plans:
        lines.append(f"- Plan {plan.id} · {plan.status} · {text(plan.summary)}")
        for execution in plan.executions:
            lines.append(f"  - Execution {execution.id} · {execution.status}")
            if execution.error_message:
                lines.append(f"    - {text(execution.error_message)}")
            for run in execution.verification_runs:
                lines.append(f"    - Verification {run.status} · {text(run.summary or 'Awaiting result')}")
        for event in await service.get_audit_events_for_plan(plan.id, db):
            lines.append(f"  - {event.created_at.isoformat()} · {text(event.actor)} · {event.event_type}")
    return Response(
        "\n".join(lines) + "\n", media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="opspilot-{incident.id}.md"'},
    )
