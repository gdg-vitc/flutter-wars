"""Allow preloaded participants to bind Google subject on first login."""

from alembic import op

revision = "0004_email_binding"
down_revision = "0003_align_unique_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("user_identity", "google_subject", nullable=True)


def downgrade() -> None:
    op.alter_column("user_identity", "google_subject", nullable=False)
