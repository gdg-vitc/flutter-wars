"""Importable application factory without environment or database initialization."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.db import configure_database
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.modules import register_modules
from app.trading.registration import build_runtime


def build_app(*, title: str = "Flutter Wars backend") -> FastAPI:
    """Build routes and middleware without reading settings or opening a database."""
    app = FastAPI(title=title)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_exception_handlers(app)
    register_modules(app)
    return app


def initialize_app(app: FastAPI, settings: Settings) -> None:
    """Attach runtime settings after the route graph has been constructed."""
    configure_logging(settings)
    app.state.settings = settings
    app.state.backend_runtime = build_runtime(settings)
    app.title = settings.app_name
    if app.openapi_schema is not None:
        app.openapi_schema["info"]["title"] = settings.app_name


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application with explicit Foundation infrastructure only."""
    active_settings = settings or get_settings()
    configure_database(active_settings)
    app = build_app(title=active_settings.app_name)
    initialize_app(app, active_settings)
    return app
