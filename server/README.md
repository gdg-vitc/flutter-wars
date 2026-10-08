# Flutter Wars backend

Python + FastAPI + SQLModel on Neon PostgreSQL (through Cloudflare Hyperdrive), following *GDG Flutter Workshop: Final Modular Backend Architecture* v1.0.

## What is here

| Path | Module | Status |
|---|---|---|
| `app/core/` | A: Foundation, plus the B/K principal | **Placeholder.** One `get_session`, `get_principal`, `AppError` shape and `get_now` for every module, plus `/health` and `/ready`. `get_principal` rejects every request until Module B replaces it. |
| `app/modules/catalog/` | D: Widget Catalog | **Placeholder.** A minimal `widget` table plus `get_widget()`. |
| `migrations/` | M: Infrastructure | One Alembic chain: `0001` placeholder widget table (D replaces it). |

Each module follows the same layout: `models.py` (SQLModel tables), `schemas.py` (API payloads), `service.py` (business rules and the internal contracts for other modules), `router.py` (thin FastAPI routes). Modules register in `app/main.py:MODULES`.

## Develop

```bash
cd server
uv sync
uv run pytest                    # in-memory SQLite: unit and API tests
DATABASE_URL=postgresql+psycopg://... uv run alembic upgrade head
DATABASE_URL=... uv run fastapi dev app/main.py
```
