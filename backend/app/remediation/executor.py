"""Controlled Remediation Execution Engine with distributed locking, safety re-validation, and audit logging."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models.incident import IncidentModel
from app.remediation.distributed_lock import (
    LostLockError,
    RedisUnavailableError,
    RemediationDistributedLock,
)
from app.remediation.models import (
    ActionStatus,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationAuditEventModel,
    RemediationAuditEventType,
    RemediationExecutionModel,
    RemediationPlanModel,
    RemediationVerificationRunModel,
    VerificationStatus,
)
from app.remediation.policy import SafetyPolicyEngine, safety_policy_engine
from app.remediation.registry import ActionRegistry, action_registry

logger = logging.getLogger("opspilot.remediation.executor")


class RemediationExecutor:
    """Safely executes approved remediation actions within controlled boundaries."""

    def __init__(
        self,
        registry: ActionRegistry | None = None,
        policy_engine: SafetyPolicyEngine | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.registry = registry or action_registry
        self.policy = policy_engine or safety_policy_engine
        self.redis_client = redis_client
        self.settings = get_settings()

    async def execute_action(
        self,
        execution_id: str,
        session_factory: Any = None,
        *,
        fail_if_locked: bool = False,
    ) -> dict[str, Any]:
        """Execute an approved remediation action with safety revalidation and locking."""
        factory = session_factory or get_session_factory()

        stmt = (
            select(RemediationExecutionModel)
            .join(RemediationExecutionModel.plan)
            .options(
                selectinload(RemediationExecutionModel.action),
                selectinload(RemediationExecutionModel.plan),
                selectinload(RemediationExecutionModel.approval),
            )
            .where(RemediationExecutionModel.id == execution_id)
        )
        claim_stmt = stmt.with_for_update(of=(RemediationPlanModel, RemediationExecutionModel))
        async with factory() as db:
            execution = (await db.execute(stmt)).scalar_one_or_none()
            if not execution:
                logger.error("Remediation execution '%s' not found", execution_id)
                return {"status": "error", "error": f"Execution {execution_id} not found"}

            action = execution.action
            plan = execution.plan

            # 2. Duplicate Execution Protection
            if execution.status != ExecutionStatus.PENDING.value:
                logger.warning(
                    "Execution '%s' is already in status '%s'. Duplicate execution prohibited.",
                    execution_id,
                    execution.status,
                )
                return {
                    "status": "duplicate_prevented",
                    "execution_status": execution.status,
                    "message": "Action has already executed or is currently in progress",
                }

        # 4. Acquire Distributed Lock for Target Service
        try:
            lock = RemediationDistributedLock(
                service_name=action.target_service,
                ttl_seconds=self.settings.REMEDIATION_EXECUTION_TIMEOUT_SECONDS + 10.0,
                redis_client=self.redis_client,
            )
        except ValueError as err:
            logger.error("Service allowlist rejection for lock: %s", err)
            return {"status": "error", "error": str(err)}

        try:
            locked = await lock.acquire()
        except RedisUnavailableError as exc:
            logger.error(
                "Redis unavailable during lock acquisition for service '%s': %s. Failing closed.",
                action.target_service,
                exc,
            )
            async with factory() as db:
                stmt = select(RemediationExecutionModel).where(RemediationExecutionModel.id == execution_id)
                e_rec = (await db.execute(stmt)).scalar_one_or_none()
                if e_rec and e_rec.status == ExecutionStatus.PENDING.value:
                    e_rec.status = ExecutionStatus.FAILED.value
                    e_rec.completed_at = datetime.now(UTC)
                    e_rec.error_message = f"Redis lock infrastructure failure: {exc}"
                    audit = RemediationAuditEventModel(
                        incident_id=plan.incident_id,
                        investigation_id=plan.investigation_id,
                        plan_id=plan.id,
                        action_id=action.id,
                        execution_id=execution.id,
                        event_type=RemediationAuditEventType.EXECUTION_FAILED.value,
                        actor="executor",
                        details={"error": str(exc), "stage": "lock_acquisition"},
                    )
                    db.add(audit)
                    await db.commit()

            return {
                "status": "redis_unavailable",
                "error": f"Redis infrastructure failure during lock acquisition: {exc}",
                "service": action.target_service,
            }

        if not locked:
            message = f"Service '{action.target_service}' is currently locked by another operation"
            if fail_if_locked:
                async with factory() as db:
                    pending = (await db.execute(claim_stmt)).scalar_one()
                    if pending.status == ExecutionStatus.PENDING.value:
                        pending.status = ExecutionStatus.FAILED.value
                        pending.completed_at = datetime.now(UTC)
                        pending.error_message = message
                        if pending.plan.status == PlanStatus.APPROVED.value:
                            pending.action.status = ActionStatus.FAILED.value
                            pending.plan.status = PlanStatus.FAILED.value
                        db.add(RemediationAuditEventModel(
                            incident_id=pending.plan.incident_id,
                            plan_id=pending.plan_id,
                            action_id=pending.action_id,
                            execution_id=pending.id,
                            event_type=RemediationAuditEventType.EXECUTION_FAILED.value,
                            actor="executor",
                            details={"stage": "lock_contention", "error": message},
                        ))
                        await db.commit()
            logger.warning(
                "Could not acquire lock for service '%s'; action '%s' is locked due to contention",
                action.target_service,
                action.id,
            )
            return {
                "status": "locked",
                "message": message,
            }

        start_time = time.time()
        start_dt = datetime.now(UTC)

        try:
            # 5. Transition to EXECUTING status
            async with factory() as db:
                execution = (await db.execute(claim_stmt)).scalar_one()
                if execution.status != ExecutionStatus.PENDING.value:
                    return {"status": "duplicate_prevented", "execution_status": execution.status}

                action = execution.action
                plan = execution.plan
                if action.target_service != lock.service_name:
                    execution.status = ExecutionStatus.FAILED.value
                    execution.completed_at = start_dt
                    execution.error_message = "The target service changed before execution."
                    await db.commit()
                    return {"status": "policy_rejected", "reasons": [execution.error_message]}
                if (
                    not self.settings.REMEDIATION_ENABLED
                    or action.status != ActionStatus.APPROVED.value
                    or plan.status != PlanStatus.APPROVED.value
                ):
                    execution.status = ExecutionStatus.FAILED.value
                    execution.completed_at = start_dt
                    execution.error_message = "Remediation is disabled or the action is no longer approved."
                    await db.commit()
                    return {"status": "policy_rejected", "reasons": [execution.error_message]}

                # 3. Pre-flight Safety Policy Re-evaluation
                readiness = self.policy.evaluate_execution_readiness(
                    action=action,
                    plan=plan,
                    approval=execution.approval,
                    incident=await db.get(IncidentModel, plan.incident_id),
                    has_already_executed=False,
                )
                if not readiness.is_allowed:
                    logger.error(
                        "Safety Policy blocked execution '%s': %s",
                        execution_id,
                        readiness.failure_reasons,
                    )
                    execution.status = ExecutionStatus.FAILED.value
                    execution.completed_at = start_dt
                    execution.error_message = f"Safety Policy rejection: {'; '.join(readiness.failure_reasons)}"
                    action.status = ActionStatus.FAILED.value
                    plan.status = PlanStatus.FAILED.value

                    audit = RemediationAuditEventModel(
                        incident_id=plan.incident_id,
                        investigation_id=plan.investigation_id,
                        plan_id=plan.id,
                        action_id=action.id,
                        execution_id=execution.id,
                        event_type=RemediationAuditEventType.EXECUTION_FAILED.value,
                        actor="executor",
                        details={"reasons": readiness.failure_reasons},
                    )
                    db.add(audit)
                    await db.commit()
                    return {"status": "policy_rejected", "reasons": readiness.failure_reasons}

                execution.status = ExecutionStatus.EXECUTING.value
                execution.started_at = start_dt
                action.status = ActionStatus.EXECUTING.value
                plan.status = PlanStatus.EXECUTING.value

                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.EXECUTION_STARTED.value,
                    actor="executor",
                    details={"target_service": action.target_service, "action_type": action.action_type},
                )
                db.add(audit)
                await db.commit()

            # 6. Execute registered action handler
            action_def = self.registry.get(action.action_type)
            if not action_def:
                raise ValueError(f"Action type '{action.action_type}' is not registered.")

            # Immediate lease ownership check before executing outbound network side effects
            if not await lock.validate_ownership():
                logger.error(
                    "Lock ownership lost for service '%s' (execution %s) prior to handler dispatch!",
                    action.target_service,
                    execution_id,
                )
                raise LostLockError(
                    f"Lost lock lease ownership for service '{action.target_service}' prior to handler dispatch"
                )

            handler_timeout = min(action_def.timeout_seconds, self.settings.REMEDIATION_EXECUTION_TIMEOUT_SECONDS)
            logger.info(
                "Executing handler for action '%s' on '%s' (timeout: %.1fs)",
                action.action_type,
                action.target_service,
                handler_timeout,
            )

            async with asyncio.timeout(handler_timeout):
                execution_output = await action_def.handler(
                    action.target_service,
                    action.parameters,
                    handler_timeout,
                )

            # 7. Execution Succeeded -> Transition to VERIFYING
            duration_ms = (time.time() - start_time) * 1000.0
            completed_dt = datetime.now(UTC)

            async with factory() as db:
                stmt = select(RemediationExecutionModel).where(RemediationExecutionModel.id == execution_id)
                e_record = (await db.execute(stmt)).scalar_one()
                e_record.status = ExecutionStatus.VERIFYING.value
                e_record.completed_at = completed_dt
                e_record.duration_ms = duration_ms
                e_record.execution_output = execution_output

                act_stmt = select(RemediationActionModel).where(RemediationActionModel.id == action.id)
                act_record = (await db.execute(act_stmt)).scalar_one()
                act_record.status = ActionStatus.EXECUTED.value

                plan_stmt = select(RemediationPlanModel).where(RemediationPlanModel.id == plan.id)
                plan_record = (await db.execute(plan_stmt)).scalar_one()
                plan_record.status = PlanStatus.VERIFYING.value

                # Create verification run record
                verif_run = RemediationVerificationRunModel(
                    execution_id=execution_id,
                    status=VerificationStatus.VERIFYING.value,
                    started_at=completed_dt,
                    required_healthy_count=self.settings.REMEDIATION_CONSECUTIVE_HEALTHY_REQUIRED,
                    consecutive_healthy_count=0,
                    observations=[],
                )
                db.add(verif_run)

                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.EXECUTION_SUCCEEDED.value,
                    actor="executor",
                    details={"duration_ms": duration_ms, "output": execution_output},
                )
                db.add(audit)
                await db.commit()

            logger.info("Execution %s completed successfully in %.1fms; transitioned to VERIFYING", execution_id, duration_ms)

            # 8. Trigger Celery Verification Task
            try:
                from app.workers.remediation_tasks import run_remediation_verification_task
                run_remediation_verification_task.delay(execution_id)
                logger.info("Dispatched Celery verification task for execution %s", execution_id)
            except Exception as exc:
                logger.warning("Could not dispatch Celery verification task immediately: %s", exc)

            return {
                "status": "success",
                "execution_id": execution_id,
                "duration_ms": duration_ms,
                "output": execution_output,
            }

        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000.0
            unknown_outcome = isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.TransportError))
            error_message = (
                "The service outcome is unknown after a transport failure. Check the target before trying again."
                if unknown_outcome else str(exc)
            )
            logger.error("Execution %s failed: %s", execution_id, exc, exc_info=True)

            async with factory() as db:
                stmt = select(RemediationExecutionModel).where(RemediationExecutionModel.id == execution_id)
                e_record = (await db.execute(stmt)).scalar_one()
                e_record.status = ExecutionStatus.FAILED.value
                e_record.completed_at = datetime.now(UTC)
                e_record.duration_ms = duration_ms
                e_record.error_message = error_message

                act_stmt = select(RemediationActionModel).where(RemediationActionModel.id == action.id)
                act_record = (await db.execute(act_stmt)).scalar_one()
                act_record.status = ActionStatus.FAILED.value

                plan_stmt = select(RemediationPlanModel).where(RemediationPlanModel.id == plan.id)
                plan_record = (await db.execute(plan_stmt)).scalar_one()
                plan_record.status = PlanStatus.FAILED.value

                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.EXECUTION_FAILED.value,
                    actor="executor",
                    details={"error": error_message, "duration_ms": duration_ms, "outcome": "unknown" if unknown_outcome else "failed"},
                )
                db.add(audit)
                await db.commit()

            return {"status": "failed", "error": error_message, "duration_ms": duration_ms}

        finally:
            # 9. Always safely release distributed lock via atomic Lua
            try:
                await lock.release()
            except Exception as rel_err:
                logger.warning("Error releasing lock for service '%s': %s", action.target_service, rel_err)


# Global singleton executor
remediation_executor = RemediationExecutor()
