"""Module C: team API keys and read-only IDE state synchronization."""

from fastapi import FastAPI

from app.modules.ide_sync.router import router


def register(app: FastAPI) -> None:
    app.include_router(router)  # /ide/state, /admin/teams/{id}/api-keys
