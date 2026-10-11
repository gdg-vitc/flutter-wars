"""Module D business rules on real PostgreSQL.
Tests marked (teammate) are our teammate's original tests, ported from SQLite."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.catalog import service as catalog
from app.modules.catalog.errors import (
    FieldImmutable,
    InvalidWidgetData,
    VersionConflict,
    WidgetArchived,
    WidgetDuplicate,
    WidgetNotArchived,
    WidgetNotFound,
)


def _id(prefix="w"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _create(db, wid=None, key=None, **kw):
    wid = wid or _id()
    return catalog.create_widget(
        db,
        widget_id=wid,
        appdev_key=key or f"appdev.{wid}",
        display_name=kw.pop("display_name", "Some Widget"),
        category=kw.pop("category", "layout"),
        **kw,
    )


# ---------------------------------------------------------------- ported from the teammate's suite


def test_create_and_get_widget(db):  # (teammate)
    wid = _id("icon_pack")
    w = _create(db, wid, display_name="Icon Pack", category="media", flutter_classes=["Icon", "IconButton"])
    db.commit()
    assert (w.id, w.status, w.version, w.archived) == (wid, "ACTIVE", 1, False)
    assert w.created_at is not None and w.updated_at is not None  # DB clock
    assert catalog.get_widget(db, wid).display_name == "Icon Pack"


def test_duplicate_widget_id_rejected(db):  # (teammate)
    wid = _id("row")
    _create(db, wid)
    with pytest.raises(WidgetDuplicate) as e:
        _create(db, wid, key=f"other.{wid}")
    assert e.value.details == {"field": "id"}


def test_optimistic_locking_conflict(db):  # (teammate)
    w = _create(db)
    db.commit()
    with pytest.raises(VersionConflict) as e:
        catalog.update_widget(db, w.id, {"display_name": "New Column"}, expected_version=99)
    assert e.value.details == {"current_version": 1}


def test_archived_widget_behavior(db):  # (teammate)
    w = _create(db)
    catalog.archive_widget(db, w.id, expected_version=1)
    db.commit()
    assert catalog.get_widget(db, w.id).status == "ARCHIVED"
    with pytest.raises(WidgetArchived):
        catalog.require_active_widget(db, w.id)


# ---------------------------------------------------------------- spec §6 definition of done


def test_duplicate_appdev_key_rejected(db):
    w = _create(db)
    with pytest.raises(WidgetDuplicate) as e:
        _create(db, key=w.appdev_key)
    assert e.value.details == {"field": "appdev_key"}


def test_archived_widget_remains_resolvable(db):
    w = _create(db)
    catalog.archive_widget(db, w.id, expected_version=1)
    db.commit()
    assert catalog.get_widget(db, w.id).archived is True  # history lookups still work
    assert catalog.get_widgets(db, [w.id])[w.id].archived is True
    assert w.id not in {x.id for x in catalog.list_widgets(db)}  # but it's not offered anymore
    assert w.id in {x.id for x in catalog.list_widgets(db, include_archived=True)}


# ---------------------------------------------------------------- identity, deletion, edits


def test_id_and_appdev_key_are_immutable(db):
    w = _create(db)
    for field in ("id", "appdev_key"):
        with pytest.raises(FieldImmutable):
            catalog.update_widget(db, w.id, {field: "changed"}, expected_version=1)
    db.commit()
    with pytest.raises(DBAPIError, match="immutable"):  # even raw SQL can't do it
        db.exec(text("UPDATE widget SET appdev_key = 'hacked' WHERE id = :w"), params={"w": w.id})


def test_widget_can_never_be_deleted(db):
    w = _create(db)
    db.commit()
    with pytest.raises(DBAPIError, match="never deleted"):
        db.exec(text("DELETE FROM widget WHERE id = :w"), params={"w": w.id})


def test_update_bumps_version_and_keeps_notes(db):
    w = _create(db, internal_notes="Hand out after round 1")
    w2, diff = catalog.update_widget(db, w.id, {"display_name": "Renamed"}, expected_version=1)
    assert w2.version == 2 and diff == {"display_name": ["Some Widget", "Renamed"]}
    assert w2.internal_notes == "Hand out after round 1"  # not overwritten with "Updated by ..."


def test_no_change_means_no_version_bump(db):
    w = _create(db)
    w2, diff = catalog.update_widget(db, w.id, {"display_name": "Some Widget"}, expected_version=1)
    assert (w2.version, diff) == (1, {})


@pytest.mark.parametrize("field", ["display_name", "category", "flutter_classes"])
def test_required_fields_cannot_be_nulled(db, field):
    w = _create(db)
    with pytest.raises(InvalidWidgetData):
        catalog.update_widget(db, w.id, {field: None}, expected_version=1)


def test_catalog_holds_no_price_stock_or_allocation(db):
    """Spec §6: catalog must not own price or stock. Not in the table, not accepted by the service."""
    cols = {
        r[0]
        for r in db.exec(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'widget'")).all()
    }
    assert cols == {
        "id",
        "appdev_key",
        "display_name",
        "description",
        "category",
        "flutter_classes",
        "status",
        "internal_notes",
        "version",
        "created_at",
        "updated_at",
        "archived_at",
    }
    w = _create(db)
    for field in ("price", "stock", "is_free", "free_quantity"):
        with pytest.raises(InvalidWidgetData):
            catalog.update_widget(db, w.id, {field: 0}, expected_version=1)


def test_archived_widget_cannot_be_edited_until_restored(db):
    w = _create(db)
    catalog.archive_widget(db, w.id, expected_version=1)
    with pytest.raises(WidgetArchived):
        catalog.update_widget(db, w.id, {"display_name": "x"}, expected_version=2)
    with pytest.raises(WidgetArchived):
        catalog.archive_widget(db, w.id, expected_version=2)
    r = catalog.restore_widget(db, w.id, expected_version=2)
    assert (r.status, r.archived_at, r.version) == ("ACTIVE", None, 3)
    with pytest.raises(WidgetNotArchived):
        catalog.restore_widget(db, w.id, expected_version=3)


def test_unknown_widget(db):
    with pytest.raises(WidgetNotFound):
        catalog.get_widget(db, "does_not_exist")
    assert catalog.widget_exists(db, "does_not_exist") is False
    assert catalog.get_widgets(db, ["does_not_exist"]) == {}


def test_db_rejects_bad_rows_even_without_python(db):
    """CHECK constraints are the last line of defence if someone writes SQL by hand."""
    cols = "INSERT INTO widget (id, appdev_key, display_name, category"
    for sql in (
        f"{cols}) VALUES ('Bad-ID', 'k1', 'x', 'c')",  # bad id format
        f"{cols}, flutter_classes) VALUES ('ok_id1', 'k2', 'x', 'c', '{{}}'::jsonb)",  # classes not a list
        f"{cols}, status) VALUES ('ok_id2', 'k3', 'x', 'c', 'ARCHIVED')",  # archived without archived_at
    ):
        with pytest.raises(DBAPIError, match="ck_widget"):
            db.exec(text(sql))
        db.rollback()
