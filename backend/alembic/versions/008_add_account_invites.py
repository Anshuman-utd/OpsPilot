"""Add expiring, single-use account invites."""

import sqlalchemy as sa

from alembic import op

revision = "008_add_account_invites"
down_revision = "007_add_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_invites",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('operator', 'viewer')", name="ck_account_invites_role"),
    )
    op.create_index("ix_account_invites_expires_at", "account_invites", ["expires_at"])


def downgrade() -> None:
    op.drop_table("account_invites")
