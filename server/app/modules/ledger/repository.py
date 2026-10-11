"""Module E data access. SQL only — no business rules, no commits."""

from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.modules.ledger.errors import DuplicateReference
from app.modules.ledger.models import CreditLedgerEntry, CreditReservation, TeamWallet


def get_wallet(s: Session, team_id: UUID) -> TeamWallet | None:
    # populate_existing: never return a stale object cached earlier in this session
    stmt = select(TeamWallet).where(TeamWallet.team_id == team_id).execution_options(populate_existing=True)
    return s.exec(stmt).first()


def get_wallets(s: Session, team_ids: list[UUID]) -> dict[UUID, TeamWallet]:
    """Batch read for the organizer dashboard: one query for any number of teams (no N+1)."""
    if not team_ids:
        return {}
    stmt = select(TeamWallet).where(col(TeamWallet.team_id).in_(team_ids)).execution_options(populate_existing=True)
    return {w.team_id: w for w in s.exec(stmt).all()}


def ensure_wallet(s: Session, team_id: UUID) -> None:
    s.exec(pg_insert(TeamWallet).values(team_id=team_id).on_conflict_do_nothing(index_elements=["team_id"]))


def add_to_balance(s: Session, team_id: UUID, amount: int) -> int | None:
    """balance += amount (amount > 0). Returns new balance, or None if the wallet does not exist."""
    stmt = (
        update(TeamWallet)
        .where(col(TeamWallet.team_id) == team_id)
        .values(balance=TeamWallet.balance + amount, updated_at=func.now())
        .returning(TeamWallet.balance)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def subtract_if_available(s: Session, team_id: UUID, amount: int) -> int | None:
    """balance -= amount only if (balance - held) >= amount. Row-locks the wallet. None = refused."""
    stmt = (
        update(TeamWallet)
        .where(
            col(TeamWallet.team_id) == team_id,
            (TeamWallet.balance - TeamWallet.held) >= amount,
        )
        .values(balance=TeamWallet.balance - amount, updated_at=func.now())
        .returning(TeamWallet.balance)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def hold_if_available(s: Session, team_id: UUID, amount: int) -> int | None:
    """held += amount only if (balance - held) >= amount. None = refused."""
    stmt = (
        update(TeamWallet)
        .where(
            col(TeamWallet.team_id) == team_id,
            (TeamWallet.balance - TeamWallet.held) >= amount,
        )
        .values(held=TeamWallet.held + amount, updated_at=func.now())
        .returning(TeamWallet.held)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def unhold(s: Session, team_id: UUID, amount: int) -> int | None:
    stmt = (
        update(TeamWallet)
        .where(col(TeamWallet.team_id) == team_id, TeamWallet.held >= amount)
        .values(held=TeamWallet.held - amount, updated_at=func.now())
        .returning(TeamWallet.held)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def unhold_and_subtract(s: Session, team_id: UUID, held_amount: int, debit_amount: int) -> int | None:
    """Capture: release the whole hold and debit (part of) it, atomically."""
    stmt = (
        update(TeamWallet)
        .where(
            col(TeamWallet.team_id) == team_id,
            TeamWallet.held >= held_amount,
            TeamWallet.balance >= debit_amount,
        )
        .values(
            held=TeamWallet.held - held_amount,
            balance=TeamWallet.balance - debit_amount,
            updated_at=func.now(),
        )
        .returning(TeamWallet.balance)
        .execution_options(synchronize_session=False)
    )
    return s.exec(stmt).scalar_one_or_none()


def entry_exists(s: Session, team_id: UUID, ref_type: str, ref_id: str, kind: str) -> bool:
    stmt = select(CreditLedgerEntry.id).where(
        CreditLedgerEntry.team_id == team_id,
        CreditLedgerEntry.ref_type == ref_type,
        CreditLedgerEntry.ref_id == ref_id,
        CreditLedgerEntry.kind == kind,
    )
    return s.exec(stmt).first() is not None


def insert_entry(s: Session, entry: CreditLedgerEntry) -> CreditLedgerEntry:
    s.add(entry)
    try:
        s.flush()
    except IntegrityError as exc:
        # A concurrent transaction committed the same reference first (race past the pre-check).
        if "uq_ledger_team_ref_kind" in str(exc.orig):
            raise DuplicateReference(entry.ref_type, entry.ref_id, entry.kind) from exc
        raise
    s.refresh(entry)
    return entry


def list_entries(s: Session, team_id: UUID, before_id: int | None, limit: int) -> list[CreditLedgerEntry]:
    stmt = select(CreditLedgerEntry).where(CreditLedgerEntry.team_id == team_id)
    if before_id is not None:
        stmt = stmt.where(col(CreditLedgerEntry.id) < before_id)
    stmt = stmt.order_by(col(CreditLedgerEntry.id).desc()).limit(limit)
    return list(s.exec(stmt).all())


def ledger_sum(s: Session, team_id: UUID) -> int:
    stmt = select(func.coalesce(func.sum(CreditLedgerEntry.amount), 0)).where(CreditLedgerEntry.team_id == team_id)
    return int(s.exec(stmt).one())


def active_reserved_sum(s: Session, team_id: UUID) -> int:
    stmt = select(func.coalesce(func.sum(CreditReservation.amount), 0)).where(
        CreditReservation.team_id == team_id, CreditReservation.status == "ACTIVE"
    )
    return int(s.exec(stmt).one())


def active_reservation_exists(s: Session, team_id: UUID, ref_type: str, ref_id: str) -> bool:
    stmt = select(CreditReservation.id).where(
        CreditReservation.team_id == team_id,
        CreditReservation.ref_type == ref_type,
        CreditReservation.ref_id == ref_id,
        CreditReservation.status == "ACTIVE",
    )
    return s.exec(stmt).first() is not None


def get_active_reservation(
    s: Session, team_id: UUID, ref_type: str, ref_id: str
) -> CreditReservation | None:
    stmt = (
        select(CreditReservation)
        .where(
            CreditReservation.team_id == team_id,
            CreditReservation.ref_type == ref_type,
            CreditReservation.ref_id == ref_id,
            CreditReservation.status == "ACTIVE",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return s.exec(stmt).first()


def lock_wallets(s: Session, team_ids: list[UUID]) -> list[UUID]:
    """FOR UPDATE on each team's wallet in sorted team_id order. Returns the ids that have one."""
    stmt = (
        select(TeamWallet.team_id)
        .where(col(TeamWallet.team_id).in_(sorted(set(team_ids))))
        .order_by(col(TeamWallet.team_id))
        .with_for_update()
    )
    return list(s.exec(stmt).all())


def insert_reservation(s: Session, res: CreditReservation) -> CreditReservation:
    s.add(res)
    try:
        s.flush()
    except IntegrityError as exc:
        if "uq_reservation_active_ref" in str(exc.orig):
            raise DuplicateReference(res.ref_type, res.ref_id, "RESERVE") from exc
        raise
    s.refresh(res)
    return res


def get_reservation_for_update(s: Session, reservation_id: UUID) -> CreditReservation | None:
    stmt = (
        select(CreditReservation)
        .where(CreditReservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return s.exec(stmt).first()


def close_reservation(s: Session, res: CreditReservation, status: str) -> CreditReservation:
    with s.no_autoflush:  # read the clock before changing the row
        ts = s.exec(select(func.now())).one()
    res.status = status
    res.closed_at = ts
    s.add(res)
    s.flush()
    return res
