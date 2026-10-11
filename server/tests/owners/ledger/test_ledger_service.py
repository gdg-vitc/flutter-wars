import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.ledger import service as ledger
from app.modules.ledger.errors import (
    DuplicateReference,
    InsufficientCredits,
    InvalidAmount,
    ReservationNotActive,
    TeamNotFound,
    WalletNotFound,
)
from app.modules.ledger.service import list_ledger

ACTOR = "test"


def _fund(db, team, amount=120):
    ledger.grant_initial(db, team, amount, actor=ACTOR)
    db.commit()


def test_initial_grant_creates_wallet_and_entry(db, make_team):
    t = make_team()
    e = ledger.grant_initial(db, t, 120, actor=ACTOR)
    db.commit()
    assert e.kind == "GRANT" and e.amount == 120 and e.balance_after == 120
    w = ledger.get_wallet(db, t)
    assert (w.balance, w.held, w.available) == (120, 0, 120)


def test_initial_grant_only_once(db, make_team):
    t = make_team()
    _fund(db, t)
    with pytest.raises(DuplicateReference):
        ledger.grant_initial(db, t, 120, actor=ACTOR)


def test_unknown_team_cannot_be_credited(db):
    with pytest.raises(TeamNotFound):
        ledger.grant_initial(db, uuid.uuid4(), 10, actor=ACTOR)


def test_wallet_of_unknown_team_reads_as_zero(db):
    w = ledger.get_wallet(db, uuid.uuid4())
    assert (w.balance, w.held, w.available) == (0, 0, 0)


def test_debit_reduces_balance(db, make_team):
    t = make_team()
    _fund(db, t)
    e = ledger.debit(db, t, 30, ref_type="purchase", ref_id="trade-1", reason="Buy Row", actor=ACTOR)
    db.commit()
    assert e.amount == -30 and e.balance_after == 90
    assert ledger.get_wallet(db, t).balance == 90


def test_debit_insufficient_changes_nothing(db, make_team):
    t = make_team()
    _fund(db, t, 20)
    with pytest.raises(InsufficientCredits) as ei:
        ledger.debit(db, t, 21, ref_type="purchase", ref_id="trade-x", reason="too much", actor=ACTOR)
    assert ei.value.details == {"available": 20, "requested": 21}
    db.rollback()
    assert ledger.get_wallet(db, t).balance == 20


def test_debit_without_wallet(db, make_team):
    with pytest.raises(WalletNotFound):
        ledger.debit(db, make_team(), 1, ref_type="purchase", ref_id="t", reason="r", actor=ACTOR)


def test_same_reference_cannot_charge_twice(db, make_team):
    t = make_team()
    _fund(db, t)
    ledger.debit(db, t, 10, ref_type="purchase", ref_id="trade-dup", reason="r", actor=ACTOR)
    db.commit()
    with pytest.raises(DuplicateReference):
        ledger.debit(db, t, 10, ref_type="purchase", ref_id="trade-dup", reason="r", actor=ACTOR)
    db.rollback()
    assert ledger.get_wallet(db, t).balance == 110


def test_same_reference_different_team_is_fine(db, make_team):
    a, b = make_team(), make_team()
    _fund(db, a)
    _fund(db, b)
    ledger.debit(db, a, 10, ref_type="auction", ref_id="auc-1", reason="r", actor=ACTOR)
    ledger.debit(db, b, 10, ref_type="auction", ref_id="auc-1", reason="r", actor=ACTOR)
    db.commit()


@pytest.mark.parametrize("bad", [0, -5, 1.5, 10.0, "10", True, None, 1_000_001])
def test_invalid_amounts_rejected(db, make_team, bad):
    t = make_team()
    _fund(db, t)
    with pytest.raises(InvalidAmount):
        ledger.debit(db, t, bad, ref_type="purchase", ref_id="x", reason="r", actor=ACTOR)  # type: ignore[arg-type]


def test_admin_adjust_positive_and_negative(db, make_team):
    t = make_team()
    _fund(db, t, 50)
    ledger.admin_adjust(db, t, 25, reason="challenge reward", actor="org", idempotency_key=uuid.uuid4())
    ledger.admin_adjust(db, t, -5, reason="correction", actor="org", idempotency_key=uuid.uuid4())
    db.commit()
    assert ledger.get_wallet(db, t).balance == 70


def test_admin_adjust_double_click_blocked(db, make_team):
    t = make_team()
    key = uuid.uuid4()
    ledger.admin_adjust(db, t, 25, reason="reward", actor="org", idempotency_key=key)
    db.commit()
    with pytest.raises(DuplicateReference):
        ledger.admin_adjust(db, t, 25, reason="reward", actor="org", idempotency_key=key)


