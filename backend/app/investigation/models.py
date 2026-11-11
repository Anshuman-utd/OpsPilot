import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.incident import IncidentModel

JSONType = JSON().with_variant(JSONB, "postgresql")


class InvestigationStatus(StrEnum):
    """Execution status of an autonomous incident investigation."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InvestigationTriggerType(StrEnum):
    """Trigger mode initiating the investigation."""

    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"


class AgentStatus(StrEnum):
    """Lifecycle state of an individual specialized investigation agent."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class InvestigationModel(Base):
    """SQLAlchemy model representing an autonomous investigation lifecycle and final report."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InvestigationStatus.PENDING.value,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InvestigationTriggerType.MANUAL.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_report: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    incident: Mapped["IncidentModel"] = relationship(back_populates="investigations")
    agent_runs: Mapped[list["InvestigationAgentRunModel"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationAgentRunModel.created_at.asc()",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_investigations_incident_status", "incident_id", "status"),
        Index("ix_investigations_created_at_status", "created_at", "status"),
    )


class InvestigationAgentRunModel(Base):
    """SQLAlchemy model capturing individual agent execution telemetry and findings."""

    __tablename__ = "investigation_agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    investigation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentStatus.PENDING.value,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    findings: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    investigation: Mapped["InvestigationModel"] = relationship(back_populates="agent_runs")

    __table_args__ = (Index("ix_agent_runs_investigation_name", "investigation_id", "agent_name"),)
