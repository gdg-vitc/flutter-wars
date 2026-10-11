# Market safeguards and participant rules

These rules implement the October 10 meeting and PR13 bug review. Start with a
static trading round and organizer inventory grants, hold a separate manually
controlled auction round for exclusive widgets, then open dynamic trading rounds.
Use the ordinary round/auction controls; the backend does not automatically change
phases. Previously acquired widgets and net market demand carry into later rounds.
Widgets offered through auctions cannot be resold through ordinary trading.

## Visible rules

Everyone sees the same public price. Dynamic prices stay between 98% and 102% of
base price, moving by at most 1% of base per interval. Low-priced widgets that
cannot move a whole credit within that bound stay stable. Static prices stay fixed.
The default interval is 120 seconds; pausing freezes the pricing clock and preserves
the remaining part of its interval. Quiet intervals do not repeatedly multiply prices.

A purchase pays the public price. Resale uses the team's actual acquisition cost,
a 1% fee, and the following protections:

- A team's own buying never unlocks profit on its acquired units.
- Growth in other teams' net purchases since acquisition can unlock modest profit.
  The gross bonus scales with that growth and is capped at 5% of acquisition cost.
- Total positive realized profit consumes a team-wide allowance of 2% of granted
  starting credits. Spending profits, realizing losses, switching widgets, splitting
  sales, or opening another round never replenishes it.
- A sale is refused if its loss exceeds 5% of allocated acquisition cost, or if total
  realized losses would exceed 5% of granted starting credits. Widgets remain owned
  after refusal. This is a sale restriction, not a promise of immediate liquidity.
- Fees round cumulatively in whole credits, so splitting a sale cannot avoid fees.
- Free organizer grants have no speculative cost basis; teams may redeem their actual
  granted inventory at the lower of public price and base price, less fees. They cannot regenerate free units.

Example: a team with 5,000 granted credits has at most 100 credits of positive
realized trading gains across the event. Buying 20 widgets at 100, raising the public
price itself, and reselling them cannot increase its balance. Other teams' later
buying can unlock a small gain within both caps. These rules limit coordinated
pumping; they do not claim to identify colluding accounts or eliminate every
profitable coordinated strategy. Team identities and organizer grants remain trusted
administrative controls.

## Why this design

The original multiplier bands compounded prices from gross interval activity and
allowed a buyer to cash out its own price impact. The replacement is a custom
bounded demand curve, not a copied exchange formula. Net retained demand drives the
quote; a separate team-wide settlement rule prevents self-impact from paying out.

Uniswap's [oracle documentation](https://developers.uniswap.org/docs/protocols/v2/concepts/oracles)
and [protocol paper](https://docs.uniswap.org/whitepaper.pdf) describe manipulation
costs, time averaging, and transaction fees. Time averaging or fees alone do not
prove this game's wallet safe: it lacks an exchange's conserved collateral reserves.
Here, cost accounting and a non-renewing allowance provide the monetary bound.

The Nobel committee's [review of Thaler's research](https://www.nobelprize.org/uploads/2018/06/advanced-economicsciences2017.pdf)
explains how reference transactions and perceived fairness affect acceptance of price
changes. That supports small, predictable movements and explaining deductions before
confirmation. The SEC's [order-types guide](https://www.sec.gov/files/trading101basics.pdf)
explains price limits and why a displayed quote need not be the execution price;
optional request limits give participants comparable protection against stale quotes.
The percentages above are conservative game-design choices, not research-derived
universal thresholds. Present participants as people learning the rules; use plain
language, stable prices, and an explicit proceeds preview.

## Formula and accounting

For a finite listing, let `held = max(0, prior_round_net_units + initial_supply -
interval_start_stock + interval_buys - interval_sells)`. Reference units are
`max(1, initial_supply + prior_round_net_units)`. Pressure is
`min(1, held / (reference_units * target_fraction))`; target price is rounded
`base * (0.98 + 0.04 * pressure)`, then bounded by the allowed factors and the
per-interval movement limit. `target_fraction` defaults to 0.1.

A position stores quantity, total acquisition cost, quantity-weighted external
net-demand baseline, and acquisition units for scaling the bonus. Partial sales
allocate cost conservatively using integer arithmetic. A team account stores
cumulative fee notional, positive realized gains, and realized losses. Shared
team advisory locks and wallet/inventory locks serialize these changes in the same
transaction as inventory, credits, trade receipts, and pricing. Retries return the
original receipt instead of charging again. Organizer mutations write audit records
in their mutation transaction. Authentication and mutation reuse one DB session.

`RESALE_PROFIT_BPS=500`, `RESALE_EVENT_PROFIT_BPS=200`, and
`RESALE_MAX_LOSS_BPS=500` may be lowered in Worker configuration; validation prevents
raising them beyond these safety ceilings. Fees are fixed at 100 basis points.
Funding means ledger GRANT entries; additional authorized grants increase the
allowance proportionally. Credit adjustments and trading profits do not.

## HTTP client integration

- `GET /market/trading-rules` publishes limits and a plain-language explanation.
- `POST /market/resale-quote` accepts the same listing, quantity and retry-key shape
  as a sale. It returns public amount, allocated cost, cap, fee, net proceeds,
  external-demand growth, and remaining profit allowance. Preview can settle lazy
  pricing and initialize accounting rows, but never exchanges widgets or credits.
- `POST /market/purchase` accepts optional `max_unit_price`.
- `POST /market/sell` accepts optional `min_final_amount`. Refusal uses HTTP 409 and
  leaves financial state unchanged. A preview is not a reservation.
- Existing trade receipts retain `brokerage_amount = gross_amount - final_amount`;
  it includes both the resale cap deduction and the fee. Use the preview fields to
  display those separately. Clients should show net proceeds before confirmation.
- `GET /auctions` lists published auctions; organizers also have an admin list.
  Manual close stops bidding; cancel refunds reservations and returns its live lot.
  Ordinary lot release is restricted to draft auctions to avoid stranded bids.

Round Durable Objects retain hibernating authenticated sockets and alarm recovery.
Refresh bursts queue at most one successor behind the current refresh, sampling the
latest committed state instead of accumulating one DB refresh per incoming request.

## Migration and operational limits

Apply `0010_market_safeguards` to staging through the direct Neon connection before
publishing the Worker. It adds pause time, demand seeds, resale accounting, and an
auction CANCELLED enum value. Existing dynamic settings and prices are normalized
into the new narrow bounds. Historical receipts are never rewritten.

Legacy acquired holdings use the cheapest recorded purchase price as a conservative
cost basis; undocumented inventory remains a grant. Historic speculative gains
cannot be reconstructed reliably and are not retroactively charged. Previously
expensive holdings can fail the new loss protection and remain owned. Previously
elapsed pauses are not reconstructed; future pauses use the new clock. Deploy while
trading is frozen, preserving and restoring any existing operational freeze state.
Once live accounting exists, prefer a forward fix over a downgrade that discards it.

The PostgreSQL regression suite covers self-pumping, external-demand profit, cross-
round cost persistence, concurrent resale, quote limits, pause/resume, auction
refunds, disabled IDE keys, cumulative rounding, finite event allowances, and Durable
Object refresh bursts/cancellation. This is evidence for the tested invariants,
not a blanket guarantee that every line of the application is vulnerability-free.
