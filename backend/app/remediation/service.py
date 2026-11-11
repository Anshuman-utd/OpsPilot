"""Remediation Service managing plan creation, approval workflows, and audit trails."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.investigation.models import InvestigationModel
from app.models.incident import IncidentModel
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
)
from app.remediation.planner import RemediationPlanner, remediation_planner
from app.remediation.policy import SafetyPolicyEngine, safety_policy_engine
from app.remediation.schemas import (
    RemediationApprovalRequest,
    RemediationPlanCreateRequest,
    RemediationRejectionRequest,
)

logger = logging.getLogger("opspilot.remediation.service")


class RemediationService:
    """Core domain service for controlled remediation workflows."""

    def __init__(
        self,
        planner: RemediationPlanner | None = None,
        policy_engine: SafetyPolicyEngine | None = None,
    ) -> None:
        self.planner = planner or remediation_planner
        self.policy = policy_engine or safety_policy_engine
        self.settings = get_settings()

    async def create_plan(
        self,
        incident_id: str,
        db: AsyncSession,
        request: RemediationPlanCreateRequest | None = None,
    ) -> RemediationPlanModel:
        """Formulate, policy-validate, and persist a new remediation plan for an incident."""
        # 1. Fetch incident
        stmt = select(IncidentModel).where(IncidentModel.id == incident_id)
        incident = (await db.execute(stmt)).scalar_one_or_none()
        if not incident:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident '{incident_id}' not found.",
            )

        # 2. Fetch associated investigation if provided or find latest
        investigation_id = request.investigation_id if request else None
        investigation: InvestigationModel | None = None
        if investigation_id:
            inv_stmt = select(InvestigationModel).where(InvestigationModel.id == investigation_id)
            investigation = (await db.execute(inv_stmt)).scalar_one_or_none()
        else:
            inv_stmt = (
                select(InvestigationModel)
                .where(InvestigationModel.incident_id == incident_id)
                .order_by(desc(InvestigationModel.created_at))
            )
            investigation = (await db.execute(inv_stmt)).scalars().first()

        final_report = investigation.final_report if investigation else {}
        evidence_sources = final_report.get("evidence_sources", [])

        # 3. Formulate plan proposal via planner
        proposal = await self.planner.plan(
            incident=incident,
            final_report=final_report,
            evidence_refs=evidence_sources,
        )

        now = datetime.now(UTC)
        expiry_minutes = self.settings.REMEDIATION_APPROVAL_EXPIRY_MINUTES
        expires_at = now + timedelta(minutes=expiry_minutes)

        plan_id = str(uuid.uuid4())
        plan = RemediationPlanModel(
            id=plan_id,
            incident_id=incident_id,
            investigation_id=investigation.id if investigation else None,
            status=PlanStatus.PENDING_APPROVAL.value,
            version=1,
            summary=proposal.summary,
            root_cause_hypothesis=proposal.root_cause_hypothesis,
            supporting_evidence_ids=proposal.supporting_evidence_ids,
            contradicting_evidence_ids=proposal.contradicting_evidence_ids,
            risk_assessment=proposal.risk_assessment,
            assumptions=proposal.assumptions,
            limitations=proposal.limitations,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        db.add(plan)
        await db.flush()

        # 4. Create and policy-evaluate proposed actions
        ev_id_set = {e.get("evidence_id") for e in evidence_sources if e.get("evidence_id")}
        for prop_act in proposal.proposed_actions:
            action_id = str(uuid.uuid4())
            action = RemediationActionModel(
                id=action_id,
                plan_id=plan_id,
                action_type=prop_act.action_type,
                target_service=prop_act.target_service,
                environment=prop_act.environment,
                parameters=prop_act.parameters,
                rationale=prop_act.rationale,
                expected_outcome=prop_act.expected_outcome,
                risk_level=prop_act.risk_level.value if hasattr(prop_act.risk_level, "value") else str(prop_act.risk_level),
                reversible=prop_act.reversible,
                preconditions=prop_act.preconditions,
                verification_conditions=prop_act.verification_conditions,
                status=ActionStatus.PENDING_APPROVAL.value,
                created_at=now,
            )

            # Evaluate policy
            eval_res = self.policy.evaluate_proposal(
                action=action,
                plan=plan,
                incident=incident,
                investigation_evidence_ids=ev_id_set,
            )

            if not eval_res.is_allowed:
                action.status = ActionStatus.PROPOSED.value

            db.add(action)

            # Audit policy evaluation
            audit = RemediationAuditEventModel(
                id=str(uuid.uuid4()),
                incident_id=incident_id,
                investigation_id=investigation.id if investigation else None,
                plan_id=plan_id,
                action_id=action_id,
                event_type=RemediationAuditEventType.POLICY_EVALUATED.value,
                actor="safety_policy_engine",
                details=eval_res.model_dump(),
                created_at=now,
            )
            db.add(audit)

        # Audit plan creation
        plan_audit = RemediationAuditEventModel(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            investigation_id=investigation.id if investigation else None,
            plan_id=plan_id,
            event_type=RemediationAuditEventType.PLAN_CREATED.value,
            actor="remediation_planner",
            details={
                "summary": plan.summary,
                "actions_count": len(proposal.proposed_actions),
                "risk": proposal.risk_assessment,
            },
            created_at=now,
        )
        db.add(plan_audit)

        await db.commit()

        # Re-query with eager loading
        return await self.get_plan(plan_id, db)

    async def get_plan(self, plan_id: str, db: AsyncSession) -> RemediationPlanModel:
        """Fetch remediation plan with child actions and approvals eagerly loaded."""
        stmt = (
            select(RemediationPlanModel)
            .options(
                selectinload(RemediationPlanModel.actions),
                selectinload(RemediationPlanModel.approvals),
                selectinload(RemediationPlanModel.executions).selectinload(RemediationExecutionModel.verification_runs),
            )
            .where(RemediationPlanModel.id == plan_id)
        )
        plan = (await db.execute(stmt)).scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Remediation plan '{plan_id}' not found.",
            )
        return plan

    async def list_plans_for_incident(
        self, incident_id: str, db: AsyncSession
    ) -> list[RemediationPlanModel]:
        """List all remediation plans created for an incident."""
        stmt = (
            select(RemediationPlanModel)
            .options(
                selectinload(RemediationPlanModel.actions),
                selectinload(RemediationPlanModel.approvals),
                selectinload(RemediationPlanModel.executions).selectinload(RemediationExecutionModel.verification_runs),
            )
            .where(RemediationPlanModel.incident_id == incident_id)
            .order_by(desc(RemediationPlanModel.created_at))
        )
        return list((await db.execute(stmt)).scalars().all())

    async def approve_action(
        self,
        plan_id: str,
        action_id: str,
        approval_req: RemediationApprovalRequest,
        db: AsyncSession,
    ) -> tuple[RemediationApprovalModel, RemediationExecutionModel]:
        """Approve an allowlisted action, record approval, create execution, and trigger Celery task."""
        now = datetime.now(UTC)

        # 1. Fetch and validate plan
        plan = await self.get_plan(plan_id, db)
        if plan.status not in (PlanStatus.PENDING_APPROVAL.value, PlanStatus.APPROVED.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve action on plan with status '{plan.status}'.",
            )

        # Check plan expiry
        exp = plan.expires_at.replace(tzinfo=UTC) if plan.expires_at and plan.expires_at.tzinfo is None else plan.expires_at
        if exp and exp < now:
            plan.status = PlanStatus.EXPIRED.value
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Remediation plan has expired. Please formulate a new plan.",
            )

        # 2. Check plan version immutability
        if approval_req.plan_version != plan.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Plan version mismatch. Submitted approval was for version {approval_req.plan_version}, but plan is at version {plan.version}.",
            )

        # 3. Locate action
        action = next((a for a in plan.actions if a.id == action_id), None)
        if not action:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Action '{action_id}' not found on plan '{plan_id}'.",
            )

        if action.status == ActionStatus.APPROVED.value:
            # Idempotent return if already approved
            existing_app = next((app for app in plan.approvals if app.action_id == action_id), None)
            existing_exec = next((e for e in plan.executions if e.action_id == action_id), None)
            if existing_app and existing_exec:
                return existing_app, existing_exec

        if action.status not in (ActionStatus.PENDING_APPROVAL.value, ActionStatus.PROPOSED.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action '{action_id}' has status '{action.status}' and cannot be approved.",
            )

        # 4. Check if action has already executed
        exec_stmt = select(RemediationExecutionModel).where(RemediationExecutionModel.action_id == action_id)
        prior_exec = (await db.execute(exec_stmt)).scalar_one_or_none()
        if prior_exec:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action '{action_id}' has already been executed.",
            )

        # 5. Create approval record
        approval_id = str(uuid.uuid4())
        expiry_minutes = self.settings.REMEDIATION_APPROVAL_EXPIRY_MINUTES
        approval = RemediationApprovalModel(
            id=approval_id,
            plan_id=plan_id,
            action_id=action_id,
            decision=ApprovalDecision.APPROVED.value,
            approver_id=approval_req.approver_id,
            comment=approval_req.comment,
            plan_version=plan.version,
            expires_at=now + timedelta(minutes=expiry_minutes),
            decided_at=now,
            created_at=now,
        )
        db.add(approval)

        # 6. Pre-flight Safety Policy Re-evaluation
        readiness = self.policy.evaluate_execution_readiness(
            action=action,
            plan=plan,
            approval=approval,
            has_already_executed=False,
        )
        if not readiness.is_allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Safety Policy blocked execution: {'; '.join(readiness.failure_reasons)}",
            )

        # 7. Update action and plan statuses
        action.status = ActionStatus.APPROVED.value
        plan.status = PlanStatus.APPROVED.value
        plan.updated_at = now

        # 8. Create execution record with idempotency key
        execution_id = str(uuid.uuid4())
        idempotency_key = f"exec-{action_id}-v{plan.version}"
        execution = RemediationExecutionModel(
            id=execution_id,
            plan_id=plan_id,
            action_id=action_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            status=ExecutionStatus.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        db.add(execution)

        # 9. Audit event
        audit = RemediationAuditEventModel(
            id=str(uuid.uuid4()),
            incident_id=plan.incident_id,
            investigation_id=plan.investigation_id,
            plan_id=plan_id,
            action_id=action_id,
            execution_id=execution_id,
            event_type=RemediationAuditEventType.APPROVAL_GRANTED.value,
            actor=approval_req.approver_id,
            details={
                "comment": approval_req.comment,
                "plan_version": plan.version,
                "expires_at": approval.expires_at.isoformat(),
            },
            created_at=now,
        )
        db.add(audit)

        await db.commit()
        logger.info("Action %s approved by %s; execution record %s created", action_id, approval_req.approver_id, execution_id)

        # 10. Enqueue execution task via Celery
        try:
            from app.workers.remediation_tasks import run_remediation_execution_task
            run_remediation_execution_task.delay(execution_id)
            logger.info("Enqueued Celery execution task for execution %s", execution_id)
        except Exception as exc:
            logger.warning("Could not dispatch Celery execution task immediately (%s); execution is in PENDING state", exc)

        return approval, execution

    async def reject_action(
        self,
        plan_id: str,
        action_id: str,
        rejection_req: RemediationRejectionRequest,
        db: AsyncSession,
    ) -> RemediationApprovalModel:
        """Reject a proposed action and mark plan and action as REJECTED."""
        now = datetime.now(UTC)
        plan = await self.get_plan(plan_id, db)
        action = next((a for a in plan.actions if a.id == action_id), None)
        if not action:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Action '{action_id}' not found on plan '{plan_id}'.",
            )

        if action.status in (ActionStatus.EXECUTING.value, ActionStatus.EXECUTED.value, ActionStatus.VERIFIED.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject action '{action_id}' because it has already started execution (status: {action.status}).",
            )

        approval_id = str(uuid.uuid4())
        approval = RemediationApprovalModel(
            id=approval_id,
            plan_id=plan_id,
            action_id=action_id,
            decision=ApprovalDecision.REJECTED.value,
            approver_id=rejection_req.approver_id,
            comment=rejection_req.comment,
            plan_version=plan.version,
            expires_at=now,
            decided_at=now,
            created_at=now,
        )
        db.add(approval)

        action.status = ActionStatus.REJECTED.value
        plan.status = PlanStatus.REJECTED.value
        plan.updated_at = now

        audit = RemediationAuditEventModel(
            id=str(uuid.uuid4()),
            incident_id=plan.incident_id,
            investigation_id=plan.investigation_id,
            plan_id=plan_id,
            action_id=action_id,
            event_type=RemediationAuditEventType.APPROVAL_REJECTED.value,
            actor=rejection_req.approver_id,
            details={"comment": rejection_req.comment, "plan_version": plan.version},
            created_at=now,
        )
        db.add(audit)

        await db.commit()
        logger.info("Action %s rejected by %s: %s", action_id, rejection_req.approver_id, rejection_req.comment)
        return approval

    async def cancel_plan(
        self, plan_id: str, reason: str, actor: str, db: AsyncSession
    ) -> RemediationPlanModel:
        """Cancel a pending remediation plan."""
        now = datetime.now(UTC)
        plan = await self.get_plan(plan_id, db)
        if plan.status in (PlanStatus.EXECUTING.value, PlanStatus.SUCCEEDED.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel plan with status '{plan.status}'.",
            )

        plan.status = PlanStatus.CANCELLED.value
        plan.updated_at = now
        for act in plan.actions:
            if act.status in (ActionStatus.PROPOSED.value, ActionStatus.PENDING_APPROVAL.value):
                act.status = ActionStatus.CANCELLED.value

        audit = RemediationAuditEventModel(
            id=str(uuid.uuid4()),
            incident_id=plan.incident_id,
            investigation_id=plan.investigation_id,
            plan_id=plan_id,
            event_type=RemediationAuditEventType.PLAN_CANCELLED.value,
            actor=actor,
            details={"reason": reason},
            created_at=now,
        )
        db.add(audit)
        await db.commit()
        return plan

    async def get_execution(self, execution_id: str, db: AsyncSession) -> RemediationExecutionModel:
        """Retrieve full execution record including verification runs."""
        stmt = (
            select(RemediationExecutionModel)
            .options(
                selectinload(RemediationExecutionModel.verification_runs),
                selectinload(RemediationExecutionModel.action),
                selectinload(RemediationExecutionModel.approval),
            )
            .where(RemediationExecutionModel.id == execution_id)
        )
        execution = (await db.execute(stmt)).scalar_one_or_none()
        if not execution:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Remediation execution '{execution_id}' not found.",
            )
        return execution

    async def get_audit_events_for_plan(
        self, plan_id: str, db: AsyncSession
    ) -> list[RemediationAuditEventModel]:
        """Retrieve chronological audit trail of all actions, approvals, and executions for a plan."""
        stmt = (
            select(RemediationAuditEventModel)
            .where(RemediationAuditEventModel.plan_id == plan_id)
            .order_by(RemediationAuditEventModel.created_at.asc())
        )
        return list((await db.execute(stmt)).scalars().all())
