"""FastAPI application factory and ASGI entrypoint."""

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.db import configure_database
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.modules import register_modules


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application with explicit Foundation infrastructure only."""
    active_settings = settings or get_settings()
    configure_logging(active_settings)
    configure_database(active_settings)
    app = FastAPI(title=active_settings.app_name)
    app.state.settings = active_settings
    install_exception_handlers(app)
    register_modules(app)
    return app


app = create_app()
