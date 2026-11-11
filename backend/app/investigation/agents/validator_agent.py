"""Evidence Validator Agent for verifying factual consistency and calibrating hypothesis confidence."""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.investigation.state import (
    ConfidenceLevel,
    InvestigationState,
)

logger = logging.getLogger("opspilot.investigation.agents.validator")


def _parse_ts(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


async def run_validator_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Validate synthesized hypotheses against empirical evidence references.
    Performs deterministic checks for citation referential integrity, service attribution,
    time-window bounds, numerical consistency, and contradictory evidence.
    """
    synth = state.get("synthesis_result", {})
    evidence_refs = state.get("evidence_references", [])
    ev_by_id = {e.get("evidence_id"): e for e in evidence_refs if e.get("evidence_id")}
    valid_ev_ids = set(ev_by_id.keys())
    ev_type_map = {
        eid: ev.get("source_type") for eid, ev in ev_by_id.items() if ev.get("source_type")
    }

    # Investigation window boundaries
    plan = state.get("investigation_plan", {})
    tw = plan.get("time_window", {})
    win_start = _parse_ts(tw.get("start"))
    win_end = _parse_ts(tw.get("end"))

    # Affected service context
    ctx = state.get("incident_context", {})
    affected_service = state.get("affected_service") or ctx.get("service_name") or "unknown"

    raw_hypotheses = synth.get("hypotheses", [])
    validated_hypotheses: list[dict[str, Any]] = []

    for h_data in raw_hypotheses:
        validation_notes: list[str] = list(h_data.get("validation_notes", []))
        assumptions: list[str] = list(h_data.get("assumptions", []))
        is_contested = bool(h_data.get("is_contested", False))

        # 1. Evidence ID existence & referential integrity
        cited_supporting = h_data.get("supporting_evidence_ids", [])
        verified_supporting = [eid for eid in cited_supporting if eid in valid_ev_ids]
        phantom_supporting = [eid for eid in cited_supporting if eid not in valid_ev_ids]
        if phantom_supporting:
            validation_notes.append(
                f"Removed non-existent supporting evidence IDs: {phantom_supporting}"
            )

        cited_contradicting = h_data.get("contradicting_evidence_ids", [])
        verified_contradicting = [eid for eid in cited_contradicting if eid in valid_ev_ids]
        phantom_contradicting = [eid for eid in cited_contradicting if eid not in valid_ev_ids]
        if phantom_contradicting:
            validation_notes.append(
                f"Removed non-existent contradicting evidence IDs: {phantom_contradicting}"
            )

        # 2. Contradicting evidence check
        if verified_contradicting:
            is_contested = True
            validation_notes.append(
                f"Contradicting evidence detected: {verified_contradicting}"
            )
            assumptions.append(
                f"Contested hypothesis: contradicted by observed evidence {verified_contradicting}"
            )

        # 3. Service attribution verification
        affected_components = set(h_data.get("affected_components", []))
        if affected_service and affected_service != "unknown":
            affected_components.add(affected_service)
        # Standard infra subsystems that can be shared
        common_infra = {"postgresql", "database", "redis", "kafka", "payment-gateway", "stripe"}

        attribution_warnings: list[str] = []
        validly_attributed_eids: list[str] = []
        for eid in verified_supporting:
            ev = ev_by_id[eid]
            ev_svc = ev.get("service_name", "")
            if (
                ev_svc
                and ev_svc != "unknown"
                and affected_components
                and ev_svc not in affected_components
                and ev_svc not in common_infra
            ):
                attribution_warnings.append(
                    f"Evidence {eid} service '{ev_svc}' not in affected components {sorted(affected_components)}"
                )
            else:
                validly_attributed_eids.append(eid)

        if attribution_warnings:
            validation_notes.extend(attribution_warnings)

        # 4. Temporal window consistency check
        if win_start is not None and win_end is not None and win_end >= win_start:
            for eid in verified_supporting:
                ev = ev_by_id[eid]
                ev_ts = _parse_ts(ev.get("timestamp"))
                # Allow 60s tolerance for clock skew
                if ev_ts is not None and (ev_ts < (win_start - 60.0) or ev_ts > (win_end + 60.0)):
                    validation_notes.append(
                        f"Evidence {eid} timestamp ({ev_ts:.0f}) falls outside investigation window [{win_start:.0f}, {win_end:.0f}]"
                    )

        # 5. Numerical observation verification against evidence values
        has_numerical_discrepancy = False
        explanation_text = h_data.get("explanation", "")
        quoted_pcts = [float(p) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", explanation_text)]
        for eid in verified_supporting:
            ev = ev_by_id[eid]
            obs_val = ev.get("observed_value")
            if obs_val is not None and isinstance(obs_val, (int, float)):
                norm_obs = obs_val * 100.0 if obs_val <= 1.0 else obs_val
                for pct in quoted_pcts:
                    if abs(pct - norm_obs) > 40.0:
                        has_numerical_discrepancy = True
                        validation_notes.append(
                            f"Numerical discrepancy: hypothesis claims {pct:.1f}% but evidence {eid} recorded {norm_obs:.1f}%"
                        )

        # 6. Distinct telemetry sources calculation
        has_total_attribution_mismatch = bool(
            verified_supporting and not validly_attributed_eids
        )
        if has_total_attribution_mismatch:
            distinct_sources = set()
            validation_notes.append(
                "Service attribution mismatch: all supporting evidence originates from unrelated services"
            )
        else:
            sources_to_count = validly_attributed_eids if validly_attributed_eids else verified_supporting
            distinct_sources = {
                ev_type_map[eid] for eid in sources_to_count if eid in ev_type_map
            }

        # 7. Confidence Calibration (Deterministic Heuristic Scoring)
        original_conf = h_data.get("confidence", ConfidenceLevel.LOW.value)
        if len(distinct_sources) >= 2:
            calibrated_conf = ConfidenceLevel.HIGH.value
            score = 0.85
        elif len(distinct_sources) == 1:
            if original_conf == ConfidenceLevel.HIGH.value:
                calibrated_conf = ConfidenceLevel.MEDIUM.value
            else:
                calibrated_conf = original_conf
            score = 0.65
        else:
            calibrated_conf = ConfidenceLevel.LOW.value
            score = 0.30
            if has_total_attribution_mismatch:
                assumptions.append(
                    "Service attribution failure: evidence belongs to unrelated service"
                )
            else:
                assumptions.append(
                    "Unverified claim: no matching empirical evidence reference found"
                )

        # Apply penalties for discrepancies and contradictions
        if is_contested:
            if calibrated_conf == ConfidenceLevel.HIGH.value:
                calibrated_conf = ConfidenceLevel.MEDIUM.value
            elif calibrated_conf == ConfidenceLevel.MEDIUM.value:
                calibrated_conf = ConfidenceLevel.LOW.value
            score = max(0.20, min(0.50, score - 0.25))

        if has_numerical_discrepancy:
            if calibrated_conf == ConfidenceLevel.HIGH.value:
                calibrated_conf = ConfidenceLevel.MEDIUM.value
            score = max(0.20, score - 0.20)

        h_data["supporting_evidence_ids"] = verified_supporting
        h_data["contradicting_evidence_ids"] = verified_contradicting
        h_data["confidence"] = calibrated_conf
        h_data["confidence_score"] = round(score, 2)
        h_data["is_contested"] = is_contested
        h_data["validation_notes"] = validation_notes
        h_data["assumptions"] = assumptions
        validated_hypotheses.append(h_data)

    # Determine overall report confidence
    overall_confidence = ConfidenceLevel.LOW.value
    if any(h["confidence"] == ConfidenceLevel.HIGH.value for h in validated_hypotheses):
        overall_confidence = ConfidenceLevel.HIGH.value
    elif any(h["confidence"] == ConfidenceLevel.MEDIUM.value for h in validated_hypotheses):
        overall_confidence = ConfidenceLevel.MEDIUM.value

    # Check for telemetry gaps and record limitations honestly
    limitations: list[str] = list(synth.get("investigation_limitations", []))
    limitations.append(
        "Deterministic validation verifies citation referential integrity, source diversity, service attribution, and window bounds. "
        "Semantic causal assertions remain probabilistic hypotheses subject to engineering verification."
    )

    agent_statuses = state.get("agent_statuses", {})
    agent_errors = state.get("agent_errors", {})
    for agent_name, status in agent_statuses.items():
        if status == "FAILED":
            err = agent_errors.get(agent_name, "telemetry retrieval failure")
            limitations.append(f"Telemetry limitation: {agent_name} failed ({err}); data incomplete.")

    if any(h.get("is_contested") for h in validated_hypotheses):
        limitations.append("Contradicting evidence was identified for one or more hypotheses.")

    # Reconstruct final validated report
    final_report = dict(synth)
    final_report["hypotheses"] = validated_hypotheses
    final_report["confidence_level"] = overall_confidence
    final_report["evidence_sources"] = evidence_refs
    final_report["investigation_limitations"] = limitations
    final_report["validated_at"] = datetime.now(UTC).isoformat()

    return {
        "hypotheses": validated_hypotheses,
        "final_report": final_report,
        "validation_result": {
            "status": "VALIDATED",
            "overall_confidence": overall_confidence,
            "hypotheses_validated": len(validated_hypotheses),
            "evidence_count": len(evidence_refs),
            "contested_hypotheses_count": sum(1 for h in validated_hypotheses if h.get("is_contested")),
        },
        "agent_statuses": {"validator_agent": "COMPLETED"},
        "investigation_status": "COMPLETED",
        "investigation_completed_at": datetime.now(UTC).isoformat(),
    }
