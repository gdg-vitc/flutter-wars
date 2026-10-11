"""Module K (organizer/admin control plane) contract.

Module K decides who is an organizer and what they may do, keeps the audit log and
the emergency freeze. Every module's organizer routes use require_permission()
(app/core/auth.py), which asks Module K through this gateway on every request.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Protocol
from uuid import UUID


class Role(StrEnum):
    OWNER = "OWNER"  # everything, incl. managing organizers
    OPERATOR = "OPERATOR"  # runs the event: credits, inventory, teams, market, freeze
    VIEWER = "VIEWER"  # read-only dashboards (volunteers, judges)


class Permission(StrEnum):
    VIEW = "view"
    AUDIT_READ = "audit.read"
    TEAMS_MANAGE = "teams.manage"
    CREDITS_ADJUST = "credits.adjust"
    INVENTORY_ADJUST = "inventory.adjust"
    CATALOG_MANAGE = "catalog.manage"  # Module D admin routes
    MARKET_MANAGE = "market.manage"  # Modules G/H/J admin routes (rounds, pricing, auctions)
    API_KEYS_MANAGE = "api_keys.manage"  # Module C admin routes
    CONTROLS_FREEZE = "controls.freeze"
    ORGANIZERS_MANAGE = "organizers.manage"


class AuditAction(StrEnum):
    """Every organizer mutation writes one of these to admin_action_log."""

    ORGANIZER_CREATE = "organizer.create"
    ORGANIZER_UPDATE = "organizer.update"
    CONTROL_FREEZE = "control.freeze"
    CONTROL_UNFREEZE = "control.unfreeze"
    TEAM_CREATE = "team.create"
    TEAM_STATUS = "team.status"
    CREDITS_GRANT_INITIAL = "credits.grant_initial"
    CREDITS_ADJUST = "credits.adjust"
    INVENTORY_ADJUST = "inventory.adjust"
    WIDGET_CREATE = "catalog.widget_create"
    WIDGET_UPDATE = "catalog.widget_update"
    WIDGET_ARCHIVE = "catalog.widget_archive"
    WIDGET_RESTORE = "catalog.widget_restore"


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


class AdminGateway(Protocol):
    OWNER: ClassVar[str] = "Module K admin"

    def is_organizer(self, email: str) -> bool:
        """Is this Google-verified email an active organizer? (Module B's organizer logins.)"""
        ...

    def authorize(
        self, *, email: str | None, user_id: str, permission: Permission | None
    ) -> OrganizerPrincipal:
        """The caller as an organizer holding `permission` (any organizer when None).

        Raises 403 NOT_ORGANIZER / MISSING_PERMISSION."""
        ...

    def ensure_not_frozen(self, scope: str) -> None:
        """Call FIRST in a participant mutation ("TRADING" or "BIDDING"); 423 if frozen."""
        ...

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
        """Record one organizer mutation in the caller's transaction."""
        ...
