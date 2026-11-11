"""SQLAlchemy ORM models for Phase 5: Controlled Remediation & Recovery Verification."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base

if TYPE_CHECKING:
    from app.investigation.models import InvestigationModel
    from app.models.incident import IncidentModel

JSONType = JSON().with_variant(JSONB, "postgresql")


class PlanStatus(StrEnum):
    """Lifecycle status of a remediation plan."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActionStatus(StrEnum):
    """Execution status of an individual remediation action."""

    PROPOSED = "PROPOSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    CANCELLED = "CANCELLED"


class ApprovalDecision(StrEnum):
    """Decision made on a proposed remediation action."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ExecutionStatus(StrEnum):
    """Status of an asynchronous remediation action execution."""

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    VERIFYING = "VERIFYING"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RECOVERED = "RECOVERED"


class VerificationStatus(StrEnum):
    """Recovery verification evaluation status."""

    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    STILL_DEGRADED = "STILL_DEGRADED"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    TELEMETRY_UNAVAILABLE = "TELEMETRY_UNAVAILABLE"


class RiskLevel(StrEnum):
    """Risk rating for a proposed remediation action."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RemediationAuditEventType(StrEnum):
    """Categorization of remediation audit events."""

    PLAN_CREATED = "PLAN_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    PLAN_CANCELLED = "PLAN_CANCELLED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_OBSERVATION = "VERIFICATION_OBSERVATION"
    RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class RemediationPlanModel(Base):
    """Structured remediation plan formulated from Phase 4 RCA hypotheses."""

    __tablename__ = "remediation_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    investigation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("investigations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PlanStatus.PENDING_APPROVAL.value,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_hypothesis: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    supporting_evidence_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    contradicting_evidence_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    risk_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    assumptions: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    limitations: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    incident: Mapped["IncidentModel"] = relationship("IncidentModel", foreign_keys=[incident_id])
    investigation: Mapped["InvestigationModel | None"] = relationship(
        "InvestigationModel", foreign_keys=[investigation_id]
    )
    actions: Mapped[list["RemediationActionModel"]] = relationship(
        "RemediationActionModel",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="RemediationActionModel.created_at",
    )
    approvals: Mapped[list["RemediationApprovalModel"]] = relationship(
        "RemediationApprovalModel",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="RemediationApprovalModel.created_at",
    )
    executions: Mapped[list["RemediationExecutionModel"]] = relationship(
        "RemediationExecutionModel",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="RemediationExecutionModel.created_at",
    )

    __table_args__ = (
        Index("ix_remediation_plans_incident_status", "incident_id", "status"),
    )


class RemediationActionModel(Base):
    """Specific allowlisted action proposed within a remediation plan."""

    __tablename__ = "remediation_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_service: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="demo")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default=RiskLevel.LOW.value)
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preconditions: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    verification_conditions: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ActionStatus.PROPOSED.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    plan: Mapped["RemediationPlanModel"] = relationship("RemediationPlanModel", back_populates="actions")
    executions: Mapped[list["RemediationExecutionModel"]] = relationship(
        "RemediationExecutionModel",
        back_populates="action",
        cascade="all, delete-orphan",
    )


class RemediationApprovalModel(Base):
    """Auditable record of explicit human approval or rejection of an action."""

    __tablename__ = "remediation_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    approver_id: Mapped[str] = mapped_column(String(100), nullable=False, default="operator")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    plan: Mapped["RemediationPlanModel"] = relationship("RemediationPlanModel", back_populates="approvals")
    action: Mapped["RemediationActionModel"] = relationship("RemediationActionModel")


class RemediationExecutionModel(Base):
    """Asynchronous execution record for an approved remediation action."""

    __tablename__ = "remediation_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_approvals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ExecutionStatus.PENDING.value,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_output: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    plan: Mapped["RemediationPlanModel"] = relationship("RemediationPlanModel", back_populates="executions")
    action: Mapped["RemediationActionModel"] = relationship("RemediationActionModel", back_populates="executions")
    approval: Mapped["RemediationApprovalModel"] = relationship("RemediationApprovalModel")
    verification_runs: Mapped[list["RemediationVerificationRunModel"]] = relationship(
        "RemediationVerificationRunModel",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="RemediationVerificationRunModel.created_at",
    )


class RemediationVerificationRunModel(Base):
    """Post-execution telemetry verification checking actual service recovery."""

    __tablename__ = "remediation_verification_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    execution_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("remediation_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationStatus.VERIFYING.value,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_healthy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_healthy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    observations: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    execution: Mapped["RemediationExecutionModel"] = relationship(
        "RemediationExecutionModel", back_populates="verification_runs"
    )


class RemediationAuditEventModel(Base):
    """Append-only audit trail capturing all remediation lifecycle events."""

    __tablename__ = "remediation_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    investigation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

