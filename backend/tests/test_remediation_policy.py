"""Tests for Allowlisted Action Registry and Safety Policy Engine."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    PlanStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationPlanModel,
    RiskLevel,
)
from app.remediation.policy import PolicyDecisionStatus, safety_policy_engine
from app.remediation.registry import action_registry


def test_registry_contains_default_allowlisted_actions():
    actions = action_registry.list_all()
    action_types = {a.action_type for a in actions}
    assert "disable_fault_injection" in action_types
    assert "reset_all_faults" in action_types
    assert "reset_db_connection_pool" in action_types
    assert "restore_service_default_config" in action_types


def test_registry_validates_disable_fault_injection_correctly():
    # Valid
    valid, err = action_registry.validate_action(
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
    )
    assert valid is True
    assert err is None

    # Invalid fault_type
    valid, err = action_registry.validate_action(
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "arbitrary_malicious_fault"},
    )
    assert valid is False
    assert "Invalid fault_type" in err


def test_registry_rejects_unregistered_actions():
    valid, err = action_registry.validate_action(
        action_type="execute_shell_script",
        target_service="order-service",
        environment="demo",
        parameters={"command": "rm -rf /"},
    )
    assert valid is False
    assert "not registered" in err


def test_registry_rejects_unsupported_services_and_environments():
    # Production environment is strictly rejected
    valid, err = action_registry.validate_action(
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="production",
        parameters={"fault_type": "payment_failure_spike"},
    )
    assert valid is False
    assert "Environment 'production' is not supported" in err

    # Unsupported service rejected
    valid, err = action_registry.validate_action(
        action_type="disable_fault_injection",
        target_service="unregistered-kubernetes-daemon",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
    )
    assert valid is False
    assert "not supported" in err


def test_policy_engine_evaluates_valid_proposal():
    plan = RemediationPlanModel(
        id=str(uuid.uuid4()),
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        summary="Remediate payment decline",
        supporting_evidence_ids=["EV-METRIC-01"],
    )
    action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Clear payment fault",
        expected_outcome="Success",
        risk_level=RiskLevel.LOW.value,
        reversible=True,
    )

    res = safety_policy_engine.evaluate_proposal(
        action=action,
        plan=plan,
        investigation_evidence_ids={"EV-METRIC-01"},
    )

    assert res.decision == PolicyDecisionStatus.ALLOWED_PENDING_APPROVAL
    assert res.is_allowed is True
    assert "action_registered" in res.checks_passed
    assert "environment_supported" in res.checks_passed
    assert "evidence_verified" in res.checks_passed
    assert not res.checks_failed


def test_policy_engine_blocks_on_resolved_incident():
    incident = IncidentModel(
        id="inc-resolved",
        title="Resolved incident",
        service_name="payment-service",
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.RESOLVED,
        fingerprint="fp-resolved",
    )
    plan = RemediationPlanModel(
        id=str(uuid.uuid4()),
        incident_id=incident.id,
        status=PlanStatus.PENDING_APPROVAL.value,
        summary="Test",
    )
    action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Clear payment fault",
        expected_outcome="Success",
    )

    res = safety_policy_engine.evaluate_proposal(
        action=action,
        plan=plan,
        incident=incident,
    )
    assert res.decision == PolicyDecisionStatus.BLOCKED
    assert any("already RESOLVED" in r for r in res.failure_reasons)


def test_policy_engine_detects_conflicting_active_action():
    plan = RemediationPlanModel(
        id=str(uuid.uuid4()),
        incident_id="inc-1",
        status=PlanStatus.PENDING_APPROVAL.value,
        summary="Test",
    )
    action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_type="disable_fault_injection",
        target_service="order-service",
        environment="demo",
        parameters={"fault_type": "db_pool_exhaustion"},
        rationale="Clear db pool fault",
        expected_outcome="Success",
    )
    conflicting_action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id="another-plan",
        action_type="reset_db_connection_pool",
        target_service="order-service",
        environment="demo",
        parameters={},
        rationale="Reset pool",
        expected_outcome="Success",
        status=ActionStatus.EXECUTING.value,
    )

    res = safety_policy_engine.evaluate_proposal(
        action=action,
        plan=plan,
        active_service_actions=[conflicting_action],
    )
    assert res.decision == PolicyDecisionStatus.BLOCKED
    assert any("Conflict detected" in r for r in res.failure_reasons)


def test_policy_engine_execution_readiness_checks():
    plan = RemediationPlanModel(
        id=str(uuid.uuid4()),
        incident_id="inc-1",
        status=PlanStatus.APPROVED.value,
        version=1,
        summary="Test",
    )
    action = RemediationActionModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_type="disable_fault_injection",
        target_service="payment-service",
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Clear payment fault",
        expected_outcome="Success",
        status=ActionStatus.APPROVED.value,
    )
    now = datetime.now(UTC)

    # 1. Missing approval -> BLOCKED / REJECTED
    res_no_app = safety_policy_engine.evaluate_execution_readiness(
        action=action,
        plan=plan,
        approval=None,
    )
    assert res_no_app.decision == PolicyDecisionStatus.REJECTED
    assert any("No explicit human approval" in r for r in res_no_app.failure_reasons)

    # 2. Expired approval -> EXPIRED
    expired_approval = RemediationApprovalModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_id=action.id,
        decision=ApprovalDecision.APPROVED.value,
        action_content_hash=action.approval_digest(),
        plan_version=1,
        expires_at=now - timedelta(minutes=5),  # in the past
    )
    res_expired = safety_policy_engine.evaluate_execution_readiness(
        action=action,
        plan=plan,
        approval=expired_approval,
    )
    assert res_expired.decision == PolicyDecisionStatus.EXPIRED

    # 3. Plan version mismatch -> REJECTED
    mismatched_approval = RemediationApprovalModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_id=action.id,
        decision=ApprovalDecision.APPROVED.value,
        action_content_hash=action.approval_digest(),
        plan_version=2,  # plan version is 1
        expires_at=now + timedelta(minutes=30),
    )
    res_version = safety_policy_engine.evaluate_execution_readiness(
        action=action,
        plan=plan,
        approval=mismatched_approval,
    )
    assert res_version.decision == PolicyDecisionStatus.REJECTED
    assert any("plan version" in r for r in res_version.failure_reasons)

    # 4. Duplicate execution -> BLOCKED
    valid_approval = RemediationApprovalModel(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        action_id=action.id,
        decision=ApprovalDecision.APPROVED.value,
        action_content_hash=action.approval_digest(),
        plan_version=1,
        expires_at=now + timedelta(minutes=30),
    )
    res_dup = safety_policy_engine.evaluate_execution_readiness(
        action=action,
        plan=plan,
        approval=valid_approval,
        has_already_executed=True,
    )
    assert res_dup.decision == PolicyDecisionStatus.BLOCKED
    assert any("already been executed" in r for r in res_dup.failure_reasons)

    # 5. Valid and ready to execute -> APPROVED_READY_TO_EXECUTE
    res_ready = safety_policy_engine.evaluate_execution_readiness(
        action=action,
        plan=plan,
        approval=valid_approval,
        has_already_executed=False,
    )
    assert res_ready.decision == PolicyDecisionStatus.APPROVED_READY_TO_EXECUTE
    assert res_ready.is_allowed is True
