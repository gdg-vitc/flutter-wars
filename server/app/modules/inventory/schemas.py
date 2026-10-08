"""Module F request/response schemas."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.inventory.models import MAX_QUANTITY_STEP

WidgetId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
SignedQty = Annotated[int, Field(strict=True, ge=-MAX_QUANTITY_STEP, le=MAX_QUANTITY_STEP)]


class InventoryItemOut(BaseModel):
    widget_id: str
    appdev_key: str
    display_name: str
    quantity: int
    archived: bool


class InventoryOut(BaseModel):
    team_id: UUID
    items: list[InventoryItemOut]


class InventoryAdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widget_id: WidgetId
    delta: SignedQty
    reason: Annotated[str, Field(min_length=3, max_length=500)]
    idempotency_key: UUID

    @field_validator("delta")
    @classmethod
    def _non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("delta must not be 0")
        return v

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("reason must be at least 3 characters")
        return v


class InventoryEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: UUID
    widget_id: str
    kind: str
    delta: int
    quantity_after: int
    ref_type: str
    ref_id: str
    reason: str
    actor: str
    created_at: datetime


class InventoryMismatchOut(BaseModel):
    widget_id: str
    quantity: int
    event_sum: int


class InventoryVerifyOut(BaseModel):
    team_id: UUID
    ok: bool
    mismatches: list[InventoryMismatchOut]
