"""Many sessions hitting the same wallet at the same moment (one thread = one request)."""

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session

from app.core.db import get_engine
from app.core.errors import AppError
from app.modules.ledger import service as ledger


def _run_parallel(n: int, fn) -> list[str]:
    barrier = threading.Barrier(n)

    def worker(i: int) -> str:
        barrier.wait()  # release all threads together
        with Session(get_engine()) as s:
            try:
                fn(s, i)
                s.commit()
                return "ok"
            except AppError as e:
                s.rollback()
                return e.code

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))


def _funded(make_team, amount):
    t = make_team()
    with Session(get_engine()) as s:
        ledger.grant_initial(s, t, amount, actor="test")
        s.commit()
    return t


def test_twenty_parallel_debits_never_overspend(make_team):
    t = _funded(make_team, 100)
    results = _run_parallel(
        20,
        lambda s, i: ledger.debit(s, t, 10, ref_type="purchase", ref_id=f"p-{i}", reason="r", actor="t"),
    )
    assert results.count("ok") == 10
    assert results.count("INSUFFICIENT_CREDITS") == 10
    with Session(get_engine()) as s:
        w = ledger.get_wallet(s, t)
        assert w.balance == 0
        assert ledger.verify_wallet(s, t).ok


def test_parallel_retries_of_same_operation_charge_once(make_team):
    t = _funded(make_team, 100)
    results = _run_parallel(
        10,
        lambda s, i: ledger.debit(s, t, 10, ref_type="purchase", ref_id="same-trade", reason="r", actor="t"),
    )
    assert results.count("ok") == 1
    assert results.count("DUPLICATE_REFERENCE") == 9
    with Session(get_engine()) as s:
        assert ledger.get_wallet(s, t).balance == 90


def test_bids_and_purchases_racing_never_exceed_balance(make_team):
    t = _funded(make_team, 100)

    def op(s, i):
        if i % 2:
            ledger.reserve(s, t, 15, ref_type="auction_bid", ref_id=f"b-{i}")
        else:
            ledger.debit(s, t, 15, ref_type="purchase", ref_id=f"p-{i}", reason="r", actor="t")

    results = _run_parallel(20, op)
    assert results.count("ok") == 6  # 6 x 15 = 90 <= 100; a 7th would need 105
    with Session(get_engine()) as s:
        w = ledger.get_wallet(s, t)
        assert 0 <= w.held <= w.balance and w.available == 10
        assert ledger.verify_wallet(s, t).ok
