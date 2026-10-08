from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class TradeType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeTransaction(SQLModel, table=True):
    __tablename__ = "trade_transaction"
    __table_args__ = (
        Index("ix_trade_team_created", "team_id", "created_at", "id"),
        CheckConstraint(
            "gross_amount = unit_price::bigint * quantity::bigint",
            name="ck_trade_gross_calculation",
        ),
        CheckConstraint(
            "final_amount = gross_amount - brokerage_amount",
            name="ck_trade_final_calculation",
        ),
        CheckConstraint(
            "transaction_type <> 'BUY' OR brokerage_amount = 0",
            name="ck_trade_buy_brokerage",
        ),
        UniqueConstraint(
            "team_id",
            "transaction_type",
            "idempotency_key",
            name="uq_trade_team_type_idempotency",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_trade_quantity_positive",
        ),
        CheckConstraint(
            "unit_price BETWEEN 0 AND 2147483647",
            name="ck_trade_unit_price_range",
        ),
        CheckConstraint(
            "gross_amount BETWEEN 0 AND 2147483647",
            name="ck_trade_gross_amount_range",
        ),
        CheckConstraint(
            "brokerage_amount BETWEEN 0 AND 2147483647",
            name="ck_trade_brokerage_amount_range",
        ),
        CheckConstraint(
            "final_amount BETWEEN 0 AND 2147483647",
            name="ck_trade_final_amount_range",
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    team_id: UUID = Field(index=True)
    listing_id: UUID
    widget_id: UUID
    idempotency_key: UUID
    transaction_type: TradeType
    quantity: int = Field(gt=0, le=2147483647)
    unit_price: int = Field(ge=0, le=2147483647)
    gross_amount: int = Field(ge=0, le=2147483647)
    brokerage_amount: int = Field(ge=0, le=2147483647)
    final_amount: int = Field(ge=0, le=2147483647)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
