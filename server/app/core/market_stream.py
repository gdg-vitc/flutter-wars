"""Transport-independent authentication and snapshots for public market streams.

Browsers obtain a 30-second ticket over authenticated HTTP, then send it in a
WebSocket subprotocol (never a URL). Tickets use a separate derived signing key,
so a stream ticket cannot be used as a REST access token. The underlying access
token is revalidated against current identity/team status while connected.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from sqlmodel import Session

from app.contracts.identity import IdentityGateway
from app.contracts.market import MarketGateway
from app.core.config import Settings
from app.core.errors import AppError
from app.core.services import gateway

PROTOCOL = "flutter-wars.v1"
RECOVERY_SECONDS = 5
MAX_CONNECTIONS = 128


@dataclass(frozen=True)
class StreamAuth:
    access_token: str
    expires_at: float


def _key(settings: Settings) -> str:
    return hmac.new(
        settings.jwt_secret_key.get_secret_value().encode(), b"flutter-wars/market-stream/v1", hashlib.sha256
    ).hexdigest()


def issue_ticket(access_token: str, round_id: UUID, settings: Settings) -> dict:
    try:
        claims = jwt.decode(
            access_token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise AppError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401) from exc
    now = datetime.now(UTC)
    expires_at = min(now + timedelta(seconds=30), datetime.fromtimestamp(claims["exp"], UTC))
    token = jwt.encode(
        {"access_token": access_token, "round_id": str(round_id), "exp": expires_at, "aud": PROTOCOL},
        _key(settings),
        algorithm="HS256",
    )
    return {
        "ticket": token,
        "expires_at": expires_at.isoformat(),
        "protocol": PROTOCOL,
        "path": f"/market/rounds/{round_id}/stream",
    }


def read_ticket(protocols: str, round_id: UUID, settings: Settings) -> StreamAuth:
    offered = [value.strip() for value in protocols.split(",")]
    tickets = [value.removeprefix("ticket.") for value in offered if value.startswith("ticket.")]
    try:
        if PROTOCOL not in offered or len(tickets) != 1:
            raise ValueError("Missing stream ticket")
        claims = jwt.decode(
            tickets[0],
            _key(settings),
            algorithms=["HS256"],
            audience=PROTOCOL,
            options={"require": ["exp", "round_id", "access_token"]},
        )
        if claims["round_id"] != str(round_id) or not isinstance(claims["access_token"], str):
            raise ValueError("Wrong round")
        access = jwt.decode(
            claims["access_token"],
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
        return StreamAuth(claims["access_token"], float(access["exp"]))
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise AppError("INVALID_STREAM_TICKET", "Stream ticket is invalid or expired.", 401) from exc


def authorize(session: Session, auth: StreamAuth, settings: Settings) -> None:
    gateway(IdentityGateway, session).principal_for_token(auth.access_token, settings)


def authorize_many(session: Session, tokens: list[str], settings: Settings) -> set[str]:
    return gateway(IdentityGateway, session).authorized_stream_tokens(tokens, settings)


def snapshot(session: Session, round_id: UUID, now: datetime) -> dict:
    payload = gateway(MarketGateway, session).stream_snapshot(round_id, now)
    # Revision describes the data, not the sampling clock or unrelated database writes.
    stable = {key: value for key, value in payload.items() if key != "server_time"}
    revision = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"type": "market_snapshot", "revision": revision, **payload}


def next_alarm(payload: dict, now: datetime, expires_at: float) -> float:
    deadline = datetime.fromisoformat(payload["valid_until"]).timestamp() if payload["valid_until"] else float("inf")
    # A five-second recovery check covers failed commit notifications and revocations.
    return max(now.timestamp() + 0.05, min(now.timestamp() + RECOVERY_SECONDS, deadline, expires_at))
