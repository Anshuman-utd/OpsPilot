"""Evaluation ground-truth dataset for autonomous SRE investigation benchmarking."""

from dataclasses import dataclass, field


@dataclass
class EvaluationGroundTruth:
    scenario_id: str
    scenario_name: str
    description: str
    target_service: str
    incident_type: str
    expected_primary_cause_keyword: str
    expected_confidence: str  # HIGH, MEDIUM, LOW
    required_evidence_sources: list[str] = field(default_factory=list)  # METRIC, LOG, TRACE, INCIDENT_HISTORY
    forbidden_claims: list[str] = field(default_factory=list)


EVALUATION_DATASET: list[EvaluationGroundTruth] = [
    EvaluationGroundTruth(
        scenario_id="scenario-a-payment-cascade",
        scenario_name="Scenario A: Payment Failure Cascade",
        description="Downstream payment service declination causes cascading timeouts in order-service.",
        target_service="order-service",
        incident_type="high_http_5xx_rate",
        expected_primary_cause_keyword="payment",
        expected_confidence="HIGH",
        required_evidence_sources=["METRIC", "TRACE"],
        forbidden_claims=["database corruption", "cpu hardware failure"],
    ),
    EvaluationGroundTruth(
        scenario_id="scenario-b-db-saturation",
        scenario_name="Scenario B: Database Connection Pool Exhaustion",
        description="Order service active checked-out connections reach capacity, causing request thread stalls.",
        target_service="order-service",
        incident_type="high_p95_latency",
        expected_primary_cause_keyword="database",
        expected_confidence="HIGH",
        required_evidence_sources=["METRIC", "LOG"],
        forbidden_claims=["network interface card physical fault"],
    ),
    EvaluationGroundTruth(
        scenario_id="scenario-c-partial-telemetry-failure",
        scenario_name="Scenario C: Partial Telemetry Outage",
        description="Loki or Tempo connection drops while Prometheus reports breach; agent must degrade gracefully.",
        target_service="payment-service",
        incident_type="payment_failure",
        expected_primary_cause_keyword="payment",
        expected_confidence="LOW",
        required_evidence_sources=["METRIC"],
        forbidden_claims=[],
    ),
    EvaluationGroundTruth(
        scenario_id="scenario-d-missing-llm-key",
        scenario_name="Scenario D: Missing LLM API Key (Offline Fallback)",
        description="No Groq/LLM key supplied; deterministic heuristic engine synthesizes structured RCA.",
        target_service="order-service",
        incident_type="high_http_5xx_rate",
        expected_primary_cause_keyword="error",
        expected_confidence="MEDIUM",
        required_evidence_sources=["METRIC"],
        forbidden_claims=[],
    ),
]
