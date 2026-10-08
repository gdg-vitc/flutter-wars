# GDG Flutter Workshop Backend

Module A provides the intentionally thin FastAPI foundation shared by all backend modules. It does not own business entities or authentication logic.

## Prerequisites and installation

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or install the dependencies from `pyproject.toml` with your preferred Python environment manager).

```powershell
uv sync --all-groups
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `.env`. For local development, start PostgreSQL in Docker:

```powershell
docker run --name flutter-wars-postgres `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=flutter_wars `
  -p 5432:5432 `
  -d postgres:18
```

The `.env.example` URL points to this container. Stop and restart it with:

```powershell
docker stop flutter-wars-postgres
docker start flutter-wars-postgres
```

After PostgreSQL is running, apply the canonical Alembic migration chain:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/flutter_wars"
uv run alembic upgrade head
```

Do not run the migration chain with `DATABASE_URL=sqlite://`; the deployment
migrations target PostgreSQL. SQLite remains available only for the automated
unit-test fixtures.

To inspect or roll back the latest revision:

```powershell
uv run alembic current
uv run alembic downgrade -1
```

For deployment, set `DATABASE_URL` to the provider-supplied Cloudflare Hyperdrive route to Neon; no provider-specific adapter is hard-coded here.

## Run and test

```powershell
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check .
uv run alembic upgrade head
```

`GET /health` returns `{"status":"ok"}` for process liveness. `GET /ready` verifies the configured database path and returns `{"status":"ready"}` or a safe `503` error.

`JWT_SECRET_KEY` is required for Module B. Generate a unique high-entropy value
for each environment. `GOOGLE_OAUTH_CLIENT_ID` must be set in production so
Google ID tokens are verified for this backend's client ID. Run authentication
and synchronization tests with `uv run pytest tests/test_authentication.py tests/test_ide_sync.py`.

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
JWT_ACCESS_TOKEN_MINUTES=60
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

## Structure

- `app/core`: settings, database lifecycle, error mapping, logging hooks, principal boundary.
- `app/contracts`: stable public shared contracts.
- `app/modules/foundation`: health/readiness router only.
- `tests`: Foundation contract tests.

Future modules expose an `APIRouter` and add it explicitly in `app/modules/__init__.py`. For example:

```python
router = APIRouter(prefix="/example")


@router.get("/")
def example():
    return {"status": "ok"}
```

See [docs/module-a.md](docs/module-a.md) for integration boundaries.

## Module C IDE synchronization

Module C adds organizer-managed team API keys and `GET /ide/state`. The IDE must send `X-Team-API-Key`; this is a separate credential from participant JWTs. See [docs/module-c.md](docs/module-c.md) for endpoint details, integration boundaries, and the local fake-inventory test setup. Run its coverage with `uv run pytest tests/test_ide_sync.py`.

## Module B authentication

`POST /auth/google` verifies a Google ID token, resolves the registered identity's current team membership, and issues a participant JWT. `GET /auth/me` requires `Authorization: Bearer <jwt>` and returns the current server-resolved principal. See [docs/module-b.md](docs/module-b.md).
