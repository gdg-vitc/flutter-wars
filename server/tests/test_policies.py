from uuid import uuid4

import pytest

from app.integration.contracts import PurchaseListing
from app.trading.policies import BasisPointsBrokerage, FixedFeeBrokerage


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
