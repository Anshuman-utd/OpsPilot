"""FastAPI Router for Phase 5 Remediation & Approval APIs."""

from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import authorize_workspace
from app.core.config import get_settings
from app.core.database import get_db_session
from app.models.account import Account
from app.remediation.schemas import (
    RemediationApprovalRequest,
    RemediationApprovalResponse,
    RemediationAuditEventResponse,
    RemediationExecutionResponse,
    RemediationPlanCreateRequest,
    RemediationPlanResponse,
    RemediationRejectionRequest,
)
from app.remediation.service import RemediationService

router = APIRouter(tags=["Remediation & Recovery"])
service = RemediationService()
operator_bearer = HTTPBearer(auto_error=False)


async def require_remediation_operator(
    account: Annotated[Account, Depends(authorize_workspace)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(operator_bearer)],
) -> str:
    settings = get_settings()
    if settings.auth_required:
        if not settings.REMEDIATION_ENABLED:
            raise HTTPException(status_code=503, detail="Remediation is disabled.")
        return account.username
    token = settings.REMEDIATION_OPERATOR_TOKEN
    if not settings.REMEDIATION_ENABLED or token is None or not token.get_secret_value():
        raise HTTPException(status_code=503, detail="Remediation operator access is disabled or unconfigured.")
    if credentials is None or not compare_digest(
        credentials.credentials.encode(), token.get_secret_value().encode()
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid remediation operator token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings.REMEDIATION_OPERATOR_ID


@router.post(
    "/api/v1/incidents/{incident_id}/remediation-plans",
    response_model=RemediationPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_remediation_plan(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    request: RemediationPlanCreateRequest | None = None,
) -> RemediationPlanResponse:
    """Formulate and persist a new evidence-backed remediation plan for an incident."""
    plan = await service.create_plan(incident_id, db, request)
    return RemediationPlanResponse.model_validate(plan)


@router.get(
    "/api/v1/incidents/{incident_id}/remediation-plans",
    response_model=list[RemediationPlanResponse],
)
async def list_incident_remediation_plans(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[RemediationPlanResponse]:
    """List all remediation plans created for a specific incident."""
    plans = await service.list_plans_for_incident(incident_id, db)
    return [RemediationPlanResponse.model_validate(p) for p in plans]


@router.get(
    "/api/v1/remediation-plans/{plan_id}",
    response_model=RemediationPlanResponse,
)
async def get_remediation_plan(
    plan_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationPlanResponse:
    """Retrieve full details of a remediation plan and its proposed actions."""
    plan = await service.get_plan(plan_id, db)
    return RemediationPlanResponse.model_validate(plan)


@router.post(
    "/api/v1/remediation-plans/{plan_id}/actions/{action_id}/approve",
    response_model=RemediationApprovalResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_remediation_action(
    plan_id: str,
    action_id: str,
    request: RemediationApprovalRequest,
    operator: Annotated[str, Depends(require_remediation_operator)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationApprovalResponse:
    """Explicitly approve an allowlisted action, scheduling its asynchronous controlled execution."""
    if get_settings().auth_required and not request.action_content_hash:
        raise HTTPException(status_code=422, detail="Review the current action before approving it.")
    approval, _execution = await service.approve_action(
        plan_id, action_id, request.model_copy(update={"approver_id": operator}), db
    )
    return RemediationApprovalResponse.model_validate(approval)


@router.post(
    "/api/v1/remediation-plans/{plan_id}/actions/{action_id}/reject",
    response_model=RemediationApprovalResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_remediation_action(
    plan_id: str,
    action_id: str,
    request: RemediationRejectionRequest,
    operator: Annotated[str, Depends(require_remediation_operator)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationApprovalResponse:
    """Reject a proposed action with an auditable operator rationale."""
    approval = await service.reject_action(
        plan_id, action_id, request.model_copy(update={"approver_id": operator}), db
    )
    return RemediationApprovalResponse.model_validate(approval)


@router.post(
    "/api/v1/remediation-plans/{plan_id}/cancel",
    response_model=RemediationPlanResponse,
)
async def cancel_remediation_plan(
    plan_id: str,
    operator: Annotated[str, Depends(require_remediation_operator)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    reason: str = "Operator cancelled plan",
) -> RemediationPlanResponse:
    """Cancel a pending remediation plan."""
    plan = await service.cancel_plan(plan_id, reason, operator, db)
    return RemediationPlanResponse.model_validate(plan)


@router.get(
    "/api/v1/remediation-plans/{plan_id}/executions",
    response_model=list[RemediationExecutionResponse],
)
async def list_plan_executions(
    plan_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[RemediationExecutionResponse]:
    """Retrieve all execution records associated with a remediation plan."""
    plan = await service.get_plan(plan_id, db)
    return [RemediationExecutionResponse.model_validate(e) for e in plan.executions]


@router.get(
    "/api/v1/remediation-executions/{execution_id}",
    response_model=RemediationExecutionResponse,
)
async def get_remediation_execution_detail(
    execution_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationExecutionResponse:
    """Retrieve execution status, duration, error messages, and recovery verification runs."""
    execution = await service.get_execution(execution_id, db)
    return RemediationExecutionResponse.model_validate(execution)


@router.get(
    "/api/v1/remediation-plans/{plan_id}/audit-events",
    response_model=list[RemediationAuditEventResponse],
)
async def list_plan_audit_events(
    plan_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[RemediationAuditEventResponse]:
    """Retrieve full audit event history for a remediation plan."""
    events = await service.get_audit_events_for_plan(plan_id, db)
    return [RemediationAuditEventResponse.model_validate(e) for e in events]
