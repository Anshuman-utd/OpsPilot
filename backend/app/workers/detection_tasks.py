import logging
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.redis import get_redis_client
from app.services.detection.engine import DetectionEngine
from app.workers.async_runtime import run_async_task

logger = logging.getLogger("opspilot.workers.detection")

LOCK_KEY = "opspilot:lock:detection_cycle"
LOCK_TTL_SECONDS = 60
RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


async def _execute_detection_pass() -> dict:
    """Async execution helper for Celery detection task with distributed lock safety."""
    redis_client = get_redis_client()
    token = uuid4().hex
    if not await redis_client.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS):
        logger.info("Skipping detection cycle because another worker holds the lock.")
        return {"status": "skipped", "reason": "lock_held"}

    try:
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
    finally:
        try:
            await redis_client.eval(RELEASE_LOCK, 1, LOCK_KEY, token)
        except RedisError:
            logger.warning("Could not release detection lock. It will expire.", exc_info=True)


@celery_app.task(name="app.workers.detection_tasks.run_detection_cycle", bind=True, max_retries=3)
def run_detection_cycle(self) -> dict:
    """Celery periodic task to evaluate all deterministic detection rules against Prometheus."""
    settings = get_settings()
    if not settings.INCIDENT_DETECTION_ENABLED:
        logger.info("Incident detection engine is disabled by configuration. Skipping cycle.")
        return {"status": "disabled"}

    logger.info("Starting scheduled incident detection cycle...")
    try:
        return run_async_task(_execute_detection_pass())
    except Exception as exc:
        logger.exception("Detection cycle encountered an error: %s", exc)
        raise self.retry(exc=exc, countdown=10) from exc
