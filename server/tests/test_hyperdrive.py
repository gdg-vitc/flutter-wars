"""Hyperdrive binding -> connection target resolution (no live Hyperdrive needed)."""

from types import SimpleNamespace

import pytest

from app.infra.db import get_binding, resolve_target, target_from_hyperdrive
from app.infra.settings import ConfigError, Settings


class FakeBinding:
    user = "hduser"
    password = "hd-super-secret"
    host = "hyperdrive.local"
    port = 5432
    database = "neondb"


def test_target_from_hyperdrive_fields():
    target = target_from_hyperdrive(FakeBinding())
    assert target.source == "hyperdrive"
    assert target.url.drivername == "postgresql+psycopg"
    assert target.url.host == "hyperdrive.local"
    assert target.url.port == 5432
    assert target.url.database == "neondb"
    # Worker <-> Hyperdrive hop is not TLS.
    assert target.url.query.get("sslmode") == "disable"


def test_hyperdrive_target_repr_redacts_password():
    target = target_from_hyperdrive(FakeBinding())
    assert FakeBinding.password not in repr(target)
    assert "***" in repr(target)


def test_resolve_prefers_hyperdrive_over_settings():
    settings = Settings(database_url="postgresql+psycopg://local:pw@localhost:5432/db")
    assert resolve_target({"HYPERDRIVE": FakeBinding()}, settings).source == "hyperdrive"
    assert resolve_target(None, settings).source == "settings"


def test_resolve_requires_url_when_no_binding():
    with pytest.raises(ConfigError):
        resolve_target(None, Settings(database_url=None))


def test_get_binding_supports_mapping_and_attribute_objects():
    assert get_binding({"A": 1}, "A") == 1
    assert get_binding(SimpleNamespace(A=2), "A") == 2
    assert get_binding(None, "A") is None
    assert get_binding(SimpleNamespace(), "A") is None
