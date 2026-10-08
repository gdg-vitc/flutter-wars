import threading
import time

import pytest
from sqlmodel import Session

from app.core.db import engine
from app.modules import ledger
from app.modules.admin import ensure_not_frozen, service
from app.modules.admin.errors import OperationFrozen


def _fund(team, amount=120):
    with Session(engine) as s:
        ledger.grant_initial(s, team, amount, actor="test")
        s.commit()


def test_freeze_all_needs_typed_confirmation(client, login, login_organizer):
    login_organizer()
    r = client.put("/admin/controls/ALL", json={"frozen": True, "reason": "db issue"})
    assert r.status_code == 422 and r.json()["error"]["details"] == {"expected": "FREEZE ALL"}
    r = client.put("/admin/controls/ALL", json={"frozen": True, "reason": "db issue", "confirm": "FREEZE ALL"})
    assert r.status_code == 200 and r.json()["frozen"] is True


def test_freeze_trading_blocks_trading_only(client, login, login_organizer, db):
    login_organizer()
    r = client.put("/admin/controls/TRADING", json={"frozen": True, "reason": "price bug, back in 5 min"})
    assert r.status_code == 200 and r.json()["changed_by"].endswith("@gdg.test")

    with pytest.raises(OperationFrozen) as e:
        ensure_not_frozen(db, "TRADING")
    assert e.value.status_code == 423
    assert e.value.details == {"scope": "TRADING", "reason": "price bug, back in 5 min"}
    ensure_not_frozen(db, "BIDDING")  # still open
    db.rollback()

    client.put("/admin/controls/TRADING", json={"frozen": False, "reason": "fixed"})
    ensure_not_frozen(db, "TRADING")


def test_freeze_all_blocks_everything(client, login, login_organizer, db):
    login_organizer()
    client.put("/admin/controls/ALL", json={"frozen": True, "reason": "incident", "confirm": "FREEZE ALL"})
    for scope in ("TRADING", "BIDDING"):
        with pytest.raises(OperationFrozen) as e:
            ensure_not_frozen(db, scope)
        assert e.value.details["scope"] == "ALL"
        db.rollback()


def test_participants_see_open_closed_but_not_who(client, login, login_organizer, make_team):
    login_organizer()
    client.put("/admin/controls/BIDDING", json={"frozen": True, "reason": "Auction paused for 2 minutes"})
    login(team_id=make_team())
    assert client.get("/controls").json() == {
        "trading_open": True,
        "bidding_open": False,
        "message": "Auction paused for 2 minutes",
    }


def test_repeating_a_freeze_is_a_no_op(client, login, login_organizer):
    login_organizer()
    first = client.put("/admin/controls/BIDDING", json={"frozen": True, "reason": "pause"}).json()
    second = client.put("/admin/controls/BIDDING", json={"frozen": True, "reason": "pause again"}).json()
    assert second["version"] == first["version"] and second["reason"] == "pause"
    rows = client.get("/admin/audit?target_type=control&target_id=BIDDING&limit=100").json()["items"]
    assert sum(1 for r in rows if r["created_at"] >= first["changed_at"]) == 1


def test_expected_version_guards_double_clicks(client, login, login_organizer):
    login_organizer()
    v = next(c for c in client.get("/admin/controls").json() if c["scope"] == "TRADING")["version"]
    ok = client.put("/admin/controls/TRADING", json={"frozen": True, "reason": "pause", "expected_version": v})
    stale = client.put("/admin/controls/TRADING", json={"frozen": False, "reason": "resume", "expected_version": v})
    assert ok.status_code == 200 and stale.status_code == 409


def test_bad_scope_and_bad_body(client, login, login_organizer):
    login_organizer()
    assert client.put("/admin/controls/EVERYTHING", json={"frozen": True, "reason": "abc"}).status_code == 422
    assert client.put("/admin/controls/TRADING", json={"frozen": "yes", "reason": "abc"}).status_code == 422
    with Session(engine) as s, pytest.raises(ValueError):
        ensure_not_frozen(s, "ALL")


def test_freeze_waits_for_in_flight_purchase(make_team, org_principal):
    """A purchase that already passed the check finishes; the freeze commits only after it."""
    team = make_team()
    _fund(team)
    actor = org_principal("OPERATOR")
    checked = threading.Event()
    times: dict[str, float] = {}

    def purchase():
        with Session(engine) as s:
            ensure_not_frozen(s, "TRADING")  # FOR SHARE on control rows
            checked.set()
            time.sleep(0.5)  # slow purchase still running
            ledger.debit(s, team, 10, ref_type="purchase", ref_id="inflight", reason="buy", actor="t")
            s.commit()
            times["purchase"] = time.monotonic()

    def freeze():
        checked.wait()
        with Session(engine) as s:
            service.set_frozen(s, actor, "TRADING", frozen=True, reason="incident")  # blocks on FOR UPDATE
            s.commit()
            times["freeze"] = time.monotonic()

    tp, tf = threading.Thread(target=purchase), threading.Thread(target=freeze)
    tp.start(), tf.start()
    tp.join(), tf.join()

    assert times["freeze"] >= times["purchase"]
    with Session(engine) as s:
        assert ledger.get_wallet(s, team).balance == 110  # the in-flight purchase completed
        with pytest.raises(OperationFrozen):
            ensure_not_frozen(s, "TRADING")  # and the next one is refused


def test_purchase_waits_for_uncommitted_freeze_then_is_refused(make_team, org_principal):
    team = make_team()
    _fund(team)
    actor = org_principal("OPERATOR")
    locked = threading.Event()
    outcome: dict[str, str] = {}

    def freeze():
        with Session(engine) as s:
            service.set_frozen(s, actor, "TRADING", frozen=True, reason="incident")
            locked.set()
            time.sleep(0.4)
            s.commit()

    def purchase():
        locked.wait()
        with Session(engine) as s:
            try:
                ensure_not_frozen(s, "TRADING")  # waits for the freeze to commit, then re-reads
                ledger.debit(s, team, 10, ref_type="purchase", ref_id="late", reason="buy", actor="t")
                s.commit()
                outcome["p"] = "bought"
            except OperationFrozen:
                s.rollback()
                outcome["p"] = "frozen"

    tf, tp = threading.Thread(target=freeze), threading.Thread(target=purchase)
    tf.start(), tp.start()
    tf.join(), tp.join()

    assert outcome["p"] == "frozen"
    with Session(engine) as s:
        assert ledger.get_wallet(s, team).balance == 120


def test_parallel_purchases_do_not_block_each_other(make_team):
    """FOR SHARE is shared: 10 purchases holding the check at once all succeed."""
    team = make_team()
    _fund(team)
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def buy(i):
        try:
            with Session(engine) as s:
                ensure_not_frozen(s, "TRADING")
                barrier.wait(timeout=5)  # all 10 hold the shared lock together
                ledger.debit(s, team, 1, ref_type="purchase", ref_id=f"par{i}", reason="buy", actor="t")
                s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=buy, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with Session(engine) as s:
        assert ledger.get_wallet(s, team).balance == 110
