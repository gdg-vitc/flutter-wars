"""Application entrypoint.

PLACEHOLDER bootstrap until Module A lands. G/H contribute routers through
MODULES. All modules share one principal (app.core.auth.get_principal)
and one error shape (app.core.errors).
"""

from collections.abc import Sequence

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session

from app.core.db import get_session
from app.core.errors import install_error_handlers
from app.modules.market import router as market_router
from app.modules.pricing import router as pricing_router

MODULES: dict[str, Sequence[APIRouter]] = {
    "market": (market_router.router, market_router.admin),
    "pricing": (pricing_router.router, pricing_router.admin),
}


def create_app(modules: Sequence[str] | None = None) -> FastAPI:
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

    return app


app = create_app()
