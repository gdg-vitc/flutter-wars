from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.auction.schemas import AuctionCreate
from app.trading.schemas import PurchaseRequest, TradeResponse


def data():
    return dict(listing_id=uuid4(), quantity=2, idempotency_key=uuid4())


@pytest.mark.parametrize("field", ["listing_id", "quantity", "idempotency_key"])
def test_purchase_requires_fields(field):
    payload = data()
    del payload[field]
    with pytest.raises(ValidationError):
        PurchaseRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["listing_id", "idempotency_key"])
def test_purchase_requires_uuid(field):
    payload = data()
    payload[field] = "invalid"
    with pytest.raises(ValidationError):
        PurchaseRequest.model_validate(payload)


@pytest.mark.parametrize("quantity", [False, 2.0, "2"])
def test_quantities_are_strict(quantity):
    with pytest.raises(ValidationError):
        PurchaseRequest(**dict(data(), quantity=quantity))


def test_response_has_only_receipt_fields():
    assert set(TradeResponse.model_fields) == {
        "id",
        "listing_id",
        "widget_id",
        "transaction_type",
        "quantity",
        "unit_price",
        "gross_amount",
        "brokerage_amount",
        "final_amount",
        "created_at",
    }


def test_auction_configuration_requires_aware_times():
    with pytest.raises(ValidationError):
        AuctionCreate(
            round_id=uuid4(),
            listing_id=uuid4(),
            widget_id=uuid4(),
            quantity=1,
            starts_at=datetime(2026, 1, 1),
            closes_at=datetime(2026, 1, 2),
        )
