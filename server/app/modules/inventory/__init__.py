"""Module F — Team Widget Inventory.

Other modules reach it only through InventoryGateway (app/contracts/inventory.py).
"""

from fastapi import FastAPI

from app.contracts.inventory import InventoryGateway
from app.core.services import provide
from app.modules.inventory.admin_router import router as admin_router
from app.modules.inventory.gateway import InventoryGatewayImpl
from app.modules.inventory.router import router


def register(app: FastAPI) -> None:
    provide(InventoryGateway, InventoryGatewayImpl)
    app.include_router(router)  # /inventory
    app.include_router(admin_router)  # /admin/teams/{team_id}/...
