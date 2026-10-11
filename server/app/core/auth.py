"""Shared access helpers over Module B's verified principal.

Module B (app/core/principal.py) proves WHO is calling and which team they belong
to. Module K decides who is an organizer, from its own `organizer` table, on every
request, through its gateway. Feature modules use these helpers instead of reading
tokens themselves:

    @router.post("/admin/...")
    def handler(org: OrganizerPrincipal = Depends(require_permission(Permission.MARKET_MANAGE))): ...
"""

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends
from sqlmodel import Session

from app.contracts.admin import AdminGateway, OrganizerPrincipal, Permission
from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.principal import get_principal
from app.core.services import gateway

__all__ = [
    "Principal",
    "get_principal",
    "require_organizer",
    "require_participant",
    "require_permission",
    "require_team",
]


def require_participant(principal: Principal = Depends(get_principal)) -> Principal:
    """The caller must act for a team (participants; not organizer-only logins)."""
    if principal.team_id is None:
        raise AppError("NOT_A_TEAM_MEMBER", "This endpoint needs a team member login.", 403)
    return principal


def require_team(principal: Principal = Depends(require_participant)) -> UUID:
    """The caller's team id."""
    assert principal.team_id is not None
    return principal.team_id


def require_organizer(
    principal: Principal = Depends(get_principal), s: Session = Depends(get_db)
) -> OrganizerPrincipal:
    """Any active organizer (Module K decides, on every request)."""
    return gateway(AdminGateway, s).authorize(email=principal.email, user_id=principal.user_id, permission=None)


def require_permission(permission: Permission) -> Callable[..., OrganizerPrincipal]:
    """An active organizer whose role grants `permission`."""

    def _dep(org: OrganizerPrincipal = Depends(require_organizer)) -> OrganizerPrincipal:
        if not org.can(permission):
            raise AppError(
                "MISSING_PERMISSION", "Your organizer role cannot do this", 403, {"permission": permission.value}
            )
        return org

    _dep.__name__ = f"require_permission_{permission.name.lower()}"
    return _dep
