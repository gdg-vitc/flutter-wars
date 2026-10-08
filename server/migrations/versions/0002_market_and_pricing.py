"""Module G (market, rounds, listings) and Module H (pricing) tables.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('market',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_market_single_active', 'market', ['is_active'], unique=True, postgresql_where=sa.text('is_active'), sqlite_where=sa.text('is_active'))
    op.create_table('market_round',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('market_id', sa.Uuid(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=120), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('scheduled_open_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scheduled_close_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['market_id'], ['market.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('market_id', 'sequence', name='uq_market_round_sequence')
    )
    op.create_index(op.f('ix_market_round_market_id'), 'market_round', ['market_id'], unique=False)
    op.create_index('uq_market_round_single_live', 'market_round', ['market_id'], unique=True, postgresql_where=sa.text("status IN ('open', 'paused')"), sqlite_where=sa.text("status IN ('open', 'paused')"))
    op.create_table('market_listing',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('round_id', sa.Uuid(), nullable=False),
    sa.Column('widget_id', sa.Uuid(), nullable=False),
    sa.Column('base_price', sa.BigInteger(), nullable=False),
    sa.Column('supply_total', sa.Integer(), nullable=True),
    sa.Column('stock_remaining', sa.Integer(), nullable=True),
    sa.Column('max_per_purchase', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('(supply_total IS NULL) = (stock_remaining IS NULL)', name='ck_market_listing_infinite_consistent'),
    sa.CheckConstraint('base_price > 0', name='ck_market_listing_base_price_positive'),
    sa.CheckConstraint('max_per_purchase IS NULL OR max_per_purchase > 0', name='ck_market_listing_max_per_purchase'),
    sa.CheckConstraint('stock_remaining IS NULL OR stock_remaining >= 0', name='ck_market_listing_stock_nonneg'),
    sa.CheckConstraint('supply_total IS NULL OR supply_total >= 0', name='ck_market_listing_supply_nonneg'),
    sa.ForeignKeyConstraint(['round_id'], ['market_round.id'], ),
    sa.ForeignKeyConstraint(['widget_id'], ['widget.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('round_id', 'widget_id', name='uq_market_listing_round_widget')
    )
    op.create_index(op.f('ix_market_listing_round_id'), 'market_listing', ['round_id'], unique=False)
    op.create_index(op.f('ix_market_listing_widget_id'), 'market_listing', ['widget_id'], unique=False)
    op.create_table('market_round_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('from_status', sa.String(length=16), nullable=False),
    sa.Column('to_status', sa.String(length=16), nullable=False),
    sa.Column('actor', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
    sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['round_id'], ['market_round.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_market_round_event_round_id'), 'market_round_event', ['round_id'], unique=False)
    op.create_table('listing_pricing',
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('strategy', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('params_version', sa.Integer(), nullable=False),
    sa.Column('current_price', sa.BigInteger(), nullable=False),
    sa.Column('interval_index', sa.Integer(), nullable=False),
    sa.Column('interval_bought', sa.Integer(), nullable=False),
    sa.Column('interval_sold', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('current_price > 0', name='ck_listing_pricing_price_positive'),
    sa.CheckConstraint('interval_bought >= 0 AND interval_sold >= 0', name='ck_listing_pricing_counts'),
    sa.ForeignKeyConstraint(['listing_id'], ['market_listing.id'], ),
    sa.PrimaryKeyConstraint('listing_id')
    )
    op.create_table('price_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('interval_index', sa.Integer(), nullable=False),
    sa.Column('price', sa.BigInteger(), nullable=False),
    sa.Column('previous_price', sa.BigInteger(), nullable=True),
    sa.Column('reason', sa.String(length=16), nullable=False),
    sa.Column('strategy', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
    sa.Column('params_version', sa.Integer(), nullable=False),
    sa.Column('demand', sa.Integer(), nullable=True),
    sa.Column('supply', sa.Integer(), nullable=True),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['market_listing.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_price_history_listing_id'), 'price_history', ['listing_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_price_history_listing_id'), table_name='price_history')
    op.drop_table('price_history')
    op.drop_table('listing_pricing')
    op.drop_index(op.f('ix_market_round_event_round_id'), table_name='market_round_event')
    op.drop_table('market_round_event')
    op.drop_index(op.f('ix_market_listing_widget_id'), table_name='market_listing')
    op.drop_index(op.f('ix_market_listing_round_id'), table_name='market_listing')
    op.drop_table('market_listing')
    op.drop_index('uq_market_round_single_live', table_name='market_round', postgresql_where=sa.text("status IN ('open', 'paused')"), sqlite_where=sa.text("status IN ('open', 'paused')"))
    op.drop_index(op.f('ix_market_round_market_id'), table_name='market_round')
    op.drop_table('market_round')
    op.drop_index('uq_market_single_active', table_name='market', postgresql_where=sa.text('is_active'), sqlite_where=sa.text('is_active'))
    op.drop_table('market')
