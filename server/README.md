# Flutter Wars backend

Python + FastAPI + SQLModel on Neon PostgreSQL (through Cloudflare Hyperdrive), following *GDG Flutter Workshop: Final Modular Backend Architecture* v1.0.

## What is here

| Path | Module | Status |
|---|---|---|
| `app/modules/market/` | G: Market & Round Lifecycle | Implemented, incl. auction lots. See [docs/market.md](docs/market.md). |
| `app/modules/pricing/` | H: Pricing Engine | Implemented. See [docs/pricing.md](docs/pricing.md). |
| `app/core/` | A: Foundation, plus the B/K principal | **Placeholder.** One `get_session`, `get_principal`, `AppError` shape and `get_now` for every module, plus `/health` and `/ready`. `get_principal` rejects every request until Module B replaces it. |
| `app/modules/catalog/` | D: Widget Catalog | **Placeholder.** A minimal `widget` table plus `get_widget()`. |
| `migrations/` | M: Infrastructure | One Alembic chain: `0001` placeholder widget table (D replaces it), `0002` G/H tables, `0003` G auction lots. |
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

The suite drops and recreates every table, so never point it at a shared database.
