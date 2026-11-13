from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "opspilot",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.detection_tasks",
        "app.workers.demo_tasks",
        "app.workers.investigation_tasks",
        "app.workers.remediation_tasks",
        "app.workers.recovery_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "reconcile-interrupted-jobs": {
            "task": "app.workers.recovery_tasks.run_reconciliation",
            "schedule": 60.0,
        },
        "evaluate-detection-rules-periodic": {
            "task": "app.workers.detection_tasks.run_detection_cycle",
            "schedule": float(settings.INCIDENT_EVALUATION_INTERVAL_SECONDS),
        },
    },
)
