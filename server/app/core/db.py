from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings

_engine: Engine | None = None


def configure_engine(url: str, **kwargs: Any) -> Engine:
    """Use this engine instead of one built from DATABASE_URL.

    For runtimes whose connection details arrive at request time, such as a
    Cloudflare Worker's Hyperdrive binding (see server/cloudflare/).
    """
    global _engine
    _engine = create_engine(url, **kwargs)
    return _engine


def engine_configured() -> bool:
    return _engine is not None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session() -> Iterator[Session]:
    """One session per request. Mutating routes commit explicitly; anything
    left uncommitted (including after an exception) is rolled back on close."""
    with Session(get_engine()) as session:
        yield session
