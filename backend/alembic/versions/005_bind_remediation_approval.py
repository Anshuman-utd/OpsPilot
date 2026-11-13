"""Bind remediation approvals to reviewed action content."""

import sqlalchemy as sa

from alembic import op

revision = "005_bind_remediation_approval"
down_revision = "004_create_remediation_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remediation_approvals", sa.Column("action_content_hash", sa.String(64)))


def downgrade() -> None:
    op.drop_column("remediation_approvals", "action_content_hash")
