# Flutter Wars backend

The GDG VIT Chennai Flutter Workshop backend: Python + FastAPI + SQLModel on Neon
PostgreSQL, served by a Cloudflare Python Worker through Hyperdrive. It follows
*GDG Flutter Workshop: Final Modular Architecture* v1.0 and combines the modules
three teams built.

## Modules and owners

| Module | Path | Team |
|---|---|---|
| A Foundation, configuration, shared contracts | `app/core`, `app/contracts`, `app/main.py` | Team 6 (Aadityasiva) |
| B Authentication, team identity, JWT | `app/modules/authentication` | Team 6 |
| C Team API keys, IDE sync | `app/modules/ide_sync` | Team 6 |
| D Widget catalog | `app/modules/catalog` | Team 3 (Anish Prakash, Saswat Singh) |
| E Credit ledger and wallet | `app/modules/ledger` | Team 3 |
| F Team widget inventory | `app/modules/inventory` | Team 3 |
| G Market and round lifecycle | `app/modules/market` | Team 2 (Srijan Guchhait) |
| H Pricing engine | `app/modules/pricing` | Team 2 |
| I Purchase / trade engine | `app/trading` | Team 2 (Daksh Agarwal, Srijan Guchhait) |
| J Auction and bidding | `app/auction` | Team 2 (Daksh Agarwal, Srijan Guchhait) |
| K Organizer / admin control plane | `app/modules/admin` | Team 3 |
| L Audit, observability | `app/core/logging.py` hook only | not yet owned |
| M Infrastructure, deployment, DB operations | `app/infra`, `cloudflare/`, `migrations/`, `scripts/`, `docs/` | Team 2 (J. Navin, Srijan Guchhait) |

Module docs: [A](docs/module-a.md), [B](docs/module-b.md), [C](docs/module-c.md),
[G](docs/market.md), [H](docs/pricing.md), [I/J integration](docs/INTEGRATION.md),
[M](docs/deployment.md).

## How the modules connect

Each module registers its own gateway and routes. Shared contracts in `app/contracts/`
are resolved through `app/core/services.py`; feature modules never import another
module's implementation. Boundary tests enforce this in CI:

- **Identity:** Module B verifies Google sign-in and issues the JWT; every route uses its
  principal. Team ids are UUIDs. Module K's `organizer` table decides who is an
  organizer (checked on every request), and its permissions guard every `/admin` route.
  An organizer signs in with Google like anyone else; their token has no team.
- **Teams:** Module K creates and disables teams through Module B's team directory.
- **Purchases and auctions:** Modules I and J run each operation in one transaction over
  the owners' services: G (round, stock, auction lots), H (price), D (widget), E (credits
  and bid holds) and F (inventory). Module K's emergency freeze is checked first in every
  purchase, sale and bid.
- **IDE sync:** Module C reads team inventory from Module F.
- **Dashboard:** Module K reads market status from G and the cross-team trade feed from I.

Errors share one shape: `{"error": {"code": ..., "message": ..., "context": {...}}}`.

## Run locally

```bash
cd server
uv sync
cp .env.example .env            # then fill in the values (see below)
uv run alembic upgrade head     # one migration history for every module
uv run python -m app.modules.admin.cli add-owner --email you@example.com --name "Event Lead"
uv run uvicorn app.main:app --reload
```

Settings come from the environment (or `.env`); see `.env.example` and
[docs/environment-reference.md](docs/environment-reference.md).

## Tests

All suites use real PostgreSQL (locks, CHECKs, triggers and partial indexes must be real):

| Directory | Covers | Database |
|---|---|---|
| `tests/foundation/` | A, B, C | `TEST_DATABASE_URL` (migrated schema) |
| `tests/owners/` | D, E, F, K | `TEST_DATABASE_URL` |
| `tests/market/` | G, H, I, J, M | `MODULES_TEST_DATABASE_URL` (`*_modules_test`) |
| `tests/e2e/` | the whole app over HTTP with real JWTs | `TEST_DATABASE_URL` |

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:17
for db in flutterwars_test flutter_modules_test flutter_migration_test; do
  docker exec pg psql -U postgres -c "CREATE DATABASE $db"; done
TEST_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_test \
MODULES_TEST_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:5432/flutter_modules_test \
MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:pw@localhost:5432/flutter_migration_test \
uv run pytest
```

Every database named here is wiped by the tests: never point them at a real one.

## Deploy (Cloudflare)

The Worker (`cloudflare/`) builds the app on its first request from its vars, secrets
and the `HYPERDRIVE` binding (pg8000, NullPool). Fill in the placeholders in
`cloudflare/wrangler.jsonc` (or keep your own git-ignored `wrangler.local.jsonc`), set
the secrets, migrate, then deploy:

```bash
cd server/cloudflare
npx wrangler secret put JWT_SECRET_KEY
npx wrangler secret put GOOGLE_OAUTH_CLIENT_SECRET
(cd .. && DATABASE_URL=<Neon direct URL> uv run alembic upgrade head)
npm ci && uv sync && ./build.sh && uv run pywrangler deploy
```

See [docs/deployment.md](docs/deployment.md) for the Hyperdrive setup (Neon's direct
host, caching disabled) and the gated GitHub deploy workflow.

## OAuth and JWT setup

### Google OAuth

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select the workshop project.
3. Configure the OAuth consent screen and add the participant test accounts.
4. Create an OAuth client ID for the client application. Use the client type
   required by the Flutter app (typically Android, iOS, or Web).
5. Copy the client ID into `.env`:

   ```dotenv
   GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
   ```

6. Configure the client app with the matching package name, signing certificate,
   bundle ID, or authorized JavaScript origin/redirect URI in Google Cloud.

The client sends the Google ID token to `POST /auth/google`:

```json
{"credential":"<google-id-token>"}
```

The backend verifies the token, requires the Google subject to be registered in
`user_identity`, requires an active `team_membership`, and returns a backend JWT.
The backend does not accept the Google token directly on other API routes.

For server-initiated sign-in, configure the Web OAuth client secret and redirect
URI, then open:

```text
GET /auth/google/login
```

Google redirects back to `/auth/google/callback?code=<code>&state=<state>`. The
server validates the state cookie, exchanges the code using the client secret,
verifies the returned Google ID token, and returns the backend JWT. Register the
exact redirect URI in Google Cloud and keep `GOOGLE_OAUTH_CLIENT_SECRET` only on
the server.

### JWT configuration

Generate a strong secret locally. Do not commit it or place it in client code:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$jwtSecret = [Convert]::ToBase64String($bytes)
Write-Output $jwtSecret
```

Set the result in `.env`:

```dotenv
JWT_SECRET_KEY=<generated-secret>
JWT_ALGORITHM=HS256
AUTH_ACCESS_TOKEN_MINUTES=15
```

The JWT is returned by `POST /auth/google` and is used for participant APIs:

```http
Authorization: Bearer <backend-jwt>
```

Use `GET /auth/me` to verify the current server-resolved user, team, and role.
JWT authentication is separate from Module C's IDE credential:

```http
X-Team-API-Key: <team-api-key>
```

Do not use participant JWTs for `GET /ide/state`, and do not use team API keys
for participant marketplace or account APIs.
