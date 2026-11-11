"""Tests for Phase 5 Remediation Models and Enums."""

import uuid
from datetime import UTC, datetime, timedelta

from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationAuditEventModel,
    RemediationAuditEventType,
    RemediationExecutionModel,
    RemediationPlanModel,
    RemediationVerificationRunModel,
    RiskLevel,
    VerificationStatus,
)


def test_remediation_enums():
    assert PlanStatus.PENDING_APPROVAL == "PENDING_APPROVAL"
    assert ActionStatus.PROPOSED == "PROPOSED"
    assert ApprovalDecision.APPROVED == "APPROVED"
    assert ExecutionStatus.PENDING == "PENDING"
    assert VerificationStatus.RECOVERED == "RECOVERED"
    assert VerificationStatus.TELEMETRY_UNAVAILABLE == "TELEMETRY_UNAVAILABLE"
    assert RiskLevel.LOW == "LOW"
    assert RemediationAuditEventType.PLAN_CREATED == "PLAN_CREATED"


def test_remediation_plan_model_instantiation():
    plan_id = str(uuid.uuid4())
    incident_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id=incident_id,
        status=PlanStatus.PENDING_APPROVAL.value,
        version=1,
        summary="Test remediation plan for payment decline spike",
        root_cause_hypothesis={"hypothesis_id": "HYP-01", "title": "Payment gateway failure"},
        supporting_evidence_ids=["EV-METRIC-01", "EV-TRACE-01"],
        contradicting_evidence_ids=[],
        risk_assessment={"overall_risk": "LOW"},
        assumptions=["Fault is active"],
        limitations=[],
        expires_at=now + timedelta(minutes=30),
    )

    assert plan.id == plan_id
    assert plan.incident_id == incident_id
    assert plan.status == PlanStatus.PENDING_APPROVAL.value
    assert plan.version == 1
    assert len(plan.supporting_evidence_ids) == 2


def test_remediation_action_model_instantiation():
    action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id=str(uuid.uuid4()),
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Disable active simulated payment failure",
        expected_outcome="Payment decline rate falls back to baseline 0%",
        risk_level=RiskLevel.LOW.value,
        reversible=True,
        preconditions={"service_reachable": True},
        verification_conditions={"metric": "payment_failures_total", "max_ratio": 5.0},
        status=ActionStatus.PROPOSED.value,
    )

    assert action.action_type == "disable_fault_injection"
    assert action.target_service == "payment-service"
    assert action.reversible is True
    assert action.status == ActionStatus.PROPOSED.value


def test_remediation_approval_and_execution_instantiation():
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    approval = RemediationApprovalModel(
        id=approval_id,
        plan_id=plan_id,
        action_id=action_id,
        decision=ApprovalDecision.APPROVED.value,
        approver_id="sre-operator-1",
        comment="Approved following verification of log errors",
        plan_version=1,
        expires_at=now + timedelta(minutes=30),
    )

    execution = RemediationExecutionModel(
        id=str(uuid.uuid4()),
        plan_id=plan_id,
        action_id=action_id,
        approval_id=approval_id,
        idempotency_key=f"exec-{action_id}-v1",
        status=ExecutionStatus.PENDING.value,
    )

    assert approval.decision == ApprovalDecision.APPROVED.value
    assert execution.idempotency_key == f"exec-{action_id}-v1"
    assert execution.status == ExecutionStatus.PENDING.value


def test_remediation_verification_and_audit_event_instantiation():
    execution_id = str(uuid.uuid4())
    verif = RemediationVerificationRunModel(
        id=str(uuid.uuid4()),
        execution_id=execution_id,
        status=VerificationStatus.VERIFYING.value,
        consecutive_healthy_count=0,
        required_healthy_count=3,
        observations=[],
    )

    audit = RemediationAuditEventModel(
        id=str(uuid.uuid4()),
        incident_id=str(uuid.uuid4()),
        execution_id=execution_id,
        event_type=RemediationAuditEventType.EXECUTION_STARTED.value,
        actor="operator",
        details={"action": "disable_fault_injection"},
    )

    assert verif.required_healthy_count == 3
    assert audit.event_type == RemediationAuditEventType.EXECUTION_STARTED.value
