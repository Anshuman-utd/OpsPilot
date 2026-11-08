from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IncidentEventResponse(BaseModel):
    """Schema for an audit timeline event."""

    id: str
    incident_id: str
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    message: str
    actor: str = "system"
    payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentResponse(BaseModel):
    """Schema for incident list views."""

    id: str
    title: str
    description: str | None = None
    service_name: str
    severity: str
    status: str
    incident_type: str
    source: str
    fingerprint: str
    triggered_value: float | None = None
    threshold_value: float | None = None
    occurrence_count: int
    triggered_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentDetailResponse(IncidentResponse):
    """Detailed schema including structured evidence, links, and audit events."""

    evidence: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    events: list[IncidentEventResponse] = Field(default_factory=list)


class IncidentActionRequest(BaseModel):
    """Payload for manual incident lifecycle transitions (acknowledge, investigate, resolve)."""

    comment: str | None = Field(default=None, description="Optional operator comment or notes")
    actor: str = Field(
        default="user", description="Identifier of the operator performing the action"
    )


class IncidentStatsResponse(BaseModel):
    """Summary statistics for SRE incident dashboard."""

    total_incidents: int
    open_incidents: int
    critical_incidents: int
    high_incidents: int
    acknowledged_incidents: int
    investigating_incidents: int
    resolved_incidents: int
    by_service: dict[str, int]
    by_severity: dict[str, int]
