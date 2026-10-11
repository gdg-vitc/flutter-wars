"""Module F tables. These must mirror migrations/versions/0004_inventory.py exactly."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel

INVENTORY_EVENT_KINDS = ("INCREMENT", "DECREMENT", "ADJUST")
MAX_QUANTITY_STEP = 10_000


class TeamWidgetInventory(SQLModel, table=True):
    """Authoritative 'how many of widget X does team T own'. One row per (team, widget)."""

    __tablename__ = "team_widget_inventory"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),)

    team_id: UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), ForeignKey("team.id", ondelete="RESTRICT"), primary_key=True)
    )
    widget_id: str = Field(sa_column=Column(String(40), ForeignKey("widget.id", ondelete="RESTRICT"), primary_key=True))
    quantity: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class InventoryEvent(SQLModel, table=True):
    """Append-only history. Σ delta per (team, widget) must equal the inventory quantity."""

    __tablename__ = "inventory_event"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_inv_event_delta_nonzero"),
        CheckConstraint("quantity_after >= 0", name="ck_inv_event_qty_after_nonneg"),
        CheckConstraint("kind IN ('INCREMENT','DECREMENT','ADJUST')", name="ck_inv_event_kind"),
        UniqueConstraint(
            "team_id", "widget_id", "ref_type", "ref_id", "kind", name="uq_inv_event_team_widget_ref_kind"
        ),
        Index("ix_inv_event_team_widget", "team_id", "widget_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    team_id: UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), ForeignKey("team.id", ondelete="RESTRICT"), nullable=False)
    )
    widget_id: str = Field(sa_column=Column(String(40), ForeignKey("widget.id", ondelete="RESTRICT"), nullable=False))
    kind: str = Field(sa_column=Column(String(16), nullable=False))
    delta: int = Field(sa_column=Column(Integer, nullable=False))
    quantity_after: int = Field(sa_column=Column(Integer, nullable=False))
    ref_type: str = Field(sa_column=Column(String(40), nullable=False))
    ref_id: str = Field(sa_column=Column(String(100), nullable=False))
    reason: str = Field(sa_column=Column(Text, nullable=False))
    actor: str = Field(sa_column=Column(String(200), nullable=False))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
