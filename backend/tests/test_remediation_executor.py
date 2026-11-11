"""Tests for Phase 5 Controlled Remediation Executor."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.remediation.executor import RemediationExecutor
from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationExecutionModel,
    RemediationPlanModel,
    RiskLevel,
)


@pytest.mark.asyncio
async def test_executor_successful_action_execution():
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-1",
        status=PlanStatus.APPROVED.value,
        version=1,
        summary="Test Plan",
    )
    action = RemediationActionModel(
        id=action_id,
        plan_id=plan_id,
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Disable simulated failure",
        expected_outcome="Restored",
        risk_level=RiskLevel.LOW.value,
        status=ActionStatus.APPROVED.value,
    )
    approval = RemediationApprovalModel(
        id=approval_id,
        plan_id=plan_id,
        action_id=action_id,
        decision=ApprovalDecision.APPROVED.value,
        plan_version=1,
        expires_at=now + timedelta(minutes=30),
    )
    execution = RemediationExecutionModel(
        id=execution_id,
        plan_id=plan_id,
        action_id=action_id,
        approval_id=approval_id,
        idempotency_key=f"exec-{action_id}-v1",
        status=ExecutionStatus.PENDING.value,
    )
    execution.action = action
    execution.plan = plan
    execution.approval = approval

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = execution
    mock_result.scalar_one.return_value = execution
    mock_db.execute.return_value = mock_result

    class MockSessionFactory:
        def __call__(self):
            class Context:
                async def __aenter__(self):
                    return mock_db

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return Context()

    executor = RemediationExecutor()

    # Mock handler and celery verification task
    with patch.object(executor.registry.get("disable_fault_injection"), "handler", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = {"success": True, "fault_type": "payment_failure_spike"}
        with patch("app.workers.remediation_tasks.run_remediation_verification_task.delay") as mock_verif_task:
            res = await executor.execute_action(execution_id, session_factory=MockSessionFactory())

    assert res["status"] == "success"
    assert res["execution_id"] == execution_id
    mock_handler.assert_called_once()
    mock_verif_task.assert_called_once_with(execution_id)


@pytest.mark.asyncio
async def test_executor_prevents_duplicate_execution():
    execution_id = str(uuid.uuid4())
    execution = RemediationExecutionModel(
        id=execution_id,
        plan_id=str(uuid.uuid4()),
        action_id=str(uuid.uuid4()),
        approval_id=str(uuid.uuid4()),
        idempotency_key="exec-already-ran",
        status=ExecutionStatus.SUCCEEDED.value,  # Already executed!
    )
    execution.action = MagicMock()
    execution.plan = MagicMock()
    execution.approval = MagicMock()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = execution
    mock_db.execute.return_value = mock_result

    class MockSessionFactory:
        def __call__(self):
            class Context:
                async def __aenter__(self):
                    return mock_db

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return Context()

    executor = RemediationExecutor()
    res = await executor.execute_action(execution_id, session_factory=MockSessionFactory())
    assert res["status"] == "duplicate_prevented"


@pytest.mark.asyncio
async def test_executor_handles_handler_failure_cleanly():
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    plan = RemediationPlanModel(id=plan_id, incident_id="inc-1", status=PlanStatus.APPROVED.value, version=1, summary="Plan")
    action = RemediationActionModel(
        id=action_id,
        plan_id=plan_id,
        action_type="disable_fault_injection",
        target_service="order-service",
        environment="demo",
        parameters={"fault_type": "order_service_error_spike"},
        rationale="Disable error fault",
        expected_outcome="Fixed",
        status=ActionStatus.APPROVED.value,
    )
    approval = RemediationApprovalModel(
        id=approval_id,
        plan_id=plan_id,
        action_id=action_id,
        decision=ApprovalDecision.APPROVED.value,
        plan_version=1,
        expires_at=now + timedelta(minutes=30),
    )
    execution = RemediationExecutionModel(
        id=execution_id,
        plan_id=plan_id,
        action_id=action_id,
        approval_id=approval_id,
        idempotency_key="exec-fail-1",
        status=ExecutionStatus.PENDING.value,
    )
    execution.action = action
    execution.plan = plan
    execution.approval = approval

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = execution
    mock_result.scalar_one.return_value = execution
    mock_db.execute.return_value = mock_result

    class MockSessionFactory:
        def __call__(self):
            class Context:
                async def __aenter__(self):
                    return mock_db

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            return Context()

    executor = RemediationExecutor()

    with patch.object(executor.registry.get("disable_fault_injection"), "handler", new_callable=AsyncMock) as mock_handler:
        mock_handler.side_effect = RuntimeError("Target service connection refused")
        res = await executor.execute_action(execution_id, session_factory=MockSessionFactory())

    assert res["status"] == "failed"
    assert "connection refused" in res["error"]
