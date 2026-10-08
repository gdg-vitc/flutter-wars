"""Align Module B uniqueness with SQLModel's unique-index metadata."""

from alembic import op

revision = "0003_align_unique_indexes"
down_revision = "0002_module_b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0002 created named unique constraints and ordinary indexes. SQLModel's
    # Field(unique=True, index=True) is represented as a unique index instead.
    op.drop_constraint("team_name_key", "team", type_="unique")
    op.drop_index("ix_team_name", table_name="team")
    op.create_index("ix_team_name", "team", ["name"], unique=True)

    op.drop_constraint(
        "team_membership_user_identity_id_key", "team_membership", type_="unique"
    )
    op.drop_index("ix_team_membership_user_identity_id", table_name="team_membership")
    op.create_index(
        "ix_team_membership_user_identity_id",
        "team_membership",
        ["user_identity_id"],
        unique=True,
    )

    op.drop_constraint("user_identity_google_subject_key", "user_identity", type_="unique")
    op.drop_index("ix_user_identity_google_subject", table_name="user_identity")
    op.create_index(
        "ix_user_identity_google_subject", "user_identity", ["google_subject"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_user_identity_google_subject", table_name="user_identity")
    op.create_index("ix_user_identity_google_subject", "user_identity", ["google_subject"])
    op.create_unique_constraint(
        "user_identity_google_subject_key", "user_identity", ["google_subject"]
    )

    op.drop_index("ix_team_membership_user_identity_id", table_name="team_membership")
    op.create_index(
        "ix_team_membership_user_identity_id", "team_membership", ["user_identity_id"]
    )
    op.create_unique_constraint(
        "team_membership_user_identity_id_key",
        "team_membership",
        ["user_identity_id"],
    )

    op.drop_index("ix_team_name", table_name="team")
    op.create_index("ix_team_name", "team", ["name"])
    op.create_unique_constraint("team_name_key", "team", ["name"])
