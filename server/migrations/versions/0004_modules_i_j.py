"""Modules I (trading) and J (auction) tables.

Runs the reviewed SQL in migrations/0001_modules_i_j.sql unchanged, so that
file stays the single source tests/test_migration.py checks.

Revision ID: 0004
Revises: 0003
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL = Path(__file__).parents[1] / "0001_modules_i_j.sql"


def upgrade() -> None:
    # Driver-level execute: the file holds several statements, `::` casts and a
    # $$-quoted function, none of which should go through bind-parameter parsing.
    op.get_bind().exec_driver_sql(SQL.read_text())


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TABLE auction_bid_receipt, auction_result, auction_bid, trade_transaction, auction;
        DROP FUNCTION ij_reject_trade_rewrite();
        DROP TYPE tradetype;
        DROP TYPE auctionstate;
        """
    )
