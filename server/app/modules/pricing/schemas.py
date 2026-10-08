from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PriceQuoteOut(BaseModel):
    listing_id: UUID
    price: int
    strategy: str
    interval_index: int
    valid_until: datetime | None = Field(
        description="Earliest time the price may change; null if no change is scheduled."
    )
    server_time: datetime


class PricingUpdate(BaseModel):
    """Draft rounds: strategy and params may change. Live rounds: params only,
    applied from the next repricing boundary."""

    model_config = ConfigDict(extra="forbid")

    strategy: str | None = Field(default=None, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class PricingConfigOut(BaseModel):
    listing_id: UUID
    strategy: str
    params: dict[str, Any]
    params_version: int
    current_price: int
    interval_index: int


class PriceHistoryOut(BaseModel):
    interval_index: int
    price: int
    previous_price: int | None
    reason: str
    strategy: str
    params_version: int
    demand: int | None
    supply: int | None
    effective_at: datetime


class StrategyOut(BaseModel):
    key: str
    params_schema: dict[str, Any]
