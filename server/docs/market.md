# Module G: Market & Round Lifecycle

Code: `app/modules/market/`. Owns *when* and *what* can be traded: the market, its rounds, and the listings in each round, including stock.

## Entities owned

| Table | Purpose |
|---|---|
| `market` | Event marketplace. At most one has `is_active = true` (partial unique index). |
| `market_round` | A trading or auction round: `kind`, `status`, timestamps, `version` (bumped on each transition). At most one `open`/`paused` round per market (partial unique index). |
| `market_listing` | A widget in a round: `base_price`, `supply_total`, `stock_remaining`, `max_per_purchase`. Unique per (round, widget). |
| `market_round_event` | Append-only log of every transition: action, from/to status, actor, reason, time. |
| `market_auction_lot` | Units of an auction-round listing held for one Auction Engine (J) auction, keyed by J's auction id. Taken out of `stock_remaining` when created; `consumed` once J awards them. |

IDs of the market, rounds, listings and widgets are **UUIDs** (they cross module boundaries; I/J use UUIDs throughout). The append-only logs keep integer keys, since they are ordered by them.

Credits are integers (`BIGINT` prices); no floats anywhere.

**Infinite supply** is `supply_total = stock_remaining = NULL` (a CHECK constraint keeps the two consistent). In the API, requests send `"supply": "infinite"`; responses send `"infinite_supply": true, "stock_remaining": null`.

## Round lifecycle

```
          open              pause
  draft ────────► open ◄──────────► paused
                   │      open       │
                   └──── close ──────┘
                           ▼
                        closed ── finalize ──► finalized
```

- Only organizers can change state. Anything not drawn above returns `409 INVALID_ROUND_TRANSITION`.
- **First open** needs at least one listing (`ROUND_HAS_NO_LISTINGS`) and no archived widgets (`ROUND_HAS_ARCHIVED_WIDGETS`). It sets `opened_at`, which anchors the pricing intervals. Resuming from `paused` keeps `opened_at`.
- **Listings** can only be added, edited or deleted while the round is a `draft` (`ROUND_NOT_EDITABLE`). Rounds that have run never change, so history is preserved. Use a new round to offer the same widget at a different price or supply.
- **Concurrency:** transitions take the round row `FOR UPDATE`, so racing organizers are serialised and the second one acts on fresh state. Pause-then-close is valid; close-then-pause gets `INVALID_ROUND_TRANSITION`. To refuse acting on a stale *screen*, clients send the `expected_version` they saw; a mismatch gives `ROUND_VERSION_CONFLICT`, checked first.
- **Draft edits** take the round row `FOR UPDATE` and re-read the round and listing under it. Edits are serialised with each other and with opening: no edit lands after a concurrent open, opening sees every committed edit, and an edit to a listing deleted meanwhile returns `LISTING_NOT_FOUND`.
- **Pausing** rejects new trades and bids. Reads keep working.
- **Close boundary:** trades hold a `FOR SHARE` lock on the round row (see `lock_listing_for_trade`), and the transition waits for them. A trade that passed the open check commits first; every trade that starts afterwards sees the new status. `closed_at` is never earlier than the latest committed trade on the round, so a close request stamped before an in-flight trade cannot freeze prices "in the past". This is covered by `test_close_waits_for_in_flight_purchase` and `test_close_never_predates_a_committed_trade`.
- **Restart/recovery:** all state lives in Postgres. No in-process timers or caches, so nothing is lost on a Worker restart.

## Participant API (backend JWT, any team)

| Method | Path | Notes |
|---|---|---|
| GET | `/market` | `{market, current_round, server_time}`. |
| GET | `/market/rounds/current` | The open/paused round, else the most recent closed/finalized one. Drafts are never shown. `404 NO_CURRENT_ROUND` before the first round opens. |
| GET | `/market/listings` | Listings of the current round, each with its server-side price (see [pricing.md](pricing.md)). |

```http
GET /market/listings
```
```json
{
  "round": {"id": 1, "sequence": 1, "name": "Round 1", "kind": "trading", "status": "open",
            "opened_at": "2026-10-12T09:00:00Z", "paused_at": null, "closed_at": null, ...},
  "listings": [
    {"id": 1, "round_id": 1, "widget_id": 3, "widget_name": "Button", "description": "An interactive button.", "base_price": 100,
     "infinite_supply": false, "supply_total": 10, "stock_remaining": 7, "sold_out": false,
     "max_per_purchase": null,
     "price": {"amount": 115, "strategy": "dynamic", "interval_index": 4,
               "valid_until": "2026-10-12T09:10:00Z"}},
    {"id": 2, "widget_id": 4, "infinite_supply": true, "supply_total": null, "stock_remaining": null,
     "price": {"amount": 50, "strategy": "static", "interval_index": 0, "valid_until": null}, ...}
  ],
  "server_time": "2026-10-12T09:08:12Z"
}
```

