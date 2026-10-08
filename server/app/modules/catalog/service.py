"""Module D business rules — the internal contract used by Modules F, G, H, I, J, C and K.

RULES FOR CALLERS (same as E and F):
  1. Pass your own Session. These functions NEVER commit; they only flush.
  2. If any function raises, roll back your whole transaction.
  3. Widgets are never deleted. `get_widget` resolves archived widgets too (history must stay
     readable); `require_active_widget` is what Market/Purchase/Auction use before selling one.
  4. Catalog owns NO price, stock or ownership (spec §6). Market (G), Pricing (H) and Inventory (F) do.
"""

import re
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.modules.catalog import repository as repo
from app.modules.catalog.errors import (
    FieldImmutable,
    InvalidWidgetData,
    VersionConflict,
    WidgetArchived,
    WidgetDuplicate,
    WidgetNotArchived,
    WidgetNotFound,
)
from app.modules.catalog.models import WIDGET_ID_PATTERN, Widget, WidgetStatus

EDITABLE_FIELDS = frozenset({"display_name", "description", "category", "flutter_classes", "internal_notes"})
IMMUTABLE_FIELDS = ("id", "appdev_key")
NOT_NULL_FIELDS = frozenset({"display_name", "category", "flutter_classes"})


# ---------------------------------------------------------------- reads


def get_widget(s: Session, widget_id: str) -> Widget:
    """Any status. Use this to RESOLVE a widget referenced by history (trades, ledger, inventory)."""
    w = repo.get(s, widget_id)
    if w is None:
        raise WidgetNotFound(widget_id)
    return w


def require_active_widget(s: Session, widget_id: str) -> Widget:
    """Use this before SELLING/LISTING a widget (Modules G, I, J). Archived -> 409 WIDGET_ARCHIVED."""
    w = get_widget(s, widget_id)
    if w.archived:
        raise WidgetArchived(widget_id)
    return w


def get_widgets(s: Session, widget_ids: list[str]) -> dict[str, Widget]:
    """Batch resolve (one query), archived included. Missing ids are simply absent from the dict."""
    return repo.get_many(s, widget_ids)


def widget_exists(s: Session, widget_id: str) -> bool:
    return repo.id_exists(s, widget_id)


def list_widgets(s: Session, *, include_archived: bool = False) -> list[Widget]:
    return repo.list_widgets(s, include_archived=include_archived)


# ---------------------------------------------------------------- organizer changes


def create_widget(
    s: Session,
    *,
    widget_id: str,
    appdev_key: str,
    display_name: str,
    category: str,
    description: str | None = None,
    flutter_classes: list[str] | None = None,
    internal_notes: str | None = None,
) -> Widget:
    if not re.fullmatch(WIDGET_ID_PATTERN, widget_id):
        raise InvalidWidgetData("widget id must match ^[a-z][a-z0-9_]{1,39}$")
    # Friendly pre-checks; the UNIQUE constraints below are the real guarantee under races.
    if repo.id_exists(s, widget_id):
        raise WidgetDuplicate("id")
    if repo.appdev_key_exists(s, appdev_key):
        raise WidgetDuplicate("appdev_key")
    w = Widget(
        id=widget_id,
        appdev_key=appdev_key,
        display_name=display_name,
        description=description,
        category=category,
        flutter_classes=list(flutter_classes or []),
        internal_notes=internal_notes,  # organizer's own notes; WHO did it goes to the audit log
    )
    try:
        return repo.insert(s, w)
    except IntegrityError as e:
        field = "appdev_key" if "appdev_key" in str(e.orig) else "id"
        raise WidgetDuplicate(field) from e


def update_widget(
    s: Session, widget_id: str, changes: dict[str, Any], *, expected_version: int
) -> tuple[Widget, dict[str, Any]]:
    """Edit metadata. Returns (widget, {field: [old, new]}) — the diff is what K writes to the audit log.

    id/appdev_key -> 422 FIELD_IMMUTABLE. Archived widgets can't be edited (restore first).
    Renaming is safe for history: trades/ledger/inventory store widget_id, never the name."""
    for f in IMMUTABLE_FIELDS:
        if f in changes:
            raise FieldImmutable(f)
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise InvalidWidgetData(f"unknown field(s): {', '.join(sorted(unknown))}")
    for f in NOT_NULL_FIELDS & set(changes):
        if changes[f] is None:
            raise InvalidWidgetData(f"{f} cannot be null")

    w = repo.get_for_update(s, widget_id)  # row lock: concurrent edits are serialized
    if w is None:
        raise WidgetNotFound(widget_id)
    if w.version != expected_version:
        raise VersionConflict(w.version)
    if w.archived:
        raise WidgetArchived(widget_id)

    diff = {f: [getattr(w, f), v] for f, v in changes.items() if getattr(w, f) != v}
    if not diff:
        return w, {}  # nothing changed: no version bump, no audit row
    for f, (_old, new) in diff.items():
        setattr(w, f, new)
    return repo.save(s, w), diff


def archive_widget(s: Session, widget_id: str, *, expected_version: int) -> Widget:
    """Stop future listing/sale. History and team ownership are untouched (spec §6)."""
    w = repo.get_for_update(s, widget_id)
    if w is None:
        raise WidgetNotFound(widget_id)
    if w.version != expected_version:
        raise VersionConflict(w.version)
    if w.archived:
        raise WidgetArchived(widget_id)
    ts = repo.now(s)  # read the clock BEFORE changing the row (a query would autoflush a half-updated row)
    w.status = WidgetStatus.ARCHIVED.value
    w.archived_at = ts
    return repo.save(s, w, ts)


def restore_widget(s: Session, widget_id: str, *, expected_version: int) -> Widget:
    """Undo an accidental archive (spec §13 edge case)."""
    w = repo.get_for_update(s, widget_id)
    if w is None:
        raise WidgetNotFound(widget_id)
    if w.version != expected_version:
        raise VersionConflict(w.version)
    if not w.archived:
        raise WidgetNotArchived(widget_id)
    w.status = WidgetStatus.ACTIVE.value
    w.archived_at = None
    return repo.save(s, w)
