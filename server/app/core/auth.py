"""TEMP until Module B (Auth) is merged.

Module B owns identity: it verifies the backend JWT and returns WHO is calling.
It does NOT decide who is an organizer — Module K (app/modules/admin) does that
from its own `organizer` table, so organizer access can be granted/revoked instantly.

`get_principal` deliberately refuses every request, so nothing can run
unauthenticated by accident. Tests replace it with `app.dependency_overrides`.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends

from app.core.errors import AppError


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str | None = None  # Google-verified email (Module B must check email_verified)
    team_id: UUID | None = None  # None for organizers / users without a team


def get_principal() -> Principal:
    raise AppError("AUTH_NOT_AVAILABLE", "Authentication module is not merged yet", 501)


def require_participant(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.team_id is None:
        raise AppError("NOT_A_TEAM_MEMBER", "This endpoint needs a team member login", 403)
    return principal
