"""Create indexes declared by the incident and remediation models."""

from alembic import op

revision = "006_align_model_indexes"
down_revision = "005_bind_remediation_approval"
branch_labels = None
depends_on = None

INDEXES = (
    ("incidents", "service_name"),
    ("remediation_audit_events", "created_at"),
    ("remediation_audit_events", "investigation_id"),
    ("remediation_executions", "approval_id"),
    ("remediation_executions", "created_at"),
)


def upgrade() -> None:
    for table, column in INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, column in reversed(INDEXES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
