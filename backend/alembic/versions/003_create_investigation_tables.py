"""create investigations and investigation_agent_runs tables

Revision ID: 003_create_investigation_tables
Revises: 002_partial_unique_index
Create Date: 2026-11-09 19:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_create_investigation_tables"
down_revision: str | None = "002_partial_unique_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create investigations table
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("total_duration_ms", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "final_report",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_investigations_created_at"), "investigations", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_investigations_incident_id"), "investigations", ["incident_id"], unique=False
    )
    op.create_index(op.f("ix_investigations_status"), "investigations", ["status"], unique=False)
    op.create_index(
        "ix_investigations_incident_status",
        "investigations",
        ["incident_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_investigations_created_at_status",
        "investigations",
        ["created_at", "status"],
        unique=False,
    )

    # 2. Create investigation_agent_runs table
    op.create_table(
        "investigation_agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "findings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_investigation_agent_runs_agent_name"),
        "investigation_agent_runs",
        ["agent_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_investigation_agent_runs_created_at"),
        "investigation_agent_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_investigation_agent_runs_investigation_id"),
        "investigation_agent_runs",
        ["investigation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_investigation_agent_runs_status"),
        "investigation_agent_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_investigation_name",
        "investigation_agent_runs",
        ["investigation_id", "agent_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("investigation_agent_runs")
    op.drop_table("investigations")
