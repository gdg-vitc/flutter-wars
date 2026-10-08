"""Module K: organizer roles and what each role may do. Server-side only — never from the client."""

from enum import StrEnum


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


_VIEWER = {Permission.VIEW, Permission.AUDIT_READ}
_OPERATOR = _VIEWER | {
    Permission.TEAMS_MANAGE,
    Permission.CREDITS_ADJUST,
    Permission.INVENTORY_ADJUST,
    Permission.CATALOG_MANAGE,
    Permission.MARKET_MANAGE,
    Permission.API_KEYS_MANAGE,
    Permission.CONTROLS_FREEZE,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_VIEWER),
    Role.OPERATOR: frozenset(_OPERATOR),
    Role.OWNER: frozenset(Permission),
}


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
