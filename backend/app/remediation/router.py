"""FastAPI Router for Phase 5 Remediation & Approval APIs."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
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

logger = logging.getLogger("opspilot.api.remediation")

router = APIRouter(tags=["Remediation & Recovery"])
service = RemediationService()


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
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationApprovalResponse:
    """Explicitly approve an allowlisted action, scheduling its asynchronous controlled execution."""
    approval, _execution = await service.approve_action(plan_id, action_id, request, db)
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
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> RemediationApprovalResponse:
    """Reject a proposed action with an auditable operator rationale."""
    approval = await service.reject_action(plan_id, action_id, request, db)
    return RemediationApprovalResponse.model_validate(approval)


@router.post(
    "/api/v1/remediation-plans/{plan_id}/cancel",
    response_model=RemediationPlanResponse,
)
async def cancel_remediation_plan(
    plan_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    reason: str = "Operator cancelled plan",
) -> RemediationPlanResponse:
    """Cancel a pending remediation plan."""
    plan = await service.cancel_plan(plan_id, reason, "operator", db)
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
