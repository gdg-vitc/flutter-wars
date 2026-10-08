"""Google ID-token verification adapter."""

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token


def verify_google_token(credential: str, audience: str | None) -> dict[str, str] | None:
    """Return only stable identity claims; never log credentials or provider details."""
    try:
        claims = id_token.verify_oauth2_token(credential, GoogleRequest(), audience=audience)
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not isinstance(email, str):
            return None
        return {"google_subject": subject, "email": email}
    except (ValueError, KeyError):
        return None
