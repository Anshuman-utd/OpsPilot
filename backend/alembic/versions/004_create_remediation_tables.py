"""create remediation tables for Phase 5

Revision ID: 004_create_remediation_tables
Revises: 003_create_investigation_tables
Create Date: 2026-11-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_create_remediation_tables"
down_revision: str | None = "003_create_investigation_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    # 1. Create remediation_plans table
    op.create_table(
        "remediation_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause_hypothesis", json_type, nullable=True),
        sa.Column("supporting_evidence_ids", json_type, nullable=False),
        sa.Column("contradicting_evidence_ids", json_type, nullable=False),
        sa.Column("risk_assessment", json_type, nullable=True),
        sa.Column("assumptions", json_type, nullable=False),
        sa.Column("limitations", json_type, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_plans_created_at"), "remediation_plans", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_plans_incident_id"), "remediation_plans", ["incident_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_plans_investigation_id"), "remediation_plans", ["investigation_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_plans_status"), "remediation_plans", ["status"], unique=False
    )
    op.create_index(
        "ix_remediation_plans_incident_status",
        "remediation_plans",
        ["incident_id", "status"],
        unique=False,
    )

    # 2. Create remediation_actions table
    op.create_table(
        "remediation_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_service", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("parameters", json_type, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("preconditions", json_type, nullable=False),
        sa.Column("verification_conditions", json_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["remediation_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_actions_plan_id"), "remediation_actions", ["plan_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_actions_action_type"), "remediation_actions", ["action_type"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_actions_target_service"), "remediation_actions", ["target_service"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_actions_status"), "remediation_actions", ["status"], unique=False
    )

    # 3. Create remediation_approvals table
    op.create_table(
        "remediation_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("approver_id", sa.String(length=100), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["remediation_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["remediation_actions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_approvals_plan_id"), "remediation_approvals", ["plan_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_approvals_action_id"), "remediation_approvals", ["action_id"], unique=False
    )

    # 4. Create remediation_executions table
    op.create_table(
        "remediation_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("execution_output", json_type, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["remediation_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["remediation_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["remediation_approvals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_executions_idempotency_key"), "remediation_executions", ["idempotency_key"], unique=True
    )
    op.create_index(
        op.f("ix_remediation_executions_plan_id"), "remediation_executions", ["plan_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_executions_action_id"), "remediation_executions", ["action_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_executions_status"), "remediation_executions", ["status"], unique=False
    )

    # 5. Create remediation_verification_runs table
    op.create_table(
        "remediation_verification_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_healthy_count", sa.Integer(), nullable=False),
        sa.Column("required_healthy_count", sa.Integer(), nullable=False),
        sa.Column("observations", json_type, nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["remediation_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_verification_runs_execution_id"), "remediation_verification_runs", ["execution_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_verification_runs_status"), "remediation_verification_runs", ["status"], unique=False
    )

    # 6. Create remediation_audit_events table
    op.create_table(
        "remediation_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=True),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("action_id", sa.String(length=36), nullable=True),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("details", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_remediation_audit_events_incident_id"), "remediation_audit_events", ["incident_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_audit_events_plan_id"), "remediation_audit_events", ["plan_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_audit_events_action_id"), "remediation_audit_events", ["action_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_audit_events_execution_id"), "remediation_audit_events", ["execution_id"], unique=False
    )
    op.create_index(
        op.f("ix_remediation_audit_events_event_type"), "remediation_audit_events", ["event_type"], unique=False
    )


def downgrade() -> None:
    op.drop_table("remediation_audit_events")
    op.drop_table("remediation_verification_runs")
    op.drop_table("remediation_executions")
    op.drop_table("remediation_approvals")
    op.drop_table("remediation_actions")
    op.drop_table("remediation_plans")

