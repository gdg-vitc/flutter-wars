# Troubleshooting Runbook

## Health vs readiness

| Endpoint | Meaning | Touches DB |
| --- | --- | --- |
| `GET /health` | The application process is alive. | No |
| `GET /ready` | The application can use required dependencies (DB reachable). | Yes (`SELECT 1`) |

`app/main.py` owns both endpoints. `/ready` returns 200 with
`{"status":"ready"}` after a successful DB query, or 503 with
`{"status":"unavailable"}` when that query fails. It does not expose dependency
names, source, latency, or error types. Configuration/connection failures while
creating the session may fail before the query handler and return 500.

## Reading a failing `/ready`

Example query failure:
```json
{"status":"unavailable"}
```

| Context | Likely cause | Action |
| --- | --- | --- |
| Worker | Neon unavailable, or Hyperdrive binding/connection misconfigured | Check Neon status; verify the binding id (`npx wrangler hyperdrive list` from `cloudflare/`) matches `cloudflare/wrangler.jsonc` |
| Local app | Missing or wrong/unreachable `DATABASE_URL` | Verify environment, host/credentials, and `sslmode` without printing secrets |
| First request after idle | Cold start | Retry; inspect logs if failures persist |

The richer diagnostic payload belongs to `app/infra/health.py` helper tests;
it is not wired into the current HTTP endpoint.

## Common failures

**Worker deploy fails: "Script startup exceeded CPU time limit".** Global-scope work
exceeds 1 s. Keep import-time work minimal (the ASGI adapter drives lifespan per
request; do not put heavy initialization in lifespan). Check `wrangler deploy`
startup output.

**Worker bundle too large.** Guardrails (project): <45 MiB comfortable, 45–52 MiB
review, >52 MiB stop. Inspect `du -sh python_modules/*` from `cloudflare/` after bundling.

**Local `pywrangler dev`: "Network connection lost" / "Error inside ProxyWorker".**
Miniflare's local dev proxy under heavy bursts; not a production behavior. Do not
add DDL per request (the harness table is created by `scripts/dev_setup.py`).

**Migration fails during apply.** Alembic uses Postgres transactional DDL; a failed
upgrade rolls back its transaction. Confirm the actual revision with
`uv run alembic current` and `uv run alembic history`. Fix forward with a corrective
revision; do not hand-edit the schema or `alembic_version`.

**`alembic check` reports drift.** Autogenerate detects a difference between models
and DB — e.g., a table created outside migrations. The dev harness `infra_probe`
table is excluded; any other extra table is a real drift signal. Do not suppress it.

**Application errors but `/ready` is green.** Readiness only covers DB
connectivity; check Worker logs (`npx wrangler tail`).

## Logs and diagnostics

- `npx wrangler tail` — live Worker logs (Observability is enabled).
- `uv run alembic current` / `history` — schema state.
- `scripts/verify_deployment.sh <base-url>` — post-deploy smoke.

Run Wrangler commands from `cloudflare/` (the only config location).
Never print secrets; `/ready` exposes only status, and infra diagnostics redact credentials.
