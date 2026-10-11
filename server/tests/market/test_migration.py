from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_migration_creates_owned_schema_and_immutable_trades(ij_engine):
    engine = ij_engine
    schema = "ij_migration_" + uuid4().hex
    source = (Path(__file__).parents[2] / "migrations" / "0001_modules_i_j.sql").read_text()
    try:
        with engine.begin() as c:
            c.exec_driver_sql(f"CREATE SCHEMA {schema}")
            c.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
            c.exec_driver_sql(source)
            names = set(
                c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema=:schema"
                    ),
                    {"schema": schema},
                ).scalars()
            )
            assert names == {
                "trade_transaction",
                "auction",
                "auction_bid",
                "auction_bid_receipt",
                "auction_result",
            }
            c.execute(
                text("""INSERT INTO trade_transaction
                (id,team_id,listing_id,widget_id,idempotency_key,transaction_type,quantity,
                 unit_price,gross_amount,brokerage_amount,final_amount,created_at)
                VALUES (:id,:team,:listing,:widget,:key,'BUY',1,100,100,0,100,clock_timestamp())"""),
                dict(id=uuid4(), team=uuid4(), listing=uuid4(), widget=uuid4(), key=uuid4()),
            )
        for statement in (
            "UPDATE trade_transaction SET unit_price=100",
            "DELETE FROM trade_transaction",
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                with engine.begin() as c:
                    c.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
                    c.exec_driver_sql(statement)
    finally:
        with engine.begin() as c:
            c.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
