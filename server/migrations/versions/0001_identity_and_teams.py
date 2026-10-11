"""Module B: identity, team and membership tables.

Consolidates Team 6's 0002-0004 (identity/teams, SQLModel unique indexes, nullable
google_subject for first-login email binding) into their final state, with UUID team
IDs so the ledger, inventory, trading and auction tables can reference `team.id`.

Revision ID: 0001_identity
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identity",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("google_subject", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_user_identity_google_subject", "user_identity", ["google_subject"], unique=True
    )
    op.create_index("ix_user_identity_email", "user_identity", ["email"])
    op.create_table(
        "team",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_team_name", "team", ["name"], unique=True)
    op.create_table(
        "team_membership",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_identity_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="participant"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"]),
        sa.ForeignKeyConstraint(["user_identity_id"], ["user_identity.id"]),
    )
    op.create_index(
        "ix_team_membership_user_identity_id", "team_membership", ["user_identity_id"], unique=True
    )
    op.create_index("ix_team_membership_team_id", "team_membership", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_team_membership_team_id", table_name="team_membership")
    op.drop_index("ix_team_membership_user_identity_id", table_name="team_membership")
    op.drop_table("team_membership")
    op.drop_index("ix_team_name", table_name="team")
    op.drop_table("team")
    op.drop_index("ix_user_identity_email", table_name="user_identity")
    op.drop_index("ix_user_identity_google_subject", table_name="user_identity")
    op.drop_table("user_identity")
