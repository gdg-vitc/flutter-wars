"""Module D x Modules E/F: catalog edits must never rewrite purchase history (spec §6 DoD)."""

import uuid

from sqlalchemy import text

from app.modules import catalog, inventory, ledger


def _create(db, **kw):
    wid = f"w_{uuid.uuid4().hex[:10]}"
    return catalog.create_widget(
        db,
        widget_id=wid,
        appdev_key=f"appdev.{wid}",
        display_name=kw.pop("display_name", "Some Widget"),
        category="layout",
        **kw,
    )


def test_catalog_changes_do_not_mutate_past_transactions(db, make_team):
    """Rename + archive after a purchase: history rows are byte-for-byte unchanged."""
    t = make_team()
    w = _create(db, display_name="Old Name")
    ledger.grant_initial(db, t, 120, actor="test")
    ledger.debit(db, t, 10, ref_type="purchase", ref_id="tx-1", reason=f"Buy {w.id}", actor="test")
    inventory.increment(db, t, w.id, 1, ref_type="purchase", ref_id="tx-1", reason="Purchase", actor="test")
    db.commit()
    snapshot = lambda: db.exec(  # noqa: E731
        text(
            "SELECT e.widget_id, e.delta, e.reason, l.reason FROM inventory_event e "
            "JOIN credit_ledger_entry l ON l.ref_id = e.ref_id AND l.team_id = e.team_id WHERE e.team_id = :t"
        ),
        params={"t": t},
    ).all()
    before = snapshot()
    catalog.update_widget(db, w.id, {"display_name": "New Name"}, expected_version=1)
    catalog.archive_widget(db, w.id, expected_version=2)
    db.commit()
    assert snapshot() == before
    item = inventory.get_team_inventory(db, t)[0]
    assert (item.display_name, item.archived, item.quantity) == ("New Name", True, 1)  # still owned
