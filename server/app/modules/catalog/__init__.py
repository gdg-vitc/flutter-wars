"""Module D — Widget Catalog & Component Registry.

Other modules reach it only through CatalogGateway (app/contracts/catalog.py).
"""

from fastapi import FastAPI

from app.contracts.catalog import CatalogGateway
from app.core.services import provide
from app.modules.catalog.admin_router import router as admin_router
from app.modules.catalog.gateway import CatalogGatewayImpl
from app.modules.catalog.router import router


def register(app: FastAPI) -> None:
    provide(CatalogGateway, CatalogGatewayImpl)
    app.include_router(router)  # /widgets
    app.include_router(admin_router)  # /admin/widgets
