"""Tests for Phase 5 Remediation Planner."""

from unittest.mock import AsyncMock

import pytest

from app.investigation.state import ConfidenceLevel
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.remediation.models import RiskLevel
from app.remediation.planner import RemediationPlanner, _deterministic_heuristic_planner
from app.remediation.schemas import ProposedActionSchema, RemediationPlanProposal


@pytest.fixture
def sample_incident():
    return IncidentModel(
        id="inc-test-1",
        title="High payment decline spike",
        service_name="payment-service",
        severity=IncidentSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
        fingerprint="fp-payment-failure",
    )


@pytest.fixture
def sample_rca_report():
    return {
        "executive_summary": "Payment failure spike caused by downstream decline simulation",
        "confidence_level": "HIGH",
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Downstream payment gateway decline",
                "explanation": "Simulated payment failure fault triggered 80% decline rate",
                "confidence": "HIGH",
                "confidence_score": 0.85,
                "supporting_evidence_ids": ["EV-METRIC-01", "EV-TRACE-01"],
                "affected_components": ["payment-service"],
            }
        ],
        "evidence_sources": [
            {"evidence_id": "EV-METRIC-01", "source_type": "METRIC", "service_name": "payment-service"},
            {"evidence_id": "EV-TRACE-01", "source_type": "TRACE", "service_name": "payment-service"},
        ],
    }


@pytest.mark.asyncio
async def test_heuristic_planner_payment_failure(sample_incident, sample_rca_report):
    proposal = _deterministic_heuristic_planner(
        sample_incident,
        sample_rca_report,
        sample_rca_report["evidence_sources"],
    )

    assert len(proposal.proposed_actions) == 1
    act = proposal.proposed_actions[0]
    assert act.action_type == "disable_fault_injection"
    assert act.target_service == "payment-service"
    assert act.parameters == {"fault_type": "payment_failure_spike"}
    assert act.risk_level == RiskLevel.LOW
    assert "EV-METRIC-01" in proposal.supporting_evidence_ids


@pytest.mark.asyncio
async def test_heuristic_planner_db_pool_exhaustion():
    incident = IncidentModel(
        id="inc-db-1",
        title="DB connection pool starvation",
        service_name="order-service",
        severity=IncidentSeverity.HIGH,
        status=IncidentStatus.OPEN,
        fingerprint="fp-db-pool",
    )
    report = {
        "executive_summary": "Database connection pool saturated",
        "confidence_level": "HIGH",
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Database connection pool exhaustion",
                "explanation": "Active pool connections held for prolonged duration",
                "confidence": "HIGH",
                "supporting_evidence_ids": ["EV-METRIC-DB"],
            }
        ],
        "evidence_sources": [{"evidence_id": "EV-METRIC-DB", "source_type": "METRIC"}],
    }

    proposal = _deterministic_heuristic_planner(incident, report, report["evidence_sources"])
    assert len(proposal.proposed_actions) == 1
    act = proposal.proposed_actions[0]
    assert act.action_type == "reset_db_connection_pool"
    assert act.target_service == "order-service"


@pytest.mark.asyncio
async def test_planner_converts_invalid_actions_to_manual_recommendations(sample_incident, sample_rca_report):
    mock_llm = AsyncMock()
    # Simulate LLM proposing an unregistered action (e.g. bash script execution)
    mock_llm.generate_structured.return_value = RemediationPlanProposal(
        summary="Plan with hallucinated command",
        root_cause_hypothesis=sample_rca_report["hypotheses"][0],
        supporting_evidence_ids=["EV-METRIC-01"],
        contradicting_evidence_ids=[],
        proposed_actions=[
            ProposedActionSchema(
                action_type="execute_shell_command",  # Unregistered!
                target_service="payment-service",
                environment="demo",
                parameters={"command": "systemctl restart payment-service"},
                rationale="Restart daemon",
                expected_outcome="Fixed",
            ),
            ProposedActionSchema(
                action_type="disable_fault_injection",  # Registered and valid
                target_service="payment-service",
                environment="demo",
                parameters={"fault_type": "payment_failure_spike"},
                rationale="Disable fault",
                expected_outcome="Fixed",
            ),
        ],
        manual_recommendations=[],
        risk_assessment={"overall_risk": "LOW"},
    )

    planner = RemediationPlanner()
    proposal = await planner.plan(
        incident=sample_incident,
        final_report=sample_rca_report,
        evidence_refs=sample_rca_report["evidence_sources"],
        provider=mock_llm,
    )

    # The unregistered action must have been stripped from proposed_actions and converted to manual_recommendations
    action_types = [a.action_type for a in proposal.proposed_actions]
    assert "execute_shell_command" not in action_types
    assert "disable_fault_injection" in action_types
    assert any("execute_shell_command" in rec for rec in proposal.manual_recommendations)


@pytest.mark.asyncio
async def test_planner_low_confidence_and_contradiction_escalation(sample_incident, sample_rca_report):
    # Set confidence to LOW and is_contested to True
    sample_rca_report["hypotheses"][0]["confidence"] = ConfidenceLevel.LOW.value
    sample_rca_report["hypotheses"][0]["is_contested"] = True
    sample_rca_report["hypotheses"][0]["contradicting_evidence_ids"] = ["EV-METRIC-CONFLICT"]

    planner = RemediationPlanner()
    proposal = await planner.plan(
        incident=sample_incident,
        final_report=sample_rca_report,
        evidence_refs=sample_rca_report["evidence_sources"],
    )

    assert proposal.risk_assessment["overall_risk"] == RiskLevel.HIGH.value
    assert any("Caution:" in rec for rec in proposal.manual_recommendations)
