"""Server-side Google OAuth authorization-code flow."""

from urllib.parse import urlencode

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.authentication import http

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def authorization_url(settings: Settings, state: str) -> str:
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise AppError("AUTH_CONFIG_MISSING", "Google OAuth is not configured.", 503)
    query = urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTHORIZATION_URL}?{query}"


async def exchange_code(code: str, settings: Settings) -> str:
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise AppError("AUTH_CONFIG_MISSING", "Google OAuth is not configured.", 503)
    try:
        payload = await http.post_form(
            GOOGLE_TOKEN_URL,
            {
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret.get_secret_value(),
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token = payload.get("id_token")
    except (http.HttpError, AttributeError) as exc:
        raise AppError(
            "GOOGLE_CODE_EXCHANGE_FAILED", "Google sign-in could not be completed.", 401
        ) from exc
    if not isinstance(token, str) or not token:
        raise AppError("GOOGLE_CODE_EXCHANGE_FAILED", "Google sign-in could not be completed.", 401)
    return token
