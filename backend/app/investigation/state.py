from enum import StrEnum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


def merge_dict(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer function to merge dictionaries in parallel LangGraph execution branches."""
    res = dict(left or {})
    if right:
        res.update(right)
    return res


class EvidenceSourceType(StrEnum):
    """Categorized source of retrieved investigative evidence."""

    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    INCIDENT_HISTORY = "INCIDENT_HISTORY"


class EvidenceStrength(StrEnum):
    """Strength of evidence correlation to incident cause."""

    SUPPORTING = "SUPPORTING"
    CORROBORATING = "CORROBORATING"
    INCONCLUSIVE = "INCONCLUSIVE"
    CONTRADICTING = "CONTRADICTING"


class ConfidenceLevel(StrEnum):
    """Calibrated confidence tier for investigative hypotheses."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvidenceReference(BaseModel):
    """Structured representation of verified telemetry evidence."""

    evidence_id: str = Field(description="Unique identifier e.g. EV-METRIC-01, EV-LOG-01")
    source_type: EvidenceSourceType = Field(
        description="Origin source: METRIC, LOG, TRACE, INCIDENT_HISTORY"
    )
    source_reference: str = Field(
        description="Query expression, span ID, log stream, or incident UUID"
    )
    service_name: str = Field(description="Associated microservice name")
    timestamp: str | None = Field(default=None, description="ISO timestamp of observation")
    observation: str = Field(description="Concise description of the observed anomaly or baseline")
    observed_value: float | str | None = Field(
        default=None, description="Exact recorded metric or status"
    )
    relevant_threshold: float | str | None = Field(
        default=None, description="Configured SLA/SLO threshold"
    )
    relevance: str = Field(description="Why this evidence matters to the incident")
    evidence_strength: EvidenceStrength = Field(default=EvidenceStrength.SUPPORTING)
    supporting_data: dict[str, Any] = Field(
        default_factory=dict, description="Raw query payload / attributes"
    )
    limitations: str | None = Field(default=None, description="Telemetry bounds or unobserved gaps")


class Hypothesis(BaseModel):
    """Evidence-backed root cause hypothesis distinguishing fact from inference."""

    hypothesis_id: str = Field(description="Unique ID e.g. HYP-01")
    title: str = Field(description="Concise title of hypothesis")
    explanation: str = Field(
        description="Detailed technical reasoning supported by observed evidence"
    )
    confidence: ConfidenceLevel = Field(description="Confidence classification: LOW, MEDIUM, HIGH")
    confidence_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Heuristic score [0.0 - 1.0]"
    )
    supporting_evidence_ids: list[str] = Field(
        default_factory=list, description="List of evidence_ids supporting this hypothesis"
    )
    contradicting_evidence_ids: list[str] = Field(
        default_factory=list, description="List of evidence_ids contradicting this hypothesis"
    )
    affected_components: list[str] = Field(
        default_factory=list, description="Services and subsystems affected"
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Assumptions made by the reasoning model"
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Information gaps that would increase confidence"
    )
    is_contested: bool = Field(
        default=False, description="Whether contradicting evidence contests this hypothesis"
    )
    validation_notes: list[str] = Field(
        default_factory=list, description="Deterministic validation notes and warnings"
    )


class FinalRCAReport(BaseModel):
    """Complete, evidence-validated Root Cause Analysis investigation report."""

    executive_summary: str = Field(description="High-level incident summary and primary findings")
    incident_description: str = Field(
        description="Contextual overview of the incident trigger and impact"
    )
    timeline: list[dict[str, Any]] = Field(
        default_factory=list, description="Chronological event and telemetry milestones"
    )
    observed_facts: list[str] = Field(
        default_factory=list, description="Deterministic empirical facts directly observed in data"
    )
    hypotheses: list[Hypothesis] = Field(
        default_factory=list, description="Formulated hypotheses backed by evidence references"
    )
    confidence_level: ConfidenceLevel = Field(description="Overall synthesis confidence rating")
    affected_services: list[str] = Field(
        default_factory=list, description="All impacted microservices"
    )
    contributing_factors: list[str] = Field(
        default_factory=list, description="Cascading effects, network conditions, dependencies"
    )
    recommended_next_steps: list[str] = Field(
        default_factory=list, description="Non-operational investigative suggestions"
    )
    evidence_sources: list[EvidenceReference] = Field(
        default_factory=list, description="All verified evidence references cited"
    )
    investigation_limitations: list[str] = Field(
        default_factory=list, description="Telemetry gaps, missing probes, or offline sources"
    )
    validated_at: str | None = Field(
        default=None, description="Timestamp of evidence validator pass"
    )


class InvestigationState(TypedDict, total=False):
    """
    Shared typed state object for LangGraph autonomous investigation workflow.
    Ensures parallel agents operate on structured context and avoids storing live sockets/sessions.
    """

    investigation_id: str
    incident_id: str
    incident_context: dict[str, Any]
    investigation_started_at: str
    investigation_status: str
    model_name: str | None

    affected_service: str
    incident_severity: str
    incident_evidence: dict[str, Any]
    incident_timeline: list[dict[str, Any]]

    # Step 1: Analyzer output
    investigation_plan: dict[str, Any]

    # Step 2: Parallel Telemetry findings
    metrics_findings: dict[str, Any]
    logs_findings: dict[str, Any]
    traces_findings: dict[str, Any]
    historical_findings: dict[str, Any]

    # Step 3: Aggregated evidence & facts
    observed_facts: list[str]
    evidence_references: list[dict[str, Any]]

    # Step 4: RCA Synthesis & Validation
    hypotheses: list[dict[str, Any]]
    synthesis_result: dict[str, Any]
    validation_result: dict[str, Any]
    final_report: dict[str, Any]

    # Execution telemetry
    agent_statuses: Annotated[dict[str, str], merge_dict]
    agent_errors: Annotated[dict[str, str], merge_dict]
    investigation_iterations: int
    investigation_completed_at: str
