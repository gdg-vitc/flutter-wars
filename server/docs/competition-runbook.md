# Competition-Day Operations

~200 participants, bursty traffic around competition activity. Correctness and
availability matter more than throughput tuning.

## Deployment freeze

During active competition rounds: **no deployments except emergency fixes**, and no
destructive migrations. Prefer read-only operations and existing versions.

## BEFORE the event

- [ ] Verify production deployment is live and the intended version is serving.
- [ ] `GET /health` returns 200.
- [ ] `GET /ready` returns 200 (`{"status":"ready"}`).
- [ ] Database connectivity confirmed from the edge (the `/ready` check above).
- [ ] Migration version verified: `alembic current` shows the expected head (`0004` today).
- [ ] Secrets configured (Cloudflare token, DB URL for migrations; none needed at runtime).
- [ ] Hyperdrive binding id matches `cloudflare/wrangler.jsonc`; primary caching is disabled.
- [ ] A known-good previous Worker version exists for rollback
      (`cd cloudflare && npx wrangler versions list`).
- [ ] Recovery posture known: Neon history window set; PITR available for the event
      window (see [recovery.md](recovery.md)).

## DURING the event

- [ ] Avoid unnecessary production deployments.
- [ ] No destructive migrations; only backward-compatible changes if unavoidable.
- [ ] Emergency-only changes; use `wrangler rollback` for application regressions.
- [ ] Monitor `/ready`; a DB query failure shows as 503 with `{"status":"unavailable"}`.
      Check logs for configuration/session failures (which may return 500).
- [ ] Remember: a Worker rollback does not undo database changes.

## AFTER the event

- [ ] Inspect errors (`cd cloudflare && npx wrangler tail`, Cloudflare dashboard metrics).
- [ ] Inspect database state (row counts, key invariants owned by the feature modules).
- [ ] Preserve required audit/recovery information; capture the migration version.
- [ ] If data integrity is in question, plan a Neon PITR restore window before making
      further changes.

## Capacity note

Historical local synthetic results (Phase 3) are a baseline, not a provisioning
claim. The current app does not expose the old `/db/roundtrip` harness endpoint:

| Endpoint | Concurrency | Success |
| --- | --- | --- |
| `/ready` | 50 | 300/300 |
| `/ready` | 100 | 400/400 |
| `/db/roundtrip` | 50 | 150/150 |
| `/db/roundtrip` | 100 | 200/200 |

`/db/roundtrip` at c=100 had p99 ≈ 2277 ms locally. This is **not** evidence that
the system is comfortably provisioned for 200 concurrent users; it is a local
reference point.
