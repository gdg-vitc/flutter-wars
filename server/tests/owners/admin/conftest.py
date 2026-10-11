"""Fixtures only Module K tests need (organizer rows, organizer login, SQL counter)."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import pytest
from sqlalchemy import event, text

from app.core.db import get_engine
from app.modules.admin import OrganizerPrincipal, Role
from app.modules.admin.permissions import ROLE_PERMISSIONS


@pytest.fixture(autouse=True)
def _controls_open() -> Iterator[None]:
    """Control rows are global: every admin test starts and ends with everything unfrozen."""

    def _reset() -> None:
        with get_engine().begin() as c:
            c.execute(text("UPDATE operational_control SET frozen = false, reason = NULL"))

    _reset()
    yield
    _reset()


@pytest.fixture()
def make_organizer() -> Callable[..., tuple[uuid.UUID, str]]:
    """Insert an organizer row directly (bypassing the API). Returns (id, email)."""

    def _make(role: str = "OPERATOR", email: str | None = None, active: bool = True) -> tuple[uuid.UUID, str]:
        oid = uuid.uuid4()
        email = email or f"org-{oid.hex[:10]}@gdg.test"
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO organizer (id, email, display_name, role, active, created_by) "
                    "VALUES (:id, :e, :n, :r, :a, 'test')"
                ),
                {"id": oid, "e": email, "n": email.split("@")[0], "r": role, "a": active},
            )
        return oid, email

    return _make


@pytest.fixture()
def login_organizer(login, make_organizer) -> Callable[..., str]:  # noqa: ANN001
    """login_organizer("VIEWER") -> creates a NEW organizer with that role, logs in as them, returns the email."""

    def _login(role: str = "OPERATOR") -> str:
        _, email = make_organizer(role=role)
        login(email=email)
        return email

    return _login


@pytest.fixture()
def org_principal(make_organizer) -> Callable[..., OrganizerPrincipal]:  # noqa: ANN001
    """An organizer row + the principal object Module K would build for it (for service-level tests)."""

    def _make(role: str = "OPERATOR") -> OrganizerPrincipal:
        oid, email = make_organizer(role=role)
        r = Role(role)
        return OrganizerPrincipal(id=oid, email=email, display_name=email, role=r, permissions=ROLE_PERMISSIONS[r])

    return _make


@pytest.fixture()
def count_queries() -> Callable[[], AbstractContextManager[list[str]]]:
    """with count_queries() as q: ...   then len(q) == number of SQL statements sent."""

    @contextmanager
    def _count() -> Iterator[list[str]]:
        seen: list[str] = []

        def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            seen.append(statement)

        event.listen(get_engine(), "before_cursor_execute", _before)
        try:
            yield seen
        finally:
            event.remove(get_engine(), "before_cursor_execute", _before)

    return _count
