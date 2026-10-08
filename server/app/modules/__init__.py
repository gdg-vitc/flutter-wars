"""Explicit module-router registration."""

from fastapi import FastAPI

from app.modules.authentication.router import router as authentication_router
from app.modules.foundation.router import router as foundation_router
from app.modules.ide_sync.router import router as ide_sync_router


def register_modules(app: FastAPI) -> None:
    """Register independently owned feature routers explicitly."""
    app.include_router(foundation_router)
    app.include_router(authentication_router)
    app.include_router(ide_sync_router)
