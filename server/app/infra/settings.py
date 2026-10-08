"""Infrastructure configuration boundary (Module M).

Owns the environment-separation contract and validation for database
configuration. Foundation / Module A owns the application configuration model;
this module provides the DB-specific settings record that Foundation consumes.

Environment separation:
- development : DATABASE_URL -> a Neon development branch (direct, TLS).
- test        : DATABASE_URL -> an isolated test database.
- staging     : a HYPERDRIVE binding (staging branch); set by the Worker `vars`.
- production  : a HYPERDRIVE binding (no DATABASE_URL needed in the Worker).

Dependency-light (stdlib only) so it is safe in the Workers runtime.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

VALID_ENVS = ("development", "test", "staging", "production")
_POSTGRES_SCHEMES = ("postgresql://", "postgresql+psycopg://", "postgres://")

# Cloudflare Hyperdrive binding name (Module M owns the deployment config).
HYPERDRIVE_BINDING = "HYPERDRIVE"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def redact_database_url(raw: str) -> str:
    """Return the URL with the password replaced by '***' for safe logging."""
    if not raw:
        return "<unset>"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable-url>"
    netloc = parts.netloc
    if "@" in netloc:
        credentials, host = netloc.rsplit("@", 1)
        user = credentials.split(":", 1)[0]
        netloc = f"{user}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    database_url: str | None = None
    db_connect_timeout_seconds: int = 5
    db_application_name: str = "flutter-wars"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def describe(self) -> dict[str, object]:
        """Safe, secret-free summary for diagnostics/logs."""
        return {
            "app_env": self.app_env,
            "database_url": redact_database_url(self.database_url or ""),
            "db_connect_timeout_seconds": self.db_connect_timeout_seconds,
            "db_application_name": self.db_application_name,
        }

    def __repr__(self) -> str:  # never leak credentials via repr()/f-strings
        return f"Settings({self.describe()!r})"


def _parse_int(
    env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not (minimum <= value <= maximum):
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _parse_str(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load and validate DB settings. Invalid values fail loudly with ConfigError."""
    env = os.environ if environ is None else environ

    app_env = (env.get("APP_ENV") or "development").strip().lower()
    if app_env not in VALID_ENVS:
        raise ConfigError(f"APP_ENV must be one of {VALID_ENVS}, got {app_env!r}")

    database_url = (env.get("DATABASE_URL") or "").strip() or None
    if database_url is not None and not database_url.startswith(_POSTGRES_SCHEMES):
        raise ConfigError("DATABASE_URL must be a postgresql:// URL")

    return Settings(
        app_env=app_env,
        database_url=database_url,
        db_connect_timeout_seconds=_parse_int(
            env, "DB_CONNECT_TIMEOUT_SECONDS", 5, minimum=1, maximum=60
        ),
        db_application_name=_parse_str(env, "DB_APPLICATION_NAME", "flutter-wars"),
    )
