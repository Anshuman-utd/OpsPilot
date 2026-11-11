"""Integration and unit tests for Phase 5 Remediation Service and Approval Workflows."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationPlanModel,
    RiskLevel,
)
from app.remediation.schemas import (
    RemediationApprovalRequest,
    RemediationRejectionRequest,
)
from app.remediation.service import RemediationService


@pytest.fixture
def mock_db_session():
    session = AsyncMock(spec=AsyncSession)
    session.add = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_approve_action_success():
    service = RemediationService()
    now = datetime.now(UTC)
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        version=1,
        summary="Test Plan",
        expires_at=now + timedelta(minutes=30),
        actions=[
            RemediationActionModel(
                id=action_id,
                plan_id=plan_id,
                action_type="disable_fault_injection",
                target_service="payment-service",
                environment="demo",
                parameters={"fault_type": "payment_failure_spike"},
                rationale="Disable simulated failure",
                expected_outcome="Restored",
                risk_level=RiskLevel.LOW.value,
                reversible=True,
                status=ActionStatus.PENDING_APPROVAL.value,
            )
        ],
        approvals=[],
        executions=[],
    )

    mock_db = AsyncMock(spec=AsyncSession)
    mock_exec_result = MagicMock()
    mock_exec_result.scalar_one_or_none.return_value = None  # no prior execution
    mock_db.execute.return_value = mock_exec_result

    with (
        patch.object(service, "get_plan", return_value=plan),
        patch("app.workers.remediation_tasks.run_remediation_execution_task.delay") as mock_delay,
    ):
        req = RemediationApprovalRequest(
            approver_id="sre-alice",
            comment="Approved after reviewing metric graph",
            plan_version=1,
        )
        approval, execution = await service.approve_action(plan_id, action_id, req, mock_db)

    assert approval.decision == ApprovalDecision.APPROVED.value
    assert approval.approver_id == "sre-alice"
    assert approval.plan_version == 1
    assert plan.status == PlanStatus.APPROVED.value
    assert plan.actions[0].status == ActionStatus.APPROVED.value
    assert execution.idempotency_key == f"exec-{action_id}-v1"
    assert execution.status == ExecutionStatus.PENDING.value
    mock_delay.assert_called_once_with(execution.id)


@pytest.mark.asyncio
async def test_approve_action_fails_on_version_mismatch():
    service = RemediationService()
    now = datetime.now(UTC)
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        version=2,  # Current version is 2
        summary="Test Plan",
        expires_at=now + timedelta(minutes=30),
        actions=[
            RemediationActionModel(
                id=action_id,
                plan_id=plan_id,
                action_type="disable_fault_injection",
                target_service="payment-service",
                environment="demo",
                parameters={"fault_type": "payment_failure_spike"},
                rationale="Disable simulated failure",
                expected_outcome="Restored",
                status=ActionStatus.PENDING_APPROVAL.value,
            )
        ],
        approvals=[],
        executions=[],
    )

    mock_db = AsyncMock(spec=AsyncSession)

    with patch.object(service, "get_plan", return_value=plan):
        req = RemediationApprovalRequest(
            approver_id="sre-alice",
            plan_version=1,  # Stale version!
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_action(plan_id, action_id, req, mock_db)

    assert exc_info.value.status_code == 409
    assert "version mismatch" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_approve_action_fails_on_expired_plan():
    service = RemediationService()
    now = datetime.now(UTC)
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        version=1,
        summary="Test Plan",
        expires_at=now - timedelta(minutes=10),  # Expired!
        actions=[
            RemediationActionModel(
                id=action_id,
                plan_id=plan_id,
                action_type="disable_fault_injection",
                target_service="payment-service",
                environment="demo",
                parameters={"fault_type": "payment_failure_spike"},
                rationale="Disable simulated failure",
                expected_outcome="Restored",
                status=ActionStatus.PENDING_APPROVAL.value,
            )
        ],
        approvals=[],
        executions=[],
    )

    mock_db = AsyncMock(spec=AsyncSession)

    with patch.object(service, "get_plan", return_value=plan):
        req = RemediationApprovalRequest(approver_id="sre-alice", plan_version=1)
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_action(plan_id, action_id, req, mock_db)

    assert exc_info.value.status_code == 400
    assert "expired" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_reject_action_success():
    service = RemediationService()
    now = datetime.now(UTC)
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        version=1,
        summary="Test Plan",
        expires_at=now + timedelta(minutes=30),
        actions=[
            RemediationActionModel(
                id=action_id,
                plan_id=plan_id,
                action_type="disable_fault_injection",
                target_service="payment-service",
                environment="demo",
                parameters={"fault_type": "payment_failure_spike"},
                rationale="Disable simulated failure",
                expected_outcome="Restored",
                status=ActionStatus.PENDING_APPROVAL.value,
            )
        ],
    )

    mock_db = AsyncMock(spec=AsyncSession)

    with patch.object(service, "get_plan", return_value=plan):
        req = RemediationRejectionRequest(
            approver_id="sre-bob",
            comment="Root cause hypothesis is incorrect, rejecting action.",
        )
        approval = await service.reject_action(plan_id, action_id, req, mock_db)

    assert approval.decision == ApprovalDecision.REJECTED.value
    assert plan.status == PlanStatus.REJECTED.value
    assert plan.actions[0].status == ActionStatus.REJECTED.value
    assert approval.comment == "Root cause hypothesis is incorrect, rejecting action."


@pytest.mark.asyncio
async def test_cancel_plan():
    service = RemediationService()
    now = datetime.now(UTC)
    plan_id = str(uuid.uuid4())

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        version=1,
        summary="Test Plan",
        expires_at=now + timedelta(minutes=30),
        actions=[
            RemediationActionModel(
                id=str(uuid.uuid4()),
                plan_id=plan_id,
                action_type="reset_all_faults",
                target_service="order-service",
                environment="demo",
                parameters={},
                rationale="Reset faults",
                expected_outcome="Restored",
                status=ActionStatus.PENDING_APPROVAL.value,
            )
        ],
    )

    mock_db = AsyncMock(spec=AsyncSession)
    with patch.object(service, "get_plan", return_value=plan):
        cancelled_plan = await service.cancel_plan(plan_id, "Incident resolved by external team", "operator", mock_db)

    assert cancelled_plan.status == PlanStatus.CANCELLED.value
    assert cancelled_plan.actions[0].status == ActionStatus.CANCELLED.value
