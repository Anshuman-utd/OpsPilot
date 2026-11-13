"""Celery background tasks for asynchronous controlled remediation execution and verification."""

import logging
from typing import Any

from redis.exceptions import LockNotOwnedError

from app.core.celery_app import celery_app
from app.core.redis import get_redis_client
from app.remediation.executor import remediation_executor
from app.remediation.verifier import recovery_verifier
from app.workers.async_runtime import run_async_task

logger = logging.getLogger("opspilot.workers.remediation")


@celery_app.task(
    name="app.workers.remediation_tasks.run_remediation_execution_task",
    bind=True,
    max_retries=8,
    time_limit=90,
    default_retry_delay=5,
)
def run_remediation_execution_task(self: Any, execution_id: str) -> dict[str, Any]:
    """Execute approved remediation action in Celery background worker process."""
    logger.info("Celery task started: Executing remediation %s", execution_id)
    try:
        result = run_async_task(remediation_executor.execute_action(
            execution_id, fail_if_locked=self.request.retries >= self.max_retries
        ))
    except Exception as exc:
        logger.error("Celery remediation execution task failed for %s: %s", execution_id, exc, exc_info=True)
        # Never blindly retry potentially non-idempotent actions
        return {"status": "error", "execution_id": execution_id, "error": str(exc)}

    if result["status"] == "locked" and self.request.retries < self.max_retries:
        raise self.retry()
    return result


@celery_app.task(
    name="app.workers.remediation_tasks.run_remediation_verification_task",
    bind=True,
    max_retries=1,
    time_limit=220,
    default_retry_delay=5,
)
def run_remediation_verification_task(self: Any, execution_id: str) -> dict[str, Any]:
    """Execute recovery verification loop in Celery background worker process."""
    logger.info("Celery task started: Recovery verification for execution %s", execution_id)
    try:
        final_status = run_async_task(verify_once(execution_id))
        if final_status is None:
            return {"status": "skipped", "execution_id": execution_id}
        return {"status": "completed", "execution_id": execution_id, "verification_status": str(final_status)}
    except Exception as exc:
        logger.error("Celery recovery verification task failed for %s: %s", execution_id, exc, exc_info=True)
        return {"status": "error", "execution_id": execution_id, "error": str(exc)}



async def verify_once(execution_id: str):
    lock = get_redis_client().lock(f"opspilot:verification:{execution_id}", timeout=230, blocking=False)
    if not await lock.acquire():
        return None
    try:
        return await recovery_verifier.verify_recovery(execution_id)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            logger.warning("Verification lease expired for %s", execution_id)
