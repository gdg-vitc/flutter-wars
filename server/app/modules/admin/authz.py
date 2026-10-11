"""Module K authorization: who is an organizer, and what may they do.

Module B only proves identity (a Google-verified email). Module K decides organizer
status from its own `organizer` table on EVERY request, so removing someone takes
effect on their very next click — no waiting for a token to expire.

Every module's organizer routes use app.core.auth.require_permission(...), which
calls authorize() below through Module K's gateway (app/modules/admin/gateway.py).
"""

import logging

from sqlmodel import Session

from app.contracts.admin import OrganizerPrincipal, Permission
from app.core.auth import require_organizer, require_permission
from app.modules.admin import repository as repo
from app.modules.admin.errors import MissingPermission, NotOrganizer
from app.modules.admin.permissions import ROLE_PERMISSIONS, Role

__all__ = ["OrganizerPrincipal", "authorize", "require_organizer", "require_permission"]

log = logging.getLogger("flutterwars.admin")


def authorize(
    s: Session, *, email: str | None, user_id: str, permission: Permission | None
) -> OrganizerPrincipal:
    if not email:
        raise NotOrganizer()
    org = repo.get_active_organizer_by_email(s, email)
    if org is None:
        # Log the attempt (no tokens, no secrets) so organizers can spot probing.
        log.warning("admin access denied: user_id=%s", user_id)
        raise NotOrganizer()
    role = Role(org.role)
    principal = OrganizerPrincipal(
        id=org.id, email=org.email, display_name=org.display_name, role=role, permissions=ROLE_PERMISSIONS[role]
    )
    if permission is not None and not principal.can(permission):
        log.warning("admin permission denied: organizer=%s permission=%s", org.id, permission.value)
        raise MissingPermission(permission.value)
    return principal
