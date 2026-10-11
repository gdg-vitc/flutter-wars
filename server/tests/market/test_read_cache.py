"""Cache correctness against PostgreSQL, including out-of-order writer commits."""

from datetime import timedelta

from sqlalchemy import event, text
from sqlmodel import Session

from app.core.read_cache import ReadCache
from tests.market.conftest import PARTICIPANT, T0
from tests.market.test_market_api import act, create_round, setup_market


def test_read_cache_hits_and_copies_values(engine):
    cache = ReadCache()
    calls = []

    def load():
        calls.append(1)
        return {"items": [1]}

    with Session(engine) as session:
        cache.read(session, "key", T0, load)["items"].append(2)
        assert cache.read(session, "key", T0, load) == {"items": [1]}
    assert len(calls) == 1


def test_out_of_order_commit_and_rollback_do_not_hide_changes(engine):
    # Two writers may allocate transaction IDs in one order and commit in another.
    cache = ReadCache()
    calls = []

    def load():
        calls.append(1)
        return len(calls)

    with Session(engine) as reader, engine.connect() as first, engine.connect() as second:
        first.execute(text("SELECT pg_current_xact_id()"))
        second.execute(text("SELECT pg_current_xact_id()"))
        assert cache.read(reader, "key", T0, load) == 1
        assert cache.read(reader, "key", T0, load) == 1
        second.commit()
        assert cache.read(reader, "key", T0, load) == 2
        first.rollback()
        assert cache.read(reader, "key", T0, load) == 3


def test_writer_sessions_and_repeatable_read_bypass_cache(engine):
    cache = ReadCache()
    calls = []

    def load():
        calls.append(1)
        return len(calls)

    with Session(engine) as session:
        session.connection().execute(text("SELECT pg_current_xact_id()"))
        assert cache.read(session, "key", T0, load) == 1
        assert cache.read(session, "key", T0, load) == 2
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with Session(connection) as session:
            assert cache.read(session, "key", T0, load) == 3
            assert cache.read(session, "key", T0, load) == 4


def test_deadline_ttl_and_clock_regression_expire_cache(engine):
    cache = ReadCache(ttl_seconds=2)
    calls = []

    def load():
        calls.append(1)
        return len(calls)

    with Session(engine) as session:

        def deadline(value):
            return (T0 + timedelta(seconds=1),)

        assert cache.read(session, "key", T0, load, deadline) == 1
        assert cache.read(session, "key", T0 + timedelta(milliseconds=999), load, deadline) == 1
        assert cache.read(session, "key", T0 + timedelta(seconds=1), load, deadline) == 2
        assert cache.read(session, "key", T0, load) == 3
        assert cache.read(session, "key", T0 + timedelta(seconds=2), load) == 4


def test_changes_during_fill_are_not_published(engine):
    cache = ReadCache()
    calls = []

    def load():
        calls.append(1)
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_current_xact_id()"))
        return len(calls)

    with Session(engine) as session:
        assert cache.read(session, "key", T0, load) == 1
        assert cache.read(session, "key", T0, load) == 2


def test_cache_is_bounded_and_separated_by_database(engine):
    cache = ReadCache(max_entries=1)
    calls = []

    def load():
        calls.append(1)
        return len(calls)

    with Session(engine) as session:
        assert cache.read(session, "a", T0, load) == 1
        assert cache.read(session, "b", T0, load) == 2
        assert cache.read(session, "a", T0, load) == 3
    from sqlalchemy import create_engine

    other = create_engine(engine.url)
    try:
        with Session(other) as session:
            assert cache.read(session, "a", T0, load) == 4
    finally:
        other.dispose()


def test_market_hit_reduces_queries_and_refreshes_server_clock(api, widgets, engine, clock):
    setup_market(api)
    rnd = create_round(api, widgets)
    assert act(api, rnd["id"], "open").status_code == 200
    api.as_(PARTICIPANT)
    counts = []

    def record(conn, cursor, statement, parameters, context, executemany):
        counts.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        first = api.get("/market/listings")
        cold = len(counts)
        counts.clear()
        clock.advance(milliseconds=100)
        second = api.get("/market/listings")
        assert first.status_code == second.status_code == 200
        assert len(counts) == 1 < cold
        assert first.json()["listings"] == second.json()["listings"]
        assert first.json()["server_time"] != second.json()["server_time"]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    api.as_(None)
    assert api.get("/market/listings").status_code == 401


def test_round_change_and_catalog_edit_invalidate_warm_reads(api, widgets, engine):
    setup_market(api)
    rnd = create_round(api, widgets)
    assert act(api, rnd["id"], "open").status_code == 200
    first = api.get("/market/listings").json()
    assert first["round"]["status"] == "open"
    with engine.begin() as conn:
        conn.execute(text("UPDATE widget SET display_name = 'Renamed' WHERE id = :id"), {"id": widgets[0].id})
    updated = api.get("/market/listings").json()
    assert next(w for w in updated["listings"] if w["widget_id"] == widgets[0].id)["widget_name"] == "Renamed"
    assert act(api, rnd["id"], "pause").status_code == 200
    assert api.get("/market/listings").json()["round"]["status"] == "paused"
