from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.contracts.marketplace import MAX_CREDITS

from .models import AuctionState


class BidRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    amount: int = Field(strict=True, ge=0, le=MAX_CREDITS)
    idempotency_key: UUID


class MyBid(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    auction_id: UUID
    amount: int
    amount_reached_at: datetime


class AuctionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    listing_id: UUID
    widget_id: str
    quantity: int
    state: AuctionState
    starts_at: datetime
    closes_at: datetime
    minimum_bid: int | None


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    auction_id: UUID
    winner_team_id: UUID | None
    winning_amount: int | None
    settled_at: datetime


class AuctionCreate(BaseModel):
    """Admin configuration. Market owner must allocate stock before activation."""

    model_config = ConfigDict(extra="forbid")
    round_id: UUID
    listing_id: UUID
    widget_id: str
    quantity: int = Field(strict=True, gt=0, le=MAX_CREDITS)
    starts_at: AwareDatetime
    closes_at: AwareDatetime
    minimum_bid: int | None = Field(default=None, strict=True, ge=0, le=MAX_CREDITS)

    @model_validator(mode="after")
    def valid_window(self):
        if self.starts_at >= self.closes_at:
            raise ValueError("starts_at must precede closes_at")
        return self
