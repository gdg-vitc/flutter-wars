# PR #13 review progress

Source: https://github.com/gdg-vitc/flutter-wars/pull/13#issuecomment-6087145226

The agreed order is service boundaries, performance, then live price delivery. Changes belong on `integration/staging-backend`. The reviewer reply remains a draft and has not been posted.

## Implemented locally

- Shared contracts and session-bound gateway registry; each owner registers its own gateway and routes.
- Removed central wiring and owner adapters. Core and feature modules use contracts rather than another feature's internals.
- Moved catalog, wallet, and inventory organizer routes to their owners, preserving HTTP paths, authorization, and transaction/audit behavior.
- Market publishes a SQL listing/round read model so Pricing retains one-statement consistent quote snapshots.
- Batched organizer listing pricing configuration and catalog names.
- CI boundary checks and a query-count regression test for organizer round detail.

- Bounded market/pricing cache with PostgreSQL snapshot freshness checks, transaction bypass, and expiry at quote deadlines.
- Authenticated per-round Python Durable Object sockets, hibernation, persistent sequences, price alarms, committed-change notifications, and REST fallback.
- Batched connection membership checks and a local ASGI stream for development.

Validation: all 468 PostgreSQL 18 tests pass with `PGTZ=UTC`, including cache concurrency/rollback, socket authorization, reconnect, and alarm coverage. Ruff and Worker packaging pass. A real local workerd smoke check covers successful and rejected trades, interval alarms, reconnects, round closure, and revoked team access.

See [market stream and cache documentation](market-stream.md) for client protocol, limits, and performance tradeoffs. The October 10 safeguards update replaces the provisional dynamic formula; see [market safeguards](market-safeguards.md) for the research, rules, reviewed failure cases, and migration limits.

## Deployment status

Changes target upstream PR #13 through the fork's `integration/staging-backend` branch.
Staging releases use the `flutter-wars-staging` Worker and its existing RoundStream
Durable Object binding. Apply the direct-Neon migration before Worker publishing,
and verify health/readiness before restoring the prior operational freeze state.
Deployment version IDs and the active release are available in the Cloudflare dashboard.


## October 10 safeguards update

Self-pump resale gains are excluded using cross-round acquisition cost and external
net-demand accounting. Legitimate gains and losses have finite team-wide limits.
The update also fixes pause-time decay, missing auction discovery, live-lot release,
cancellation refunds, zero-wallet resale, disabled-team IDE access, missing mutation
audits, redundant transaction connections, and Durable Object refresh queues.

Validation: 488 PostgreSQL tests pass, including legacy migration backfill, adversarial
trading and concurrent inventory/credit operations. Ruff and the Worker dry-run pass.
This records tested safeguards and known migration limits, not an assertion that the
entire repository is free of vulnerabilities.
