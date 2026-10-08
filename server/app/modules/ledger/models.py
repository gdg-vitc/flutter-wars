"""Module E tables. These must mirror migrations/versions/0003_ledger.py exactly."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel

LEDGER_KINDS = ("GRANT", "CREDIT", "DEBIT", "CAPTURE", "ADJUST")
RESERVATION_STATUSES = ("ACTIVE", "RELEASED", "CAPTURED")
MAX_AMOUNT = 1_000_000


class TeamWallet(SQLModel, table=True):
    """Cached balance. Always changed in the same transaction as a ledger row (or a hold)."""

    __tablename__ = "team_wallet"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_nonneg"),
        CheckConstraint("held >= 0", name="ck_wallet_held_nonneg"),
        CheckConstraint("held <= balance", name="ck_wallet_held_le_balance"),
    )

    team_id: UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), ForeignKey("team.id", ondelete="RESTRICT"), primary_key=True)
    )
    balance: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    held: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0")))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class CreditLedgerEntry(SQLModel, table=True):
    """Append-only. Every real credit movement is exactly one row."""

    __tablename__ = "credit_ledger_entry"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_ledger_amount_nonzero"),
        CheckConstraint(f"amount BETWEEN -{MAX_AMOUNT} AND {MAX_AMOUNT}", name="ck_ledger_amount_range"),
        CheckConstraint("balance_after >= 0", name="ck_ledger_balance_after_nonneg"),
        CheckConstraint("kind IN ('GRANT','CREDIT','DEBIT','CAPTURE','ADJUST')", name="ck_ledger_kind"),
        UniqueConstraint("team_id", "ref_type", "ref_id", "kind", name="uq_ledger_team_ref_kind"),
        Index("ix_ledger_team_id_id", "team_id", "id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    team_id: UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), ForeignKey("team.id", ondelete="RESTRICT"), nullable=False)
    )
    kind: str = Field(sa_column=Column(String(16), nullable=False))
    amount: int = Field(sa_column=Column(BigInteger, nullable=False))
    balance_after: int = Field(sa_column=Column(BigInteger, nullable=False))
    ref_type: str = Field(sa_column=Column(String(40), nullable=False))
    ref_id: str = Field(sa_column=Column(String(100), nullable=False))
    reason: str = Field(sa_column=Column(Text, nullable=False))
    actor: str = Field(sa_column=Column(String(200), nullable=False))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class CreditReservation(SQLModel, table=True):
    """Credits held for an auction bid. Not a movement, so no ledger row until capture."""

    __tablename__ = "credit_reservation"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_reservation_amount_pos"),
        CheckConstraint(f"amount <= {MAX_AMOUNT}", name="ck_reservation_amount_max"),
        CheckConstraint("status IN ('ACTIVE','RELEASED','CAPTURED')", name="ck_reservation_status"),
        Index(
            "uq_reservation_active_ref",
            "team_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_reservation_team_status", "team_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, sa_column=Column(PG_UUID(as_uuid=True), primary_key=True))
    team_id: UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), ForeignKey("team.id", ondelete="RESTRICT"), nullable=False)
    )
    amount: int = Field(sa_column=Column(BigInteger, nullable=False))
    status: str = Field(default="ACTIVE", sa_column=Column(String(16), nullable=False, server_default="ACTIVE"))
    ref_type: str = Field(sa_column=Column(String(40), nullable=False))
    ref_id: str = Field(sa_column=Column(String(100), nullable=False))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    closed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
