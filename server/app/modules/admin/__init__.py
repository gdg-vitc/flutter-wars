"""Module K — Organizer/Admin Control Plane.

Other modules reach Module K only through AdminGateway (app/contracts/admin.py):
organizer checks (app.core.auth.require_permission), the audit log and the freeze.
"""

from fastapi import FastAPI

from app.contracts.admin import AdminGateway, OrganizerPrincipal, Permission, Role
from app.core.auth import require_organizer, require_permission
from app.core.services import provide
from app.modules.admin.gateway import AdminGatewayImpl
from app.modules.admin.router import public_router, router
from app.modules.admin.service import ensure_not_frozen


def register(app: FastAPI) -> None:
    provide(AdminGateway, AdminGatewayImpl)
    app.include_router(public_router)  # /controls
    app.include_router(router)  # /admin


# Compatibility exports for callers of the former public authorization API.

__all__ = [
    "register",
    "OrganizerPrincipal",
    "Permission",
    "Role",
    "require_organizer",
    "require_permission",
    "ensure_not_frozen",
]
