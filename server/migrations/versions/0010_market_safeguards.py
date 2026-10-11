"""Bounded resale accounting and a pause-aware pricing clock (additive schema).

Revision ID: 0010_market_safeguards
Revises: 0009_trading_auction
"""

import sqlalchemy as sa
from alembic import op

from app.trading.models import ResaleAccount, ResalePosition

revision = "0010_market_safeguards"
down_revision = "0009_trading_auction"
branch_labels = depends_on = None


def upgrade():
    op.add_column("market_round", sa.Column("paused_seconds", sa.Float(), nullable=False, server_default="0"))
    op.add_column("market_listing", sa.Column("demand_seed", sa.BigInteger(), nullable=False, server_default="0"))
    op.execute("ALTER TYPE auctionstate ADD VALUE IF NOT EXISTS 'CANCELLED'")
    # Replace provisional multiplier-band settings with the custom bounded policy.
    op.execute("""
        INSERT INTO price_history(listing_id, interval_index, price, previous_price,
                                  reason, strategy, params_version, demand, supply, effective_at, created_at)
        SELECT p.listing_id, p.interval_index,
               LEAST(FLOOR(l.base_price * 1.02)::bigint,
                     GREATEST(CEIL(l.base_price * 0.98)::bigint, p.current_price)),
               p.current_price, 'config_change', p.strategy, p.params_version+1,
               NULL, l.stock_remaining, NOW(), NOW()
        FROM listing_pricing p JOIN market_listing l ON l.id=p.listing_id
        WHERE p.strategy='dynamic'
    """)
    op.execute("""
        UPDATE listing_pricing p SET params=jsonb_build_object(
            'interval_seconds', COALESCE(p.params->'interval_seconds', '120'::jsonb),
            'target_fraction', COALESCE(p.params->'target_fraction', '0.1'::jsonb),
            'min_factor', '0.98', 'max_factor', '1.02', 'price_step', 1),
            current_price=LEAST(FLOOR(l.base_price * 1.02)::bigint,
                                GREATEST(CEIL(l.base_price * 0.98)::bigint, p.current_price)),
            params_version=p.params_version+1
        FROM market_listing l WHERE l.id=p.listing_id AND p.strategy='dynamic'
    """)
    ResaleAccount.__table__.create(op.get_bind())
    ResalePosition.__table__.create(op.get_bind())
    # Legacy trades have no per-sale cost basis. The cheapest historical acquisition
    # is a conservative bound; don't retroactively assign a high resale entitlement.
    op.execute("""
        INSERT INTO resale_position(team_id, widget_id, quantity, cost, external_units, reward_units)
        SELECT i.team_id, i.widget_id, i.quantity, i.quantity::bigint * b.minimum_price,
               i.quantity::bigint * GREATEST(0, COALESCE(n.units,0) - COALESCE(own.units,0)), i.quantity
        FROM team_widget_inventory i
        JOIN (SELECT team_id, widget_id, MIN(unit_price) AS minimum_price
              FROM trade_transaction WHERE transaction_type='BUY' GROUP BY team_id, widget_id) b
          ON b.team_id=i.team_id AND b.widget_id=i.widget_id
        LEFT JOIN (SELECT widget_id, SUM(CASE WHEN transaction_type='BUY' THEN quantity ELSE -quantity END) AS units
                   FROM trade_transaction GROUP BY widget_id) n ON n.widget_id=i.widget_id
        LEFT JOIN (SELECT team_id, widget_id, SUM(CASE WHEN transaction_type='BUY' THEN quantity ELSE -quantity END) AS units
                   FROM trade_transaction GROUP BY team_id, widget_id) own ON own.team_id=i.team_id AND own.widget_id=i.widget_id
        WHERE i.quantity > 0
    """)
    op.execute(
        "INSERT INTO resale_account(team_id, profit_paid, fee_notional, loss_realized) SELECT DISTINCT team_id, 0, 0, 0 FROM trade_transaction"
    )


def downgrade():
    # Forward fixes are required once this accounting has live trades. The enum's
    # extra label is harmless and removed by 0009's DROP TYPE on a full test reset.
    op.drop_table("resale_position")
    op.drop_table("resale_account")
    op.drop_column("market_listing", "demand_seed")
    op.drop_column("market_round", "paused_seconds")
