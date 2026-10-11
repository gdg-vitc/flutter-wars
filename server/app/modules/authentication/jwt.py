"""JWT creation and verification for participant application requests."""

from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError

from app.core.config import Settings
from app.core.errors import AppError


def create_access_token(
    *, user_id: str, email: str, team_id: str | None, role: str, settings: Settings, session_id: str | None = None
) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_minutes)
    claims = {"sub": user_id, "email": email, "team_id": team_id, "role": role, "exp": expires_at}
    if session_id is not None:
        claims["sid"] = session_id
    return jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> dict[str, object]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except InvalidTokenError as exc:
        raise AppError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401) from exc
