from uuid import uuid4

import pytest

from app.integration.contracts import MAX_CREDITS, PurchaseListing
from app.trading.policies import DEFAULT_BROKERAGE, BasisPointsBrokerage, FixedFeeBrokerage


def fee(policy, gross):
    return policy.fee(
        team_id=uuid4(),
        listing=PurchaseListing(uuid4(), uuid4(), uuid4()),
        quantity=1,
        unit_price=gross,
        gross_amount=gross,
    )


def test_fixed_fee_configuration():
    assert fee(FixedFeeBrokerage(amount=7), 100) == 7


@pytest.mark.parametrize("rounding,expected", [("floor", 12), ("ceil", 13), ("half_up", 13)])
def test_fractional_brokerage_rounding_is_explicit(rounding, expected):
    assert fee(BasisPointsBrokerage(rate_bps=500, rounding=rounding), 250) == expected


@pytest.mark.parametrize("rate", [True, -1, 10001])
def test_invalid_rate_rejected(rate):
    with pytest.raises(ValueError):
        BasisPointsBrokerage(rate_bps=rate, rounding="floor")


def test_rounding_required():
    with pytest.raises(TypeError):
        BasisPointsBrokerage(rate_bps=500)


def test_unknown_rounding_rejected():
    with pytest.raises(ValueError):
        BasisPointsBrokerage(rate_bps=500, rounding="unknown")


def resale_fee(quantity, price, prior=0):
    return DEFAULT_BROKERAGE.fee(
        team_id=uuid4(),
        listing=PurchaseListing(uuid4(), uuid4(), uuid4()),
        quantity=quantity,
        unit_price=price,
        gross_amount=quantity * price,
        prior_quantity=prior,
    )


def test_default_brokerage_bulk_fee_is_unchanged():
    assert resale_fee(200, 100) == 1881
    assert resale_fee(200, 1000) == 18813


@pytest.mark.parametrize("price", [1, 100, 500, 501, 1000])
def test_split_sale_pays_the_same_total_as_one_sale(price):
    one_by_one = sum(resale_fee(1, price, prior) for prior in range(200))
    uneven = resale_fee(37, price) + resale_fee(1, price, 37) + resale_fee(162, price, 38)
    assert one_by_one == uneven == resale_fee(200, price)


def test_long_resale_history_keeps_fees_within_gross():
    fee = resale_fee(1000, 50_000, prior=1_000_000)
    assert 0 <= fee <= 1000 * 50_000


@pytest.mark.parametrize("prior", [-1, True, MAX_CREDITS + 1])
def test_invalid_prior_quantity_rejected(prior):
    with pytest.raises(ValueError):
        resale_fee(1, 100, prior)
