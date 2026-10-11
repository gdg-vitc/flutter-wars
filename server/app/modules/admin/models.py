"""Module K tables. These must mirror migrations/versions/0005_admin.py exactly."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel

CONTROL_SCOPES = ("ALL", "TRADING", "BIDDING")


class Organizer(SQLModel, table=True):
    """Who may use /admin. Looked up on EVERY admin request, so deactivation is immediate."""

    __tablename__ = "organizer"
    __table_args__ = (
        CheckConstraint("role IN ('OWNER','OPERATOR','VIEWER')", name="ck_organizer_role"),
        CheckConstraint("email = lower(email)", name="ck_organizer_email_lower"),
    )

    id: UUID = Field(default_factory=uuid4, sa_column=Column(PG_UUID(as_uuid=True), primary_key=True))
    email: str = Field(sa_column=Column(String(254), nullable=False, unique=True))
    display_name: str = Field(sa_column=Column(String(100), nullable=False))
    role: str = Field(sa_column=Column(String(16), nullable=False))
    active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    version: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default=text("1")))
    created_by: str = Field(sa_column=Column(String(254), nullable=False))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class AdminActionLog(SQLModel, table=True):
    """Append-only audit trail of organizer mutations (trigger blocks UPDATE/DELETE/TRUNCATE)."""

    __tablename__ = "admin_action_log"
    __table_args__ = (
        CheckConstraint("length(btrim(reason)) > 0", name="ck_admin_log_reason_nonblank"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_admin_log_details_object"),
        Index("ix_admin_log_target", "target_type", "target_id"),
        Index("ix_admin_log_action", "action"),
        Index("ix_admin_log_actor", "actor_email"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    actor_id: str = Field(sa_column=Column(String(100), nullable=False))
    actor_email: str = Field(sa_column=Column(String(254), nullable=False))
    actor_role: str = Field(sa_column=Column(String(16), nullable=False))
    action: str = Field(sa_column=Column(String(60), nullable=False))
    target_type: str = Field(sa_column=Column(String(40), nullable=False))
    target_id: str = Field(sa_column=Column(String(100), nullable=False))
    reason: str = Field(sa_column=Column(Text, nullable=False))
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    )
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class OperationalControl(SQLModel, table=True):
    """Emergency switches. Purchase (I) and Auction (J) call ensure_not_frozen() inside their transaction."""

    __tablename__ = "operational_control"
    __table_args__ = (CheckConstraint("scope IN ('ALL','TRADING','BIDDING')", name="ck_control_scope"),)

    scope: str = Field(sa_column=Column(String(16), primary_key=True))
    frozen: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false")))
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    changed_by: str | None = Field(default=None, sa_column=Column(String(254), nullable=True))
    changed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    version: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default=text("1")))
