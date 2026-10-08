"""TEMP stub of the `team` table, owned by Module B (Team 6), so our foreign keys resolve.

DELETE THIS FILE when Module B's migration is merged, and set `down_revision`
of 0002_catalog to Module B's latest revision instead.

Revision ID: 0001_temp_team
Revises:
"""

from alembic import op

revision = "0001_temp_team"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team (
            id         UUID PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            status     VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS team;")
