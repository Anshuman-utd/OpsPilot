from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.investigation.models import (
    AgentStatus,
    InvestigationStatus,
    InvestigationTriggerType,
)


class InvestigationCreateRequest(BaseModel):
    """Payload to request or trigger an autonomous investigation."""

    actor: str = Field(default="operator", description="Operator identity initiating investigation")
    trigger_type: InvestigationTriggerType = Field(
        default=InvestigationTriggerType.MANUAL,
        description="Mode of initiation: MANUAL or AUTOMATIC",
    )
    model_override: str | None = Field(default=None, description="Optional LLM model override")


class InvestigationAgentRunResponse(BaseModel):
    """Execution status and findings for an individual investigation agent."""

    id: str
    investigation_id: str
    agent_name: str
    status: AgentStatus | str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    findings: dict[str, Any] | None = None
    error_message: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvestigationResponse(BaseModel):
    """High-level summary of an autonomous incident investigation run."""

    id: str
    incident_id: str
    status: InvestigationStatus | str
    trigger_type: InvestigationTriggerType | str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model_provider: str | None = None
    model_name: str | None = None
    total_duration_ms: float | None = None
    summary: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvestigationDetailResponse(InvestigationResponse):
    """Complete detail view including synthesized RCA report and individual agent runs."""

    final_report: dict[str, Any] | None = None
    agent_runs: list[InvestigationAgentRunResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
