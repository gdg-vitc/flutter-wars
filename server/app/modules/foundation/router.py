"""Thin process health and dependency readiness endpoints."""

from collections.abc import Callable

from fastapi import APIRouter, Request

from app.core.db import check_database_connection
from app.core.errors import AppError
from app.core.logging import log_exception

router = APIRouter(tags=["foundation"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process liveness only; it deliberately does not contact the database."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    """Verify the configured database route is usable without disclosing details."""
    checker: Callable[[], None] = getattr(
        request.app.state, "database_check", check_database_connection
    )
    try:
        checker()
    except Exception as exc:
        log_exception("readiness_database_failed", exc)
        raise AppError(
            "DEPENDENCY_UNAVAILABLE", "Required dependencies are unavailable.", 503
        ) from exc
    return {"status": "ready"}
