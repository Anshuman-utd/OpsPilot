import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import require_account
from app.core.config import get_settings
from app.core.database import get_db_session
from app.models.account import Account
from app.models.incident import (
    IncidentEventModel,
    IncidentEventType,
    IncidentModel,
    IncidentSeverity,
    IncidentStatus,
)
from app.schemas.detection import DetectionRuleResponse, DetectionRunResponse
from app.schemas.incident import (
    IncidentActionRequest,
    IncidentDetailResponse,
    IncidentResponse,
    IncidentStatsResponse,
)
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import get_default_rules
from app.workers.detection_tasks import run_detection_cycle

logger = logging.getLogger("opspilot.api.incidents")

router = APIRouter(prefix="/api/v1/incidents", tags=["Incidents"])


@router.get("", response_model=list[IncidentResponse])
async def list_incidents(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    severity: Annotated[str | None, Query()] = None,
    service: Annotated[str | None, Query()] = None,
    incident_type: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Annotated[str, Query()] = "created_at",
    order: Annotated[str, Query()] = "desc",
) -> list[IncidentResponse]:
    """Retrieve filtered, paginated list of incidents."""
    query = select(IncidentModel)

    valid_statuses = {s.value for s in IncidentStatus}
    if status_filter:
        if status_filter.lower() == "active":
            query = query.where(
                IncidentModel.status.in_(
                    [
                        IncidentStatus.OPEN.value,
                        IncidentStatus.ACKNOWLEDGED.value,
                        IncidentStatus.INVESTIGATING.value,
                    ]
                )
            )
        elif status_filter.upper() in valid_statuses:
            query = query.where(IncidentModel.status == status_filter.upper())
        else:
            allowed = ", ".join(sorted(valid_statuses))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status '{status_filter}'. Allowed: active, {allowed}",
            )

    valid_severities = {s.value for s in IncidentSeverity}
    if severity:
        if severity.upper() in valid_severities:
            query = query.where(IncidentModel.severity == severity.upper())
        else:
            allowed_sev = ", ".join(sorted(valid_severities))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid severity '{severity}'. Allowed: {allowed_sev}",
            )

    if service:
        query = query.where(IncidentModel.service_name == service.lower())

    if incident_type:
        query = query.where(IncidentModel.incident_type == incident_type)

    if search and (term := search.strip()):
        query = query.where(or_(
            IncidentModel.title.icontains(term, autoescape=True),
            IncidentModel.service_name.icontains(term, autoescape=True),
            IncidentModel.id == term,
        ))

    # Sorting
    allowed_sort_fields = {
        "created_at",
        "updated_at",
        "last_seen_at",
        "severity",
        "status",
        "occurrence_count",
    }
    sort_field = sort_by if sort_by in allowed_sort_fields else "created_at"
    sort_column = getattr(IncidentModel, sort_field, IncidentModel.created_at)
    if order.lower() == "asc":
        query = query.order_by(sort_column.asc(), IncidentModel.id.asc())
    else:
        query = query.order_by(sort_column.desc(), IncidentModel.id.desc())

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    incidents = result.scalars().all()
    return [IncidentResponse.model_validate(inc) for inc in incidents]


@router.get("/rules", response_model=list[DetectionRuleResponse])
async def get_detection_rules() -> list[DetectionRuleResponse]:
    """List all registered deterministic detection rules and their configured thresholds."""
    rules = get_default_rules()
    settings = get_settings()

    response: list[DetectionRuleResponse] = []
    for r in rules:
        high_t = 0.0
        crit_t = None
        op = ">"
        query_str = ""

        if r.rule_id == "high_http_5xx_rate":
            high_t = settings.HTTP_5XX_HIGH_THRESHOLD * 100
            crit_t = settings.HTTP_5XX_CRITICAL_THRESHOLD * 100
            query_str = 'sum by (service) (rate(http_requests_total{status_code=~"5.."}[5m]))'
        elif r.rule_id == "high_p95_latency":
            high_t = settings.P95_LATENCY_HIGH_SECONDS
            crit_t = settings.P95_LATENCY_CRITICAL_SECONDS
            query_str = (
                "histogram_quantile(0.95, sum by (le, service) "
                "(rate(http_request_duration_seconds_bucket[5m])))"
            )
        elif r.rule_id == "payment_failure_spike":
            high_t = settings.PAYMENT_FAILURE_HIGH_THRESHOLD
            crit_t = settings.PAYMENT_FAILURE_CRITICAL_THRESHOLD
            query_str = '100 * sum(increase(payments_processed_total{status="FAILED"}[5m])) / total'
        elif r.rule_id == "service_unavailable":
            high_t = 0.0
            crit_t = 0.0
            op = "=="
            query_str = 'up{job=~"api-gateway|order-service|payment-service"}'
        elif r.rule_id == "db_pool_exhaustion":
            high_t = settings.DB_POOL_HIGH_THRESHOLD
            crit_t = settings.DB_POOL_CRITICAL_THRESHOLD
            query_str = "100 * (db_pool_checked_out / db_pool_size)"
        elif r.rule_id == "high_order_failure_rate":
            high_t = settings.ORDER_FAILURE_HIGH_THRESHOLD
            crit_t = settings.ORDER_FAILURE_CRITICAL_THRESHOLD
            query_str = (
                '100 * sum(increase(orders_created_total{status="PAYMENT_FAILED"}[5m])) / total'
            )

        response.append(
            DetectionRuleResponse(
                rule_id=r.rule_id,
                name=r.name,
                description=r.description,
                enabled=r.enabled,
                service_name="all" if "service" not in r.rule_id else r.rule_id,
                promql_query=query_str,
                high_threshold=high_t,
                critical_threshold=crit_t,
                operator=op,
            )
        )

    return response


