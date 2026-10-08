import threading
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session

from app.core.db import engine
from app.core.errors import AppError
from app.modules import inventory


def _run_parallel(n, fn):
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        with Session(engine) as s:
            try:
                fn(s, i)
                s.commit()
                return "ok"
            except AppError as e:
                s.rollback()
                return e.code

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))


def test_parallel_sales_never_go_negative(make_team, make_widget):
    t, w = make_team(), make_widget()
    with Session(engine) as s:
        inventory.increment(s, t, w, 5, ref_type="purchase", ref_id="seed", reason="r", actor="t")
        s.commit()
    results = _run_parallel(
        10, lambda s, i: inventory.decrement(s, t, w, 1, ref_type="sale", ref_id=f"s-{i}", reason="r", actor="t")
    )
    assert results.count("ok") == 5 and results.count("INSUFFICIENT_QUANTITY") == 5
    with Session(engine) as s:
        assert inventory.get_quantity(s, t, w) == 0
        assert inventory.verify_inventory(s, t) == []


def test_parallel_first_purchases_of_same_widget(make_team, make_widget):
    """Row does not exist yet: 20 concurrent upserts must still sum correctly."""
    t, w = make_team(), make_widget()
    results = _run_parallel(
        20, lambda s, i: inventory.increment(s, t, w, 1, ref_type="purchase", ref_id=f"p-{i}", reason="r", actor="t")
    )
    assert results.count("ok") == 20
    with Session(engine) as s:
        assert inventory.get_quantity(s, t, w) == 20
        assert inventory.verify_inventory(s, t) == []
