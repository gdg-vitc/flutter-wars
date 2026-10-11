"""Foundation-only operational endpoints."""

from fastapi import FastAPI

from app.modules.foundation.router import router


def register(app: FastAPI) -> None:
    app.include_router(router)  # /health, /ready
