from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.integration.contracts import MAX_CREDITS

from .models import TradeType


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    listing_id: UUID
    quantity: int = Field(strict=True, gt=0, le=MAX_CREDITS)
    idempotency_key: UUID


class SellRequest(PurchaseRequest):
    """Owner-approved resale destination listing, quantity, and retry key."""


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    listing_id: UUID
    widget_id: UUID
    transaction_type: TradeType
    quantity: int
    unit_price: int
    gross_amount: int
    brokerage_amount: int
    final_amount: int
    created_at: datetime
