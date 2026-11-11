"""Evaluation benchmarks across Scenarios A, B, C, and D."""


from app.investigation.evaluation.dataset import EVALUATION_DATASET
from app.investigation.evaluation.evaluator import InvestigationEvaluator


def test_evaluation_scenario_a_payment_cascade():
    ground_truth = next(s for s in EVALUATION_DATASET if s.scenario_id == "scenario-a-payment-cascade")

    mock_report = {
        "executive_summary": "Downstream payment-service gateway declination caused order-service timeout cascade.",
        "incident_description": "Elevated order failures",
        "observed_facts": [
            "payment-service failure rate reached 28%",
            "order-service HTTP 500 error rate reached 22%",
        ],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Downstream payment gateway declination",
                "explanation": "Payment gateway errors caused order processing to stall and timeout.",
                "confidence": "HIGH",
                "confidence_score": 0.85,
                "supporting_evidence_ids": ["EV-METRIC-01", "EV-TRACE-01"],
                "affected_components": ["order-service", "payment-service"],
            }
        ],
        "confidence_level": "HIGH",
        "affected_services": ["order-service", "payment-service"],
        "evidence_sources": [
            {
                "evidence_id": "EV-METRIC-01",
                "source_type": "METRIC",
                "source_reference": "payments_processed_total",
                "service_name": "payment-service",
                "observation": "Payment failure spike",
            },
            {
                "evidence_id": "EV-TRACE-01",
                "source_type": "TRACE",
                "source_reference": "trace_id:4bf92f3577b34da6a3ce929d0e0e4736",
                "service_name": "payment-service",
                "observation": "Failed downstream HTTP 503 span",
            },
        ],
    }

    score = InvestigationEvaluator.evaluate(mock_report, ground_truth)
    assert score.passed is True
    assert score.accuracy_score >= 0.80
    assert score.checks["zero_evidence_hallucinations"] is True
    assert score.checks["primary_cause_match"] is True


def test_evaluation_scenario_b_db_saturation():
    ground_truth = next(s for s in EVALUATION_DATASET if s.scenario_id == "scenario-b-db-saturation")

    mock_report = {
        "executive_summary": "Database connection pool exhaustion on order-service caused connection starvation.",
        "incident_description": "High latency on order-service",
        "observed_facts": ["Database connection pool saturation reached 92%."],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Database connection pool exhaustion on order-service",
                "explanation": "Checked out DB connections exceeded limits, stalling threads.",
                "confidence": "HIGH",
                "confidence_score": 0.85,
                "supporting_evidence_ids": ["EV-METRIC-01", "EV-LOG-01"],
                "affected_components": ["order-service", "postgresql"],
            }
        ],
        "confidence_level": "HIGH",
        "affected_services": ["order-service"],
        "evidence_sources": [
            {
                "evidence_id": "EV-METRIC-01",
                "source_type": "METRIC",
                "source_reference": "db_pool_checked_out",
                "service_name": "order-service",
                "observation": "DB pool saturation",
            },
            {
                "evidence_id": "EV-LOG-01",
                "source_type": "LOG",
                "source_reference": '{service="order-service"}',
                "service_name": "order-service",
                "observation": "Connection pool timeout error log",
            },
        ],
    }

    score = InvestigationEvaluator.evaluate(mock_report, ground_truth)
    assert score.passed is True
    assert score.accuracy_score >= 0.80
    assert score.checks["primary_cause_match"] is True


def test_evaluation_scenario_c_telemetry_outage():
    ground_truth = next(s for s in EVALUATION_DATASET if s.scenario_id == "scenario-c-partial-telemetry-failure")

    mock_report = {
        "executive_summary": "Elevated payment failure rate observed on payment-service. Telemetry logs unavailable.",
        "incident_description": "Payment failures",
        "observed_facts": ["payment-service failure rate reached 25%"],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Payment gateway failure",
                "explanation": "Payment transaction declines observed; log telemetry offline.",
                "confidence": "LOW",
                "confidence_score": 0.40,
                "supporting_evidence_ids": ["EV-METRIC-01"],
                "affected_components": ["payment-service"],
            }
        ],
        "confidence_level": "LOW",
        "affected_services": ["payment-service"],
        "evidence_sources": [
            {
                "evidence_id": "EV-METRIC-01",
                "source_type": "METRIC",
                "source_reference": "payments_processed_total",
                "service_name": "payment-service",
                "observation": "Payment failure spike",
            }
        ],
    }

    score = InvestigationEvaluator.evaluate(mock_report, ground_truth)
    assert score.passed is True
    assert score.checks["confidence_calibrated"] is True
    assert score.checks["zero_evidence_hallucinations"] is True


def test_evaluation_scenario_d_missing_llm_key():
    ground_truth = next(s for s in EVALUATION_DATASET if s.scenario_id == "scenario-d-missing-llm-key")

    mock_report = {
        "executive_summary": "Elevated error rate observed on order-service. Deterministic fallback activated.",
        "incident_description": "HTTP 5xx rate breach",
        "observed_facts": ["order-service HTTP error rate peaked at 0.18"],
        "hypotheses": [
            {
                "hypothesis_id": "HYP-01",
                "title": "Elevated HTTP 5xx error rate on order-service",
                "explanation": "Service experienced elevated error response rates.",
                "confidence": "MEDIUM",
                "confidence_score": 0.65,
                "supporting_evidence_ids": ["EV-METRIC-01"],
                "affected_components": ["order-service"],
            }
        ],
        "confidence_level": "MEDIUM",
        "affected_services": ["order-service"],
        "evidence_sources": [
            {
                "evidence_id": "EV-METRIC-01",
                "source_type": "METRIC",
                "source_reference": "http_requests_total",
                "service_name": "order-service",
                "observation": "High error rate",
            }
        ],
    }

    score = InvestigationEvaluator.evaluate(mock_report, ground_truth)
    assert score.passed is True
    assert score.accuracy_score >= 0.80
