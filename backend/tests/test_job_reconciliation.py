from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.investigation.models import InvestigationModel, InvestigationStatus
from app.models.incident import IncidentModel
from app.remediation.models import (
    ActionStatus,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationExecutionModel,
    RemediationPlanModel,
)
from app.workers import recovery_tasks


@pytest.mark.asyncio
async def test_reconciler_requeues_pending_work_and_flags_interrupted_dispatch(monkeypatch, sqlite_session):
    now = datetime.now(UTC)
    old = now - timedelta(minutes=10)
    incident = IncidentModel(
        id="incident", title="Fault", service_name="payment-service", severity="HIGH",
        status="OPEN", incident_type="payment_failure", source="test", fingerprint="fault",
    )
    pending = InvestigationModel(
        id="queued", incident_id=incident.id, status=InvestigationStatus.PENDING, updated_at=old
    )
    running = InvestigationModel(
        id="interrupted", incident_id=incident.id, status=InvestigationStatus.RUNNING, updated_at=old
    )
    plan = RemediationPlanModel(
        id="plan", incident_id=incident.id, status=PlanStatus.EXECUTING, summary="Fix payment fault"
    )
    action = RemediationActionModel(
        id="action", plan_id=plan.id, action_type="disable_fault_injection",
        target_service="payment-service", status=ActionStatus.EXECUTING,
        rationale="Fault observed", expected_outcome="Healthy payment requests",
    )
    approval = RemediationApprovalModel(
        id="approval", plan_id=plan.id, action_id=action.id,
        decision="APPROVED", approver_id="test-operator", expires_at=now,
    )
    execution = RemediationExecutionModel(
        id="execution", plan_id=plan.id, action_id=action.id, approval_id=approval.id,
        idempotency_key="test-execution", status=ExecutionStatus.EXECUTING, updated_at=old,
    )
    sqlite_session.add_all([incident, pending, running, plan, action, approval, execution])
    await sqlite_session.commit()

    factory = async_sessionmaker(sqlite_session.bind, expire_on_commit=False)
    monkeypatch.setattr(recovery_tasks, "get_session_factory", lambda: factory)
    dispatch = Mock()
    monkeypatch.setattr("app.workers.investigation_tasks.run_investigation_task.apply_async", dispatch)
    execution_dispatch = Mock()
    monkeypatch.setattr("app.workers.remediation_tasks.run_remediation_execution_task.apply_async", execution_dispatch)

    assert await recovery_tasks.reconcile_jobs() == {"dispatched": 1, "interrupted": 2}
    dispatch.assert_called_once_with(args=["queued"], retry=False)
    execution_dispatch.assert_not_called()
    await sqlite_session.refresh(running)
    await sqlite_session.refresh(execution)
    assert running.status == InvestigationStatus.FAILED
    assert execution.status == ExecutionStatus.FAILED
    assert "Inspect the target" in execution.error_message
    assert await recovery_tasks.reconcile_jobs() == {"dispatched": 0, "interrupted": 0}
