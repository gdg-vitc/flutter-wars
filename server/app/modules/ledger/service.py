"""Module E business rules — the internal contract used by Modules I, J and K.

RULES FOR CALLERS (put these in the integration note):
  1. Pass your own Session. These functions NEVER commit; they only flush.
  2. If any function raises, roll back your whole transaction.
  3. Lock order across modules: operational_control -> market_listing -> team_wallet -> team_widget_inventory.
  4. `ref_type` + `ref_id` identify YOUR business operation (e.g. "purchase", <trade_id>).
     The same (team, ref_type, ref_id, kind) can only ever be applied once.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.modules.ledger import repository as repo
from app.modules.ledger.errors import (
    DuplicateReference,
    InsufficientCredits,
    InvalidAmount,
    InvalidReference,
    LedgerInvariantError,
    ReservationNotActive,
    ReservationNotFound,
    TeamNotFound,
    WalletNotFound,
)
from app.modules.ledger.models import MAX_AMOUNT, CreditLedgerEntry, CreditReservation


@dataclass(frozen=True)
class WalletView:
    team_id: UUID
    balance: int
    held: int
    available: int


@dataclass(frozen=True)
class WalletCheck:
    team_id: UUID
    balance: int
    held: int
    ledger_sum: int
    active_reserved: int
    ok: bool


# ---------------------------------------------------------------- validation


def _check_amount(amount: object, *, allow_negative: bool = False) -> int:
    # bool is a subclass of int in Python — reject it explicitly. Floats are rejected too.
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InvalidAmount(amount)
    if amount == 0 or abs(amount) > MAX_AMOUNT or (amount < 0 and not allow_negative):
        raise InvalidAmount(amount)
    return amount


def _check_text(value: str, field: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise InvalidReference(field)
    return value


def _check_ref(ref_type: str, ref_id: str, reason: str | None = None, actor: str | None = None) -> None:
    _check_text(ref_type, "ref_type", 40)
    _check_text(ref_id, "ref_id", 100)
    if reason is not None:
        _check_text(reason, "reason", 500)
    if actor is not None:
        _check_text(actor, "actor", 200)


# ---------------------------------------------------------------- reads


def get_wallet(s: Session, team_id: UUID) -> WalletView:
    w = repo.get_wallet(s, team_id)
    if w is None:
        return WalletView(team_id=team_id, balance=0, held=0, available=0)
    return WalletView(team_id=team_id, balance=w.balance, held=w.held, available=w.balance - w.held)


def get_wallets(s: Session, team_ids: list[UUID]) -> dict[UUID, WalletView]:
    """Many wallets in ONE query (Module K dashboard). Teams without a wallet show zeros."""
    found = repo.get_wallets(s, list(team_ids))
    out: dict[UUID, WalletView] = {}
    for tid in team_ids:
        w = found.get(tid)
        out[tid] = (
            WalletView(team_id=tid, balance=w.balance, held=w.held, available=w.balance - w.held)
            if w
            else WalletView(team_id=tid, balance=0, held=0, available=0)
        )
    return out


def list_ledger(
    s: Session, team_id: UUID, *, cursor: int | None = None, limit: int = 50
) -> tuple[list[CreditLedgerEntry], int | None]:
    limit = max(1, min(limit, 100))
    rows = repo.list_entries(s, team_id, cursor, limit)
    next_cursor = rows[-1].id if len(rows) == limit else None
    return rows, next_cursor


def verify_wallet(s: Session, team_id: UUID) -> WalletCheck:
    w = repo.get_wallet(s, team_id)
    balance = w.balance if w else 0
    held = w.held if w else 0
    total = repo.ledger_sum(s, team_id)
    reserved = repo.active_reserved_sum(s, team_id)
    return WalletCheck(team_id, balance, held, total, reserved, ok=(total == balance and reserved == held))


# ---------------------------------------------------------------- movements


def _apply(
    s: Session,
    team_id: UUID,
    delta: int,
    *,
    kind: str,
    ref_type: str,
    ref_id: str,
    reason: str,
    actor: str,
) -> CreditLedgerEntry:
    """Single code path for every balance change: duplicate check -> locked update -> ledger row."""
    if repo.entry_exists(s, team_id, ref_type, ref_id, kind):
        raise DuplicateReference(ref_type, ref_id, kind)

    if delta > 0:
        if not repo.team_exists(s, team_id):
            raise TeamNotFound()
        repo.ensure_wallet(s, team_id)
        new_balance = repo.add_to_balance(s, team_id, delta)
        if new_balance is None:
            raise LedgerInvariantError("wallet missing after ensure_wallet")
    else:
        new_balance = repo.subtract_if_available(s, team_id, -delta)
        if new_balance is None:
            w = repo.get_wallet(s, team_id)
            if w is None:
                raise WalletNotFound()
            raise InsufficientCredits(available=w.balance - w.held, requested=-delta)

    return repo.insert_entry(
        s,
        CreditLedgerEntry(
            team_id=team_id,
            kind=kind,
            amount=delta,
            balance_after=new_balance,
            ref_type=ref_type,
            ref_id=ref_id,
            reason=reason,
            actor=actor,
        ),
    )


def credit(
    s: Session, team_id: UUID, amount: int, *, ref_type: str, ref_id: str, reason: str, actor: str
) -> CreditLedgerEntry:
    """Add credits (sale refund, auction refund, reward). Creates the wallet if needed."""
    _check_amount(amount)
    _check_ref(ref_type, ref_id, reason, actor)
    return _apply(s, team_id, amount, kind="CREDIT", ref_type=ref_type, ref_id=ref_id, reason=reason, actor=actor)


def debit(
    s: Session, team_id: UUID, amount: int, *, ref_type: str, ref_id: str, reason: str, actor: str
) -> CreditLedgerEntry:
    """Remove credits. Fails with INSUFFICIENT_CREDITS if balance - held < amount."""
    _check_amount(amount)
    _check_ref(ref_type, ref_id, reason, actor)
    return _apply(s, team_id, -amount, kind="DEBIT", ref_type=ref_type, ref_id=ref_id, reason=reason, actor=actor)


def grant_initial(s: Session, team_id: UUID, amount: int, *, actor: str) -> CreditLedgerEntry:
    """Starting credits. Can only ever happen once per team (ref = initial_grant/<team_id>)."""
    _check_amount(amount)
    _check_ref("initial_grant", str(team_id), "Initial credit grant", actor)
    return _apply(
        s,
        team_id,
        amount,
        kind="GRANT",
        ref_type="initial_grant",
        ref_id=str(team_id),
        reason="Initial credit grant",
        actor=actor,
    )


def admin_adjust(
    s: Session, team_id: UUID, amount: int, *, reason: str, actor: str, idempotency_key: UUID
) -> CreditLedgerEntry:
    """Organizer correction/award. Positive adds, negative removes (still cannot go below held).
    Reusing the same idempotency_key is rejected, so a double click never grants twice."""
    _check_amount(amount, allow_negative=True)
    _check_ref("admin_adjustment", str(idempotency_key), reason, actor)
    return _apply(
        s,
        team_id,
        amount,
        kind="ADJUST",
        ref_type="admin_adjustment",
        ref_id=str(idempotency_key),
        reason=reason,
        actor=actor,
    )


# ---------------------------------------------------------------- holds (auctions)


def reserve(s: Session, team_id: UUID, amount: int, *, ref_type: str, ref_id: str) -> CreditReservation:
    """Hold credits for a bid. Held credits cannot be spent by debit()."""
    _check_amount(amount)
    _check_ref(ref_type, ref_id)
    if repo.active_reservation_exists(s, team_id, ref_type, ref_id):
        raise DuplicateReference(ref_type, ref_id, "RESERVE")
    if repo.hold_if_available(s, team_id, amount) is None:
        w = repo.get_wallet(s, team_id)
        if w is None:
            raise WalletNotFound()
        raise InsufficientCredits(available=w.balance - w.held, requested=amount)
    return repo.insert_reservation(
        s, CreditReservation(team_id=team_id, amount=amount, ref_type=ref_type, ref_id=ref_id)
    )


def release(s: Session, reservation_id: UUID) -> CreditReservation:
    """Give held credits back (outbid, auction cancelled). No ledger row: nothing moved."""
    res = repo.get_reservation_for_update(s, reservation_id)
    if res is None:
        raise ReservationNotFound()
    if res.status != "ACTIVE":
        raise ReservationNotActive(res.status)
    if repo.unhold(s, res.team_id, res.amount) is None:
        raise LedgerInvariantError("held < reservation amount")
    return repo.close_reservation(s, res, "RELEASED")


def capture(
    s: Session,
    reservation_id: UUID,
    *,
    ref_type: str,
    ref_id: str,
    reason: str,
    actor: str,
    amount: int | None = None,
) -> CreditLedgerEntry:
    """Auction won: turn the hold into a real debit. `amount` (<= reserved) lets an auction charge
    less than was held (e.g. second-price); the rest of the hold is released in the same step."""
    _check_ref(ref_type, ref_id, reason, actor)
    res = repo.get_reservation_for_update(s, reservation_id)
    if res is None:
        raise ReservationNotFound()
    if res.status != "ACTIVE":
        raise ReservationNotActive(res.status)
    charge = res.amount if amount is None else _check_amount(amount)
    if charge > res.amount:
        raise InvalidAmount(amount)
    if repo.entry_exists(s, res.team_id, ref_type, ref_id, "CAPTURE"):
        raise DuplicateReference(ref_type, ref_id, "CAPTURE")

    new_balance = repo.unhold_and_subtract(s, res.team_id, res.amount, charge)
    if new_balance is None:
        raise LedgerInvariantError("capture would break held/balance invariant")
    repo.close_reservation(s, res, "CAPTURED")
    return repo.insert_entry(
        s,
        CreditLedgerEntry(
            team_id=res.team_id,
            kind="CAPTURE",
            amount=-charge,
            balance_after=new_balance,
            ref_type=ref_type,
            ref_id=ref_id,
            reason=reason,
            actor=actor,
        ),
    )
