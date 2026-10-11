"""Module B: Google authentication and team identity.

Other modules reach it only through IdentityGateway (app/contracts/identity.py).
"""

from fastapi import FastAPI

from app.contracts.identity import IdentityGateway
from app.core.services import provide
from app.modules.authentication.gateway import IdentityGatewayImpl
from app.modules.authentication.router import router


def register(app: FastAPI) -> None:
    provide(IdentityGateway, IdentityGatewayImpl)
    app.include_router(router)  # /auth
