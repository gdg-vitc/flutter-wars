"""Organizer audit log, kept by Module K: every module's organizer mutation records
one row in the SAME transaction as the change, through Module K's gateway."""

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.contracts.admin import AdminGateway, AuditAction, OrganizerPrincipal
from app.core.services import gateway


def audit(
    s: Session,
    actor: OrganizerPrincipal,
    action: AuditAction | str,
    *,
    target_type: str,
    target_id: str | UUID,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    gateway(AdminGateway, s).audit(
        actor, action, target_type=target_type, target_id=target_id, reason=reason, details=details
    )
