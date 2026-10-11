import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.inventory import service as inventory
from app.modules.inventory.errors import (
    DuplicateReference,
    InsufficientQuantity,
    InvalidQuantity,
    TeamNotFound,
    WidgetNotFound,
)
from app.modules.ledger import service as ledger


def _inc(db, t, w, q=1, ref="p-1"):
    return inventory.increment(db, t, w, q, ref_type="purchase", ref_id=ref, reason="bought", actor="test")


def test_increment_creates_and_adds(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    e1 = _inc(db, t, w, 2, "p-1")
    e2 = _inc(db, t, w, 3, "p-2")
    db.commit()
    assert (e1.quantity_after, e2.quantity_after) == (2, 5)
    assert inventory.get_quantity(db, t, w) == 5


def test_decrement_never_negative(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    _inc(db, t, w, 1)
    db.commit()
    with pytest.raises(InsufficientQuantity) as ei:
        inventory.decrement(db, t, w, 2, ref_type="sale", ref_id="s-1", reason="sell", actor="test")
    assert ei.value.details == {"owned": 1, "requested": 2}
    db.rollback()
    assert inventory.get_quantity(db, t, w) == 1


def test_decrement_widget_never_owned(db, make_team, make_widget):
    with pytest.raises(InsufficientQuantity):
        inventory.decrement(db, make_team(), make_widget(), 1, ref_type="sale", ref_id="s", reason="r", actor="t")


def test_same_award_twice_blocked(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    _inc(db, t, w, 1, "auction-9")
    db.commit()
    with pytest.raises(DuplicateReference):
        _inc(db, t, w, 1, "auction-9")


def test_unknown_widget_and_team(db, make_team, make_widget):
    with pytest.raises(WidgetNotFound):
        _inc(db, make_team(), "does_not_exist")
    with pytest.raises(TeamNotFound):
        _inc(db, uuid.uuid4(), make_widget())


@pytest.mark.parametrize("bad", [0, -1, 1.0, "1", True, 10_001])
def test_invalid_quantities(db, make_team, make_widget, bad):
    with pytest.raises(InvalidQuantity):
        _inc(db, make_team(), make_widget(), bad)


def test_archived_widget_still_listed_for_owner(db, make_team, make_widget):
    t = make_team()
    live, old = make_widget(), make_widget(status="ARCHIVED")
    _inc(db, t, live, 1, "a")
    _inc(db, t, old, 2, "b")
    db.commit()
    items = {i.widget_id: i for i in inventory.get_team_inventory(db, t)}
    assert items[live].archived is False
    assert items[old].archived is True and items[old].quantity == 2


def test_zero_quantity_hidden_by_default(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    _inc(db, t, w, 1)
    inventory.decrement(db, t, w, 1, ref_type="sale", ref_id="s", reason="sold", actor="t")
    db.commit()
    assert inventory.get_team_inventory(db, t) == []
    assert inventory.get_team_inventory(db, t, include_zero=True)[0].quantity == 0


def test_admin_adjust_and_verify(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    inventory.admin_adjust(db, t, w, 3, reason="compensation", actor="org", idempotency_key=uuid.uuid4())
    inventory.admin_adjust(db, t, w, -1, reason="correction", actor="org", idempotency_key=uuid.uuid4())
    db.commit()
    assert inventory.get_quantity(db, t, w) == 2
    assert inventory.verify_inventory(db, t) == []


def test_events_are_append_only(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    _inc(db, t, w)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.exec(text("UPDATE inventory_event SET delta = 50 WHERE team_id = :t"), params={"t": t})


def test_widget_with_history_cannot_be_deleted(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    _inc(db, t, w)
    db.commit()
    with pytest.raises(DBAPIError, match="never deleted"):  # Module D trigger; the FK is a 2nd guard
        db.exec(text("DELETE FROM widget WHERE id = :w"), params={"w": w})


# ---------------------------------------------------------------- the contract I and J depend on


def test_purchase_failure_rolls_back_credits_and_inventory_together(db, make_team, make_widget):
    """Simulates Module I: debit + increment in ONE transaction, then something fails."""
    t, w = make_team(), make_widget()
    ledger.grant_initial(db, t, 100, actor="test")
    db.commit()

    ledger.debit(db, t, 30, ref_type="purchase", ref_id="trade-77", reason="Buy", actor="u")
    inventory.increment(db, t, w, 1, ref_type="purchase", ref_id="trade-77", reason="Buy", actor="u")
    db.rollback()  # e.g. market stock update failed afterwards

    assert ledger.get_wallet(db, t).balance == 100
    assert inventory.get_quantity(db, t, w) == 0
    assert ledger.verify_wallet(db, t).ok and inventory.verify_inventory(db, t) == []


def test_purchase_success_commits_both(db, make_team, make_widget):
    t, w = make_team(), make_widget()
    ledger.grant_initial(db, t, 100, actor="test")
    ledger.debit(db, t, 30, ref_type="purchase", ref_id="trade-78", reason="Buy", actor="u")
    inventory.increment(db, t, w, 1, ref_type="purchase", ref_id="trade-78", reason="Buy", actor="u")
    db.commit()
    assert ledger.get_wallet(db, t).balance == 70
    assert inventory.get_quantity(db, t, w) == 1
