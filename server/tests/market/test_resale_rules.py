"""Financial invariants independent of the transport and interval sampling clock."""

from copy import deepcopy
from random import Random
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.trading.models import ResaleAccount, ResalePosition
from app.trading.risk import ResaleRules


def position(quantity=20, price=100, external=0):
    return ResalePosition(
        team_id=uuid4(),
        widget_id="button",
        quantity=quantity,
        cost=quantity * price,
        external_units=external * quantity,
        reward_units=quantity,
    )


def account():
    return ResaleAccount(team_id=uuid4())


def test_no_external_demand_never_pays_more_than_cost_at_any_quote():
    rules = ResaleRules()
    for price in range(1, 1000):
        p = position()
        a = account()
        try:
            q = rules.quote(p, a, quantity=20, gross=price * 20, external=0, funding=5000)
        except AppError as exc:
            assert exc.code == "RESALE_LOSS_LIMIT"
        else:
            assert q["final_amount"] <= p.cost and q["profit"] == 0


def test_split_sales_cannot_unlock_more_profit_or_reduce_total_fee():
    rules = ResaleRules()
    p, a = position(quantity=10), account()
    bulk = rules.quote(p, a, quantity=10, gross=1100, external=1, funding=5000)
    split_total = split_fee = 0
    for _ in range(10):
        q = rules.quote(p, a, quantity=1, gross=110, external=1, funding=5000)
        split_total += q["final_amount"]
        split_fee += q["fee"]
        rules.consume(p, a, q)
    assert split_total <= bulk["final_amount"]
    # Currency rounding is cumulative for the same notional.
    p, a = position(quantity=10), account()
    for _ in range(10):
        q = rules.quote(p, a, quantity=1, gross=100, external=0, funding=5000)
        rules.consume(p, a, q)
    assert a.fee_notional == 1000 and a.loss_realized == 10


def test_repeated_coalition_pumps_cannot_compound_past_event_allowance():
    rules = ResaleRules()
    a = account()
    external = 0
    for _ in range(100):
        p = position(external=external)
        external += 20
        q = rules.quote(p, a, quantity=20, gross=3000, external=external, funding=5000)
        rules.consume(p, a, q)
        assert a.profit_paid <= 100  # 2% of starting credits across all cycles
    assert 0 < a.profit_paid <= 100


def test_losses_do_not_replenish_profit_allowance_and_loss_budget_stops_spam():
    rules = ResaleRules()
    a = account()
    a.profit_paid = 100
    for _ in range(25):
        p = position(quantity=10)
        q = rules.quote(p, a, quantity=10, gross=1000, external=1000, funding=5000)
        rules.consume(p, a, q)
        assert a.profit_paid == 100
    with pytest.raises(AppError, match="event loss limit"):
        rules.quote(position(quantity=10), a, quantity=10, gross=1000, external=1000, funding=5000)


def test_random_partial_sales_conserve_acquisition_cost_and_caps():
    rng = Random(13)
    rules = ResaleRules()
    for _ in range(100):
        qty, unit = rng.randrange(1, 100), rng.randrange(100, 500)
        p, a = position(qty, unit), account()
        original = p.cost
        consumed = proceeds = 0
        while p.quantity:
            n = rng.randrange(1, p.quantity + 1)
            q = rules.quote(p, a, quantity=n, gross=n * unit, external=0, funding=1_000_000)
            consumed += q["cost_basis"]
            proceeds += q["final_amount"]
            rules.consume(p, a, q)
        assert consumed == original and proceeds <= original
        assert p.cost == 0 and p.external_units == 0


def test_organizer_removal_cannot_leave_old_cost_entitlement():
    p = position(20, 100)
    ResaleRules.synchronize(p, 5)
    assert (p.quantity, p.cost) == (5, 500)
    ResaleRules.synchronize(p, 0)
    assert (p.quantity, p.cost, p.external_units) == (0, 0, 0)


def test_rejected_quote_does_not_mutate_account_or_position():
    p, a = position(), account()
    before = deepcopy((p.model_dump(), a.model_dump()))
    with pytest.raises(AppError):
        ResaleRules().quote(p, a, quantity=20, gross=1000, external=0, funding=5000)
    assert before == (p.model_dump(), a.model_dump())


def test_free_units_cannot_receive_a_self_pump_premium():
    rules = ResaleRules()
    p, a = position(quantity=0), account()
    q = rules.quote(p, a, quantity=1000, gross=102000, external=0, funding=0, gift_unit_price=100)
    assert q["final_amount"] == 99000 and q["profit"] == 0
