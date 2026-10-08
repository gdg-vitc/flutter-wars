"""Module F data access. SQL only — no business rules, no commits."""

from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core._temp_models import Team  # TEMP: switch to Module B's team model
from app.modules.inventory.errors import DuplicateReference
from app.modules.inventory.models import InventoryEvent, TeamWidgetInventory


def team_exists(s: Session, team_id: UUID) -> bool:
    return s.exec(select(Team.id).where(Team.id == team_id)).first() is not None


def get_quantity(s: Session, team_id: UUID, widget_id: str) -> int:
    stmt = select(TeamWidgetInventory.quantity).where(
        TeamWidgetInventory.team_id == team_id, TeamWidgetInventory.widget_id == widget_id
    )
    q = s.exec(stmt).first()
    return int(q) if q is not None else 0


def add_quantity(s: Session, team_id: UUID, widget_id: str, qty: int) -> int:
    """Upsert: create the row or add to it. Row-locks (team, widget). Returns new quantity."""
    stmt = (
        pg_insert(TeamWidgetInventory)
        .values(team_id=team_id, widget_id=widget_id, quantity=qty)
        .on_conflict_do_update(
            index_elements=["team_id", "widget_id"],
            set_={"quantity": TeamWidgetInventory.quantity + qty, "updated_at": func.now()},
        )
        .returning(TeamWidgetInventory.quantity)
    )
    return int(s.exec(stmt).scalar_one())


def subtract_if_enough(s: Session, team_id: UUID, widget_id: str, qty: int) -> int | None:
    """quantity -= qty only if quantity >= qty. None = refused."""
    stmt = (
        update(TeamWidgetInventory)
        .where(
            col(TeamWidgetInventory.team_id) == team_id,
            col(TeamWidgetInventory.widget_id) == widget_id,
            TeamWidgetInventory.quantity >= qty,
        )
        .values(quantity=TeamWidgetInventory.quantity - qty, updated_at=func.now())
        .returning(TeamWidgetInventory.quantity)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def event_exists(s: Session, team_id: UUID, widget_id: str, ref_type: str, ref_id: str, kind: str) -> bool:
    stmt = select(InventoryEvent.id).where(
        InventoryEvent.team_id == team_id,
        InventoryEvent.widget_id == widget_id,
        InventoryEvent.ref_type == ref_type,
        InventoryEvent.ref_id == ref_id,
        InventoryEvent.kind == kind,
    )
    return s.exec(stmt).first() is not None


def insert_event(s: Session, ev: InventoryEvent) -> InventoryEvent:
    s.add(ev)
    try:
        s.flush()
    except IntegrityError as exc:
        if "uq_inv_event_team_widget_ref_kind" in str(exc.orig):
            raise DuplicateReference(ev.ref_type, ev.ref_id, ev.kind) from exc
        raise
    s.refresh(ev)
    return ev


def list_team_inventory(s: Session, team_id: UUID, include_zero: bool) -> list[TeamWidgetInventory]:
    """Inventory rows only. Widget names come from Module D's contract (we never read D's table)."""
    stmt = (
        select(TeamWidgetInventory)
        .where(TeamWidgetInventory.team_id == team_id)
        .order_by(col(TeamWidgetInventory.widget_id))
        .execution_options(populate_existing=True)
    )
    if not include_zero:
        stmt = stmt.where(TeamWidgetInventory.quantity > 0)
    return list(s.exec(stmt).all())


def event_sums(s: Session, team_id: UUID) -> dict[str, int]:
    stmt = (
        select(InventoryEvent.widget_id, func.sum(InventoryEvent.delta))
        .where(InventoryEvent.team_id == team_id)
        .group_by(col(InventoryEvent.widget_id))
    )
    return {w: int(total) for w, total in s.exec(stmt).all()}


def quantities(s: Session, team_id: UUID) -> dict[str, int]:
    stmt = select(TeamWidgetInventory.widget_id, TeamWidgetInventory.quantity).where(
        TeamWidgetInventory.team_id == team_id
    )
    return {w: int(q) for w, q in s.exec(stmt).all()}


def unit_counts(s: Session, team_ids: list[UUID]) -> dict[UUID, int]:
    """Total widget units per team in ONE query (Module K dashboard)."""
    if not team_ids:
        return {}
    stmt = (
        select(TeamWidgetInventory.team_id, func.sum(TeamWidgetInventory.quantity))
        .where(col(TeamWidgetInventory.team_id).in_(team_ids))
        .group_by(col(TeamWidgetInventory.team_id))
    )
    return {tid: int(total or 0) for tid, total in s.exec(stmt).all()}
