"""Pricing strategies.

A strategy is a pure function of committed inputs: it never touches the
database, so it is deterministic and easy to test. New strategies are added
by subclassing PricingStrategy and calling register(); nothing in the Market
or Transaction modules changes.
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.errors import AppError


@dataclass(frozen=True)
class PricingInput:
    """What happened during one pricing interval."""

    base_price: int
    current_price: int
    bought: int
    sold: int
    supply_at_start: int | None  # None = infinite supply


class PricingStrategy[P: BaseModel](ABC):
    key: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]

    def parse_params(self, raw: dict[str, Any], *, infinite_supply: bool) -> P:
        try:
            params = self.params_model.model_validate(raw)
        except ValidationError as exc:
            raise AppError(
                "INVALID_PRICING_PARAMS",
                "Pricing parameters are invalid.",
                strategy=self.key,
                errors=exc.errors(include_url=False, include_context=False),
            ) from None
        self.check_supply(params, infinite_supply=infinite_supply)
        return params  # type: ignore[return-value]

    def check_supply(self, params: P, *, infinite_supply: bool) -> None:
        """Reject combinations the strategy cannot price."""

    @abstractmethod
    def interval_seconds(self, params: P) -> int | None:
        """Length of a repricing interval, or None if the price never moves."""

    @abstractmethod
    def next_price(self, params: P, data: PricingInput) -> int:
        """Price for the next interval, given what happened in this one."""


class StaticParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StaticStrategy(PricingStrategy[StaticParams]):
    """The listing's base price, forever. Used for fixed and infinite listings."""

    key = "static"
    params_model = StaticParams

    def interval_seconds(self, params: StaticParams) -> None:
        return None

    def next_price(self, params: StaticParams, data: PricingInput) -> int:
        return data.current_price


class DemandBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    below: Decimal | None = Field(
        default=None, gt=0, description="Upper bound of the demand ratio; null = catch-all"
    )
    multiplier: Decimal = Field(gt=0, le=10)


DEFAULT_BANDS = [
    DemandBand(below=Decimal("0.5"), multiplier=Decimal("0.9")),
    DemandBand(below=Decimal("1"), multiplier=Decimal("1")),
    DemandBand(below=Decimal("1.5"), multiplier=Decimal("1.15")),
    DemandBand(below=Decimal("2"), multiplier=Decimal("1.3")),
    DemandBand(below=None, multiplier=Decimal("1.5")),
]


class DynamicParams(BaseModel):
    """Supply-demand repricing. Every `interval_seconds` the price is multiplied
    by the band matching ratio = net_units_bought / (supply_at_interval_start *
    target_fraction), rounded to `price_step` and clamped to
    [base_price * min_factor, base_price * max_factor]."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(default=120, ge=10, le=86_400)
    target_fraction: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    bands: list[DemandBand] = Field(
        default_factory=lambda: list(DEFAULT_BANDS), min_length=1, max_length=20
    )
    min_factor: Decimal = Field(default=Decimal("0.75"), gt=0, le=1)
    max_factor: Decimal = Field(default=Decimal("2"), ge=1, le=100)
    price_step: int = Field(default=5, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _check_bands(self) -> "DynamicParams":
        bounds = [band.below for band in self.bands]
        if bounds[-1] is not None or any(bound is None for bound in bounds[:-1]):
            raise ValueError("only the last band may (and must) have below = null")
        finite = [bound for bound in bounds if bound is not None]
        if finite != sorted(set(finite)):
            raise ValueError("band bounds must be strictly increasing")
        return self


class DynamicSupplyDemandStrategy(PricingStrategy[DynamicParams]):
    key = "dynamic"
    params_model = DynamicParams

    def check_supply(self, params: DynamicParams, *, infinite_supply: bool) -> None:
        if infinite_supply:
            raise AppError(
                "PRICING_REQUIRES_FINITE_SUPPLY",
                "Dynamic pricing needs finite supply; use the static strategy for infinite listings.",
                strategy=self.key,
            )

    def interval_seconds(self, params: DynamicParams) -> int:
        return params.interval_seconds

    def next_price(self, params: DynamicParams, data: PricingInput) -> int:
        if not data.supply_at_start:
            # Infinite or sold out: there is no supply signal to react to.
            return data.current_price
        demand = max(data.bought - data.sold, 0)
        ratio = Decimal(demand) / (Decimal(data.supply_at_start) * params.target_fraction)
        multiplier = next(
            band.multiplier for band in params.bands if band.below is None or ratio < band.below
        )
        step = Decimal(params.price_step)
        raw = (Decimal(data.current_price) * multiplier / step).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        ) * step
        lower = math.ceil(Decimal(data.base_price) * params.min_factor / step) * params.price_step
        upper = math.floor(Decimal(data.base_price) * params.max_factor / step) * params.price_step
        lower = max(lower, params.price_step)
        if lower > upper:  # step coarser than the allowed band: hold the base price
            return data.base_price
        return int(min(max(raw, lower), upper))


_REGISTRY: dict[str, PricingStrategy[Any]] = {}


def register(strategy: PricingStrategy[Any]) -> None:
    if strategy.key in _REGISTRY:
        raise ValueError(f"pricing strategy {strategy.key!r} is already registered")
    _REGISTRY[strategy.key] = strategy


def get_strategy(key: str) -> PricingStrategy[Any]:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise AppError(
            "UNKNOWN_PRICING_STRATEGY",
            "Unknown pricing strategy.",
            strategy=key,
            available=sorted(_REGISTRY),
        ) from None


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


register(StaticStrategy())
register(DynamicSupplyDemandStrategy())
