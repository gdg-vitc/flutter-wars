"""Module K data access. SQL only — no business rules, no commits."""

from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.modules.admin.models import AdminActionLog, OperationalControl, Organizer

# ---------------- organizers


def get_active_organizer_by_email(s: Session, email: str) -> Organizer | None:
    stmt = select(Organizer).where(Organizer.email == email.lower(), col(Organizer.active).is_(True))
    return s.exec(stmt).first()


def get_organizer_for_update(s: Session, organizer_id: UUID) -> Organizer | None:
    stmt = (
        select(Organizer)
        .where(Organizer.id == organizer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return s.exec(stmt).first()


def email_exists(s: Session, email: str) -> bool:
    return s.exec(select(Organizer.id).where(Organizer.email == email.lower())).first() is not None


def list_organizers(s: Session) -> list[Organizer]:
    return list(s.exec(select(Organizer).order_by(col(Organizer.email))).all())


def lock_active_owners(s: Session) -> list[Organizer]:
    """Locks every active OWNER row, so two owners can't demote each other at the same moment."""
    stmt = (
        select(Organizer)
        .where(Organizer.role == "OWNER", col(Organizer.active).is_(True))
        .order_by(col(Organizer.id))
        .with_for_update()
    )
    return list(s.exec(stmt).all())


def add_organizer(s: Session, org: Organizer) -> Organizer:
    s.add(org)
    s.flush()
    s.refresh(org)
    return org


def save_organizer(s: Session, org: Organizer) -> Organizer:
    with s.no_autoflush:  # read the clock without flushing a half-updated row
        ts = s.exec(select(func.now())).one()
    org.version += 1
    org.updated_at = ts
    s.add(org)
    s.flush()
    return org


# ---------------- audit


def insert_audit(s: Session, row: AdminActionLog) -> AdminActionLog:
    s.add(row)
    s.flush()
    s.refresh(row)
    return row


def list_audit(
    s: Session,
    *,
    before_id: int | None,
    limit: int,
    action: str | None,
    actor_email: str | None,
    target_type: str | None,
    target_id: str | None,
) -> list[AdminActionLog]:
    stmt = select(AdminActionLog)
    if before_id is not None:
        stmt = stmt.where(col(AdminActionLog.id) < before_id)
    if action:
        stmt = stmt.where(AdminActionLog.action == action)
    if actor_email:
        stmt = stmt.where(AdminActionLog.actor_email == actor_email.lower())
    if target_type:
        stmt = stmt.where(AdminActionLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AdminActionLog.target_id == target_id)
    stmt = stmt.order_by(col(AdminActionLog.id).desc()).limit(limit)
    return list(s.exec(stmt).all())


# ---------------- operational controls


def list_controls(s: Session) -> list[OperationalControl]:
    stmt = select(OperationalControl).order_by(col(OperationalControl.scope)).execution_options(populate_existing=True)
    return list(s.exec(stmt).all())


def get_control_for_update(s: Session, scope: str) -> OperationalControl | None:
    stmt = (
        select(OperationalControl)
        .where(OperationalControl.scope == scope)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return s.exec(stmt).first()


def frozen_controls_for_share(s: Session, scopes: list[str]) -> list[OperationalControl]:
    """FOR SHARE: many purchases can hold this at once; a freeze (FOR UPDATE) waits for them to finish."""
    stmt = (
        select(OperationalControl)
        .where(col(OperationalControl.scope).in_(scopes))
        .order_by(col(OperationalControl.scope))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return [c for c in s.exec(stmt).all() if c.frozen]


def save_control(s: Session, ctl: OperationalControl) -> OperationalControl:
    with s.no_autoflush:
        ts = s.exec(select(func.now())).one()
    ctl.version += 1
    ctl.changed_at = ts
    s.add(ctl)
    s.flush()
    return ctl
