# Module H: Pricing Engine

Code: `app/modules/pricing/`. Owns every price calculation. Market, Transaction and Auction never compute prices themselves.

## Entities owned

| Table | Purpose |
|---|---|
| `listing_pricing` | One row per listing, holding the authoritative pricing state: `strategy`, `params` (JSONB), `params_version`, `current_price`, `interval_index`, and the units bought/sold in the current interval. |
| `price_history` | Append-only snapshots. One `initial` row when the round opens, one `interval` row each time the price actually changes (with the demand/supply that caused it), and one `config_change` row per organizer edit. |

A transaction stores the price it was charged in its own record (Module I). Nothing here rewrites past prices.

## Strategy interface

`app/modules/pricing/strategies.py`:

```python
class PricingStrategy[P: BaseModel](ABC):
    key: ClassVar[str]                 # "static", "dynamic", ...
    params_model: ClassVar[type[BaseModel]]
    def interval_seconds(self, params: P) -> int | None   # None = price never moves
    def next_price(self, params: P, data: PricingInput) -> int
    def check_supply(self, params: P, *, infinite_supply: bool) -> None   # optional

register(MyStrategy())   # new strategies need no change in Market or Transaction
```

Strategies are pure functions of `PricingInput(base_price, current_price, bought, sold, supply_at_start)`, so they are deterministic and unit-testable. `GET /admin/market/pricing-strategies` returns each strategy's parameter JSON Schema for admin UIs.

### `static` (default)

The price is always `base_price`. Use it for fixed-price and infinite listings.

### `dynamic` (supply-demand)

Every `interval_seconds` after the round opens:

```
demand  = units_bought − units_sold during the interval   (min 0)
ratio   = demand / (supply_at_interval_start × target_fraction)
price   = current_price × multiplier(ratio)     # first band with ratio < below
price   = round_half_up(price / price_step) × price_step
price   = clamp(price, ceil(base × min_factor), floor(base × max_factor))   # both on the step grid
```

| Param | Default | Meaning |
|---|---|---|
| `interval_seconds` | 120 | Repricing period (10 s to 1 day). Locked once the round opens. |
| `target_fraction` | 0.1 | Share of remaining stock per interval treated as "normal" demand. |
| `bands` | `<0.5→×0.9, <1→×1, <1.5→×1.15, <2→×1.3, else ×1.5` | Ascending `below` bounds; the last band is the catch-all with `below: null`. |
| `min_factor` / `max_factor` | 0.75 / 2.0 | Price guard rails relative to `base_price`. |
| `price_step` | 5 | Prices are multiples of this. |

The bands and guard rails follow the provisional rules in the earlier event_12th backend, but per interval rather than per round. A sold-out listing (supply 0) keeps its price. Dynamic pricing on infinite supply is rejected (`PRICING_REQUIRES_FINITE_SUPPLY`).

## How repricing runs: lazy, no scheduler

Interval *k* covers `[opened_at + k·I, opened_at + (k+1)·I)`, ending at `closed_at` once the round closes. Nothing runs on a timer. Instead:

- **Reads** (`quote`, the listing endpoints) compute the price at `now` from the stored state without writing anything.
- **Mutations** (`get_current_price`, `record_trade`, `update_params`) lock the `listing_pricing` row `FOR UPDATE`, apply every boundary that has passed in order, and persist the result inside the caller's transaction. If that transaction rolls back, the price change rolls back too.

Each step uses only committed inputs: the trades recorded in that interval and the supply at its start, reconstructed as `stock_now + bought − sold`. So the price is the same however often, or how late, anyone looks (`test_lazy_settlement_matches_eager`). Long quiet stretches settle in O(1) once the price stops moving. This design fits Cloudflare Workers: no background process and no in-memory state.

All purchases within one interval pay the same price. The row lock makes concurrent buyers settle the interval exactly once and then queue (`test_concurrent_buys_in_one_interval_pay_one_price`).

**Trade time vs lock wait.** A trade is priced at the `now` its caller passes in, but prices never move backwards. If another trade already settled the listing into a later interval while this one waited for the lock, this one pays and counts in that later interval. A request stamped just before a boundary that waits past it pays the earlier interval's price. Either way the result is decided by commit order and is reproducible. If the lead prefers "time of lock acquisition", `get_current_price` can read the clock itself after taking the pricing lock; that is a small change but alters the contract, so it is listed as a TBD below.