`description` is the optional catalog description (up to 500 characters), or `null` when unset.
It is also returned with widget details at `/widgets` and with each item at `/inventory`.
Organizers set or update it through `/admin/widgets`; omitting it in a PATCH preserves
the current value, and sending `null` clears it. The column already exists in migration
`0003_catalog`, so this API expansion requires no additional database migration.

## Organizer API (organizer role; every route is under `/admin/market`)

| Method | Path | Body / notes |
|---|---|---|
| POST | `/admin/market` | `{"name"}`. `409 MARKET_ALREADY_ACTIVE` if one exists. |
| GET | `/admin/market/rounds` | All rounds, drafts included. |
| POST | `/admin/market/rounds` | `{"name", "kind": "trading"\|"auction", "scheduled_open_at"?, "scheduled_close_at"?, "listings": [ListingCreate]}`. Creates a draft with the next `sequence`. |
| GET | `/admin/market/rounds/{id}` | Round with listings and their pricing configuration. |
| GET | `/admin/market/rounds/{id}/events` | Transition log. |
| POST | `/admin/market/rounds/{id}/open\|pause\|close\|finalize` | Optional `{"expected_version": 3, "reason": "..."}`. |
| POST | `/admin/market/rounds/{id}/listings` | `ListingCreate` (draft only). |
| PATCH | `/admin/market/listings/{id}` | Any subset of `ListingCreate` fields (draft only). |
| DELETE | `/admin/market/listings/{id}` | Draft only. |
| POST | `/admin/market/listings/{id}/auction-lots` | `{"auction_id", "quantity"}`. Holds units for a J auction (create the auction in J first, then the lot, then open the auction). Auction round, open or paused, finite listing. `409 AUCTION_LOT_EXISTS`, `OUT_OF_STOCK`, `INFINITE_SUPPLY`, `ROUND_NOT_LIVE`, `WRONG_ROUND_KIND`. |
| DELETE | `/admin/market/auction-lots/{auction_id}` | Release an unconsumed lot back to stock. `409 AUCTION_LOT_MISSING`. |

`ListingCreate`:
```json
{"widget_id": "6f1c…-uuid", "base_price": 100, "supply": 10, "max_per_purchase": 2,
 "pricing": {"strategy": "dynamic", "params": {"interval_seconds": 120}}}
```
`supply` is a non-negative integer or `"infinite"`. `pricing` defaults to `{"strategy": "static"}`.

### Errors

Every error looks like `{"error": {"code", "message", "context"}}`. The final shape belongs to Module A.

| Status | Code |
|---|---|
| 401 / 403 | `UNAUTHENTICATED`, `FORBIDDEN` |
| 404 | `NO_ACTIVE_MARKET`, `NO_CURRENT_ROUND`, `ROUND_NOT_FOUND`, `LISTING_NOT_FOUND`, `WIDGET_NOT_FOUND` |
| 409 | `MARKET_ALREADY_ACTIVE`, `INVALID_ROUND_TRANSITION`, `ROUND_VERSION_CONFLICT`, `ANOTHER_ROUND_LIVE`, `ROUND_HAS_NO_LISTINGS`, `ROUND_HAS_ARCHIVED_WIDGETS`, `ROUND_NOT_EDITABLE`, `DUPLICATE_LISTING`, `WIDGET_ARCHIVED`, `ROUND_CREATE_CONFLICT` |
| 400 | `INVALID_SCHEDULE`, `INVALID_PRICE`, `INVALID_SUPPLY`, plus pricing codes from [pricing.md](pricing.md) |
| 422 | Request body schema violations (FastAPI default) |

## Internal contract for Modules I (Transaction) and J (Auction)

These live in `app/modules/market/service.py` and run inside the caller's transaction. They never commit.

