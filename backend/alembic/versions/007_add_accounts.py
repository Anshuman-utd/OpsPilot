"""Add named accounts and revocable sessions."""

import sqlalchemy as sa

from alembic import op

revision = "007_add_accounts"
down_revision = "006_align_model_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("username", sa.String(64), primary_key=True),
        sa.Column("password_hash", sa.String(160), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('operator', 'viewer')", name="ck_accounts_role"),
    )
    op.create_table(
        "account_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64), sa.ForeignKey("accounts.username", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_sessions_username", "account_sessions", ["username"])
    op.create_index("ix_account_sessions_expires_at", "account_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("account_sessions")
    op.drop_table("accounts")
