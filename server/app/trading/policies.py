"""Explicitly configured example strategies. Neither has a default fee or rounding."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.integration.contracts import MAX_CREDITS, PurchaseListing


@dataclass(frozen=True, kw_only=True)
class FixedFeeBrokerage:
    amount: int

    def __post_init__(self) -> None:
        if type(self.amount) is not int or not 0 <= self.amount <= MAX_CREDITS:
            raise ValueError("Invalid whole-credit fixed fee.")

    def fee(
        self,
        *,
        team_id: UUID,
        listing: PurchaseListing,
        quantity: int,
        unit_price: int,
        gross_amount: int,
        prior_quantity: int = 0,
    ) -> int:
        return self.amount


@dataclass(frozen=True, kw_only=True)
class BasisPointsBrokerage:
    rate_bps: int
    rounding: Literal["floor", "ceil", "half_up"]

    def __post_init__(self) -> None:
        if type(self.rate_bps) is not int or not 0 <= self.rate_bps <= 10_000:
            raise ValueError("Rate must be whole basis points between 0 and 10000.")
        if self.rounding not in ("floor", "ceil", "half_up"):
            raise ValueError("An explicit supported rounding rule is required.")

    def fee(
        self,
        *,
        team_id: UUID,
        listing: PurchaseListing,
        quantity: int,
        unit_price: int,
        gross_amount: int,
        prior_quantity: int = 0,
    ) -> int:
        numerator = gross_amount * self.rate_bps
        if self.rounding == "ceil":
            return (numerator + 9999) // 10_000
        if self.rounding == "half_up":
            return (numerator + 5000) // 10_000
        return numerator // 10_000
