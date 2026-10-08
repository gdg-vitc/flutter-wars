"""Module G: units of an auction-round listing held for one Auction Engine auction.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('market_auction_lot',
    sa.Column('auction_id', sa.Uuid(), nullable=False),
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('consumed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('quantity > 0', name='ck_market_auction_lot_quantity'),
    sa.ForeignKeyConstraint(['listing_id'], ['market_listing.id'], ),
    sa.PrimaryKeyConstraint('auction_id')
    )
    op.create_index(op.f('ix_market_auction_lot_listing_id'), 'market_auction_lot', ['listing_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_market_auction_lot_listing_id'), table_name='market_auction_lot')
    op.drop_table('market_auction_lot')
