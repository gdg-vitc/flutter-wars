# Module A: Foundation and Shared Contracts

## Owned responsibilities

Module A owns the FastAPI bootstrap, environment settings, SQLModel engine/session lifecycle, safe error mapping, shared principal shape, logging hooks, module registration, and `/health` and `/ready`. It owns no business entities or tables.

## Contracts exposed

- `get_db()`: transaction-scoped SQLModel `Session` dependency. It commits on success, rolls back on exceptions, and always closes resources.
- `get_principal()`: authentication boundary. It currently returns `401 AUTHENTICATION_REQUIRED`; it performs no credential handling.
- `Principal`: immutable `user_id`, `team_id`, and `role` representation.
- `AppError`: stable `code`, safe `message`, HTTP `status_code`, and optional safe `context`.
- Shared error response: `{"code": "STABLE_CODE", "message": "Safe message", "context": {}}`, where `context` is optional.
- `Settings`: environment-driven application settings, including `DATABASE_URL`.
- `register_modules(app)`: small, explicit router registration point.

## Database and Hyperdrive

`DATABASE_URL` is a SQLAlchemy/SQLModel database URL. Local development can use a direct PostgreSQL or Neon connection. Deployment should set it to the Cloudflare Hyperdrive connection route configured by Infrastructure (Module M), which then reaches Neon PostgreSQL. Foundation intentionally does not claim an unsupported Cloudflare runtime adapter or hard-code a provider URL.

TBD: Infrastructure must decide the exact Cloudflare FastAPI deployment adapter and whether readiness is required to exercise only the Hyperdrive path (the default is the configured path).

## Integration with Module B

Module B verifies Google credentials and JWTs, then constructs `Principal` values. It can install its verified dependency through FastAPI dependency overrides or use a compatible provider at routes that depend on `get_principal`. Module A neither parses tokens nor creates sessions.

## Integration with Module C

Module C consumes `get_db()` and the team identity supplied by Module B. Its API-key authentication and IDE-state business functionality remain wholly owned by C.

## Integration with Module L

Module L can extend `configure_logging`, `safe_context`, and `log_exception` with structured events, correlation IDs, metrics, and approved retention. Foundation deliberately logs exception type rather than exception text to reduce accidental provider/secret exposure.

## Explicitly out of scope

Authentication, JWTs, Google login, team/API-key entities, widgets, inventory, credits/wallets, markets, pricing, purchases, auctions, organizer logic, Flutter validation, GitHub/submission handling, and Module L observability business functionality are not implemented here.

## Router registration pattern

Each feature module keeps an `APIRouter` in its own package. Add that router as one explicit line in `app/modules/__init__.py`; do not add feature rules to Foundation.
