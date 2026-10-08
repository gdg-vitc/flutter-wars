"""Small logging hook that Module L can extend with structured observability."""

import logging
from collections.abc import Mapping

from app.core.config import Settings

LOGGER_NAME = "gdg_workshop"
SENSITIVE_KEYS = {"authorization", "password", "token", "secret", "api_key", "database_url"}


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure a conservative process logger without logging configuration secrets."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    return logger


def safe_context(context: Mapping[str, object] | None = None) -> dict[str, object]:
    """Remove known credential fields before emitting operational context."""
    return {
        key: value for key, value in (context or {}).items() if key.lower() not in SENSITIVE_KEYS
    }


def log_exception(event: str, exc: Exception, context: Mapping[str, object] | None = None) -> None:
    """Central hook for Module L; retain type/context but not provider error text."""
    logging.getLogger(LOGGER_NAME).error(
        "%s exception_type=%s context=%s", event, type(exc).__name__, safe_context(context)
    )
