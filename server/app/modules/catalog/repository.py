"""Module D data access. SQL only — no business rules, no commits."""

from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.modules.catalog.models import Widget, WidgetStatus


def get(s: Session, widget_id: str) -> Widget | None:
    stmt = select(Widget).where(Widget.id == widget_id).execution_options(populate_existing=True)
    return s.exec(stmt).first()


def get_for_update(s: Session, widget_id: str) -> Widget | None:
    """Row lock: two organizers editing the same widget are serialized, so the version check is exact."""
    stmt = select(Widget).where(Widget.id == widget_id).with_for_update().execution_options(populate_existing=True)
    return s.exec(stmt).first()


def get_many(s: Session, widget_ids: list[str]) -> dict[str, Widget]:
    """Batch read for other modules (Inventory, Market, IDE sync): one query for any number of ids."""
    if not widget_ids:
        return {}
    stmt = select(Widget).where(col(Widget.id).in_(list(set(widget_ids)))).execution_options(populate_existing=True)
    return {w.id: w for w in s.exec(stmt).all()}


def id_exists(s: Session, widget_id: str) -> bool:
    return s.exec(select(Widget.id).where(Widget.id == widget_id)).first() is not None


def appdev_key_exists(s: Session, appdev_key: str) -> bool:
    return s.exec(select(Widget.id).where(Widget.appdev_key == appdev_key)).first() is not None


def list_widgets(s: Session, *, include_archived: bool) -> list[Widget]:
    stmt = select(Widget).order_by(col(Widget.category), col(Widget.display_name), col(Widget.id))
    if not include_archived:
        stmt = stmt.where(Widget.status == WidgetStatus.ACTIVE.value)
    return list(s.exec(stmt.execution_options(populate_existing=True)).all())


def insert(s: Session, w: Widget) -> Widget:
    """Raises IntegrityError on a duplicate id/appdev_key race (the service maps it to 409)."""
    with s.begin_nested():  # SAVEPOINT: the caller's transaction survives the race
        s.add(w)
        s.flush()
    s.refresh(w)
    return w


def save(s: Session, w: Widget, ts: datetime | None = None) -> Widget:
    """Bump version + timestamp and flush. Pass `ts` if you already read the clock."""
    with s.no_autoflush:  # never flush a half-updated row while reading the clock
        ts = ts or now(s)
    w.version += 1
    w.updated_at = ts
    s.add(w)
    s.flush()
    s.refresh(w)
    return w


def now(s: Session) -> datetime:
    """Database clock, so every module's timestamps agree."""
    return s.exec(select(func.now())).one()
