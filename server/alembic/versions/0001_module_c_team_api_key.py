"""Create Module C team API-key storage."""

import sqlalchemy as sa

from alembic import op

revision = "0001_module_c"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_api_key",
        sa.Column("key_id", sa.String(length=32), primary_key=True),
        sa.Column("team_id", sa.String(length=128), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_team_api_key_team_id", "team_api_key", ["team_id"])
    op.create_index("ix_team_api_key_status", "team_api_key", ["status"])


def downgrade() -> None:
    op.drop_index("ix_team_api_key_status", table_name="team_api_key")
    op.drop_index("ix_team_api_key_team_id", table_name="team_api_key")
    op.drop_table("team_api_key")
