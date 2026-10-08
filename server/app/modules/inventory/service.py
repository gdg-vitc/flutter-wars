"""Module F business rules — the internal contract used by Modules C, I, J and K.

RULES FOR CALLERS:
  1. Pass your own Session. These functions NEVER commit; they only flush.
  2. If any function raises, roll back your whole transaction.
  3. Call inventory AFTER the ledger in the same transaction
     (lock order: operational_control -> market_listing -> team_wallet -> team_widget_inventory).
  4. Inventory does not check if a widget is ACTIVE or purchasable — Market/Catalog do that.
     It only refuses widgets that do not exist.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.modules import catalog
from app.modules.inventory import repository as repo
from app.modules.inventory.errors import (
    DuplicateReference,
    InsufficientQuantity,
    InvalidQuantity,
    InvalidReference,
    TeamNotFound,
    WidgetNotFound,
)
from app.modules.inventory.models import MAX_QUANTITY_STEP, InventoryEvent


@dataclass(frozen=True)
class InventoryItem:
    widget_id: str
    appdev_key: str
    display_name: str
    quantity: int
    archived: bool


@dataclass(frozen=True)
class InventoryMismatch:
    widget_id: str
    quantity: int
    event_sum: int


# ---------------------------------------------------------------- validation


def _check_qty(qty: object, *, allow_negative: bool = False) -> int:
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise InvalidQuantity(qty)
    if qty == 0 or abs(qty) > MAX_QUANTITY_STEP or (qty < 0 and not allow_negative):
        raise InvalidQuantity(qty)
    return qty


def _check_text(value: str, field: str, max_len: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise InvalidReference(field)


def _check_ref(ref_type: str, ref_id: str, reason: str, actor: str) -> None:
    _check_text(ref_type, "ref_type", 40)
    _check_text(ref_id, "ref_id", 100)
    _check_text(reason, "reason", 500)
    _check_text(actor, "actor", 200)


# ---------------------------------------------------------------- reads


def get_quantity(s: Session, team_id: UUID, widget_id: str) -> int:
    return repo.get_quantity(s, team_id, widget_id)


def get_team_inventory(s: Session, team_id: UUID, *, include_zero: bool = False) -> list[InventoryItem]:
    """Read contract for IDE Sync (Module C) and GET /inventory. Two queries for any team size:
    our rows + one batch lookup in the catalog (Module D).
    Archived widgets the team still owns ARE returned, flagged archived=True."""
    rows = repo.list_team_inventory(s, team_id, include_zero)
    widgets = catalog.get_widgets(s, [r.widget_id for r in rows])
    return [
        InventoryItem(
            widget_id=r.widget_id,
            appdev_key=widgets[r.widget_id].appdev_key,
            display_name=widgets[r.widget_id].display_name,
            quantity=r.quantity,
            archived=widgets[r.widget_id].archived,
        )
        for r in rows
    ]


def unit_counts(s: Session, team_ids: list[UUID]) -> dict[UUID, int]:
    """Total units owned per team, one query. Teams with nothing show 0."""
    found = repo.unit_counts(s, list(team_ids))
    return {tid: found.get(tid, 0) for tid in team_ids}


def verify_inventory(s: Session, team_id: UUID) -> list[InventoryMismatch]:
    """Empty list = consistent. Otherwise each widget whose quantity != Σ event deltas."""
    sums = repo.event_sums(s, team_id)
    qtys = repo.quantities(s, team_id)
    return [
        InventoryMismatch(widget_id=w, quantity=qtys.get(w, 0), event_sum=sums.get(w, 0))
        for w in sorted(set(sums) | set(qtys))
        if qtys.get(w, 0) != sums.get(w, 0)
    ]


# ---------------------------------------------------------------- mutations


def _change(
    s: Session,
    team_id: UUID,
    widget_id: str,
    delta: int,
    *,
    kind: str,
    ref_type: str,
    ref_id: str,
    reason: str,
    actor: str,
) -> InventoryEvent:
    if repo.event_exists(s, team_id, widget_id, ref_type, ref_id, kind):
        raise DuplicateReference(ref_type, ref_id, kind)
    if not catalog.widget_exists(s, widget_id):
        raise WidgetNotFound(widget_id)

    if delta > 0:
        if not repo.team_exists(s, team_id):
            raise TeamNotFound()
        new_qty = repo.add_quantity(s, team_id, widget_id, delta)
    else:
        new_qty = repo.subtract_if_enough(s, team_id, widget_id, -delta)
        if new_qty is None:
            raise InsufficientQuantity(owned=repo.get_quantity(s, team_id, widget_id), requested=-delta)

    return repo.insert_event(
        s,
        InventoryEvent(
            team_id=team_id,
            widget_id=widget_id,
            kind=kind,
            delta=delta,
            quantity_after=new_qty,
            ref_type=ref_type,
            ref_id=ref_id,
            reason=reason,
            actor=actor,
        ),
    )


def increment(
    s: Session, team_id: UUID, widget_id: str, qty: int, *, ref_type: str, ref_id: str, reason: str, actor: str
) -> InventoryEvent:
    """Purchase committed / auction won / mystery box. Called inside the caller's transaction."""
    _check_qty(qty)
    _check_ref(ref_type, ref_id, reason, actor)
    return _change(
        s, team_id, widget_id, qty, kind="INCREMENT", ref_type=ref_type, ref_id=ref_id, reason=reason, actor=actor
    )


def decrement(
    s: Session, team_id: UUID, widget_id: str, qty: int, *, ref_type: str, ref_id: str, reason: str, actor: str
) -> InventoryEvent:
    """Approved sale/resale. Fails with INSUFFICIENT_QUANTITY; never goes below 0."""
    _check_qty(qty)
    _check_ref(ref_type, ref_id, reason, actor)
    return _change(
        s, team_id, widget_id, -qty, kind="DECREMENT", ref_type=ref_type, ref_id=ref_id, reason=reason, actor=actor
    )


def admin_adjust(
    s: Session, team_id: UUID, widget_id: str, delta: int, *, reason: str, actor: str, idempotency_key: UUID
) -> InventoryEvent:
    """Organizer correction. Audited via actor + reason; same idempotency_key cannot apply twice."""
    _check_qty(delta, allow_negative=True)
    _check_ref("admin_adjustment", str(idempotency_key), reason, actor)
    return _change(
        s,
        team_id,
        widget_id,
        delta,
        kind="ADJUST",
        ref_type="admin_adjustment",
        ref_id=str(idempotency_key),
        reason=reason,
        actor=actor,
    )
