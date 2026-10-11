"""Opaque session credentials. Mutations are serialized by the parent session lock."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, Session, SQLModel, select

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.authentication.jwt import create_access_token
from app.modules.authentication.schemas import TokenResponse


def now():
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_session"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: int = Field(foreign_key="user_identity.id", index=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    idle_expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class RefreshCredential(SQLModel, table=True):
    __tablename__ = "auth_refresh_credential"
    token_hash: str = Field(sa_column=Column(String(64), primary_key=True))
    session_id: UUID = Field(foreign_key="auth_session.id", index=True)
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class LoginCode(SQLModel, table=True):
    __tablename__ = "auth_login_code"
    token_hash: str = Field(sa_column=Column(String(64), primary_key=True))
    user_id: int = Field(foreign_key="user_identity.id")
    challenge: str = Field(max_length=43)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class OAuthAttempt(SQLModel, table=True):
    __tablename__ = "auth_oauth_attempt"
    state_hash: str = Field(sa_column=Column(String(64), primary_key=True))
    challenge: str = Field(max_length=43)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


def invalid_refresh():
    return AppError("INVALID_REFRESH_TOKEN", "Session is invalid or expired. Sign in again.", 401)


def active(row: AuthSession, timestamp: datetime) -> bool:
    return row.revoked_at is None and timestamp < min(row.expires_at, row.idle_expires_at)


def current_principal(db: Session, settings: Settings, user_id: int):
    from app.modules.authentication.model import UserIdentity
    from app.modules.authentication.service import AuthenticationService

    user = db.get(UserIdentity, user_id)
    if user is None or not user.google_subject:
        raise invalid_refresh()
    principal = AuthenticationService(db, settings).principal_for_google_identity(user.google_subject, user.email)
    if principal is None:
        raise invalid_refresh()
    return principal


def pair(db: Session, settings: Settings, row: AuthSession, principal) -> TokenResponse:
    raw = secrets.token_urlsafe(32)
    db.add(RefreshCredential(token_hash=digest(raw), session_id=row.id))
    db.flush()
    return TokenResponse(
        access_token=create_access_token(
            user_id=principal.user_id,
            email=principal.email,
            team_id=str(principal.team_id) if principal.team_id else None,
            role=principal.role,
            settings=settings,
            session_id=str(row.id),
        ),
        refresh_token=raw,
        expires_in=settings.auth_access_token_minutes * 60,
    )


def issue(db: Session, settings: Settings, principal) -> TokenResponse:
    timestamp = now()
    row = AuthSession(
        user_id=int(principal.user_id),
        created_at=timestamp,
        expires_at=timestamp + timedelta(days=settings.auth_session_days),
        idle_expires_at=timestamp + timedelta(days=settings.auth_refresh_idle_days),
    )
    db.add(row)
    db.flush()
    return pair(db, settings, row, principal)


def rotate(db: Session, settings: Settings, raw: str) -> TokenResponse:
    credential = db.get(RefreshCredential, digest(raw))
    if credential is None:
        raise invalid_refresh()
    row = db.exec(select(AuthSession).where(AuthSession.id == credential.session_id).with_for_update()).one()
    # Another request may have consumed the credential while this request waited.
    db.refresh(credential)
    timestamp = now()
    if not active(row, timestamp):
        raise invalid_refresh()
    if credential.consumed_at is not None:
        row.revoked_at = timestamp
        db.add(row)
        # Persist revocation even though the dependency rolls back the rejected request.
        db.commit()
        raise invalid_refresh()
    try:
        principal = current_principal(db, settings, row.user_id)
    except AppError:
        row.revoked_at = timestamp
        db.add(row)
        db.commit()
        raise invalid_refresh() from None
    credential.consumed_at = timestamp
    row.idle_expires_at = min(row.expires_at, timestamp + timedelta(days=settings.auth_refresh_idle_days))
    db.add(credential)
    db.add(row)
    return pair(db, settings, row, principal)


def verify_session(db: Session, session_id: object, user_id: int) -> None:
    try:
        row = db.get(AuthSession, UUID(str(session_id)))
    except ValueError:
        row = None
    if row is None or row.user_id != user_id or not active(row, now()):
        raise AppError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401)


class AuthRateBucket(SQLModel, table=True):
    __tablename__ = "auth_rate_bucket"
    key: str = Field(sa_column=Column(String(64), primary_key=True))
    window_start: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    attempts: int = Field(default=0)


def rate_limit(db: Session, request) -> None:
    """Persistent per-client limit; do not trust arbitrary forwarded headers."""
    from sqlalchemy.dialects.postgresql import insert

    timestamp = now().replace(second=0, microsecond=0)
    # Cloudflare sets this header on Worker requests; direct deployments use the peer address.
    peer = request.client.host if request.client else "unknown"
    if getattr(request.app.state, "cloudflare_runtime", False):
        peer = request.headers.get("cf-connecting-ip", peer)
    key = digest(peer)
    statement = insert(AuthRateBucket).values(key=key, window_start=timestamp, attempts=1)
    from sqlalchemy import case

    statement = statement.on_conflict_do_update(
        index_elements=["key"],
        set_={
            "window_start": timestamp,
            "attempts": case((AuthRateBucket.window_start == timestamp, AuthRateBucket.attempts + 1), else_=1),
        },
    ).returning(AuthRateBucket.attempts)
    count = db.execute(statement).scalar_one()
    # Failed credential requests must still count. Called before any credential writes.
    db.commit()
    if count > 60:
        raise AppError("AUTH_RATE_LIMITED", "Too many authentication attempts. Try again shortly.", 429)
