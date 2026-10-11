# Database Connection Architecture (Module M)

Scope: the database infrastructure boundary owned by Module M. No business tables.
The shared migration workflow lives in `migrations/` (see `docs/migrations.md`);
CI/CD lives in `.github/workflows/`.

## 1. Components

| File | Responsibility | Owner |
| --- | --- | --- |
| `app/infra/settings.py` | Environment separation + DB configuration record and validation | Module M (Foundation consumes it) |
| `app/infra/db.py` | Target resolution, engine/session factory, connectivity check | Module M |
| `app/infra/health.py` | Readiness probe hook + dependency status model | Module M (Foundation owns the endpoint) |
| `app/main.py` | FastAPI `create_app`; owns `/health` and `/ready` | Foundation |
| `cloudflare/src/worker.py` | Cloudflare ASGI entrypoint (`workers.asgi.entrypoint`) | Module M (deployment config) |
| `app/infra/infra_probe.py` | Infra-only probe table for lifecycle tests — **not** a business entity | Module M (temporary) |

## 2. Connection paths

```
LOCAL DEV (uvicorn)                          PRODUCTION (Cloudflare Worker)
  FastAPI                                      FastAPI (Pyodide, sync)
    -> app.core.db.get_engine()                  -> target_from_hyperdrive()
    -> DATABASE_URL (settings source)            -> env.HYPERDRIVE binding
    -> psycopg -> Neon (direct, TLS)             -> pg8000 -> Hyperdrive -> Neon
```

- Local and production use synchronous SQLModel/SQLAlchemy. Local tooling uses
  psycopg; the Worker uses pg8000 (pure Python).
- The Worker converts the Hyperdrive target URL to `postgresql+pg8000` and removes
  `sslmode`; direct Neon connections from local tooling require TLS.

## 3. Module A / Module M seam

Module M provides these infrastructure hooks:

- `db.resolve_target(scope_env, settings) -> DatabaseTarget`
- `db.engine_for(target, settings)` and `db.create_db_engine(target, settings)`
- `db.session_scope(engine)` — commit / rollback / close transaction boundary
- `db.check_connectivity(engine) -> (ok, latency_ms, error_type)`
- `health.check_database(scope_env, settings) -> DependencyStatus`
- `health.readiness(scope_env, settings) -> (is_ready, payload)`

Module M **does not** define `get_db()`, the FastAPI bootstrap, or the `/health`
and `/ready` routers. Foundation owns application composition. The current app
uses `app.core.db.get_session` and
`SELECT 1` directly for `/ready`, rather than the richer infra readiness hook.

## 4. Connection lifecycle

- The Worker configures and reuses an `Engine` with `NullPool` — Hyperdrive owns
  pooling, and no TCP connections are retained between requests. The infra helper
  `engine_for` separately creates/disposes an engine per context.
- Foundation supplies one `Session` per request; mutations own their transaction.
  The infra `session_scope` helper commits on success, rolls back on error, and closes.
- No shared mutable session/connection state across requests.

## 5. Driver decision

- Worker driver: **pg8000 (sync)**. psycopg cannot load in Pyodide (no libpq),
  verified on Cloudflare. Local dev, tests, and migrations use **psycopg**.
- ORM: **synchronous** SQLModel/SQLAlchemy. Async SQLAlchemy is unsupported in
  Python Workers (no greenlet), so async SQLModel/asyncpg are not used for the ORM.

## 6. Hyperdrive integration

- Binding name: `HYPERDRIVE` (`settings.HYPERDRIVE_BINDING`).
- The binding exposes `host/port/user/password/database` (no connection string).
- Worker -> Hyperdrive hop is not TLS: the infra helper generates `sslmode=disable`,
  then the Worker removes that query option for pg8000.
- Hyperdrive pools in **transaction mode**: keep transactions short; do not wrap
  long multi-statement units of work in a single transaction.
- Hyperdrive caches read queries (`max_age`, default 60s) and does **not**
  invalidate on writes. **Decision (final, not TBD): the primary `HYPERDRIVE`
  binding uses a cache-disabled configuration** because this platform has
  correctness-critical reads (credits, inventory, market state, post-commit reads,
  IDE sync, permission/auth reads). A future optimization MAY add
  `HYPERDRIVE_CACHED` / `HYPERDRIVE_FRESH`, with correctness-sensitive reads on the
  fresh path. See `docs/deployment.md`.

## 7. Neon configuration boundary

- Local/dev/test connect directly to Neon with TLS (`sslmode=require` in the URL).
- Use Neon **branches** for dev/test/staging isolation; production is a separate
  branch/project. A future staging Worker would need its own Hyperdrive id + Neon
  branch; no staging Worker or Wrangler environment exists today.
