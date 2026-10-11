"""Module K request/response shapes. Strict: unknown fields rejected, ints must be real ints."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.contracts.ledger import MAX_AMOUNT
from app.modules.admin.permissions import Role

_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def normalize_email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > 254 or not _EMAIL_RE.fullmatch(v):
        raise ValueError("invalid email")
    return v


def _team_name(v: str) -> str:
    v = " ".join(v.split())  # trim + collapse inner whitespace
    if not 2 <= len(v) <= 60:
        raise ValueError("team name must be 2-60 characters")
    if any(ord(c) < 32 for c in v) or "<" in v or ">" in v:
        raise ValueError("team name has forbidden characters")
    return v


Email = Annotated[str, AfterValidator(normalize_email)]
TeamName = Annotated[str, AfterValidator(_team_name)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _dedupe(v: list[str]) -> list[str]:
    return list(dict.fromkeys(v))


# ---------------------------------------------------------------- me / organizers


class MeOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    role: Role
    permissions: list[str]


class OrganizerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    role: Role
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class OrganizerCreateIn(_In):
    email: Email
    display_name: DisplayName
    role: Role
    reason: Reason


class OrganizerUpdateIn(_In):
    expected_version: int = Field(strict=True, ge=1)
    reason: Reason
    role: Role | None = None
    active: bool | None = Field(default=None, strict=True)
    display_name: DisplayName | None = None


# ---------------------------------------------------------------- audit


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_email: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    reason: str
    details: dict[str, Any]
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_cursor: int | None


# ---------------------------------------------------------------- controls

Scope = Literal["ALL", "TRADING", "BIDDING"]


class ControlOut(BaseModel):
    scope: Scope
    frozen: bool
    reason: str | None
    changed_by: str | None
    changed_at: datetime | None
    version: int


class ControlSetIn(_In):
    frozen: bool = Field(strict=True)
    reason: Reason
    confirm: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, strict=True, ge=1)


class PublicControlsOut(BaseModel):
    trading_open: bool
    bidding_open: bool
    message: str | None


# ---------------------------------------------------------------- teams


class TeamOverviewOut(BaseModel):
    id: UUID
    name: str
    status: str
    balance: int
    held: int
    available: int
    units_owned: int


class TeamCreateIn(_In):
    name: TeamName
    member_emails: list[Email] = Field(default_factory=list, max_length=10)
    initial_credits: int = Field(default=120, strict=True, ge=0, le=MAX_AMOUNT)
    reason: Reason = "Team registration"

    @field_validator("member_emails")
    @classmethod
    def dedupe_emails(cls, v: list[str]) -> list[str]:
        return _dedupe(v)


class TeamImportRow(_In):
    name: TeamName
    member_emails: list[Email] = Field(default_factory=list, max_length=10)

    @field_validator("member_emails")
    @classmethod
    def dedupe_emails(cls, v: list[str]) -> list[str]:
        return _dedupe(v)


class TeamImportIn(_In):
    teams: list[TeamImportRow] = Field(min_length=1, max_length=200)
    initial_credits: int = Field(default=120, strict=True, ge=0, le=MAX_AMOUNT)
    reason: Reason = "Bulk team registration"


class TeamStatusIn(_In):
    status: Literal["ACTIVE", "DISABLED"]
    reason: Reason
    confirm: str | None = Field(default=None, max_length=60)


class TeamSummaryOut(BaseModel):
    id: UUID
    name: str
    status: str


class TeamWalletOut(BaseModel):
    balance: int
    held: int
    available: int


class TeamInventoryItemOut(BaseModel):
    widget_id: str
    appdev_key: str
    display_name: str
    quantity: int
    archived: bool


class TeamDetailOut(BaseModel):
    team: TeamSummaryOut
    wallet: TeamWalletOut
    inventory: list[TeamInventoryItemOut]
