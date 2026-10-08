"""Connection/session lifecycle tests for the Module M database abstraction."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from app.infra.db import create_db_engine, session_scope, target_from_settings
from app.infra.infra_probe import InfraProbe
from app.infra.settings import Settings


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'db.db'}")
    InfraProbe.__table__.create(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_create_read_write(engine):
    with session_scope(engine) as session:
        session.add(InfraProbe(label="alpha"))

    with session_scope(engine) as session:
        rows = session.exec(select(InfraProbe).where(InfraProbe.label == "alpha")).all()
        assert len(rows) == 1
        assert rows[0].id is not None


def test_rollback_on_error_leaves_no_partial_write(engine):
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with session_scope(engine) as session:
            session.add(InfraProbe(label="should-not-persist"))
            raise Boom

    with session_scope(engine) as session:
        assert session.exec(select(InfraProbe)).all() == []


def test_scope_ends_without_an_open_transaction(engine):
    with session_scope(engine) as session:
        session.exec(select(InfraProbe))
        assert session.in_transaction()
    assert not session.in_transaction()


def test_engine_uses_nullpool_so_hyperdrive_can_own_pooling():
    settings = Settings(app_env="test", database_url="postgresql+psycopg://u:p@localhost:5432/db")
    engine = create_db_engine(target_from_settings(settings), settings)
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        engine.dispose()


def test_driver_connect_args_carry_timeout_and_app_name():
    from app.infra.db import driver_connect_args

    settings = Settings(db_connect_timeout_seconds=7, db_application_name="fw-test")
    args = driver_connect_args(settings)
    assert args["connect_timeout"] == 7
    assert args["application_name"] == "fw-test"