All mutating functions lock round (shared) → pricing row (exclusive) and re-read both, and quotes read pricing, listing and round in one statement, so neither acts on a stale or mixed snapshot (`tests/test_stale_reads.py`).

## API

Participant (backend JWT):

| Method | Path | Response |
|---|---|---|
| GET | `/market/listings/{id}/price` | `{listing_id, price, strategy, interval_index, valid_until, server_time}`. `valid_until` is the next repricing boundary; `null` means no change is scheduled. Draft listings return 404. |

Prices are also embedded in `GET /market/listings` (see [market.md](market.md)).

Organizer (every route is under `/admin/market`):

| Method | Path | Notes |
|---|---|---|
| GET | `/admin/market/pricing-strategies` | Keys plus parameter JSON Schema. |
| GET | `/admin/market/listings/{id}/pricing` | Current configuration and state. |
| PATCH | `/admin/market/listings/{id}/pricing` | `{"strategy"?, "params"}`. `params` replaces the whole parameter set. **Draft:** strategy and params may change, and the price resets to base. **Open/paused:** params only, applied from the next boundary after elapsed intervals settle under the old params. Changing strategy gives `PRICING_STRATEGY_LOCKED`; changing the interval gives `PRICING_INTERVAL_LOCKED`. **Closed:** `PRICING_NOT_EDITABLE`. |
| GET | `/admin/market/listings/{id}/price-history` | Snapshot list. |

Errors: `UNKNOWN_PRICING_STRATEGY`, `INVALID_PRICING_PARAMS` (context lists the field errors), `PRICING_REQUIRES_FINITE_SUPPLY`, `PRICING_STRATEGY_LOCKED`, `PRICING_INTERVAL_LOCKED`, `PRICING_NOT_EDITABLE`, `LISTING_NOT_FOUND`, `INVALID_QUANTITY`.

## Internal contract for Module I

In `app/modules/pricing/service.py`; nothing in it commits:

| Function | Contract |
|---|---|
| `get_current_price(session, listing_id, now) -> PriceQuote` | Authoritative charge price. Locks the pricing row until the transaction ends. Call it after `market.lock_listing_for_trade`. |
| `record_trade(session, listing_id, qty, TradeSide.BUY\|SELL, now)` | Counts the trade toward this interval's demand. Call it in the same transaction as `take_stock`/`return_stock`, with the same `now`. |
| `recalculate(session, listing_id, now)` | Settles elapsed intervals now. |
| `quote(session, listing_id, now)` / `quote_many(...)` | Read-only participant price. |

The client never sends a price. Module I charges `quote.price × quantity`. See the purchase recipe in [market.md](market.md#internal-contract-for-modules-i-transaction-and-j-auction).

Auction (J) does not use this engine for bids or winner selection.

I/J call this through `app/modules/pricing/adapter.py` (`PricingAdapter`, the `PricingPort` implementation): `get_unit_price` → `get_current_price(...).price`, `record_trade` → `record_trade(..., TradeSide.BUY|SELL, now)`, in I's transaction.

## Integration note

- **Reads:**
  - `market_listing` for `base_price`, `stock_remaining` and the infinite flag.
  - `market_round` for `opened_at`, `closed_at` and `status`.
- **Called by:**
  - Market (G) for configuration while a round is a draft and for embedded quotes.
  - Transaction (I) for prices and recording trades.
  - Admin (K) through the routes above.

## Decisions that need lead/team approval (spec TBDs)

1. **Formula and defaults** above, especially `target_fraction` and the band table. These are tuning knobs and can be changed per listing without code changes.
2. **Trigger:** time-based intervals anchored at `opened_at`. The clock keeps running while a round is paused, so a long pause settles as quiet intervals and the price decays toward `min_factor`. The alternative is to freeze the clock during pauses, which needs accumulated pause time stored on the round.
3. **Price history is kept**, but only rows where the price changes.
4. **Guard rails** are 0.75× to 2× base by default, configurable per listing.
5. **Resale effect:** units sold back count as negative demand in the interval. The resale price and brokerage formula still need a decision. They belong to I, and if they should depend on the market price, they would call `get_current_price`.
6. **Interval length is locked once a round opens**, because changing it would move every boundary.
7. **Trade time:** the caller's `now` (request time) with no-rewind, versus the time the pricing lock is acquired. See "Trade time vs lock wait" above.
