from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class AuctionState(str, Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class Auction(SQLModel, table=True):
    __tablename__ = "auction"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_auction_quantity"),
        CheckConstraint("starts_at < closes_at", name="ck_auction_window"),
        CheckConstraint("minimum_bid IS NULL OR minimum_bid BETWEEN 0 AND 2147483647", name="ck_auction_minimum"),
        CheckConstraint("accepted_bid_order >= 0", name="ck_auction_order"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    round_id: UUID = Field(index=True)
    listing_id: UUID
    widget_id: str = Field(max_length=40)
    quantity: int = Field(gt=0, le=2147483647)
    state: AuctionState = Field(default=AuctionState.DRAFT)
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    closes_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    minimum_bid: int | None = Field(default=None, ge=0, le=2147483647)
    accepted_bid_order: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))


class Bid(SQLModel, table=True):
    __tablename__ = "auction_bid"
    __table_args__ = (
        Index("ix_auction_bid_winner", "auction_id", "amount", "amount_reached_order"),
        UniqueConstraint("auction_id", "team_id", name="uq_bid_auction_team"),
        UniqueConstraint("auction_id", "amount_reached_order", name="uq_bid_reached_order"),
        CheckConstraint("amount BETWEEN 0 AND 2147483647", name="ck_bid_amount"),
        CheckConstraint("amount_reached_order > 0", name="ck_bid_order"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    auction_id: UUID = Field(foreign_key="auction.id", index=True)
    team_id: UUID
    amount: int = Field(ge=0, le=2147483647)
    amount_reached_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    amount_reached_order: int = Field(sa_column=Column(BigInteger, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class BidReceipt(SQLModel, table=True):
    __tablename__ = "auction_bid_receipt"
    __table_args__ = (
        UniqueConstraint("auction_id", "team_id", "idempotency_key", name="uq_bid_request"),
        CheckConstraint("amount BETWEEN 0 AND 2147483647", name="ck_bid_receipt_amount"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    auction_id: UUID = Field(foreign_key="auction.id")
    team_id: UUID
    idempotency_key: UUID
    bid_id: UUID = Field(foreign_key="auction_bid.id")
    amount: int
    amount_reached_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class AuctionResult(SQLModel, table=True):
    __tablename__ = "auction_result"
    __table_args__ = (
        CheckConstraint(
            "(winner_team_id IS NULL AND winning_amount IS NULL) OR (winner_team_id IS NOT NULL AND winning_amount IS NOT NULL AND winning_amount BETWEEN 0 AND 2147483647)",
            name="ck_auction_result_winner",
        ),
    )
    auction_id: UUID = Field(foreign_key="auction.id", primary_key=True)
    winner_team_id: UUID | None = None
    winning_amount: int | None = None
    settled_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
