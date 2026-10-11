# Deployment (Module M)

> The Worker builds routes, dependency graphs, and middleware at deployment
> for Cloudflare's Python memory snapshot. Only settings, trading runtime configuration,
> and the database engine are initialized on the first request; Hyperdrive connection
> properties cannot be read in global scope.
> Its settings and
> secrets are listed in [environment-reference.md](environment-reference.md#application-settings-module-a);
> set `JWT_SECRET_KEY` and `GOOGLE_OAUTH_CLIENT_SECRET` with `wrangler secret put` before
> the first deploy, and register `<worker-url>/auth/google/callback` as an authorized
> redirect URI on the Google OAuth web client.

Backend: Cloudflare Python Worker → FastAPI → sync SQLModel/SQLAlchemy → pg8000
→ Hyperdrive → Neon PostgreSQL. Migrations run outside the Worker, directly
against Neon (control-plane operation), never through Hyperdrive.

`app.factory` can be imported without reading settings or configuring the database;
`app.main` remains the conventional ASGI entrypoint. Keep heavy application imports
at Worker module scope so fresh isolates reuse the deployment snapshot. The Worker
also collects unreachable import-time objects before snapshotting, avoiding deferred
file-resource finalizers against the restored runtime's filesystem. Do not suppress
`ZipFile` exceptions globally. The PostgreSQL driver is loaded before snapshotting.
OpenAPI is generated lazily for documentation requests, avoiding its schema allocation
in isolates that only serve application traffic.
Placement is set to `aws:ap-southeast-1` to reduce repeated database round trips to
the Singapore Neon database. Preserve this setting in local deployment configs too.

Python Worker synchronous sockets yield to the event loop. A shared isolate-level
`asyncio.Lock` serializes complete ASGI HTTP transactions, background notification reads,
and Durable Object database work, following [Cloudflare's concurrency guidance](https://developers.cloudflare.com/hyperdrive/examples/python-workers/#concurrency-and-async-safety).
Keep commit, rollback, and connection cleanup inside that lock; locking individual
queries leaves synchronous driver stacks able to overlap. Waiting requests use
request-owned `scheduler.wait` timers: a shared Python lock Future alone causes
workerd to cancel queued requests as hung. The ASGI adapter reads request bodies
before application serialization, and lifespan startup/shutdown bypasses the lock.
Requests can queue within an isolate while its current transaction finishes.

## Worker bundle / plan capacity

The historical spike bundle was **~34.24 MiB uncompressed / ~7.66 MiB gzip**
and required Workers Paid under the recorded limits. It included psycopg binary
dependencies; the current Worker uses pg8000 because psycopg cannot load in
Pyodide (no libpq), verified on Cloudflare. Re-measure the current bundle with
`cd cloudflare && ./build.sh && uv run pywrangler deploy --dry-run` before
assessing plan capacity. Local dev, tests, and migrations still use psycopg.

## Hyperdrive caching decision: cache-disabled (primary binding)

Hyperdrive caches eligible read queries by default (`max_age` default 60s) and does
**not** invalidate cached results on writes. This platform has correctness-critical
reads — credits/wallet, inventory, market state, the read immediately following a
purchase/auction/transaction commit, IDE synchronization, and permission/auth reads
once those modules exist — where stale results are unacceptable. Therefore the
**primary `HYPERDRIVE` binding must target a cache-disabled Hyperdrive
configuration** (created with `--caching-disabled`, see step 2 below).

A future optimization MAY split this into `HYPERDRIVE_CACHED` (only for safe
catalog/public reads) and `HYPERDRIVE_FRESH`; correctness-sensitive reads must use
the fresh path. Verify the cache-disabled setting in the Cloudflare account; the binding id in
source alone does not establish cache behavior.

## Environments

| Environment | Worker | DB target | APP_ENV | Binding |
| --- | --- | --- | --- | --- |
| development | uvicorn (`app.main:app`) or local `pywrangler dev` | `DATABASE_URL` or local Hyperdrive connection string | `development` for local tooling | local mode for Worker dev |
| production | `flutter-wars-api` | Hyperdrive → Neon production branch | `production` in Worker vars | `HYPERDRIVE` |

There is one Worker and one binding in `cloudflare/wrangler.jsonc`, with no
Wrangler staging/production environment blocks. Staging is a future option;
it would need a separate Worker, Neon branch, Hyperdrive configuration, and CI wiring.
Local development uses a development database rather than the production binding.

## One-time setup (needs Cloudflare + Neon accounts)

1. Create/select the production Neon branch/project.
2. Create a **cache-disabled** Hyperdrive configuration (use placeholders here;
   supply real credentials securely outside source):
   ```
   npx wrangler hyperdrive create flutter-wars-production \
     --connection-string="postgresql://USER:PASSWORD@HOST/DB?sslmode=require" \
     --caching-disabled
   ```
3. Set the returned id in `cloudflare/wrangler.jsonc` under `hyperdrive[].id`.
   An account-specific id is already configured; verify it matches the intended
   cache-disabled configuration. Hyperdrive ids are identifiers, not secrets.

## Credentials / secrets

Nothing sensitive is committed. Configure these outside the repo:

| Name | Where | Purpose |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | GitHub Actions secret | Deploy Worker |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret | Deploy Worker |
| `PRODUCTION_DATABASE_URL` | GitHub Actions secret (env `production`) | Run migrations (Neon direct endpoint) |
| `PRODUCTION_BASE_URL` | GitHub Actions variable | Smoke-test URL |
| `DEPLOY_ENABLED` | Repository variable | Set to `true` to enable deployment |

No Worker runtime secrets are needed (the Hyperdrive binding carries DB creds). If
one is ever added: `wrangler secret put <KEY>` from `cloudflare/`. Locally use `cloudflare/.dev.vars`.

The deploy job uses the GitHub Environment `production`; add **required
reviewers** there for manual approval.

## Deployment order

Prescribed flow (see repository-root `.github/workflows/ci.yml` and
`.github/workflows/deploy.yml`):

1. CI runs lint, tests on Postgres 17, migration upgrade/check/downgrade/upgrade,
   a Worker bundle dry run, and the secret scan.
2. After green CI on `main`, deploy runs only when repository variable
   `DEPLOY_ENABLED=true`. The workflow also offers a manual dispatch under the
   same variable gate.
3. Apply migrations using `PRODUCTION_DATABASE_URL` (`alembic upgrade head`).
4. Build and deploy the Worker from `cloudflare/`.
5. Verify health/readiness with `scripts/verify_deployment.sh`.

**Compatibility rule (critical).** Migrations run **before** Worker deployment,
so they must remain compatible with the currently deployed app. Use the
**expand pattern**:

- Expand first: add nullable columns / new tables / new indexes.
- Migrate/backfill and switch reads/writes across releases.
- Contract (drop/rename/constrain) only in a later release, after no running app
  version uses the old shape.

If migrations fail, stop before deploying. If Worker deployment fails after a
successful migration, the old Worker must still work with the expanded schema.

## Commands

Wrangler commands use the config in `cloudflare/`; run them from that directory.

```
# deploy (from server/), Cloudflare auth required
scripts/deploy.sh              # Worker only; run migrations first
# equivalent: cd cloudflare && ./build.sh && uv run pywrangler deploy

# migrations (control plane; targets Neon directly)
DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require" scripts/migrate.sh

# verify a deployment
scripts/verify_deployment.sh https://flutter-wars-api.<subdomain>.workers.dev
```

## Zero-downtime policy (decided — closed)

- No hard contractual zero-downtime requirement.
- Planned application deployments should aim for no intentional user-visible downtime.
- Risky schema changes use **expand → migrate → contract**.
- Do not perform unsafe destructive migrations during normal deployment.
- Prefer **forward-fix** over a DB downgrade once deployed application/data depends
  on the new schema.
- During live competition rounds, **freeze** normal production deployments except
  emergency fixes.

## Rollback

- Application: redeploy the previous Worker version — `wrangler versions list`,
  then `wrangler rollback` (or `wrangler versions deploy <version-id>`).
- Migrations: see `docs/migrations.md` (prefer forward-fix once data/code depends
  on the change; only `downgrade` a reversible revision with no dependent deploy).

## Deployment verification

The pg8000 driver choice was verified on Cloudflare; the former claim that
psycopg runs in the Worker is incorrect. For each release, verify the intended
Worker version, `/health`, `/ready`, the Hyperdrive configuration, and migration
head. CI deployment requires the secrets/variables above and `DEPLOY_ENABLED=true`.

Refresh-token sessions require migration `0011_auth_sessions` before deploying the
updated Worker. The PKCE handoff remains opt-in; see [refresh tokens](refresh-tokens.md)
for the frontend contract and daily expired-session cleanup.
