# Shared Migration Workflow (Module M)

Module M owns the migration **execution process**. It does NOT own business
entities. Feature modules own their SQLModel entities and their migrations, and
contribute them to this one shared, ordered history.

## Tool: Alembic

Chosen because it is SQLAlchemy-native (SQLModel is built on SQLAlchemy), supports
versioned/ordered/reviewable revisions, offline SQL generation, and drift
detection (`alembic check`). It uses the same sync SQLModel/SQLAlchemy + psycopg
stack as local tooling. The Worker uses pg8000. Alembic is installed in the
server project, but is absent from `cloudflare/pyproject.toml` and never bundled
into the Worker.

## Layout

```
server/
  alembic.ini                       # script_location only; URL comes from env.py
  migrations/
    env.py                          # shared environment (DATABASE_URL, metadata, exclusions)
    script.py.mako                  # revision template
    versions/
      0001_catalog_widget_placeholder.py  # D placeholder widget table
      0002_market_and_pricing.py           # G/H tables
      0003_market_auction_lot.py           # G auction lots
      0004_modules_i_j.py                 # I/J SQL payload
  app/models.py                     # imports all module table models
```

## Commands

Run from `server/` with `DATABASE_URL` set:

| Task | Command |
| --- | --- |
| Apply all pending | `uv run alembic upgrade head` (or `scripts/migrate.sh`) |
| Current revision | `uv run alembic current` |
| History | `uv run alembic history --verbose` |
| Drift check (no DB change) | `uv run alembic check` |
| Autogenerate a revision | `uv run alembic revision --autogenerate -m "widget catalog"` |
| Empty/manual revision | `uv run alembic revision -m "..."` |
| Roll back one | `uv run alembic downgrade -1` |
| Offline SQL preview | `uv run alembic upgrade head --sql` |

`-x db_url=...` overrides `DATABASE_URL` for a single invocation.

## How future modules add migrations

1. Create your SQLModel entities in your module, e.g. `app/modules/<module>/models.py`.
2. Import the new table models in `app/models.py`. `migrations/env.py` imports
   `app.models` to populate `SQLModel.metadata`; there is no separate registry.
3. Generate the revision: `uv run alembic revision --autogenerate -m "<module>: <change>"`.
4. **Review the generated file** (autogenerate is a draft, not authority): confirm
   tables/columns/indexes, check ordering, and add explicit `downgrade()` logic.
5. Apply and verify locally against a Neon dev branch:
   `uv run alembic upgrade head && uv run alembic check`.
6. Commit the migration file with your module code. Migrations are reviewed like code.

Conventions:
- One logical change per revision; never edit a revision that has been applied to a shared database.
- `down_revision` must form a single linear history (no divergent heads).
- Prefer expand -> migrate -> contract for risky changes (see below).

## Local execution

```
export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
uv run alembic upgrade head
```

## Production execution (staging is a future option)

Migrations run **outside** the Worker and connect to Neon **directly** (not through
Hyperdrive), using a `DATABASE_URL` secret provided by the deploy environment:

```
DATABASE_URL=... scripts/migrate.sh      # uv run alembic upgrade head
```

Deployment ordering (the authoritative flow is in [deployment.md](deployment.md)):
`CI green -> apply migrations -> deploy Worker -> verify`. Because migrations
run **before** deployment, they must remain compatible with the currently
running app:
1. Expand first — add nullable columns / new tables / new indexes.
2. Backfill and switch reads/writes across releases.
3. Contract (drop/rename/constrain) only later, once no running version uses the old shape.
CI runs `alembic check` on a disposable database after upgrading to head.

Migration tests (`tests/test_migrations.py`) use `MIGRATION_DATABASE_URL` pointing
at a disposable `*_migration_test` database. PostgreSQL module/infra tests use
`TEST_DATABASE_URL` (`*_modules_test`), not the application's `DATABASE_URL`.

## Recovery

**Migration fails during apply:** Alembic uses transactional DDL on
Postgres. A failed upgrade rolls back its transaction; inspect the current
revision rather than assuming earlier revisions in that upgrade committed. Deployment must stop on non-zero exit (do not proceed to
app deploy). Inspect state with `uv run alembic current` and `uv run alembic history`.

**Choose rollback vs forward-fix:**
- Safe to `alembic downgrade -1` when the revision is reversible and no application
  version already depends on the new schema. Remember: a schema rollback also
  requires the previously deployed app version.
- Prefer **forward-fix** (add a new corrective revision) once the schema change has
  been used by deployed code or written production data — rolling back could destroy
  or orphan data.

**Stuck at a partial state:** never hand-edit the `alembic_version` table or the
schema in production. Inspect, then add a corrective revision.

## Guardrails

- No manual production database edits in normal deployment.
- The `infra_probe` dev/test harness table is excluded from migrations
  (`migrations/env.py` `include_object`) and must never enter the shared history.
- Never commit `DATABASE_URL` or any credential. Migrations read it from the
  environment.
