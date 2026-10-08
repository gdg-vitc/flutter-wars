from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _now() -> datetime:
    return datetime.now(UTC)


class PriceChangeReason(StrEnum):
    INITIAL = "initial"
    INTERVAL = "interval"
    CONFIG_CHANGE = "config_change"


class ListingPricing(SQLModel, table=True):
    """Authoritative pricing state for one market listing.

    current_price is the price for the interval `interval_index`, counted from
    the round's opened_at. interval_bought/interval_sold are the units traded
    so far in that interval; they feed the next repricing step.
    """

    __tablename__ = "listing_pricing"
    __table_args__ = (
        sa.CheckConstraint("current_price > 0", name="ck_listing_pricing_price_positive"),
        sa.CheckConstraint(
            "interval_bought >= 0 AND interval_sold >= 0", name="ck_listing_pricing_counts"
        ),
    )

    listing_id: UUID = Field(primary_key=True, foreign_key="market_listing.id")
    strategy: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict, sa_type=_JSON)
    params_version: int = 1
    current_price: int = Field(sa_type=sa.BigInteger)
    interval_index: int = 0
    interval_bought: int = 0
    interval_sold: int = 0
    updated_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))


class PriceHistory(SQLModel, table=True):
    """Append-only price snapshots. Transactions keep their own charged price;
    this table explains how a listing's price moved."""

    __tablename__ = "price_history"

    id: int | None = Field(default=None, primary_key=True)
    listing_id: UUID = Field(foreign_key="market_listing.id", index=True)
    interval_index: int
    price: int = Field(sa_type=sa.BigInteger)
    previous_price: int | None = Field(default=None, sa_type=sa.BigInteger)
    reason: str = Field(sa_type=sa.String(16))
    strategy: str = Field(max_length=40)
    params_version: int
    demand: int | None = None
    supply: int | None = None
    effective_at: datetime = Field(sa_type=sa.DateTime(timezone=True))
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))
