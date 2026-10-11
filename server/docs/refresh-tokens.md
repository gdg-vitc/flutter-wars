# Application sessions and refresh tokens

Refresh-capable logins use 15-minute JWT access tokens, opaque rotating refresh
credentials, a 7-day inactivity timeout, and a fixed 30-day maximum session lifetime.
Configure `AUTH_ACCESS_TOKEN_MINUTES`, `AUTH_REFRESH_IDLE_DAYS`, and `AUTH_SESSION_DAYS`.
All login endpoints use the refresh-capable session lifetime. `JWT_ACCESS_TOKEN_MINUTES`
is retained for compatibility with older tooling, not for application login issuance.
These are application credentials, not Google refresh tokens.

## Flutter integration (opt-in)

1. Generate a cryptographically random verifier (43–128 URL-safe characters) and
   retain it until the login completes. Compute the base64url-without-padding
   SHA-256 challenge.
2. Open `/auth/google/login?code_challenge=<challenge>&code_challenge_method=S256`
   in the system browser. Google still returns to the backend HTTPS callback.
3. Handle `flutterwars://auth-callback?code=<one-time-code>` and POST to `/auth/token`
   with JSON `{"code":"...","code_verifier":"..."}`. Codes expire after 60 seconds;
   the verifier must match, and a successful exchange consumes the code atomically.
4. Store the returned refresh token in OS-backed secure storage. Never log tokens
   or callback URLs. Refresh with `POST /auth/refresh`, JSON `{"refresh_token":"..."}`.
   The response contains `access_token`, `refresh_token`, `token_type`, `expires_in`.
   Replace stored credentials after every successful refresh.
5. Serialize refresh calls across the app. A duplicate refresh, including a retry
   after a lost successful response, revokes the session and requires login.
   Refresh once for an expired-token response; do not loop on permission errors.

Without PKCE parameters, the callback redirects to
`flutterwars://auth-callback?access_token=<jwt>&refresh_token=<opaque-token>`.
Custom-scheme interception and URL logging can expose both credentials; PKCE is
recommended when the frontend is ready. `POST /auth/google` accepts a verified Google ID
credential and returns a refresh-capable session directly over HTTPS.

`POST /auth/logout` accepts the refresh token and revokes its session (204,
including unknown tokens). `POST /auth/logout-all` uses Bearer authentication and
revokes all existing refresh-capable sessions for that user. Clear local credentials.
Previously issued JWTs without session IDs remain valid until expiry; logout cannot
revoke those old tokens. New logins always issue session-bound JWTs. Current permission checks continue to apply to every request.

## Security and operations

Only SHA-256 hashes of 256-bit random refresh credentials, login codes, and OAuth
state are persisted. Session row locks serialize refresh and logout. Consumed
refresh hashes are retained until fixed session expiry: presenting a consumed token
revokes the entire session, with revocation committed even on a rejected request.
Access JWT session IDs are checked against current session state, including stream
reauthorization. Refresh also checks current team status and organizer permissions.

Login, callback, code exchange, and refresh requests share a persistent 60-attempt
per-minute limit per client IP (429 on excess). Worker mode uses Cloudflare's
provided client IP; direct ASGI deployments use the connection peer, not arbitrary
forwarded headers. This limit is shared across isolates. Proxy-hosted ASGI deployments
must configure trusted client-IP handling; large shared NATs may need a larger limit.
Successful token responses and redirect responses use `Cache-Control: no-store`.
Do not enable request-body logging for authentication endpoints. Redact callback
query strings and Referer headers in external logging. This backend cannot configure
logging policies in frontend apps or external infrastructure.

Apply additive migration `0011_auth_sessions` to the target database before deploying
this Worker. Old Workers remain compatible with the expanded schema. New Workers
require these tables even when using legacy login. Deploy backend first; PKCE remains
opt-in until the frontend adopts it. All newly issued access tokens use `AUTH_ACCESS_TOKEN_MINUTES` (default 15).

Run `uv run python scripts/cleanup_auth.py` daily outside the Worker with the intended
`DATABASE_URL` and application settings. It removes expired codes, rate buckets and
sessions, retaining replay evidence for sessions until their fixed expiry. Configure
this job in your existing scheduler; the script does not register a schedule itself.