- Neon's pooled endpoint (`...-pooler...`) is optional for local tooling; the
  Worker path goes through Hyperdrive instead.
- No Neon-specific code in the app — only the connection target differs.

## 8. Environment separation

| Env | Target source | `DATABASE_URL` | Secrets location |
| --- | --- | --- | --- |
| development | settings | required (dev Neon branch) | local `.env` (gitignored) |
| test | test fixtures | fixtures use `TEST_DATABASE_URL`; migration tests use `MIGRATION_DATABASE_URL` | process environment / CI disposable DBs |
| staging (future) | separate Hyperdrive binding would be needed | not used in a Worker | migration secret would be needed |
| production | Hyperdrive binding | not used in the Worker | Hyperdrive config; CI secret for migrations |

Env vars: `APP_ENV`, `DATABASE_URL`, `DB_CONNECT_TIMEOUT_SECONDS`,
`DB_APPLICATION_NAME`. See `.env.example` / `.dev.vars.example`.

## 9. Concurrency decision (measured, project-specific)

Cloudflare's Hyperdrive documentation recommends serializing synchronous DB
operations with an `asyncio.Lock`. **This project does not use that lock.**

Measured in the real Pyodide Worker runtime (`GET /ready`, real DB ping):

| Concurrency | With global `asyncio.Lock` | Without lock |
| --- | --- | --- |
| 1 | 30/30 | ok |
| 3 | 1/30 | ok |
| 10 | 1/50, then 0/30 | 50/50 |
| 25 | — | 200/200 |
| 50 | — | 300/300 |
| 100 | — | 400/400 |

With the lock, waiters starve while the holder runs a synchronous DB call and the
runtime cancels them ("your Worker's code had hung"). This is a **project-specific
measured decision**, not a claim that Cloudflare's guidance is universally wrong;
it may not represent other workloads or future runtime versions.

## 10. Failure behavior

- `check_connectivity` / `check_database` never return exception messages, SQL,
  connection strings, or credentials — only `error_type` names.
- Configuration failures surface as `ConfigError` (readiness fails closed).
- `DB_CONNECT_TIMEOUT_SECONDS` bounds connections made by the psycopg infra helper;
  the current Worker does not apply these libpq options to pg8000.
- Infra readiness hooks expose dependency diagnostics. The actual `/ready` endpoint
  returns only `{"status":"ready"}` (200) or `{"status":"unavailable"}` (503)
  when its DB query fails; it does not expose those diagnostics. Session or
  configuration failures before the query handler may return 500.

## 11. Worker limits (recorded baseline)

- Historical spike bundle (dry-run): **34244 KiB uncompressed / 7661 KiB gzip**.
  This included psycopg binary dependencies and is not the current pg8000 bundle.
  Re-measure with `cd cloudflare && ./build.sh && uv run pywrangler deploy --dry-run`.
  Guardrails (project): <45 MiB comfortable, 45–52 MiB review, >52 MiB stop.
- Worker startup limit is 1 s; monitor on each deploy.

## 12. Resolved: dev-probe DDL under load

The `/db/roundtrip` dev probe originally ran `create_all` per request; under a
c=50 burst the local `wrangler dev` (Miniflare) proxy dropped connections
(`Error inside ProxyWorker ... Network connection lost`), while `/ready` stayed
stable to c=100. Fix (dev harness only, no production change): the harness table
was created once via `scripts/dev_setup.py` / test fixtures, and
`/db/roundtrip` performed only transaction + INSERT/SELECT. After the fix, a
c=50/100 regression showed zero failures on both endpoints. These are historical
harness results; `app/main.py` does not expose `/db/roundtrip` today.

## 13. Deploy-time policy decisions

Resolved (not TBD):

- **Hyperdrive caching:** primary binding is cache-disabled (see §6).
- **Zero-downtime:** no contractual requirement; planned deployments aim for no
  intentional user-visible downtime; risky changes use expand → migrate → contract;
  forward-fix preferred over downgrade once data/code depends on the new schema;
  production deployments freeze during live competition rounds except emergency
  fixes. See `docs/deployment.md` ("Zero-downtime policy").

## 14. Known open items

- Staging environment wiring is a future option; no Wrangler staging/production
  environment blocks exist. The configured Worker is `flutter-wars-api`.
- Driver choice has been verified on Cloudflare; verify health/readiness and the
  primary Hyperdrive cache-disabled setting for each deployment.

## 15. Related docs

- Migrations: `docs/migrations.md` (shared Alembic workflow; how modules contribute).
- Deployment: `cloudflare/wrangler.jsonc` (single production Worker), `scripts/deploy.sh`, `scripts/migrate.sh`,
  `scripts/verify_deployment.sh`.
