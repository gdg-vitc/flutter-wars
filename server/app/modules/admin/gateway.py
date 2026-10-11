"""Module K's gateway (app/contracts/admin.py): organizer checks, audit log, freeze."""

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.contracts.admin import AuditAction, OrganizerPrincipal, Permission
from app.modules.admin import repository as repo
from app.modules.admin import service
from app.modules.admin.authz import authorize


class AdminGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_organizer(self, email: str) -> bool:
        return repo.get_active_organizer_by_email(self.session, email) is not None

    def authorize(
        self, *, email: str | None, user_id: str, permission: Permission | None
    ) -> OrganizerPrincipal:
        return authorize(self.session, email=email, user_id=user_id, permission=permission)

    def ensure_not_frozen(self, scope: str) -> None:
        service.ensure_not_frozen(self.session, scope)

    def audit(
        self,
        actor: OrganizerPrincipal,
        action: AuditAction | str,
        *,
        target_type: str,
        target_id: str | UUID,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        service.audit(
            self.session, actor, action, target_type=target_type, target_id=target_id, reason=reason, details=details
        )
