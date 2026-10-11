"""Bounded per-process cache for public, authenticated market reads only.

PostgreSQL's snapshot includes both the next transaction ID and all active writer
IDs. Comparing it catches out-of-order commits, unlike max(updated_at) or a
sequence number. Validation never assigns an ID or locks a shared counter. This
conservatively invalidates on ANY database write, including writes in other
modules. Hyperdrive query caching must be disabled.

Only READ COMMITTED, read-only sessions may use this cache. Misses are validated
again before publication. Values are copied and never used to authorize trades.
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from time import monotonic
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import Engine, text
from sqlmodel import Session

from app.core.clock import as_utc

_VERSION = text(
    "SELECT pg_current_snapshot()::text, pg_current_xact_id_if_assigned()::text, "
    "current_setting('transaction_isolation')"
)


@dataclass(frozen=True)
class _Entry:
    version: str
    value: Any
    created_at: datetime
    expires_at: datetime
    monotonic_expiry: float


class ReadCache:
    def __init__(self, *, ttl_seconds: float = 2, max_entries: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: WeakKeyDictionary[Engine, OrderedDict[Hashable, _Entry]] = WeakKeyDictionary()
        self._lock = RLock()

    @staticmethod
    def _version(session: Session) -> str | None:
        if session.new or session.dirty or session.deleted or session.in_nested_transaction():
            return None
        if session.get_bind().dialect.name != "postgresql":
            return None
        # Connection.execute avoids ORM autoflush: unpublished changes never enter the cache.
        version, own_id, isolation = session.connection().execute(_VERSION).one()
        return version if own_id is None and isolation == "read committed" else None

    def read[T](
        self,
        session: Session,
        key: Hashable,
        now: datetime,
        load: Callable[[], T],
        deadlines: Callable[[T], Iterable[datetime | None]] = lambda value: (),
    ) -> T:
        now = as_utc(now)
        version = self._version(session)
        if version is None:
            return load()
        engine = session.connection().engine
        with self._lock:
            entries = self._entries.setdefault(engine, OrderedDict())
            entry = entries.get(key)
            if (
                entry is not None
                and entry.version == version
                and entry.created_at <= now < entry.expires_at
                and monotonic() < entry.monotonic_expiry
            ):
                entries.move_to_end(key)
                return deepcopy(entry.value)
            entries.pop(key, None)
        value = load()
        expiry = min(
            [now + timedelta(seconds=self.ttl_seconds), *(as_utc(d) for d in deadlines(value) if d is not None)]
        )
        if expiry > now and self._version(session) == version:
            # A concurrent fill with a different token can only cause a later cache miss.
            with self._lock:
                entries[key] = _Entry(
                    version,
                    deepcopy(value),
                    now,
                    expiry,
                    monotonic() + min(self.ttl_seconds, (expiry - now).total_seconds()),
                )
                entries.move_to_end(key)
                while len(entries) > self.max_entries:
                    entries.popitem(last=False)
        return value


market_reads = ReadCache()