| Function | Contract |
|---|---|
| `lock_listing_for_trade(session, listing_id, *, kind)` | Call first. Takes `FOR SHARE` on the round row and checks that `status == open` and `kind` matches (`RoundKind.TRADING` for I, `RoundKind.AUCTION` for J). Returns a `TradableListing` snapshot. Errors: `LISTING_NOT_FOUND` (drafts too), `ROUND_NOT_OPEN` (context `status`), `WRONG_ROUND_KIND`. |
| `take_stock(session, listing_id, quantity)` | Atomic conditional `UPDATE … WHERE stock_remaining >= qty`, a no-op for infinite supply. Enforces `max_per_purchase`. Returns remaining stock (`None` = infinite). Errors: `OUT_OF_STOCK` (context `available`), `QUANTITY_LIMIT_EXCEEDED`, `INVALID_QUANTITY`. |
| `return_stock(session, listing_id, quantity)` | For resale once its rules exist. Infinite stays infinite. |
| `lock_round(session, round_id, *, exclusive=False)` | Fresh read of a round under `FOR SHARE`/`FOR UPDATE`, for anything that must stay true until commit. |
| `current_round(session)`, `get_round`, `get_listing` | Plain reads (may be cached in the session; do not base mutations on them). |
| `create_auction_lot`, `guard_auction_lot`, `consume_auction_lot`, `release_auction_lot` | The auction side. `guard_auction_lot` locks the round `FOR SHARE` and the lot `FOR UPDATE`, **never the listing row**: trades lock ledger accounts before the listing, so locking it here could deadlock with a trade. Bidding needs an `open` round and an unconsumed lot; settling works after close. |

**I/J use these through `app/modules/market/gateway.py` (`MarketGatewayImpl`, the `MarketGateway` implementation).** It maps G's codes to the port errors I/J document (`LISTING_NOT_FOUND`/`WRONG_ROUND_KIND` → `INVALID_LISTING`, `ROUND_NOT_OPEN` → `MARKET_NOT_OPEN`, `OUT_OF_STOCK` → `INSUFFICIENT_STOCK`, `AUCTION_LOT_MISSING` → `CONFIGURATION_REQUIRED`). Resale goes back into the same listing and only finite listings accept it. `MarketGateway.release_auction_lot` backs the runtime's `NoBidHandler` if organizers decide unsold lots return to stock. `tests/test_gh_ij_integration.py` runs I/J end to end on these adapters.

**Purchase recipe for Module I** (lock order: round → pricing row → listing stock → ledger → inventory; every stock change must happen under the pricing-row lock, which is why `get_current_price` comes before `take_stock`):

```python
now = get_now()  # use one `now` for the whole transaction
listing = market.lock_listing_for_trade(session, listing_id, kind=RoundKind.TRADING)
quote = pricing.get_current_price(session, listing_id, now)   # authoritative price; ignore client price
market.take_stock(session, listing_id, quantity)
pricing.record_trade(session, listing_id, quantity, TradeSide.BUY, now)
ledger.debit(team_id, quote.price * quantity, reference=...)  # Module E
inventory.increment(team_id, listing.widget_id, quantity)     # Module F
session.commit()          # all or nothing
```

`tests/test_trade_contract.py` and `tests/test_postgres_concurrency.py` run this recipe without ledger and inventory. They show that:
- the last units go to exactly as many buyers as there is stock;
- a failed purchase leaves nothing changed;
- a close waits for an in-flight purchase.

## Integration note

- **Calls:**
  - Catalog (D) `get_widget(session, id)`: existence and `archived` check when listing a widget and when opening a round.
  - Pricing (H) `configure_listing`, `remove_listing`, `get_config`, `on_round_opened`, `quote_many`.
- **Called by:** Transaction (I) and Auction (J) through `MarketAdapter` (above). Admin (K) may proxy the organizer routes.
- **Exposes:** the participant and organizer routes above. `market_listing.id` is the listing ID used everywhere else.

## Decisions taken here that need lead/team approval (spec TBDs)

1. **State machine** as drawn above. Questions: is there a "cancel draft" action? Should a closed round be re-openable? (Not allowed now.)
2. **Organizer-only lifecycle.** `scheduled_open_at`/`scheduled_close_at` are stored and shown but nothing fires automatically. Auto open/close would need a Cron Trigger, which Module M would own.
3. **Infinite = NULL** in the DB and `"infinite"` in requests.
4. **Close boundary:** in-flight trades that already passed the round lock commit, then the close applies.
5. **One active market**, and **one live round per market**. Do auction and trading rounds ever need to run in parallel? If so, the live-round index becomes per (market, kind).
6. **Listings are frozen once a round opens.** There is no mid-round restock. Changing that also needs a pricing change, because supply reconstruction assumes stock moves only through trades.
7. **Integer credits.**
8. **UUID IDs** for market, round, listing and widget, matching I/J. Catalog (D) must keep widget IDs UUIDs.
9. **Auction lots** are held once the round is open (listings frozen), come out of stock immediately, and need a finite listing. What happens to an unsold lot (`release_unsold_lot` or not) is an organizer decision.
