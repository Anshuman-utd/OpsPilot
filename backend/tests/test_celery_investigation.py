"""Unit tests for Celery background investigation worker task."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.investigation.models import (
    AgentStatus,
    InvestigationModel,
    InvestigationStatus,
)
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.workers.investigation_tasks import _execute_investigation


@pytest.mark.asyncio
async def test_celery_investigation_execution_success(sqlite_session: AsyncSession):
    now = datetime.now(UTC)

    # 1. Setup Incident and Investigation in database
    inc = IncidentModel(
        id=str(uuid.uuid4()),
        fingerprint="fp-celery-test",
        title="Elevated HTTP 5xx errors on payment-service",
        service_name="payment-service",
        severity=IncidentSeverity.CRITICAL.value,
        status=IncidentStatus.INVESTIGATING.value,
        incident_type="high_http_5xx_rate",
        source="detection_engine",
        triggered_at=now,
        occurrence_count=1,
        evidence={"observed_error_ratio": 0.22},
    )
    sqlite_session.add(inc)
    await sqlite_session.flush()

    inv = InvestigationModel(
        id=str(uuid.uuid4()),
        incident_id=inc.id,
        status=InvestigationStatus.PENDING.value,
        trigger_type="manual",
        model_provider="groq",
        model_name="llama-3.3-70b-versatile",
    )
    sqlite_session.add(inv)
    await sqlite_session.commit()

    mock_final_state = {
        "investigation_status": InvestigationStatus.COMPLETED.value,
        "observed_facts": ["payment-service error rate reached 22%."],
        "evidence_references": [{"evidence_id": "EV-01"}],
        "investigation_plan": {"time_window": {"duration_minutes": 20}},
        "metrics_findings": {"summary": "Metrics analyzed"},
        "logs_findings": {"summary": "Logs analyzed"},
        "traces_findings": {"summary": "Traces analyzed"},
        "historical_findings": {"summary": "History checked"},
        "synthesis_result": {"executive_summary": "Synthesized RCA"},
        "validation_result": {"status": "VALIDATED"},
        "agent_statuses": {
            "incident_analyzer": "COMPLETED",
            "metrics_agent": "COMPLETED",
            "log_agent": "COMPLETED",
            "trace_agent": "COMPLETED",
            "historical_agent": "COMPLETED",
            "aggregator": "COMPLETED",
            "rca_agent": "COMPLETED",
            "validator_agent": "COMPLETED",
        },
        "agent_errors": {},
        "final_report": {
            "executive_summary": "Downstream payment gateway timeout caused order failure cascade.",
            "hypotheses": [],
            "confidence_level": "HIGH",
        },
    }

    class DummySessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return sqlite_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    dummy_factory = DummySessionFactory()

    with patch(
        "app.workers.investigation_tasks.run_investigation_workflow", new_callable=AsyncMock
    ) as m_workflow:
        m_workflow.return_value = mock_final_state

        result = await _execute_investigation(inv.id, session_factory=dummy_factory)

        assert result["status"] == InvestigationStatus.COMPLETED.value
        assert "Downstream payment gateway" in result["summary"]

        # Verify DB persistence
        stmt = (
            select(InvestigationModel)
            .options(selectinload(InvestigationModel.agent_runs))
            .where(InvestigationModel.id == inv.id)
        )
        saved_inv = (await sqlite_session.execute(stmt)).scalar_one()

        assert saved_inv.status == InvestigationStatus.COMPLETED.value
        assert saved_inv.completed_at is not None
        assert saved_inv.final_report is not None
        assert len(saved_inv.agent_runs) == 8
        for run in saved_inv.agent_runs:
            assert run.status == AgentStatus.COMPLETED.value
            assert run.findings is not None


@pytest.mark.asyncio
async def test_celery_investigation_nonexistent_id(sqlite_session: AsyncSession):
    class DummySessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return sqlite_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    result = await _execute_investigation("non-existent-id", session_factory=DummySessionFactory())
    assert result["status"] == "error"
    assert "not found" in result["error"]
