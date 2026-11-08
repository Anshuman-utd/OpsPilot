import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base


class IncidentSeverity(StrEnum):
    """Categorized severity levels for detected incidents."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class IncidentStatus(StrEnum):
    """Operational lifecycle states of an incident."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"


class IncidentEventType(StrEnum):
    """Categorized lifecycle and audit event types."""

    INCIDENT_CREATED = "INCIDENT_CREATED"
    INCIDENT_UPDATED = "INCIDENT_UPDATED"
    INCIDENT_ACKNOWLEDGED = "INCIDENT_ACKNOWLEDGED"
    INCIDENT_INVESTIGATING = "INCIDENT_INVESTIGATING"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    MANUAL_COMMENT = "MANUAL_COMMENT"


# Use JSONB for PostgreSQL where possible, fallback to standard JSON for SQLite tests
JSONType = JSON().with_variant(JSONB, "postgresql")


class IncidentModel(Base):
    """SQLAlchemy model representing an SRE incident."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True, default=IncidentSeverity.HIGH.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=IncidentStatus.OPEN.value
    )
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="detection_engine")

    # Deduplication fingerprint: stable hash of (rule_id, service_name, entity)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    triggered_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Structured evidence explaining why incident was created
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Chronological audit trail
    events: Mapped[list["IncidentEventModel"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentEventModel.created_at.asc()",
    )

    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_service_status", "service_name", "status"),
    )


class IncidentEventModel(Base):
    """SQLAlchemy model capturing immutable audit events and timeline records."""

    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    incident: Mapped["IncidentModel"] = relationship(back_populates="events")