@router.get("/stats", response_model=IncidentStatsResponse)
async def get_incident_stats(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> IncidentStatsResponse:
    """Retrieve operational metrics and status counters for incidents dashboard."""
    total_q = select(func.count(IncidentModel.id))
    total = (await db.execute(total_q)).scalar() or 0

    open_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.status == IncidentStatus.OPEN.value
    )
    open_cnt = (await db.execute(open_q)).scalar() or 0

    ack_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.status == IncidentStatus.ACKNOWLEDGED.value
    )
    ack_cnt = (await db.execute(ack_q)).scalar() or 0

    inv_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.status == IncidentStatus.INVESTIGATING.value
    )
    inv_cnt = (await db.execute(inv_q)).scalar() or 0

    res_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.status == IncidentStatus.RESOLVED.value
    )
    res_cnt = (await db.execute(res_q)).scalar() or 0

    crit_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.severity == "CRITICAL",
        IncidentModel.status != IncidentStatus.RESOLVED.value,
    )
    crit_cnt = (await db.execute(crit_q)).scalar() or 0

    high_q = select(func.count(IncidentModel.id)).where(
        IncidentModel.severity == "HIGH",
        IncidentModel.status != IncidentStatus.RESOLVED.value,
    )
    high_cnt = (await db.execute(high_q)).scalar() or 0

    # Service breakdown
    svc_q = select(IncidentModel.service_name, func.count(IncidentModel.id)).group_by(
        IncidentModel.service_name
    )
    by_svc = dict((await db.execute(svc_q)).all())

    # Severity breakdown
    sev_q = select(IncidentModel.severity, func.count(IncidentModel.id)).group_by(
        IncidentModel.severity
    )
    by_sev = dict((await db.execute(sev_q)).all())

    return IncidentStatsResponse(
        total_incidents=total,
        open_incidents=open_cnt,
        critical_incidents=crit_cnt,
        high_incidents=high_cnt,
        acknowledged_incidents=ack_cnt,
        investigating_incidents=inv_cnt,
        resolved_incidents=res_cnt,
        by_service=by_svc,
        by_severity=by_sev,
    )


