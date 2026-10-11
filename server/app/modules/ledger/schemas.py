"""Module E request/response schemas. Strict ints: 10.0 or "10" is rejected, never coerced."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.ledger.models import MAX_AMOUNT

PositiveAmount = Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT)]
SignedAmount = Annotated[int, Field(strict=True, ge=-MAX_AMOUNT, le=MAX_AMOUNT)]


class WalletOut(BaseModel):
    team_id: UUID
    balance: int
    held: int
    available: int


class LedgerEntryOut(BaseModel):
    """Participant-safe: no actor (may contain organizer email)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    amount: int
    balance_after: int
    ref_type: str
    ref_id: str
    reason: str
    created_at: datetime


class LedgerEntryAdminOut(LedgerEntryOut):
    team_id: UUID
    actor: str


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    next_cursor: int | None


class AdminCreditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: SignedAmount
    reason: Annotated[str, Field(min_length=3, max_length=500)]
    idempotency_key: UUID

    @field_validator("amount")
    @classmethod
    def _non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("amount must not be 0")
        return v

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("reason must be at least 3 characters")
        return v


class InitialGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: PositiveAmount


class WalletVerifyOut(BaseModel):
    team_id: UUID
    balance: int
    held: int
    ledger_sum: int
    active_reserved: int
    ok: bool
