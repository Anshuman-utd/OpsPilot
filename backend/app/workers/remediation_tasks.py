"""Celery background tasks for asynchronous controlled remediation execution and verification."""

import asyncio
import logging
from typing import Any

from app.core.celery_app import celery_app
from app.remediation.executor import remediation_executor
from app.remediation.verifier import recovery_verifier

logger = logging.getLogger("opspilot.workers.remediation")


@celery_app.task(
    name="app.workers.remediation_tasks.run_remediation_execution_task",
    bind=True,
    max_retries=1,
    default_retry_delay=5,
)
def run_remediation_execution_task(self: Any, execution_id: str) -> dict[str, Any]:
    """Execute approved remediation action in Celery background worker process."""
    logger.info("Celery task started: Executing remediation %s", execution_id)
    try:
        result = asyncio.run(remediation_executor.execute_action(execution_id))
        return result
    except Exception as exc:
        logger.error("Celery remediation execution task failed for %s: %s", execution_id, exc, exc_info=True)
        # Never blindly retry potentially non-idempotent actions
        return {"status": "error", "execution_id": execution_id, "error": str(exc)}


@celery_app.task(
    name="app.workers.remediation_tasks.run_remediation_verification_task",
    bind=True,
    max_retries=1,
    default_retry_delay=5,
)
def run_remediation_verification_task(self: Any, execution_id: str) -> dict[str, Any]:
    """Execute recovery verification loop in Celery background worker process."""
    logger.info("Celery task started: Recovery verification for execution %s", execution_id)
    try:
        final_status = asyncio.run(recovery_verifier.verify_recovery(execution_id))
        return {"status": "completed", "execution_id": execution_id, "verification_status": str(final_status)}
    except Exception as exc:
        logger.error("Celery recovery verification task failed for %s: %s", execution_id, exc, exc_info=True)
        return {"status": "error", "execution_id": execution_id, "error": str(exc)}

