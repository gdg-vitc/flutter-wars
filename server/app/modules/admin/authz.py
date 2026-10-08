"""Module K authorization: who is an organizer, and what may they do.

Module B only proves identity (a Google-verified email). Module K decides organizer
status from its own `organizer` table on EVERY request, so removing someone takes
effect on their very next click — no waiting for a token to expire.

Usage in any module's admin route:

    from app.modules.admin import OrganizerPrincipal, Permission, require_permission

    @router.post("/admin/...")
    def handler(org: OrganizerPrincipal = Depends(require_permission(Permission.MARKET_MANAGE)), ...):
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from sqlmodel import Session

from app.core.auth import Principal, get_principal
from app.core.db import get_db
from app.modules.admin import repository as repo
from app.modules.admin.errors import MissingPermission, NotOrganizer
from app.modules.admin.permissions import ROLE_PERMISSIONS, Permission, Role

log = logging.getLogger("flutterwars.admin")


@dataclass(frozen=True)
class OrganizerPrincipal:
    id: UUID
    email: str
    display_name: str
    role: Role
    permissions: frozenset[Permission]

    @property
    def actor(self) -> str:
        """What other modules store in their `actor` column."""
        return self.email

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


def require_organizer(
    principal: Principal = Depends(get_principal), s: Session = Depends(get_db)
) -> OrganizerPrincipal:
    if not principal.email:
        raise NotOrganizer()
    org = repo.get_active_organizer_by_email(s, principal.email)
    if org is None:
        # Log the attempt (no tokens, no secrets) so organizers can spot probing.
        log.warning("admin access denied: user_id=%s", principal.user_id)
        raise NotOrganizer()
    role = Role(org.role)
    return OrganizerPrincipal(
        id=org.id, email=org.email, display_name=org.display_name, role=role, permissions=ROLE_PERMISSIONS[role]
    )


def require_permission(permission: Permission) -> Callable[..., OrganizerPrincipal]:
    def _dep(org: OrganizerPrincipal = Depends(require_organizer)) -> OrganizerPrincipal:
        if not org.can(permission):
            log.warning("admin permission denied: organizer=%s permission=%s", org.id, permission.value)
            raise MissingPermission(permission.value)
        return org

    _dep.__name__ = f"require_permission_{permission.name.lower()}"
    return _dep
