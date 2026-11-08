import asyncio
import logging

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.services.detection.engine import DetectionEngine

logger = logging.getLogger("opspilot.workers.detection")


async def _execute_detection_pass() -> dict:
    """Async execution helper for Celery detection task."""
    session_factory = get_session_factory()
    engine = DetectionEngine()

    async with session_factory() as session:
        result = await engine.evaluate_all(session)
        return {
            "status": result.status,
            "rules_evaluated": result.rules_evaluated,
            "breaches_detected": result.breaches_detected,
            "incidents_created": result.incidents_created,
            "incidents_updated": result.incidents_updated,
            "incidents_resolved": result.incidents_resolved,
            "duration_ms": result.evaluation_duration_ms,
        }


@celery_app.task(name="app.workers.detection_tasks.run_detection_cycle", bind=True, max_retries=3)
def run_detection_cycle(self) -> dict:
    """Celery periodic task to evaluate all deterministic detection rules against Prometheus."""
    settings = get_settings()
    if not settings.INCIDENT_DETECTION_ENABLED:
        logger.info("Incident detection engine is disabled by configuration. Skipping cycle.")
        return {"status": "disabled"}

    logger.info("Starting scheduled incident detection cycle...")
    try:
        return asyncio.run(_execute_detection_pass())
    except Exception as exc:
        logger.exception("Detection cycle encountered an error: %s", exc)
        raise self.retry(exc=exc, countdown=10) from exc


# Configure periodic schedule
settings = get_settings()
celery_app.conf.beat_schedule = {
    "evaluate-detection-rules-periodic": {
        "task": "app.workers.detection_tasks.run_detection_cycle",
        "schedule": float(settings.INCIDENT_EVALUATION_INTERVAL_SECONDS),
    },
}
