"""Development/test harness table.

NOT a production business entity, NOT owned by any product module, and it must
NOT enter the shared migration history. It exists only to exercise the DB
connection/session/CRUD lifecycle in the Workers runtime and over Hyperdrive.

The table is created explicitly by dev/test setup (`scripts/dev_setup.py` or the
test fixtures) via `ensure_probe_schema`. No request handler creates schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InfraProbe(SQLModel, table=True):
    __tablename__ = "infra_probe"

    id: int | None = Field(default=None, primary_key=True)
    label: str = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)


def ensure_probe_schema(engine: Engine) -> None:
    """Create only the infra probe table. Dev/test harness only."""
    InfraProbe.__table__.create(engine, checkfirst=True)
