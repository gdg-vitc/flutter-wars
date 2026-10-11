"""Two organizers at once. Real threads, separate sessions, a Barrier releases them together."""

import threading
import uuid

from sqlmodel import Session

from app.core.db import get_engine
from app.core.errors import AppError
from app.modules.catalog import service as catalog


def _run_parallel(n, fn):
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i):
        with Session(get_engine()) as s:
            barrier.wait()
            try:
                fn(s, i)
                s.commit()
                out = "ok"
            except AppError as e:
                s.rollback()
                out = e.code
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(results)


def test_two_organizers_edit_same_widget_one_wins():
    wid = f"w_{uuid.uuid4().hex[:10]}"
    with Session(get_engine()) as s:
        catalog.create_widget(s, widget_id=wid, appdev_key=f"k.{wid}", display_name="Start", category="layout")
        s.commit()
    res = _run_parallel(
        2, lambda s, i: catalog.update_widget(s, wid, {"display_name": f"Edit {i}"}, expected_version=1)
    )
    assert res == ["VERSION_CONFLICT", "ok"]
    with Session(get_engine()) as s:
        assert catalog.get_widget(s, wid).version == 2  # exactly one edit applied, nothing lost silently


def test_parallel_creates_of_same_id_make_one_widget():
    wid = f"w_{uuid.uuid4().hex[:10]}"
    res = _run_parallel(
        10,
        lambda s, i: catalog.create_widget(
            s, widget_id=wid, appdev_key=f"k{i}.{wid}", display_name="Dup", category="layout"
        ),
    )
    assert res.count("ok") == 1 and res.count("WIDGET_DUPLICATE") == 9


def test_parallel_creates_with_same_appdev_key_make_one_widget():
    key = f"shared.{uuid.uuid4().hex[:10]}"
    res = _run_parallel(
        8,
        lambda s, i: catalog.create_widget(
            s, widget_id=f"w_{uuid.uuid4().hex[:10]}", appdev_key=key, display_name="Dup", category="layout"
        ),
    )
    assert res.count("ok") == 1 and res.count("WIDGET_DUPLICATE") == 7
