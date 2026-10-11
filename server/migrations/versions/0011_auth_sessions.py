"""Refresh sessions and PKCE-bound native login handoff (additive).

Revision ID: 0011_auth_sessions
Revises: 0010_market_safeguards
"""

from alembic import op

from app.modules.authentication.sessions import AuthRateBucket, AuthSession, LoginCode, OAuthAttempt, RefreshCredential

revision = "0011_auth_sessions"
down_revision = "0010_market_safeguards"
branch_labels = depends_on = None


def upgrade():
    for model in (AuthSession, RefreshCredential, LoginCode, OAuthAttempt, AuthRateBucket):
        model.__table__.create(op.get_bind())


def downgrade():
    for model in (AuthRateBucket, OAuthAttempt, LoginCode, RefreshCredential, AuthSession):
        model.__table__.drop(op.get_bind())
