"""PricingAdapter (the H side of the I/J PricingPort) on in-memory SQLite."""

from datetime import timedelta

import pytest

from app.integration.errors import ConfigurationRequired
from app.modules.market import service as market
from app.modules.pricing import service as pricing
from app.modules.pricing.adapter import PricingAdapter
from app.modules.pricing.models import ListingPricing
from tests.conftest import T0
from tests.test_trade_contract import dynamic, make_round


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_trade_crossing_a_boundary_is_booked_in_its_quoted_interval(session, widgets):
    _, [listing] = make_round(
        session, widgets, dynamic(widgets[0], params={"interval_seconds": 10})
    )
    clock = Clock(T0 + timedelta(seconds=9.9))
    adapter = PricingAdapter(session, now=clock)

    assert adapter.get_unit_price(listing_id=listing.id) == 100
    market.take_stock(session, listing.id, 50)
    clock.now = T0 + timedelta(seconds=10.1)  # ledger and inventory work outlast the interval
    adapter.record_trade(
        listing_id=listing.id, transaction_type="BUY", quantity=50, unit_price=100, trade_id=None
    )

    state = session.get(ListingPricing, listing.id)
    assert (state.interval_index, state.interval_bought) == (0, 50)
    # 50 of 100 units bought in interval 0 is high demand: the price rises, it must not fall.
    assert pricing.get_current_price(session, listing.id, T0 + timedelta(seconds=10.5)).price == 150


def test_record_trade_without_a_quote_is_refused(session, widgets):
    _, [listing] = make_round(session, widgets, dynamic(widgets[0]))
    with pytest.raises(ConfigurationRequired):
        PricingAdapter(session).record_trade(
            listing_id=listing.id, transaction_type="BUY", quantity=1, unit_price=100, trade_id=None
        )
