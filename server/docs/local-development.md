# Local Development

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (dependency + Python management, server Python 3.12+; Worker tooling Python 3.13+)
- Node.js 22 (as in CI) (for `wrangler` / `pywrangler`)
- A PostgreSQL database — a Neon development branch, or local Postgres

## 1. Install dependencies

```
cd server
uv sync --dev          # Python (runtime + dev tools)
cd cloudflare
npm ci                 # wrangler (Worker tooling)
uv sync --dev          # Worker dependencies/tooling
cd ..
```

## 2. Configure the environment

```
cp .env.example .env   # then edit DATABASE_URL
```

`.env` is gitignored. Never commit it. For the local Worker runtime
(`pywrangler dev`) copy `.dev.vars.example` to `cloudflare/.dev.vars` instead of using `.env`.
See [environment-reference.md](environment-reference.md).

## 3. Run the tests

```
export TEST_DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/flutter_modules_test?sslmode=require"
export MIGRATION_DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/flutter_migration_test?sslmode=require"
uv run pytest -q
```

Without `TEST_DATABASE_URL`, PostgreSQL module/infra cases are skipped; without
`MIGRATION_DATABASE_URL`, `tests/test_migrations.py` is skipped. These suites reset
tables: use disposable databases named `*_modules_test` and `*_migration_test`
respectively. `SPIKE_DATABASE_URL` is no longer used.

## 4. Prepare the dev/test harness schema

Infra tests use a probe table that is **not** part of migrations. Fixtures prepare
it automatically; for manual infra-helper checks, load `.env` and run:

```
uv run python scripts/dev_setup.py
```

## 5. Run the backend locally (uvicorn)

The app reads configuration from the process environment (it does not auto-load
`.env`), so load it into the shell first:

```
set -a; . ./.env; set +a
uv run uvicorn app.main:app --reload --port 8899
curl http://127.0.0.1:8899/health
curl http://127.0.0.1:8899/ready
```

`/ready` should return `{"status":"ready"}`. The only app is `app/main.py`
(`create_app`); it does not expose the historical `/db/roundtrip` endpoint.
`pywrangler dev` loads `cloudflare/.dev.vars` when run from `cloudflare/`.

## 6. Run the Worker locally (Hyperdrive local mode)

```
cd cloudflare
./build.sh                    # copies ../app into src/app
export CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE="postgresql://USER:PASSWORD@HOST:5432/DB?sslmode=require"
uv run pywrangler dev
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/ready     # {"status":"ready"}
cd ..
```

Local Hyperdrive mode connects **directly** to the database (no Hyperdrive pooling
or caching). Production uses the real `HYPERDRIVE` binding.

## Database environment separation

| Use | Connection |
| --- | --- |
| Local dev | `DATABASE_URL` directly (Neon dev branch or local Postgres; psycopg) |
| Postgres tests | `TEST_DATABASE_URL` / `MIGRATION_DATABASE_URL` (disposable DBs; psycopg) |
| Local Worker | local Hyperdrive connection string (pg8000) |
| Production | Worker → `HYPERDRIVE` binding → Neon |

Local development deliberately does not route through a production Hyperdrive
binding.

## Migrations

See [migrations.md](migrations.md). Quick start:

```
DATABASE_URL=... uv run alembic upgrade head
DATABASE_URL=... uv run alembic current
```
