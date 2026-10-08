# Flutter Wars backend

Python + FastAPI + SQLModel on Neon PostgreSQL (through Cloudflare Hyperdrive), following *GDG Flutter Workshop: Final Modular Backend Architecture* v1.0.

## What is here

| Path | Module | Status |
|---|---|---|
| `app/modules/market/` | G: Market & Round Lifecycle | Implemented, incl. auction lots and the `MarketPort` adapter for I/J. See [docs/market.md](docs/market.md). |
| `app/modules/pricing/` | H: Pricing Engine | Implemented, incl. the `PricingPort` adapter for I/J. See [docs/pricing.md](docs/pricing.md). |
| `app/trading/` | I: Purchase/Trade Engine | Implemented against owner ports; runs on the real G/H adapters (`tests/test_gh_ij_integration.py`), Ledger/Inventory/Catalog are still test stand-ins. See [Trading and Auction modules](#trading-and-auction-modules-i-j) and [docs/INTEGRATION.md](docs/INTEGRATION.md). |
| `app/auction/` | J: Auction | Implemented against owner ports. Same docs as I. |
| `app/integration/` | I/J composition | `BackendModules` runtime plus the `MarketPort`/`PricingPort`/`LedgerPort`/`InventoryPort`/`CatalogPort` contracts other owners implement. |
| `app/core/` | A: Foundation, plus the B/K principal | **Placeholder.** One `get_session`, `get_principal`, `AppError` shape and `get_now` for every module (I/J's principal and errors derive from these), plus `/health` and `/ready`. `get_principal` rejects every request until Module B replaces it. |
| `app/modules/catalog/` | D: Widget Catalog | **Placeholder.** A minimal `widget` table plus `get_widget()`. |
| `migrations/` | M: Infrastructure | One Alembic chain: `0001` placeholder widget table (D replaces it), `0002` G/H tables, `0003` G auction lots, `0004` I/J tables (runs the reviewed `migrations/0001_modules_i_j.sql`). |
| `app/infra/` | M: Infrastructure | Infra settings/DB/probe helpers: validated settings, Neon/Hyperdrive connection targets, `/ready` health checks. |

Each module follows the same layout: `models.py` (SQLModel tables), `schemas.py` (API payloads), `service.py` (business rules and the internal contracts for other modules), `router.py` (thin FastAPI routes), plus `repository.py` in market for queries. Modules register in `app/main.py:MODULES`.

## Develop

```bash
cd server
uv sync
uv run pytest                    # in-memory SQLite: unit and API tests
DATABASE_URL=postgresql+psycopg://... uv run alembic upgrade head
DATABASE_URL=... uv run fastapi dev app/main.py
```

### Tests against PostgreSQL

Row locking and the partial unique indexes can only be proven on Postgres. The concurrency suite (`tests/test_postgres_concurrency.py`) runs only when `TEST_DATABASE_URL` points at one:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=flutter_modules_test -p 5432:5432 postgres:17
TEST_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:5432/flutter_modules_test uv run pytest
```

The suite drops and recreates every table, so never point it at a shared database; the I/J fixtures refuse any database whose name does not end in `_modules_test`.

## Trading and Auction modules (I, J)

Modules I and J are implemented here. Authentication, wallets, inventory, market
lifecycle, catalog, pricing algorithms, and infrastructure remain owned by their
respective teams. No Flutter validation is implemented.

Credits are **whole integers**, as agreed in this session. Each financial amount
is limited to PostgreSQL INTEGER (0–2,147,483,647). IDs are UUIDs. A purchase uses
one authoritative unit price for its entire quantity.

### Study the files in this order

1. `app/trading/models.py`: the permanent BUY/SELL receipt and database constraints.
2. `app/trading/schemas.py`: purchase/sale inputs and public receipts.
3. `app/integration/contracts.py`: agreements with the other module owners.
4. `app/trading/repository.py`: Trading's own database queries; never commits.
5. `app/trading/service.py`: purchase/resale orchestration and retry handling.
6. `app/integration/runtime.py`: one shared transaction; return only after commit.
7. `app/trading/policies.py`: configurable brokerage examples, with explicit rounding.
8. `app/auction/models.py`, `schemas.py`, `repository.py`, `service.py`: bids,
   reservation deltas, deterministic ties, and settlement.
9. The two `router.py` files and `app/main.py`: thin HTTP/auth integration.
10. `tests/test_modules.py`: executable examples, failures, and concurrency cases.

Your original Trading imports remain available through `trading/contracts.py` and
`trading/errors.py`. Canonical contracts/errors moved to `integration/` so Auction
can reuse them without depending on Trading internals.

### Install and test

From `server/`:

```sh
uv sync
uv run python scripts/test_postgres.py
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
```

The test script needs locally installed PostgreSQL executables on PATH. It starts
an isolated instance on an unused port, creates a disposable database, runs all
tests, and stops/deletes that instance afterward. It does not use your Neon DB or
an existing PostgreSQL instance.

Alternatively, provide `TEST_DATABASE_URL` for a disposable PostgreSQL database
whose name ends in `_modules_test`, then run `uv run pytest -q`. These tests
reset all I/J, G/H and test-adapter tables in that database. Never point them at an
application database. Without a test URL, database tests are explicitly skipped.

`tests/adapters.py` contains test-only owner tables and implementations. They are
never imported by the application and are not real implementations of E/F/G/H.

### Runtime wiring and authentication

Foundation provides a fresh SQLModel session factory and an adapter factory:

```python
runtime = BackendModules(
    session_factory=foundation_session_factory,
    adapter_factory=owner_adapter_factory,
    brokerage=approved_brokerage_policy,
)
app = create_app(runtime=runtime, principal_dependency=verified_jwt_principal)
```

Imports: `BackendModules` is in `app.integration.runtime`; `create_app` is in
`app.main`. This is a wiring template: the three named integrations must come
from their owning teams. Google/JWT issuance and team eligibility are not
reimplemented here. The principal dependency must verify signed JWTs and current
eligibility, and return `Principal`. Team API keys must not authenticate these
mutation routes. Finish authentication before creating the mutation session.

All adapters declare their `session` and must reference the EXACT supplied
session. All credits, reservations, stock/allocation, inventory, price effects,
and the business record must share that transaction. No adapter may commit,
roll back, close the session, or create another engine/session. A correctly bound
session alone cannot prevent a badly written adapter from committing: owner
contract tests must verify its implementation as well.

The default `app.main:app` is intentionally unconfigured: it starts and provides
`/health`, but it cannot authenticate participants or perform mutations. It is a
Foundation composition example, not a production-ready authentication setup.

### API

All participant routes require an authenticated eligible team.

| Method | Path | Meaning |
|---|---|---|
| POST | `/market/purchase` | BUY; body: listing_id, quantity, idempotency_key |
| POST | `/market/sell` | SELL to an owner-approved listing; same body fields |
| GET | `/transactions` | Own history; limit 1–100, offset >= 0 |
| GET | `/transactions/{id}` | Own receipt; another team's ID returns 404 |
| GET | `/auctions/{id}` | Public auction configuration and effective state |
| GET | `/auctions/{id}/my-bid` | Only this team's current bid |
| POST | `/auctions/{id}/bids` | Own new amount and idempotency_key |
| POST | `/admin/auctions` | Organizer: create DRAFT configuration |
| PATCH | `/admin/auctions/{id}/minimum-bid` | Organizer: configure DRAFT minimum |
| POST | `/admin/auctions/{id}/open` | Organizer: activate an allocated lot |
| POST | `/admin/auctions/{id}/close` | Organizer: mark CLOSED after fixed deadline |
| POST | `/admin/auctions/{id}/settle` | Organizer: atomic idempotent settlement |

Example purchase body (use actual configured listing UUID):

```json
{"listing_id":"550e8400-e29b-41d4-a716-446655440000","quantity":2,"idempotency_key":"fc0f719c-11f1-456a-a944-861b48a4d31e"}
```

Example bid body:

```json
{"amount":1000,"idempotency_key":"fc0f719c-11f1-456a-a944-861b48a4d31e"}
```

Business failures return `{"error":{"code":"INSUFFICIENT_CREDITS", "message":
"Not enough available credits."}}` (409 in this example). Not-found errors are
404; missing integrations/policies are 503; amount bounds are 422; unexpected
errors return a fixed 500 response. FastAPI handles request validation as 422 and
auth/organizer checks as 401/403. Coordinate this draft HTTP error contract and
unversioned routes with Foundation before merge.

Participant schemas expose no competitor bids, identity, timestamps, sequence,
history, ranking, highest amount, or winner. The settlement response is
organizer-only, even after settlement; post-settlement participant visibility
requires a separate approved policy. There are no debug/bid-list endpoints.
Do not enable SQL echo/parameter logging or log request bodies, bids, JWTs, or
exception arguments. Foundation/Observability must preserve that restriction.

### Transaction and concurrency guarantees

The runtime owns `with session.begin()`. Success responses are returned AFTER
that context commits. Any adapter error, flush error, or commit failure rolls
back. Neither engine catches a failure and continues the business transaction.

Use PostgreSQL READ COMMITTED. The tests use PostgreSQL, not SQLite or Python
locks. Owner adapters must follow this acquisition order:

1. I/J request-key transaction advisory lock (only retries with the same key).
2. Market's round lifecycle guard, then listing/allocation guard.
3. Auction row FOR UPDATE, for auction operations.
4. Catalog/price guards required by their owner policies.
5. Ledger account guards in sorted team UUID order.
6. Reservation/current bid and Inventory mutation locks.
7. Trade/bid receipt/result insertion and final state changes.

Bid creation and settlement use the same auction row guard. That prevents a bid
from changing the winner while settlement runs. Settlement locks EVERY involved
account in sorted order before settling or releasing any reservation. All wallet
spending/grants/reservations must use the same Ledger account guard.

A bid checks PostgreSQL `clock_timestamp()` after blocking account/auction guards,
just before reservation/update. `CURRENT_TIMESTAMP` would incorrectly use
transaction-start time. Acceptance is the guarded deadline check, not HTTP
arrival time; an admitted transaction can commit after the deadline. There is no
closing-time extension. Pricing obtains its quote under protected state and receives `record_trade` after
stock/inventory/ledger mutations. That hook applies approved repricing/history
effects before commit, or does nothing for static/scheduled strategies. It must
coordinate strategy updates with those same guards.

The test Market adapter uses FOR SHARE on the round and FOR UPDATE on the
listing: pause/close waits for an already-guarded transaction; subsequent
mutations see the new state. This is the proposed boundary contract, and the
Market owner must approve it before production integration. Archive, team-disable,
resale-to-listing eligibility, and emergency settlement policies remain owner
responsibilities, not hidden assumptions in I/J.

Ties use the accepted bid sequence allocated while holding the auction row lock.
This records which team reached its FINAL amount first; a later increase receives
a new order. `amount_reached_at` is recorded too. The order resolves identical
timestamps without relying on process clocks or arbitrary team IDs. One current
bid exists per team/auction; the entire configured lot goes to one highest bidder.

### Retry handling

The client creates a UUID for each intended operation and reuses it on retries.
BUY/SELL scope: team + transaction type + key. Bid scope: auction + team + key.
Transaction advisory locks serialize concurrent duplicate requests BEFORE stock,
state, or funds checks. Successful results and key uniqueness are persisted in
owned tables. BUY/SELL replay compares listing/quantity; bid receipts compare the
requested amount. Different inputs with the same key return a conflict.

Bid receipts preserve the ORIGINAL response, even after later increases or
closing. Auction ID is settlement's natural key; the unique result plus auction
lock prevents double debit/award/release. Keep request receipts with event audit
history. No automatic deletion is implemented because deletion reopens retry risk.

### Explicit configuration / unresolved rules

- No default brokerage percentage or rounding. Inject any `BrokeragePolicy`.
  `FixedFeeBrokerage(amount=...)` and `BasisPointsBrokerage(rate_bps=...,
  rounding='floor'|'ceil'|'half_up')` are optional configured strategies, not
  event decisions. Missing policy rejects new resale; successful retries still work.
- Auction minimum is required before opening/bidding. Configuring zero permits
  zero-credit first bids deliberately; otherwise supply the agreed minimum.
  Later bids must be strictly greater; there is no extra arbitrary increment.
- Market must reserve the entire auction lot in its OWN allocation mechanism
  before opening. I/J do not duplicate stock or invent allocation on creation.
- If an auction has no bids, settlement requires an explicit `no_bid_handler` to
  resolve the allocated lot in the same transaction. Without it, settlement
  returns CONFIGURATION_REQUIRED with no effects. No disposal/restock rule is assumed.
- Cancellation has no API/state implementation until its permissions and
  reservation/allocation-release policy are finalized.
- Automatic settlement scheduling is Infrastructure's decision. An authorized
  manual endpoint and internal runtime operation are supplied for either trigger.
- External table names/FKs, archive behavior, resale mapping across rounds,
  team-disable rules, and organizer audit hooks need owner agreement.

### Migrations and deployment

`migrations/0001_modules_i_j.sql` creates only I/J tables/types/indexes and an
immutable trade-history trigger. Apply once inside the team's migration
transaction; this is a mergeable SQL payload, not a second migration framework.
The migration smoke test runs it against a new temporary schema and checks the
trigger. Foundation/Migration owners must coordinate names and add external FKs
after the other teams define their tables. Internal Auction FKs are already present.

No Neon engine, secrets, Hyperdrive adapter, Google credentials, or production
Cloudflare deployment is embedded in these modules. Foundation/Infrastructure
must verify the actual synchronous SQLModel/driver combination in Cloudflare,
transaction connection affinity, disabled/stale query caching for authoritative
reads, connection lifecycle, statement timeouts, and migrations. I/J stay portable
through their injected session boundary. No production deployment was performed.

See `docs/INTEGRATION.md` for the owner handoff checklist.
