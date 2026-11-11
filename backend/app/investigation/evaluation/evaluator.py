"""Investigation evaluation harness for benchmarking hypothesis accuracy and evidence fidelity."""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.investigation.evaluation.dataset import EvaluationGroundTruth

logger = logging.getLogger("opspilot.investigation.evaluation")


@dataclass
class EvaluationScore:
    scenario_id: str
    scenario_name: str
    passed: bool
    accuracy_score: float  # [0.0 - 1.0]
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class InvestigationEvaluator:
    """Evaluates generated FinalRCAReport against ground truth benchmarks."""

    @staticmethod
    def evaluate(report: dict[str, Any], ground_truth: EvaluationGroundTruth) -> EvaluationScore:
        checks: dict[str, bool] = {}
        notes: list[str] = []

        hypotheses = report.get("hypotheses", [])
        evidence_sources = report.get("evidence_sources", [])
        confidence = report.get("confidence_level", "LOW")

        # 1. Primary Hypothesis Keyword Match
        primary_title = hypotheses[0].get("title", "").lower() if hypotheses else ""
        primary_expl = hypotheses[0].get("explanation", "").lower() if hypotheses else ""
        kw = ground_truth.expected_primary_cause_keyword.lower()

        matched_keyword = kw in primary_title or kw in primary_expl
        checks["primary_cause_match"] = matched_keyword
        if not matched_keyword:
            notes.append(f"Expected primary cause keyword '{kw}' not found in top hypothesis: '{primary_title}'")

        # 2. Confidence Calibration Check
        if ground_truth.expected_confidence == "HIGH":
            # Report confidence should be HIGH or MEDIUM
            checks["confidence_calibrated"] = confidence in ["HIGH", "MEDIUM"]
        elif ground_truth.expected_confidence == "LOW":
            # For partial failure, confidence should NOT be HIGH
            checks["confidence_calibrated"] = confidence in ["LOW", "MEDIUM"]
        else:
            checks["confidence_calibrated"] = True

        if not checks["confidence_calibrated"]:
            notes.append(f"Confidence '{confidence}' does not align with expected '{ground_truth.expected_confidence}'")

        # 3. Evidence Fidelity Check (Zero Hallucinated Evidence IDs)
        all_valid_ids = {e.get("evidence_id") for e in evidence_sources if e.get("evidence_id")}
        hallucinated_ids = []
        for h in hypotheses:
            for cid in h.get("supporting_evidence_ids", []):
                if cid not in all_valid_ids:
                    hallucinated_ids.append(cid)

        checks["zero_evidence_hallucinations"] = len(hallucinated_ids) == 0
        if hallucinated_ids:
            notes.append(f"Found hallucinated evidence IDs cited in hypotheses: {hallucinated_ids}")

        # 4. Required Evidence Sources Coverage
        present_sources = {e.get("source_type") for e in evidence_sources}
        missing_sources = [req for req in ground_truth.required_evidence_sources if req not in present_sources]
        checks["required_sources_present"] = len(missing_sources) == 0
        if missing_sources:
            notes.append(f"Missing required evidence sources: {missing_sources}")

        # 5. Forbidden Claims Absence
        full_text = f"{report.get('executive_summary', '')} {primary_title} {primary_expl}".lower()
        violated_claims = [f_claim for f_claim in ground_truth.forbidden_claims if f_claim.lower() in full_text]
        checks["no_forbidden_claims"] = len(violated_claims) == 0
        if violated_claims:
            notes.append(f"Report asserted forbidden/unsupported claims: {violated_claims}")

        # Calculate composite score
        total_checks = len(checks)
        passed_checks = sum(1 for v in checks.values() if v)
        score = passed_checks / total_checks if total_checks > 0 else 0.0

        all_passed = score >= 0.75 and checks["zero_evidence_hallucinations"]

        return EvaluationScore(
            scenario_id=ground_truth.scenario_id,
            scenario_name=ground_truth.scenario_name,
            passed=all_passed,
            accuracy_score=round(score, 2),
            checks=checks,
            notes=notes,
        )

