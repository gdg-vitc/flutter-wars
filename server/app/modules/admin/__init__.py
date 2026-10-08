"""Module K — Organizer/Admin Control Plane. Public contract for every module's admin routes,
and for Module I (ensure_not_frozen("TRADING")) and Module J (ensure_not_frozen("BIDDING"))."""

from app.modules.admin.authz import OrganizerPrincipal, require_organizer, require_permission
from app.modules.admin.permissions import AuditAction, Permission, Role
from app.modules.admin.service import audit, ensure_not_frozen

__all__ = [
    "AuditAction",
    "OrganizerPrincipal",
    "Permission",
    "Role",
    "audit",
    "ensure_not_frozen",
    "require_organizer",
    "require_permission",
]
