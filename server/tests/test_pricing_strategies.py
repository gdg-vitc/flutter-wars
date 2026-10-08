from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError
from app.modules.pricing import strategies
from app.modules.pricing.strategies import (
    DynamicParams,
    DynamicSupplyDemandStrategy,
    PricingInput,
    PricingStrategy,
    StaticParams,
    StaticStrategy,
    get_strategy,
)

dynamic = DynamicSupplyDemandStrategy()
DEFAULTS = DynamicParams()


def step(
    price: int,
    bought: int,
    supply: int | None,
    *,
    base: int = 100,
    sold: int = 0,
    params: DynamicParams = DEFAULTS,
) -> int:
    return dynamic.next_price(params, PricingInput(base, price, bought, sold, supply))


def test_static_never_moves() -> None:
    static = StaticStrategy()
    assert static.interval_seconds(StaticParams()) is None
    for bought in (0, 1, 1000):
        assert static.next_price(StaticParams(), PricingInput(100, 100, bought, 0, 10)) == 100


@pytest.mark.parametrize(
    ("bought", "expected"),
    [
        (0, 90),  # ratio 0      -> x0.9
        (4, 90),  # ratio 0.4    -> x0.9
        (5, 100),  # ratio 0.5    -> x1.0
        (10, 115),  # ratio 1.0    -> x1.15
        (15, 130),  # ratio 1.5    -> x1.3
        (20, 150),  # ratio 2.0    -> x1.5
        (99, 150),
    ],
)
def test_dynamic_bands(bought: int, expected: int) -> None:
    # supply 100, target_fraction 0.1 -> 10 units/interval is "on target"
    assert step(100, bought, 100) == expected


def test_dynamic_clamps_to_band_around_base_price() -> None:
    assert step(200, 50, 100) == 200  # max_factor 2.0
    assert step(75, 0, 100) == 75  # min_factor 0.75
    assert step(80, 0, 100) == 75


def test_dynamic_rounds_to_price_step() -> None:
    assert step(103, 0, 100, base=103) == 95  # 92.7 -> 95 (floor of band is ceil(77.25/5)*5 = 80)
    params = DynamicParams(price_step=1)
    assert step(103, 0, 100, base=103, params=params) == 93


def test_sales_offset_purchases() -> None:
    assert step(100, 20, 100, sold=20) == 90  # net demand 0


def test_no_supply_signal_holds_price() -> None:
    assert step(120, 0, 0) == 120
    assert step(120, 5, None) == 120


def test_dynamic_is_deterministic() -> None:
    inputs = [(100, 7, 50), (130, 0, 43), (90, 30, 60)]
    assert [step(*args) for args in inputs] == [step(*args) for args in inputs]


def test_dynamic_rejects_infinite_supply() -> None:
    with pytest.raises(AppError) as exc:
        dynamic.parse_params({}, infinite_supply=True)
    assert exc.value.code == "PRICING_REQUIRES_FINITE_SUPPLY"


@pytest.mark.parametrize(
    "raw",
    [
        {"interval_seconds": 1},
        {"min_factor": 0},
        {"unknown": 1},
        {"bands": [{"below": 1, "multiplier": 1}]},  # no catch-all
        {
            "bands": [
                {"below": 2, "multiplier": 1},
                {"below": 1, "multiplier": 1},
                {"multiplier": 1},
            ]
        },
        {"bands": [{"multiplier": 1}, {"below": 1, "multiplier": 1}]},  # catch-all not last
    ],
)
def test_dynamic_rejects_bad_params(raw: dict) -> None:
    with pytest.raises(AppError) as exc:
        dynamic.parse_params(raw, infinite_supply=False)
    assert exc.value.code == "INVALID_PRICING_PARAMS"


def test_custom_bands_and_decimal_params_round_trip() -> None:
    raw = {
        "target_fraction": "0.25",
        "bands": [{"below": "1", "multiplier": "0.95"}, {"multiplier": "1.2"}],
    }
    params = dynamic.parse_params(raw, infinite_supply=False)
    assert params.target_fraction == Decimal("0.25")
    again = dynamic.parse_params(params.model_dump(mode="json"), infinite_supply=False)
    assert again == params


def test_unknown_strategy() -> None:
    with pytest.raises(AppError) as exc:
        get_strategy("nope")
    assert exc.value.code == "UNKNOWN_PRICING_STRATEGY"


def test_new_strategy_can_be_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    class FlatParams(BaseModel):
        model_config = ConfigDict(extra="forbid")
        add: int = 1

    class Ramp(PricingStrategy[FlatParams]):
        key: ClassVar[str] = "ramp"
        params_model = FlatParams

        def interval_seconds(self, params: FlatParams) -> int:
            return 60

        def next_price(self, params: FlatParams, data: PricingInput) -> int:
            return data.current_price + params.add

    monkeypatch.setattr(strategies, "_REGISTRY", dict(strategies._REGISTRY))
    strategies.register(Ramp())
    assert (
        get_strategy("ramp").next_price(FlatParams(add=3), PricingInput(10, 10, 0, 0, None)) == 13
    )
    with pytest.raises(ValueError):
        strategies.register(Ramp())
