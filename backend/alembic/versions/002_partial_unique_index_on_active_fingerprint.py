"""replace global unique constraint on fingerprint with partial unique index for active incidents

Revision ID: 002_partial_unique_index
Revises: 001_initial_incidents
Create Date: 2026-11-09 00:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_partial_unique_index"
down_revision: str | None = "001_initial_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop existing global unique constraint/index on fingerprint
    # PostgreSQL creates table constraint incidents_fingerprint_key for fingerprint
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Drop unique constraint if present
        op.execute(
            sa.text("ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_fingerprint_key")
        )
        op.drop_index("ix_incidents_fingerprint", table_name="incidents", if_exists=True)
    else:
        # SQLite / other dialects
        with op.batch_alter_table("incidents") as batch_op:
            batch_op.drop_index("ix_incidents_fingerprint")

    # 2. Create standard non-unique index on fingerprint for fast lookups
    op.create_index(
        "ix_incidents_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=False,
    )

    # 3. Create partial unique index ensuring fingerprint uniqueness ONLY among active incidents
    op.create_index(
        "uq_active_incident_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status != 'RESOLVED'"),
        sqlite_where=sa.text("status != 'RESOLVED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_incident_fingerprint", table_name="incidents")
    op.drop_index("ix_incidents_fingerprint", table_name="incidents")

    op.create_index(
        "ix_incidents_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
    )
