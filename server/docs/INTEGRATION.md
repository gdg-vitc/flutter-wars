# Owner handoff

The implementations follow Architecture.md's ownership boundaries. That document
still marks resale eligibility, format, ties, and reservations as TBD; the later
user decisions finalize finite-only resale and hidden incremental auctions. It
was left untouched rather than silently changing the team-wide specification.

## Contracts to freeze

| Owner | Required agreement |
|---|---|
| Foundation A | Fresh Session factory; READ COMMITTED; session-bound adapters; error envelope/routes; connection affinity |
| Auth B | Replace `app.core.auth.get_principal` only; I/J derive their `Principal` from it. Verified JWT Principal; eligible current team; organizer authorization; no team IDs from request bodies |
| Catalog D | Stable UUID; transactional eligibility/archive validation; historic references retained |
| Ledger E | Account guards ordered by UUID; whole credits; balance minus ACTIVE reservations; atomic debit/credit/reserve/release/settle; stable business-reference uniqueness |
| Inventory F | Atomic upsert/add; conditional nonnegative removal; mutation references; same transaction |
| Market G | **Implemented:** `app/modules/market/adapter.py` (auction allocations are G's `market_auction_lot`). Round then listing/allocation guards; finite/infinite handling; resale destination/provenance; fixed allocated auction lot; shared pause/close policy |
| Pricing H | **Implemented:** `app/modules/pricing/adapter.py`. One execution unit price per quantity; whole credits; consistent guarded state; record_trade receives completed BUY/SELL effects before commit; repricing/cache/history changes join the same transaction |
| Admin K | Organizer permissions and mandatory action-audit integration; lifecycle actions call J's service |
| Observability L | Safe codes and correlation; no bid amounts, bodies, SQL parameters, competitor identities, JWTs or raw exception logging |
| Infrastructure M | Hyperdrive/Neon/session path; migrations; scheduling; retry/timeout handling; actual Cloudflare runtime compatibility |

Adapters must not import or mutate another owner's internal tables on behalf of
Trading/Auction. Each owner supplies its adapter. The production I/J repositories
query only I/J tables. The test adapters use separate `test_*` tables to exercise
cross-table transactions while other teams are unfinished.

Ledger reserve adds ONLY the difference to the same reservation ID (the Bid UUID).
Available balance includes every active reservation. Settlement must consume the
exact held amount once; release must be harmless after prior release. Verify
reference/team/amount agreement; never allow an ID to transfer another team's hold.
Inventory receives a generic `reference` UUID: a trade ID for BUY/SELL or an auction
ID for awards. Do not assume every ownership event references a trade row.
Inventory first-row creation needs an atomic upsert or equivalent uniqueness
protection, not just SELECT FOR UPDATE on a possibly nonexistent row.

Market allocation is not created automatically by Auction DRAFT creation. G must
hold the lot, bind allocation to the J-generated auction ID, and supply matching
listing/widget/quantity before J opens it. Trading cannot buy auction-only listings.
Normal resale is finite-only and goes to G's approved target listing. Whether
auction-acquired widgets can be sold into another listing is G's explicit policy.

Pricing algorithms are still H-owned. If H stores mutable price state/history,
H must coordinate updates with the listing guard and include transaction-coupled
price effects in the same session. A pure on-read strategy can derive current
price from guarded committed supply. Do not independently commit recalculation
or cache a tentative price from a transaction that rolls back.

## Definition of ready for production integration

- Real adapters pass the SAME functional/rollback/concurrency tests as stand-ins.
- Round/catalog/price/wallet/inventory lock order reviewed by all owners.
- Failure after EVERY mutation rolls back the complete business operation.
- Concurrent requests use separate sessions/connections across instance boundaries.
- No stale cached reads authorize spending, stock, deadlines, or eligibility.
- Brokerage formula AND rounding configured explicitly; no silent default.
- Minimum bid configured; no-bid allocation handler approved or operation gated.
- Cancellation, participant result visibility, and auto-settlement trigger decided.
- External UUID FKs/table names and migration order agreed; no destructive cascade
  may erase historic trades/results.
- Auth/organizer audit integration verified; HTTP schemas remain explicitly narrow.
- SQL echo disabled and log redaction verified in the deployed Foundation stack.
- Migration SQL reviewed and imported into the central team's migration workflow.

The test suite proves I/J orchestration with transaction-compliant stand-ins.
It does not certify unfinished owner implementations or deploy anything to Neon
or Cloudflare. Default HTTP composition cannot perform authenticated operations
until Foundation/Auth supply its integrations.
