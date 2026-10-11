"""Resale safeguards use one shared public price and explainable settlement limits.

Only growth in OTHER teams' net holdings can unlock a gain. Acquisition cost is
carried across listings/rounds; realized gains consume a non-renewing fraction of
starting credits. Losses never replenish that allowance. All arithmetic is integer.
"""

from dataclasses import dataclass

from app.core.errors import AppError
from app.trading.models import ResaleAccount, ResalePosition


@dataclass(frozen=True)
class ResaleRules:
    profit_bps: int = 500
    event_profit_bps: int = 200
    max_loss_bps: int = 500
    fee_bps: int = 100

    @staticmethod
    def synchronize(position: ResalePosition, owned: int) -> None:
        # Organizer removals must not leave an inflated acquisition-cost entitlement.
        if owned < position.quantity:
            position.cost = position.cost * owned // position.quantity
            position.external_units = position.external_units * owned // position.quantity
            position.quantity = owned

    def quote(
        self,
        position: ResalePosition,
        account: ResaleAccount,
        *,
        quantity: int,
        gross: int,
        external: int,
        funding: int,
        gift_unit_price: int = 0,
    ) -> dict:
        # Gifts/untracked legacy units have zero protected cost and zero profit eligibility.
        # They may be cashed out once at no more than base price, without a self-pump premium.
        tracked = min(quantity, position.quantity)
        cost = position.cost * tracked // position.quantity if position.quantity else 0
        baseline = (
            (position.external_units + position.quantity - 1) // position.quantity if position.quantity else external
        )
        growth = max(0, external - baseline)
        remaining = max(0, funding * self.event_profit_bps // 10000 - account.profit_paid)
        # A small unrelated purchase cannot legitimize a huge self-pump: reward scales with new external units.
        bonus = min(
            cost * self.profit_bps // 10000,
            growth * cost * self.profit_bps // (max(1, position.reward_units) * 10000),
            remaining,
        )
        gift_value = min(gross * (quantity - tracked) // quantity, gift_unit_price * (quantity - tracked))
        capped = min(gross, cost + bonus + gift_value)
        # Cumulative rounding makes split sales pay exactly the same aggregate fee.
        fee = (account.fee_notional + capped) * self.fee_bps // 10000 - account.fee_notional * self.fee_bps // 10000
        final = max(0, capped - fee)
        protected_final = max(0, final - gift_value)
        if cost and protected_final * 10000 < cost * (10000 - self.max_loss_bps):
            raise AppError(
                "RESALE_LOSS_LIMIT",
                "This sale exceeds the loss limit; keep the widgets or wait for a better price.",
                409,
                context={"cost_basis": cost, "minimum_proceeds": (cost * (10000 - self.max_loss_bps) + 9999) // 10000},
            )
        loss = max(0, cost - protected_final)
        if account.loss_realized + loss > funding * self.max_loss_bps // 10000:
            raise AppError(
                "RESALE_LOSS_BUDGET", "This sale would exceed your event loss limit; keep the widgets or wait.", 409
            )
        return {
            "loss": loss,
            "quantity": quantity,
            "tracked": tracked,
            "cost_basis": cost,
            "market_amount": gross,
            "capped_amount": capped,
            "fee": fee,
            "final_amount": final,
            "profit": max(0, protected_final - cost),
            "profit_budget_remaining": remaining,
            "external_demand_growth": growth,
        }

    @staticmethod
    def consume(position: ResalePosition, account: ResaleAccount, quote: dict) -> None:
        tracked = quote["tracked"]
        if position.quantity:
            position.external_units -= position.external_units * tracked // position.quantity
        position.quantity -= tracked
        position.cost -= quote["cost_basis"]
        account.fee_notional += quote["capped_amount"]
        account.profit_paid += quote["profit"]
        account.loss_realized += quote["loss"]