def test_compensating_entry_keeps_history(db, make_team):
    t = make_team()
    _fund(db, t)
    ledger.admin_adjust(db, t, 100, reason="mistyped grant", actor="org", idempotency_key=uuid.uuid4())
    ledger.admin_adjust(db, t, -100, reason="undo mistyped grant", actor="org", idempotency_key=uuid.uuid4())
    db.commit()
    rows, _ = list_ledger(db, t)
    assert [r.amount for r in rows] == [-100, 100, 120]
    assert ledger.verify_wallet(db, t).ok


# ---------------------------------------------------------------- holds


def test_reserve_blocks_debit_of_held_credits(db, make_team):
    t = make_team()
    _fund(db, t, 100)
    ledger.reserve(db, t, 80, ref_type="auction_bid", ref_id="bid-1")
    db.commit()
    w = ledger.get_wallet(db, t)
    assert (w.balance, w.held, w.available) == (100, 80, 20)
    with pytest.raises(InsufficientCredits):
        ledger.debit(db, t, 30, ref_type="purchase", ref_id="p", reason="r", actor=ACTOR)


def test_reserve_more_than_available(db, make_team):
    t = make_team()
    _fund(db, t, 10)
    with pytest.raises(InsufficientCredits):
        ledger.reserve(db, t, 11, ref_type="auction_bid", ref_id="bid-big")


def test_release_restores_available(db, make_team):
    t = make_team()
    _fund(db, t, 100)
    r = ledger.reserve(db, t, 60, ref_type="auction_bid", ref_id="bid-2")
    db.commit()
    ledger.release(db, r.id)
    db.commit()
    assert ledger.get_wallet(db, t).available == 100
    with pytest.raises(ReservationNotActive):
        ledger.release(db, r.id)


def test_capture_full_and_cannot_repeat(db, make_team):
    t = make_team()
    _fund(db, t, 100)
    r = ledger.reserve(db, t, 40, ref_type="auction_bid", ref_id="bid-3")
    e = ledger.capture(db, r.id, ref_type="auction", ref_id="auc-3", reason="won CustomPaint", actor="system")
    db.commit()
    assert e.kind == "CAPTURE" and e.amount == -40 and e.balance_after == 60
    w = ledger.get_wallet(db, t)
    assert (w.balance, w.held) == (60, 0)
    with pytest.raises(ReservationNotActive):
        ledger.capture(db, r.id, ref_type="auction", ref_id="auc-3", reason="again", actor="system")


def test_partial_capture_releases_remainder(db, make_team):
    t = make_team()
    _fund(db, t, 100)
    r = ledger.reserve(db, t, 50, ref_type="auction_bid", ref_id="bid-4")
    ledger.capture(db, r.id, ref_type="auction", ref_id="auc-4", reason="second price", actor="system", amount=35)
    db.commit()
    w = ledger.get_wallet(db, t)
    assert (w.balance, w.held, w.available) == (65, 0, 65)
    assert ledger.verify_wallet(db, t).ok


def test_same_active_reservation_twice_rejected(db, make_team):
    t = make_team()
    _fund(db, t)
    ledger.reserve(db, t, 10, ref_type="auction_bid", ref_id="bid-5")
    with pytest.raises(DuplicateReference):
        ledger.reserve(db, t, 10, ref_type="auction_bid", ref_id="bid-5")


# ---------------------------------------------------------------- database guarantees


def test_ledger_rows_cannot_be_updated_or_deleted(db, make_team):
    t = make_team()
    _fund(db, t)
    with pytest.raises(DBAPIError, match="append-only"):
        db.exec(text("UPDATE credit_ledger_entry SET amount = 999999 WHERE team_id = :t"), params={"t": t})
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.exec(text("DELETE FROM credit_ledger_entry WHERE team_id = :t"), params={"t": t})


def test_database_refuses_negative_balance_even_if_code_is_wrong(db, make_team):
    t = make_team()
    _fund(db, t, 5)
    with pytest.raises(DBAPIError, match="ck_wallet_balance_nonneg"):
        db.exec(text("UPDATE team_wallet SET balance = -1 WHERE team_id = :t"), params={"t": t})


def test_rollback_after_debit_leaves_no_trace(db, make_team):
    t = make_team()
    _fund(db, t, 50)
    ledger.debit(db, t, 20, ref_type="purchase", ref_id="rolled", reason="r", actor=ACTOR)
    db.rollback()
    assert ledger.get_wallet(db, t).balance == 50
    assert ledger.verify_wallet(db, t).ok
