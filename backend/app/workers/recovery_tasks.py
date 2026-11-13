"""Reconcile work left behind when dispatch or a worker is interrupted."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import get_session_factory
from app.investigation.models import InvestigationModel, InvestigationStatus
from app.remediation.models import (
    ActionStatus,
    ExecutionStatus,
    PlanStatus,
    RemediationAuditEventModel,
    RemediationAuditEventType,
    RemediationExecutionModel,
    RemediationPlanModel,
)
from app.workers.async_runtime import run_async_task

logger = logging.getLogger(__name__)

QUEUE_RETRY_DELAY = timedelta(minutes=2)
# Longer than the corresponding worker's hard time limit.
INVESTIGATION_STALE = timedelta(minutes=6)
EXECUTION_STALE = timedelta(minutes=3)


async def reconcile_jobs() -> dict[str, int]:
    from app.workers.investigation_tasks import run_investigation_task
    from app.workers.remediation_tasks import (
        run_remediation_execution_task,
        run_remediation_verification_task,
    )

    now = datetime.now(UTC)
    queued = []
    interrupted = 0
    async with get_session_factory()() as db:
        investigations = await db.scalars(
            select(InvestigationModel)
            .where(
                (
                    (InvestigationModel.status == InvestigationStatus.PENDING.value)
                    & (InvestigationModel.updated_at < now - QUEUE_RETRY_DELAY)
                )
                | (
                    (InvestigationModel.status == InvestigationStatus.RUNNING.value)
                    & (InvestigationModel.updated_at < now - INVESTIGATION_STALE)
                )
            )
            .order_by(InvestigationModel.updated_at)
            .limit(25)
            .with_for_update(skip_locked=True)
        )
        for investigation in investigations:
            if investigation.status == InvestigationStatus.PENDING:
                queued.append((run_investigation_task, investigation.id))
            else:
                investigation.status = InvestigationStatus.FAILED
                investigation.error_message = "The worker stopped before finishing. Retry the investigation."
                investigation.completed_at = now
                interrupted += 1
            investigation.updated_at = now

        executions = await db.scalars(
            select(RemediationExecutionModel)
            .join(RemediationExecutionModel.plan)
            .where(
                (
                    RemediationExecutionModel.status.in_([ExecutionStatus.PENDING, ExecutionStatus.VERIFYING])
                    & (RemediationExecutionModel.updated_at < now - QUEUE_RETRY_DELAY)
                )
                | (
                    (RemediationExecutionModel.status == ExecutionStatus.EXECUTING)
                    & (RemediationExecutionModel.updated_at < now - EXECUTION_STALE)
                )
            )
            .order_by(RemediationExecutionModel.updated_at)
            .limit(25)
            .with_for_update(of=(RemediationPlanModel, RemediationExecutionModel), skip_locked=True)
        )
        for execution in executions:
            if execution.status == ExecutionStatus.PENDING:
                queued.append((run_remediation_execution_task, execution.id))
            elif execution.status == ExecutionStatus.VERIFYING:
                queued.append((run_remediation_verification_task, execution.id))
            else:
                await db.refresh(execution, ["plan", "action"])
                execution.status = ExecutionStatus.FAILED
                execution.completed_at = now
                execution.error_message = "Worker interrupted during dispatch. Inspect the target before trying again."
                execution.plan.status = PlanStatus.FAILED
                execution.action.status = ActionStatus.FAILED
                db.add(RemediationAuditEventModel(
                    incident_id=execution.plan.incident_id,
                    plan_id=execution.plan_id,
                    action_id=execution.action_id,
                    execution_id=execution.id,
                    actor="reconciler",
                    event_type=RemediationAuditEventType.EXECUTION_FAILED,
                    details={"reason": "worker_interrupted", "outcome": "unknown"},
                ))
                interrupted += 1
            execution.updated_at = now
        await db.commit()

    dispatched = 0
    for task, job_id in queued:
        try:
            task.apply_async(args=[job_id], retry=False)
            dispatched += 1
        except Exception:
            logger.warning("Could not requeue %s. It will be checked again.", job_id, exc_info=True)
    return {"dispatched": dispatched, "interrupted": interrupted}


@celery_app.task(name="app.workers.recovery_tasks.run_reconciliation", time_limit=60)
def run_reconciliation() -> dict[str, int]:
    return run_async_task(reconcile_jobs())
