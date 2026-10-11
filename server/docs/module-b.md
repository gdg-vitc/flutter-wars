# Module B: Authentication & Team Identity

## Ownership

Module B owns `user_identity`, `team`, and `team_membership`, Google ID-token verification, participant JWT issuance/verification, and the normalized `Principal` contract. Its schema is revision `0004_email_binding` in the Alembic migration chain. Run `uv run alembic upgrade head` before deployment.

## Endpoints

`POST /auth/google` accepts `{ "credential": "<Google ID token>" }`. It verifies the token, finds the preloaded participant by verified email, binds the Google subject on first sign-in, checks active team membership, and returns a bearer access token. Later sign-ins resolve the participant by the bound Google subject. Invalid credentials return `401 INVALID_GOOGLE_CREDENTIAL`; unregistered or inactive identities return `403 ACCOUNT_NOT_ALLOWED`.

For server-initiated browser sign-in, `GET /auth/google/login` redirects to
Google's authorization endpoint. Google returns to
`GET /auth/google/callback?code=<code>&state=<state>`. The callback validates
the HttpOnly state cookie, exchanges the code using the server-only OAuth client
secret, verifies the returned ID token, and issues the same backend JWT.

`GET /auth/me` requires `Authorization: Bearer <participant JWT>` and returns the current principal:

```json
{"user_id":"1","email":"participant@example.com","team_id":"1","role":"participant","auth_type":"JWT"}
```

## Principal contract and security

All participant-authenticated modules receive immutable `Principal(user_id, team_id, role, email?)`. The JWT supplies identity hints only: every request reloads the user, membership, and active team from the database. A client cannot switch teams by changing a request `team_id`, forging a stale team claim, or changing the JWT role claim.

The current model permits one team membership per user, matching the teammate implementation. Multi-team membership and principal selection are explicit future design work.

## Module C integration

Module C uses Module B's `get_principal` only for organizer/admin key lifecycle operations. Its `GET /ide/state` endpoint remains authenticated exclusively by `X-Team-API-Key`; that API key resolves the team independently of participant JWTs.
