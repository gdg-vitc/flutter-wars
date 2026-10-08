"""Application entrypoint.

PLACEHOLDER bootstrap until Module A lands. G/H contribute routers through
MODULES. I/J (trading, auction) are mounted through their own composition
boundary: `runtime` (session + owner adapters) stays unset until Ledger (E),
Inventory (F) and Catalog (D) supply adapters, so those routes answer 503.
All modules share one principal (app.core.auth.get_principal) and one error
shape (app.core.errors).
"""

from collections.abc import Callable, Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from app.auction.router import build_router as auction_router
from app.core.db import get_session
from app.core.errors import install_error_handlers
from app.integration.auth import Principal, principal_from_core
from app.integration.errors import ConfigurationRequired
from app.integration.runtime import BackendModules
from app.modules.market import router as market_router
from app.modules.pricing import router as pricing_router
from app.trading.router import build_router as trading_router

MODULES: dict[str, Sequence[APIRouter]] = {
    "market": (market_router.router, market_router.admin),
    "pricing": (pricing_router.router, pricing_router.admin),
}


def create_app(
    modules: Sequence[str] | None = None,
    *,
    runtime: BackendModules | None = None,
    principal_dependency: Callable[..., Principal] | None = None,
) -> FastAPI:
    app = FastAPI(title="Flutter Wars backend")
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(session: Session = Depends(get_session)):
        # Reports only up/down: never database names, versions or errors.
        try:
            session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    for name in MODULES if modules is None else modules:
        for router in MODULES[name]:
            app.include_router(router)

    _mount_trading_and_auction(app, runtime, principal_dependency or principal_from_core)
    return app


def _mount_trading_and_auction(
    app: FastAPI, runtime: BackendModules | None, auth: Callable[..., Principal]
) -> None:
    def get_runtime() -> BackendModules:
        if runtime is None:
            raise ConfigurationRequired()
        return runtime

    def require_team(principal: Principal = Depends(auth)) -> UUID:
        if principal.team_id is None:
            raise HTTPException(status_code=403, detail="Participant team required.")
        return principal.team_id

    def require_organizer(principal: Principal = Depends(auth)) -> None:
        if not principal.organizer:
            raise HTTPException(status_code=403, detail="Organizer permission required.")

    app.include_router(trading_router(get_runtime, require_team))
    app.include_router(auction_router(get_runtime, require_team, require_organizer))


app = create_app()
