"""Module K: organizer roles and what each role may do. Server-side only — never from the client."""

from app.contracts.admin import AuditAction, Permission, Role

__all__ = ["ROLE_PERMISSIONS", "AuditAction", "Permission", "Role"]

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
