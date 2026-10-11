"""Migration workflow tests (shared Alembic history).

They upgrade and downgrade a whole database, so they need their own disposable
one: MIGRATION_DATABASE_URL, named *_migration_test. CI also runs the same
commands against a fresh database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

SERVER_DIR = Path(__file__).resolve().parents[2]
ALEMBIC = str(Path(sys.executable).parent / "alembic")


def _alembic(*args: str, database_url: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if database_url is None:
        env.pop("DATABASE_URL", None)
        env.pop("SPIKE_DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url
    return subprocess.run([ALEMBIC, *args], cwd=SERVER_DIR, env=env, capture_output=True, text=True)


HEAD = "0011_auth_sessions"


@pytest.fixture()
def clean_db():
    postgres_url = os.environ.get("MIGRATION_DATABASE_URL")
    if not postgres_url:
        pytest.skip("Set MIGRATION_DATABASE_URL to a disposable *_migration_test database.")
    if not (make_url(postgres_url).database or "").endswith("_migration_test"):
        raise RuntimeError("Migration tests only reset databases named *_migration_test.")
    _alembic("downgrade", "base", database_url=postgres_url)
    yield postgres_url
    _alembic("downgrade", "base", database_url=postgres_url)


def test_upgrade_and_current(clean_db):
    up = _alembic("upgrade", "head", database_url=clean_db)
    assert up.returncode == 0, up.stderr
    current = _alembic("current", database_url=clean_db)
    assert f"{HEAD} (head)" in current.stdout


def test_history_is_one_linear_chain(clean_db):
    result = _alembic("heads", database_url=clean_db)
    assert result.returncode == 0
    assert result.stdout.split() == [HEAD, "(head)"]


def test_check_reports_no_drift(clean_db):
    _alembic("upgrade", "head", database_url=clean_db)
    result = _alembic("check", database_url=clean_db)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected" in (result.stdout + result.stderr)


def test_downgrade_clears_version(clean_db):
    _alembic("upgrade", "head", database_url=clean_db)
    down = _alembic("downgrade", "base", database_url=clean_db)
    assert down.returncode == 0, down.stderr
    current = _alembic("current", database_url=clean_db)
    assert "(head)" not in current.stdout


def test_missing_configuration_fails_clearly():
    result = _alembic("upgrade", "head", database_url=None)
    assert result.returncode != 0
    assert "DATABASE_URL" in (result.stdout + result.stderr)


def test_legacy_inventory_gets_conservative_cost_basis(clean_db):
    from uuid import uuid4

    from sqlalchemy import MetaData, Table, create_engine, select

    from app.modules.authentication.model import Team
    from app.modules.catalog.models import Widget
    from app.modules.inventory.models import TeamWidgetInventory
    from app.trading.models import TradeTransaction, TradeType

    result = _alembic("upgrade", "0009_trading_auction", database_url=clean_db)
    assert result.returncode == 0, result.stderr
    engine = create_engine(clean_db)
    metadata = MetaData()
    team = Team(name="Legacy team")
    widget = Widget(id="legacy_button", appdev_key="appdev.legacy", display_name="Legacy", category="input")
    with engine.begin() as connection:
        for model in (team, widget, TeamWidgetInventory(team_id=team.id, widget_id=widget.id, quantity=4)):
            table = Table(model.__tablename__, metadata, autoload_with=connection)
            values = {k: v for k, v in model.model_dump().items() if k in table.c and v is not None}
            connection.execute(table.insert().values(**values))
        for unit in (100, 200):
            trade = TradeTransaction(
                team_id=team.id,
                listing_id=uuid4(),
                widget_id=widget.id,
                idempotency_key=uuid4(),
                transaction_type=TradeType.BUY,
                quantity=5,
                unit_price=unit,
                gross_amount=unit * 5,
                brokerage_amount=0,
                final_amount=unit * 5,
            )
            table = Table(trade.__tablename__, metadata, autoload_with=connection)
            connection.execute(table.insert().values(**trade.model_dump()))
    result = _alembic("upgrade", "head", database_url=clean_db)
    assert result.returncode == 0, result.stderr
    with engine.connect() as connection:
        position = Table("resale_position", MetaData(), autoload_with=connection)
        row = connection.execute(select(position)).mappings().one()
        assert (row["quantity"], row["cost"], row["external_units"], row["reward_units"]) == (4, 400, 0, 4)
    engine.dispose()
