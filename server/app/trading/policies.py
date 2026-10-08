"""Replaceable whole-credit fee strategies and the selected Marketflow-inspired default."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.integration.contracts import MAX_CREDITS, PurchaseListing
from app.integration.errors import ConfigurationRequired


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


def _power_bounds(numerator: int, denominator: int, exponent: int, scale: int) -> tuple[int, int]:
    """Integer lower/upper bounds on scale * (numerator / denominator) ** exponent.

    Every multiplication rounds outward. Exponentiation by squaring takes
    O(log(quantity)) work, independent of the number of units being sold.
    """
    lower = upper = scale
    base_lower = numerator * scale // denominator
    base_upper = (numerator * scale + denominator - 1) // denominator
    while exponent:
        if exponent & 1:
            lower = lower * base_lower // scale
            upper = (upper * base_upper + scale - 1) // scale
        exponent //= 2
        if exponent:
            base_lower = base_lower * base_lower // scale
            base_upper = (base_upper * base_upper + scale - 1) // scale
    return lower, upper


@dataclass(frozen=True, kw_only=True)
class GeometricBrokerage:
    """SELL-only quantity-dependent fee; round the TOTAL fee half up once.

    r = 10000 / (10000 + impact_bps)
    unrounded proceeds = unit_price * (r + r**2 + ... + r**quantity)
    fee = gross_amount - unrounded proceeds

    A sale is charged its share of the team's cumulative fee on the listing,
    total(prior + quantity) - total(prior), so splitting one sale into many
    requests pays the same total as selling it at once.

    Uses exact integer rational arithmetic, not Marketflow's binary floats.
    Short quantities are calculated exactly; larger quantities use rigorous
    bounds and return a fee only when BOTH bounds round to the same integer.
    """

    impact_bps: int

    def __post_init__(self) -> None:
        if type(self.impact_bps) is not int or not 0 <= self.impact_bps <= 10_000:
            raise ValueError("Impact must be whole basis points between 0 and 10000.")

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
        if (
            type(quantity) is not int
            or not 1 <= quantity <= MAX_CREDITS
            or type(unit_price) is not int
            or not 0 <= unit_price <= MAX_CREDITS
            or type(gross_amount) is not int
            or not 0 <= gross_amount <= MAX_CREDITS
            or gross_amount != quantity * unit_price
            or type(prior_quantity) is not int
            or not 0 <= prior_quantity <= MAX_CREDITS
        ):
            raise ValueError("Invalid whole-credit brokerage inputs.")
        if self.impact_bps == 0 or gross_amount == 0:
            return 0
        return self._total_fee(prior_quantity + quantity, unit_price) - self._total_fee(
            prior_quantity, unit_price
        )

    def _total_fee(self, quantity: int, unit_price: int) -> int:
        """Rounded fee for selling `quantity` units in one go (0 for none)."""
        if quantity == 0:
            return 0
        gross_amount = quantity * unit_price
        impact = self.impact_bps
        numerator = 10_000
        denominator = numerator + impact
        if quantity <= 32:
            denominator_power = denominator**quantity
            divisor = impact * denominator_power
            proceeds_numerator = unit_price * numerator * (denominator_power - numerator**quantity)
            fee_numerator = gross_amount * divisor - proceeds_numerator
            return (2 * fee_numerator + divisor) // (2 * divisor)

        # Exact half-credit ties can only occur for short quantities: the reduced
        # revenue denominator is b**q / gcd(price, b**q), b>=2. q>32 and the
        # INTEGER price limit make that denominator >2. The exact branch above
        # therefore handles all ties without an endless precision refinement.
        for bits in (64, 128, 256):
            scale = 1 << bits
            power_lower, power_upper = _power_bounds(numerator, denominator, quantity, scale)
            divisor = impact * scale
            fee_lower = max(
                0, gross_amount * divisor - unit_price * numerator * (scale - power_lower)
            )
            fee_upper = min(
                gross_amount * divisor,
                gross_amount * divisor - unit_price * numerator * (scale - power_upper),
            )
            rounded_lower = (2 * fee_lower + divisor) // (2 * divisor)
            rounded_upper = (2 * fee_upper + divisor) // (2 * divisor)
            if rounded_lower == rounded_upper:
                return rounded_lower
        # Bounded computation, never silently approximate a financial result.
        raise ConfigurationRequired("Brokerage rounding could not be resolved safely.")


# Selected after the user delegated the decision: Marketflow's 1/1.001 factor.
# Immutable, stateless policy. Runtime callers can replace it or explicitly disable resale.
DEFAULT_BROKERAGE = GeometricBrokerage(impact_bps=10)
