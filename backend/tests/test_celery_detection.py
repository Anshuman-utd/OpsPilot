from unittest.mock import AsyncMock, patch

from app.schemas.detection import DetectionRunResponse
from app.workers.detection_tasks import run_detection_cycle


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
