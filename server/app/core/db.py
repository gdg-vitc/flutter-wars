"""TEMP until Module A (Foundation) is merged.

Delete this file and import Module A's `get_db` / `engine` instead.
Module E and F code only ever depends on `get_db()` yielding a SQLModel `Session`.
"""

import os
from collections.abc import Iterator

from sqlmodel import Session, create_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_dev",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


def get_db() -> Iterator[Session]:
    """One session per request. Routes commit explicitly; anything uncommitted is rolled back."""
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
