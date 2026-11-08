from typing import Any

from pydantic import BaseModel, Field


class DetectionRuleResponse(BaseModel):
    """Schema representing a configured deterministic detection rule."""

    rule_id: str
    name: str
    description: str
    enabled: bool
    service_name: str
    promql_query: str
    high_threshold: float
    critical_threshold: float | None = None
    operator: str
    evaluation_window: str = "5m"
    min_sample_count: int | float = 0


class DetectionRunResponse(BaseModel):
    """Result summary of a deterministic detection evaluation cycle."""

    status: str = "success"
    rules_evaluated: int
    breaches_detected: int
    incidents_created: int
    incidents_updated: int
    incidents_resolved: int
    evaluation_duration_ms: float
    details: list[dict[str, Any]] = Field(default_factory=list)
