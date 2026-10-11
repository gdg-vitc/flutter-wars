from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, UniqueConstraint
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
    widget_id: str = Field(max_length=40)
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


class ResaleAccount(SQLModel, table=True):
    """Non-renewing event profit allowance and cumulative fee rounding, shared across widgets."""

    __tablename__ = "resale_account"
    __table_args__ = (
        CheckConstraint(
            "profit_paid >= 0 AND fee_notional >= 0 AND loss_realized >= 0", name="ck_resale_account_nonnegative"
        ),
    )
    team_id: UUID = Field(primary_key=True)
    profit_paid: int = Field(default=0, sa_type=BigInteger)
    loss_realized: int = Field(default=0, sa_type=BigInteger)
    fee_notional: int = Field(default=0, sa_type=BigInteger)


class ResalePosition(SQLModel, table=True):
    """Whole-credit remaining acquisition cost; no reset at a round/listing boundary."""

    __tablename__ = "resale_position"
    __table_args__ = (
        CheckConstraint(
            "quantity >= 0 AND cost >= 0 AND external_units >= 0 AND reward_units >= 0",
            name="ck_resale_position_nonnegative",
        ),
    )
    team_id: UUID = Field(primary_key=True)
    widget_id: str = Field(primary_key=True, max_length=40)
    quantity: int = Field(default=0, sa_type=BigInteger)
    cost: int = Field(default=0, sa_type=BigInteger)
    external_units: int = Field(default=0, sa_type=BigInteger)
    reward_units: int = Field(default=0, sa_type=BigInteger)
