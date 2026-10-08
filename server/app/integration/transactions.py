from hashlib import sha256

from sqlalchemy import text
from sqlmodel import Session


def require_transaction(session: Session) -> None:
    if not session.in_transaction():
        raise RuntimeError("A caller-owned database transaction is required.")
    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Financial mutations require PostgreSQL.")


def lock_request(session: Session, *, scope: str) -> None:
    """Serialize identical retries before lifecycle/funds checks, even before INSERT.

    Transaction-scoped locks work across instances and release at commit/rollback.
    Stable hash collisions cause only extra waiting; persisted UNIQUE keys remain.
    """
    require_transaction(session)
    key = int.from_bytes(sha256(scope.encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
