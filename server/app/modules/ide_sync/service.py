"""Credential lifecycle and verification business rules for Module C."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.core.errors import AppError
from app.modules.ide_sync.model import TeamApiKey
from app.modules.ide_sync.repository import ApiKeyRepository

_PREFIX = "twk"


@dataclass(frozen=True)
class ApiKeyPrincipal:
    team_id: str
    key_id: str


class ApiKeyService:
    """Creates, revokes, and validates backend-generated IDE credentials."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ApiKeyRepository(session)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse(raw_key: str) -> tuple[str, str] | None:
        parts = raw_key.split("_")
        if len(parts) != 3 or parts[0] != _PREFIX or not all(parts[1:]):
            return None
        return parts[1], parts[2]

    def issue(self, team_id: str) -> tuple[TeamApiKey, str]:
        # Conservative temporary policy: one active key per team. A later issue
        # rotates it by revoking the previous key in the same transaction.
        now = self._now()
        for active_key in self.repository.active_for_team(team_id):
            active_key.status = "revoked"
            active_key.revoked_at = now
            self.session.add(active_key)
        # Hex avoids delimiter ambiguity while preserving secure randomness.
        key_id = secrets.token_hex(12)
        secret = secrets.token_hex(32)
        plaintext_key = f"{_PREFIX}_{key_id}_{secret}"
        record = TeamApiKey(
            key_id=key_id,
            team_id=team_id,
            secret_hash=self._hash(secret),
            status="active",
            created_at=now,
        )
        self.repository.add(record)
        self.session.flush()
        return record, plaintext_key

    def revoke(self, team_id: str, key_id: str) -> TeamApiKey:
        record = self.repository.get(key_id)
        if record is None or record.team_id != team_id:
            raise AppError("API_KEY_NOT_FOUND", "API key was not found.", 404)
        if record.status == "revoked":
            raise AppError("API_KEY_ALREADY_REVOKED", "API key is already revoked.", 409)
        record.status = "revoked"
        record.revoked_at = self._now()
        self.session.add(record)
        self.session.flush()
        return record

    def authenticate(self, raw_key: str | None) -> ApiKeyPrincipal:
        parsed = self._parse(raw_key or "")
        if parsed is None:
            raise AppError("API_KEY_INVALID", "API key is invalid.", 401)
        key_id, secret = parsed
        record = self.repository.get(key_id)
        if (
            record is None
            or record.status != "active"
            or not hmac.compare_digest(record.secret_hash, self._hash(secret))
        ):
            raise AppError("API_KEY_INVALID", "API key is invalid.", 401)
        # Best-effort metadata; it remains in this request transaction but does
        # not change the authorization decision or expose credential material.
        record.last_used_at = self._now()
        self.session.add(record)
        return ApiKeyPrincipal(team_id=record.team_id, key_id=record.key_id)
