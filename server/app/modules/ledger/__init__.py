"""Module E — Credit Ledger & Wallet.

Other modules reach it only through LedgerGateway (app/contracts/ledger.py).
"""

from fastapi import FastAPI

from app.contracts.ledger import LedgerGateway
from app.core.services import provide
from app.modules.ledger.admin_router import router as admin_router
from app.modules.ledger.gateway import LedgerGatewayImpl
from app.modules.ledger.router import router


def register(app: FastAPI) -> None:
    provide(LedgerGateway, LedgerGatewayImpl)
    app.include_router(router)  # /wallet
    app.include_router(admin_router)  # /admin/teams/{team_id}/...
