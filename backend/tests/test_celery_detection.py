from unittest.mock import AsyncMock, patch

from app.core.celery_app import celery_app
from app.schemas.detection import DetectionRunResponse
from app.workers.detection_tasks import run_detection_cycle
from tests.fake_redis import FakeRedis


class TestCeleryDetectionTask:
    """Verifies that the Celery detection task executes correctly and handles disabled state."""

    def test_run_detection_cycle_disabled(self):
        with patch("app.workers.detection_tasks.get_settings") as mock_settings:
            mock_settings.return_value.INCIDENT_DETECTION_ENABLED = False
            result = run_detection_cycle()
            assert result == {"status": "disabled"}

    def test_run_detection_cycle_execution(self):
        mock_res = DetectionRunResponse(
            status="success",
            rules_evaluated=6,
            breaches_detected=1,
            incidents_created=1,
            incidents_updated=0,
            incidents_resolved=0,
            evaluation_duration_ms=45.2,
        )

        with (
            patch("app.workers.detection_tasks.get_settings") as mock_settings,
            patch("app.workers.detection_tasks.get_redis_client", return_value=FakeRedis()),
            patch(
                "app.services.detection.engine.DetectionEngine.evaluate_all",
                new_callable=AsyncMock,
                return_value=mock_res,
            ),
        ):
            mock_settings.return_value.INCIDENT_DETECTION_ENABLED = True
            result = run_detection_cycle()
            assert result["status"] == "success"
            assert result["rules_evaluated"] == 6
            assert result["incidents_created"] == 1


def test_cli_app_loads_tasks_and_periodic_schedule():
    celery_app.loader.import_default_modules()
    worker_tasks = {task for task in celery_app.tasks if task.startswith("app.workers.")}
    assert worker_tasks == {
        "app.workers.detection_tasks.run_detection_cycle",
        "app.workers.demo_tasks.run_demo_scenario",
        "app.workers.investigation_tasks.run_investigation_task",
        "app.workers.remediation_tasks.run_remediation_execution_task",
        "app.workers.remediation_tasks.run_remediation_verification_task",
        "app.workers.recovery_tasks.run_reconciliation",
    }
    assert celery_app.conf.beat_schedule["evaluate-detection-rules-periodic"]["task"] in worker_tasks
