import threading
import uuid

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.core.db import get_engine
from app.core.errors import AppError
from app.modules.admin import cli, service
from app.modules.admin import repository as repo
from app.modules.admin.permissions import Role


def _org(client, email):
    return next(o for o in client.get("/admin/organizers").json() if o["email"] == email)


def test_owner_adds_operator_and_email_is_normalized(client, login, login_organizer):
    login_organizer("OWNER")
    body = {"email": "  New.Op@GDG.test ", "display_name": "New Op", "role": "OPERATOR", "reason": "joining crew"}
    r = client.post("/admin/organizers", json=body)
    assert r.status_code == 201
    assert r.json()["email"] == "new.op@gdg.test" and r.json()["version"] == 1
    again = client.post("/admin/organizers", json=body)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ORGANIZER_EXISTS"


def test_new_organizer_can_log_in_immediately(client, login, login_organizer):
    login_organizer("OWNER")
    body = {"email": "fresh@gdg.test", "display_name": "Fresh", "role": "VIEWER", "reason": "volunteer"}
    assert client.post("/admin/organizers", json=body).status_code == 201
    login(email="fresh@gdg.test")
    assert client.get("/admin/me").json()["role"] == "VIEWER"


def test_organizer_body_validation(client, login, login_organizer):
    login_organizer("OWNER")
    for bad in (
        {"email": "not-an-email", "display_name": "X", "role": "VIEWER", "reason": "abc"},
        {"email": "a@b.test", "display_name": "X", "role": "GOD", "reason": "abc"},
        {"email": "a@b.test", "display_name": "X", "role": "VIEWER", "reason": " "},
        {"email": "a@b.test", "display_name": "X", "role": "VIEWER", "reason": "abc", "active": False},
    ):
        assert client.post("/admin/organizers", json=bad).status_code == 422, bad


def test_update_needs_current_version(client, login, login_organizer, make_organizer):
    login_organizer("OWNER")
    _, email = make_organizer(role="VIEWER")
    o = _org(client, email)
    ok = client.patch(
        f"/admin/organizers/{o['id']}", json={"expected_version": 1, "role": "OPERATOR", "reason": "promote"}
    )
    assert ok.status_code == 200 and ok.json()["role"] == "OPERATOR" and ok.json()["version"] == 2
    stale = client.patch(
        f"/admin/organizers/{o['id']}", json={"expected_version": 1, "active": False, "reason": "remove"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == {
        "code": "VERSION_CONFLICT",
        "message": "Someone else changed this first; reload and retry",
        "context": {"current_version": 2},
    }


def test_no_change_means_no_version_bump(client, login, login_organizer, make_organizer):
    login_organizer("OWNER")
    _, email = make_organizer(role="VIEWER")
    o = _org(client, email)
    r = client.patch(f"/admin/organizers/{o['id']}", json={"expected_version": 1, "role": "VIEWER", "reason": "same"})
    assert r.status_code == 200 and r.json()["version"] == 1


def test_cannot_demote_or_deactivate_yourself(client, login, login_organizer):
    email = login_organizer("OWNER")
    me = _org(client, email)
    for change in ({"role": "VIEWER"}, {"active": False}):
        r = client.patch(f"/admin/organizers/{me['id']}", json={"expected_version": 1, "reason": "oops", **change})
        assert r.status_code == 409 and r.json()["error"]["code"] == "SELF_LOCKOUT"


def test_unknown_organizer_is_404(client, login, login_organizer):
    login_organizer("OWNER")
    r = client.patch(
        f"/admin/organizers/{uuid.uuid4()}", json={"expected_version": 1, "active": False, "reason": "remove"}
    )
    assert r.status_code == 404


def test_two_owners_demoting_each_other_at_once_leaves_one_owner(org_principal):
    """The classic race: without locking, both demotions succeed and nobody can manage organizers."""
    a, b = org_principal("OWNER"), org_principal("OWNER")
    # Make a and b the ONLY active owners (other tests left owners behind).
    with get_engine().begin() as c:
        c.execute(
            text("UPDATE organizer SET active = false WHERE role = 'OWNER' AND id NOT IN (:a, :b)"),
            {"a": a.id, "b": b.id},
        )
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def demote(actor, target_id, key):
        with Session(get_engine()) as s:
            barrier.wait()
            try:
                service.update_organizer(s, actor, target_id, expected_version=1, reason="race", role=Role.OPERATOR)
                s.commit()
                results[key] = "ok"
            except AppError as e:
                s.rollback()
                results[key] = e.code

    t1 = threading.Thread(target=demote, args=(a, b.id, "a"))
    t2 = threading.Thread(target=demote, args=(b, a.id, "b"))
    t1.start(), t2.start()
    t1.join(), t2.join()

    assert sorted(results.values())[1] == "ok"
    assert sorted(results.values())[0] in {"MISSING_PERMISSION", "LAST_OWNER"}
    with Session(get_engine()) as s:
        assert len(repo.lock_active_owners(s)) == 1


def test_cli_bootstraps_first_owner(capsys):
    assert cli.main(["add-owner", "--email", "Lead@GDG.test", "--name", "Event Lead"]) == 0
    assert "created OWNER lead@gdg.test" in capsys.readouterr().out
    assert cli.main(["add-owner", "--email", "lead@gdg.test", "--name", "Again"]) == 1
    assert cli.main(["add-owner", "--email", "bad email", "--name", "X"]) == 2
    with Session(get_engine()) as s:
        org = repo.get_active_organizer_by_email(s, "lead@gdg.test")
        assert org is not None and org.role == "OWNER"
        rows, _ = service.list_audit(s, target_id=str(org.id))
        assert [r.actor_email for r in rows] == ["cli"]


@pytest.mark.parametrize("email", ["UPPER@GDG.TEST"])
def test_db_rejects_non_lowercase_email(email):
    with pytest.raises(Exception, match="ck_organizer_email_lower"), get_engine().begin() as c:
        c.execute(
            text(
                "INSERT INTO organizer (id, email, display_name, role, created_by) "
                "VALUES (gen_random_uuid(), :e, 'x', 'VIEWER', 't')"
            ),
            {"e": email},
        )
