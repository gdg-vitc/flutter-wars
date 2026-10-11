"""Google ID-token verification adapter.

Async and dependency-light so it runs both under uvicorn and in a Cloudflare Python
Worker: Google's signing keys are fetched through app.modules.authentication.http and
the token is verified with PyJWT (RS256), checking signature, expiry, audience (our OAuth client ID), issuer and
that Google has verified the email.
"""

import logging
import time

import jwt

from app.modules.authentication import http

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
_KEYS: dict[str, dict] = {}
_KEYS_EXPIRE_AT = 0.0
log = logging.getLogger("gdg_workshop")


async def _signing_keys(refresh: bool = False) -> dict[str, dict]:
    global _KEYS, _KEYS_EXPIRE_AT
    if refresh or time.monotonic() >= _KEYS_EXPIRE_AT:
        certs = await http.get_json(GOOGLE_CERTS_URL)
        _KEYS = {key["kid"]: key for key in certs["keys"]}
        _KEYS_EXPIRE_AT = time.monotonic() + 3600
    return _KEYS


async def verify_google_token(credential: str, audience: str | None) -> dict[str, str] | None:
    """Return only stable identity claims; never log credentials or provider details."""
    if not audience:
        log.warning("google_verification_failed reason=missing_client_id")
        return None  # without our client ID the audience cannot be checked
    try:
        kid = jwt.get_unverified_header(credential).get("kid")
        keys = await _signing_keys()
        if kid not in keys:
            keys = await _signing_keys(refresh=True)  # Google rotated its keys
        if kid not in keys:
            log.warning("google_verification_failed reason=unknown_signing_key")
            return None
        claims = jwt.decode(
            credential,
            key=jwt.PyJWK(keys[kid]).key,
            algorithms=["RS256"],
            audience=audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, http.HttpError, KeyError, TypeError, ValueError) as exc:
        # Exception classes identify failed checks without exposing credentials,
        # claims, provider response text, or traceback source loading in Pyodide.
        log.warning("google_verification_failed exception_type=%s", type(exc).__name__)
        return None
    subject = claims.get("sub")
    email = claims.get("email")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        log.warning("google_verification_failed reason=invalid_issuer")
        return None
    if claims.get("email_verified") is not True:
        log.warning("google_verification_failed reason=email_not_verified")
        return None
    if not isinstance(subject, str) or not isinstance(email, str):
        log.warning("google_verification_failed reason=missing_identity_claims")
        return None
    return {"google_subject": subject, "email": email}
