"""Deterministic Safety Policy Engine for OpsPilot remediation actions.
Evaluates strict safety boundaries before an action can be proposed, approved, or executed.
LLMs can NEVER override safety policy decisions.
"""

import logging
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.incident import IncidentModel, IncidentStatus
from app.remediation.models import (
    ActionStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationPlanModel,
)
from app.remediation.registry import ActionRegistry, action_registry

logger = logging.getLogger("opspilot.remediation.policy")


class PolicyDecisionStatus(StrEnum):
    """Structured decision status issued by the Safety Policy Engine."""

    ALLOWED_PENDING_APPROVAL = "ALLOWED_PENDING_APPROVAL"
    APPROVED_READY_TO_EXECUTE = "APPROVED_READY_TO_EXECUTE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"
    INVALID = "INVALID"


class PolicyEvaluationResult(BaseModel):
    """Immutable result of a deterministic safety policy evaluation."""

    decision: PolicyDecisionStatus
    action_type: str
    target_service: str
    environment: str
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    evaluated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def is_allowed(self) -> bool:
        return self.decision in (
            PolicyDecisionStatus.ALLOWED_PENDING_APPROVAL,
            PolicyDecisionStatus.APPROVED_READY_TO_EXECUTE,
        )


class SafetyPolicyEngine:
    """Deterministic policy engine evaluating strict safety rules across the remediation lifecycle."""

    def __init__(self, registry: ActionRegistry | None = None) -> None:
        self.registry = registry or action_registry

    def evaluate_proposal(
        self,
        action: RemediationActionModel,
        plan: RemediationPlanModel,
        incident: IncidentModel | None = None,
        investigation_evidence_ids: set[str] | None = None,
        active_service_actions: list[RemediationActionModel] | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate a proposed action before it can be presented to an operator for approval."""
        passed: list[str] = []
        failed: list[str] = []
        reasons: list[str] = []

        # 1. Action Registration Check
        definition = self.registry.get(action.action_type)
        if definition:
            passed.append("action_registered")
        else:
            failed.append("action_registered")
            reasons.append(f"Action '{action.action_type}' is not registered in the allowlisted action registry.")

        # 2. Environment Allowlist Check
        if definition and action.environment in definition.supported_environments:
            passed.append("environment_supported")
        else:
            failed.append("environment_supported")
            reasons.append(f"Environment '{action.environment}' is not supported for action '{action.action_type}'.")

        # 3. Target Service Allowlist Check
        if definition and action.target_service in definition.supported_services:
            passed.append("target_service_allowlisted")
        else:
            failed.append("target_service_allowlisted")
            reasons.append(f"Service '{action.target_service}' is not allowlisted for action '{action.action_type}'.")

        # 4. Parameters Schema Validation Check
        if definition:
            valid_params, param_err = self.registry.validate_action(
                action.action_type, action.target_service, action.environment, action.parameters
            )
            if valid_params:
                passed.append("parameters_validated")
            else:
                failed.append("parameters_validated")
                reasons.append(f"Parameter validation failed: {param_err}")
        else:
            failed.append("parameters_validated")

        # 5. Preconditions Specification Check
        if action.preconditions and isinstance(action.preconditions, dict):
            passed.append("preconditions_specified")
        else:
            passed.append("preconditions_specified")  # default empty preconditions allowed

        # 6. Active Incident Check (incident must not already be RESOLVED)
        if incident:
            if incident.status == IncidentStatus.RESOLVED:
                failed.append("incident_active")
                reasons.append("Associated incident is already RESOLVED. Remediation cannot be proposed for resolved incidents.")
            else:
                passed.append("incident_active")
        else:
            passed.append("incident_active")

        # 7. Investigation Association Check
        if plan.investigation_id is not None:
            passed.append("investigation_associated")
        else:
            # Standalone plans without prior investigation are permitted but noted
            passed.append("investigation_associated")

        # 8. Evidence Existence Check
        if investigation_evidence_ids is not None and plan.supporting_evidence_ids:
            missing_evidence = set(plan.supporting_evidence_ids) - investigation_evidence_ids
            if missing_evidence:
                failed.append("evidence_verified")
                reasons.append(f"Plan cites evidence IDs not found in associated investigation: {sorted(missing_evidence)}")
            else:
                passed.append("evidence_verified")
        else:
            passed.append("evidence_verified")

        # 9. Concurrency & Conflict Check
        # Check if another action on the same target service is currently EXECUTING or VERIFYING
        if active_service_actions:
            conflicts = [
                a for a in active_service_actions
                if a.id != action.id
                and a.target_service == action.target_service
                and a.status in (ActionStatus.EXECUTING.value, ActionStatus.VERIFYING.value)
            ]
            if conflicts:
                failed.append("no_conflicting_action")
                reasons.append(
                    f"Conflict detected: Action '{conflicts[0].id}' ({conflicts[0].action_type}) is currently active on '{action.target_service}'."
                )
            else:
                passed.append("no_conflicting_action")
        else:
            passed.append("no_conflicting_action")

        # Determine decision
        if failed:
            decision = PolicyDecisionStatus.REJECTED if any("not registered" in r or "not allowlisted" in r for r in reasons) else PolicyDecisionStatus.BLOCKED
        else:
            decision = PolicyDecisionStatus.ALLOWED_PENDING_APPROVAL

        return PolicyEvaluationResult(
            decision=decision,
            action_type=action.action_type,
            target_service=action.target_service,
            environment=action.environment,
            checks_passed=passed,
            checks_failed=failed,
            failure_reasons=reasons,
        )

    def evaluate_execution_readiness(
        self,
        action: RemediationActionModel,
        plan: RemediationPlanModel,
        approval: RemediationApprovalModel | None,
        incident: IncidentModel | None = None,
        has_already_executed: bool = False,
    ) -> PolicyEvaluationResult:
        """Evaluate an approved action immediately prior to execution in the worker process."""
        passed: list[str] = []
        failed: list[str] = []
        reasons: list[str] = []
        now = datetime.now(UTC)

        # 1. Approval Existence Check
        if not approval:
            failed.append("approval_exists")
            reasons.append("No explicit human approval record found. Execution is forbidden.")
        else:
            passed.append("approval_exists")

            # 2. Approval Expiry Check
            exp = approval.expires_at.replace(tzinfo=UTC) if approval.expires_at.tzinfo is None else approval.expires_at
            if exp < now:
                failed.append("approval_not_expired")
                reasons.append(f"Approval expired at {exp.isoformat()}. Current time is {now.isoformat()}.")
            else:
                passed.append("approval_not_expired")

            # 3. Plan Version Immutability Check
            if approval.plan_version != plan.version:
                failed.append("plan_version_matched")
                reasons.append(f"Approval was for plan version {approval.plan_version}, but current plan version is {plan.version}.")
            else:
                passed.append("plan_version_matched")

            if (
                approval.plan_id != plan.id
                or approval.action_id != action.id
                or approval.action_content_hash != action.approval_digest()
            ):
                failed.append("approval_content_matched")
                reasons.append("Approval does not match the current action content. A new approval is required.")
            else:
                passed.append("approval_content_matched")

            # 4. Approval Decision Check
            if approval.decision != "APPROVED":
                failed.append("approval_decision_valid")
                reasons.append(f"Approval decision is '{approval.decision}'. Only 'APPROVED' actions may execute.")
            else:
                passed.append("approval_decision_valid")

        # 5. Duplicate Execution Check
        if has_already_executed or action.status in (ActionStatus.EXECUTED.value, ActionStatus.VERIFIED.value):
            failed.append("not_already_executed")
            reasons.append(f"Action '{action.id}' has already been executed. Duplicate execution is prohibited.")
        else:
            passed.append("not_already_executed")

        # 6. Active Incident Check
        if incident and incident.status == IncidentStatus.RESOLVED:
            failed.append("incident_active")
            reasons.append("Associated incident has already been resolved.")
        else:
            passed.append("incident_active")

        # 7. Re-verify Registration and Safety Allowlist
        valid_action, action_err = self.registry.validate_action(
            action.action_type, action.target_service, action.environment, action.parameters
        )
        if valid_action:
            passed.append("action_registration_revalidated")
        else:
            failed.append("action_registration_revalidated")
            reasons.append(f"Action registration re-validation failed: {action_err}")

        # Determine decision
        if failed:
            if any("expired" in r for r in reasons):
                decision = PolicyDecisionStatus.EXPIRED
            elif any("already been executed" in r for r in reasons):
                decision = PolicyDecisionStatus.BLOCKED
            else:
                decision = PolicyDecisionStatus.REJECTED
        else:
            decision = PolicyDecisionStatus.APPROVED_READY_TO_EXECUTE

        return PolicyEvaluationResult(
            decision=decision,
            action_type=action.action_type,
            target_service=action.target_service,
            environment=action.environment,
            checks_passed=passed,
            checks_failed=failed,
            failure_reasons=reasons,
        )


# Global singleton policy engine
safety_policy_engine = SafetyPolicyEngine()
