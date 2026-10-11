# Recovery, Rollback & Backups

## Operational targets (project targets, NOT provider guarantees)

| Target | Value | Meaning |
| --- | --- | --- |
| **RPO** | ≤ 5 minutes | Maximum acceptable data loss. |
| **RTO** | ≤ 30 minutes | Maximum acceptable time to restore service. |

These are project objectives. Actual recovery depends on the Neon plan, the
configured history window, and how quickly a human responds.

## Neon recovery capabilities (verified — do not assume more)

Documented Neon capabilities relevant here:

- **Instant restore / PITR** — Neon retains a continuous change history (WAL) for a
  configurable **history window** and can restore a root branch to an earlier point
  in time. History window defaults/limits by plan: **Free** 6 hours (capped 1 GB);
  **Launch** default 1 day, max 7 days; **Scale** default 1 day, max 30 days.
  Configure under **Settings → Postgres → History window**.
- **Branching** — copy-on-write branches; use for dev/test/staging isolation and for
  testing recovery without touching production.
- **Logical backups** — `pg_dump` / `pg_restore` are supported. Neon also documents
  an automated `pg_dump` → S3 pattern via GitHub Actions (optional; not set up here).
- **Deleted project recovery** — Neon keeps a deletion recovery period for deleted
  projects (separate from PITR).

**RPO implication:** Neon retains continuous history, so PITR can target a recent
moment — supporting the ≤5 minute RPO **provided the history window is sized for it
and History usage is within plan limits**. This is a target, not a guarantee.

**RTO implication:** restoring a branch and repointing the application is a human +
provider operation; the ≤30 minute target is achievable but not guaranteed.

## Application rollback (Cloudflare)

Cloudflare Workers support versioned deployments and rollback:

```
cd cloudflare
npx wrangler versions list
npx wrangler rollback                 # roll back to previous version
npx wrangler versions deploy <version-id>   # deploy a specific version
```

Return to `server/` after the Wrangler commands, then verify rollback:
`scripts/verify_deployment.sh <base-url>`.

> **A Worker rollback does NOT undo database schema or data changes.** Rolling back
> the application version leaves the database exactly as it is. This is why
> migration compatibility matters.

## Why backward-compatible migrations matter

The deployment order is *apply migrations → deploy Worker → verify* (see
[deployment.md](deployment.md)). Because both the previous and the new application
version may run against the post-migration schema:

- Migrations must be **backward compatible (expand → migrate → contract)**: add
  nullable columns / new tables first; backfill and switch code across releases;
  only drop/rename/constrain once no running version uses the old shape.
- If a rollback becomes necessary, the previous application version must still work
  against the current schema — guaranteed only if migrations are backward compatible.

## Decide: forward-fix vs downgrade vs PITR

| Situation | Preferred action |
| --- | --- |
| App version is broken, **no** schema/data change involved | `wrangler rollback` |
| Migration applied, not yet depended on by deployed code/data | `alembic downgrade -1` (reversible revision), then redeploy compatible app |
| Migration applied and code/data already depend on it | **Forward-fix**: add a corrective revision; do not downgrade |
| Data corrupted or destructively altered (bad migration, bad write) | **Database recovery / PITR** (Neon instant restore) — restore to a point before the event |
| Migration partially applied (rare; transactional DDL rolls back) | Inspect with `alembic current` / `history`; add a corrective revision (never hand-edit schema) |

Rules:
- Never hand-edit production schema or the `alembic_version` table.
- Prefer **forward-fix** once schema/data is depended upon.
- `downgrade` is safe only when the revision is reversible and no deployed app/data
  depends on it.
- A Worker rollback alone does not undo a migration; pair it with the DB decision above.

## Hyperdrive failure

- Symptom: `/ready` returns 503 with `{"status":"unavailable"}` on DB query failure.
  Session/configuration failures may return 500; inspect Worker logs securely.
- Check the binding id in `cloudflare/wrangler.jsonc` matches the account's Hyperdrive config
  (`npx wrangler hyperdrive list`), and that the underlying Neon database is up.
- Recover by correcting the binding/config and redeploying; Hyperdrive re-establishes
  pooled connections automatically. Local dev is unaffected (it uses `DATABASE_URL`).

## Neon unavailable

- `/ready` returns 503 with `{"status":"unavailable"}` on query failure
  (no source, error type, or connection string).
- The Worker stays up; `/health` still returns 200. Traffic requiring the DB fails
  gracefully.
- Recover when Neon is reachable again; no redeploy needed. If the outage requires
  data recovery, use PITR (above).

## Backups currently in place

- Primary: Neon continuous history + instant restore (PITR) within the history window.
- Optional: `pg_dump` / `pg_restore` (manual or scheduled); not automated in this repo.
- No heavyweight backup infrastructure is built — see the project targets above.
