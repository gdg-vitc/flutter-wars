"""Imports every table model so SQLModel.metadata is complete (Alembic, tests)."""

from app.modules.catalog.models import Widget

# Tables whose constraints also run on SQLite (fast unit tests).
SQLITE_SAFE = (Widget,)

__all__ = [
    "SQLITE_SAFE",
    "Widget",
]
