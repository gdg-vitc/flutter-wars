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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError


@dataclass(frozen=True)
class PricingInput:
    """What happened during one pricing interval."""

    base_price: int
    current_price: int
    bought: int
    sold: int
    supply_at_start: int | None  # None = infinite supply
    initial_supply: int | None = None
    initial_demand: int = 0


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

    def check_supply(self, params: P, *, infinite_supply: bool) -> None:  # noqa: B027 (optional hook)
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


class DynamicParams(BaseModel):
    """Event pricing responds to net units retained, never raw transaction volume.
    The hard 98%-102% range and 1% interval speed protect workshop participants.
    """

    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(default=120, ge=10, le=86_400)
    target_fraction: Decimal = Field(default=Decimal("0.1"), gt=0, le=1)
    min_factor: Decimal = Field(default=Decimal("0.98"), ge=Decimal("0.98"), le=1)
    max_factor: Decimal = Field(default=Decimal("1.02"), ge=1, le=Decimal("1.02"))
    price_step: int = Field(default=1, ge=1, le=1, description="Integer credits; legacy coarse steps are unsupported")


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
        # Custom event market: net retained demand, bounded target, gradual movement.
        # Volume from buying and returning the same units contributes nothing.
        if data.supply_at_start is None:
            return data.current_price
        total = data.initial_supply if data.initial_supply is not None else data.supply_at_start
        reference = max(1, total + data.initial_demand)
        held = max(0, data.initial_demand + total - data.supply_at_start + data.bought - data.sold)
        pressure = min(Decimal(1), Decimal(held) / (Decimal(reference) * params.target_fraction))
        lower = max(1, math.ceil(Decimal(data.base_price) * max(params.min_factor, Decimal("0.98"))))
        upper = max(lower, math.floor(Decimal(data.base_price) * min(params.max_factor, Decimal("1.02"))))
        target = int(
            (Decimal(data.base_price) * (Decimal("0.98") + Decimal("0.04") * pressure)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        target = min(max(target, lower), upper)
        movement = data.base_price // 100  # at most 1% per interval; tiny prices stay stable
        current = min(max(data.current_price, lower), upper)
        return min(max(target, current - movement), current + movement)


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