@router.post("/evaluate", response_model=DetectionRunResponse)
async def evaluate_detection_rules(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> DetectionRunResponse:
    """Trigger an immediate deterministic incident detection cycle."""
    if get_settings().auth_required:
        try:
            run_detection_cycle.apply_async(retry=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="The detection queue is unavailable.") from exc
        return DetectionRunResponse(
            status="queued", rules_evaluated=0, breaches_detected=0, incidents_created=0,
            incidents_updated=0, incidents_resolved=0, evaluation_duration_ms=0,
        )
    engine = DetectionEngine()
    return await engine.evaluate_all(db)


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident_detail(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> IncidentDetailResponse:
    """Retrieve full incident details including evidence payload and chronological audit trail."""
    query = (
        select(IncidentModel)
        .options(selectinload(IncidentModel.events))
        .where(IncidentModel.id == incident_id)
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    return IncidentDetailResponse.model_validate(incident)


@router.post("/{incident_id}/acknowledge", response_model=IncidentDetailResponse)
async def acknowledge_incident(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    account: Annotated[Account, Depends(require_account)],
    action: IncidentActionRequest | None = None,
) -> IncidentDetailResponse:
    """Acknowledge an incident, recording operator identity and audit event."""
    action = action or IncidentActionRequest()
    if get_settings().auth_required:
        action = action.model_copy(update={"actor": account.username})
    query = (
        select(IncidentModel)
        .options(selectinload(IncidentModel.events))
        .where(IncidentModel.id == incident_id)
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found.")

    if incident.status == IncidentStatus.RESOLVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot acknowledge an already resolved incident.",
        )

    if incident.status == IncidentStatus.ACKNOWLEDGED.value:
        return IncidentDetailResponse.model_validate(incident)

    now = datetime.now(UTC)
    from_status = incident.status
    incident.status = IncidentStatus.ACKNOWLEDGED.value
    incident.acknowledged_at = incident.acknowledged_at or now
    incident.updated_at = now

    comment_suffix = f": {action.comment}" if action.comment else ""
    db.add(
        IncidentEventModel(
            incident_id=incident.id,
            event_type=IncidentEventType.INCIDENT_ACKNOWLEDGED.value,
            from_status=from_status,
            to_status=IncidentStatus.ACKNOWLEDGED.value,
            message=f"Incident acknowledged by {action.actor}{comment_suffix}",
            actor=action.actor,
            payload={"comment": action.comment} if action.comment else None,
            created_at=now,
        )
    )
    await db.commit()
    await db.refresh(incident)
    return IncidentDetailResponse.model_validate(incident)


@router.post("/{incident_id}/investigate", response_model=IncidentDetailResponse)
async def investigate_incident(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    account: Annotated[Account, Depends(require_account)],
    action: IncidentActionRequest | None = None,
) -> IncidentDetailResponse:
    """Transition incident status to INVESTIGATING."""
    action = action or IncidentActionRequest()
    if get_settings().auth_required:
        action = action.model_copy(update={"actor": account.username})
    query = (
        select(IncidentModel)
        .options(selectinload(IncidentModel.events))
        .where(IncidentModel.id == incident_id)
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found.")

    if incident.status == IncidentStatus.RESOLVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mark a resolved incident as investigating without reopening it.",
        )

    if incident.status == IncidentStatus.INVESTIGATING.value:
        return IncidentDetailResponse.model_validate(incident)

    now = datetime.now(UTC)
    from_status = incident.status
    incident.status = IncidentStatus.INVESTIGATING.value
    incident.acknowledged_at = incident.acknowledged_at or now
    incident.updated_at = now

    comment_suffix = f": {action.comment}" if action.comment else ""
    db.add(
        IncidentEventModel(
            incident_id=incident.id,
            event_type=IncidentEventType.INCIDENT_INVESTIGATING.value,
            from_status=from_status,
            to_status=IncidentStatus.INVESTIGATING.value,
            message=f"Investigation initiated by {action.actor}{comment_suffix}",
            actor=action.actor,
            payload={"comment": action.comment} if action.comment else None,
            created_at=now,
        )
    )
    await db.commit()
    await db.refresh(incident)
    return IncidentDetailResponse.model_validate(incident)


@router.post("/{incident_id}/resolve", response_model=IncidentDetailResponse)
async def resolve_incident(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    account: Annotated[Account, Depends(require_account)],
    action: IncidentActionRequest | None = None,
) -> IncidentDetailResponse:
    """Manually resolve an incident."""
    action = action or IncidentActionRequest()
    if get_settings().auth_required:
        action = action.model_copy(update={"actor": account.username})
    query = (
        select(IncidentModel)
        .options(selectinload(IncidentModel.events))
        .where(IncidentModel.id == incident_id)
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found.")

    if incident.status == IncidentStatus.RESOLVED.value:
        return IncidentDetailResponse.model_validate(incident)

    now = datetime.now(UTC)
    from_status = incident.status
    incident.status = IncidentStatus.RESOLVED.value
    incident.resolved_at = now
    incident.updated_at = now

    comment_suffix = f": {action.comment}" if action.comment else ""
    db.add(
        IncidentEventModel(
            incident_id=incident.id,
            event_type=IncidentEventType.INCIDENT_RESOLVED.value,
            from_status=from_status,
            to_status=IncidentStatus.RESOLVED.value,
            message=f"Incident resolved by {action.actor}{comment_suffix}",
            actor=action.actor,
            payload={"comment": action.comment} if action.comment else None,
            created_at=now,
        )
    )
    await db.commit()
    await db.refresh(incident)
    return IncidentDetailResponse.model_validate(incident)
