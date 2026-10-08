"""Readiness integration hooks for Foundation / Module A.

Foundation owns the ``/ready`` endpoint and its HTTP semantics. Module M supplies
the dependency probes so readiness reflects database / Hyperdrive / configuration
failures. Results never include credentials, SQL, stack traces or provider internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.infra.db import check_connectivity, engine_for, resolve_target
from app.infra.settings import Settings


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    latency_ms: float | None = None
    source: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "ok": self.ok}
        if self.latency_ms is not None:
            payload["latency_ms"] = round(self.latency_ms, 2)
        if self.source is not None:
            payload["source"] = self.source
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        return payload


def check_database(scope_env: Any, settings: Settings) -> DependencyStatus:
    """Probe the database. Safe to call from a readiness endpoint."""
    try:
        target = resolve_target(scope_env, settings)
    except Exception as exc:  # noqa: BLE001 - configuration problem; surface type only
        return DependencyStatus(name="database", ok=False, error_type=type(exc).__name__)

    with engine_for(target, settings) as engine:
        ok, latency_ms, error_type = check_connectivity(engine)
    return DependencyStatus(
        name="database",
        ok=ok,
        latency_ms=latency_ms,
        source=target.source,
        error_type=error_type,
    )


def readiness(scope_env: Any, settings: Settings) -> tuple[bool, dict[str, object]]:
    """Return (is_ready, payload) for Foundation's /ready endpoint."""
    status = check_database(scope_env, settings)
    payload: dict[str, object] = {
        "status": "ready" if status.ok else "not_ready",
        "dependencies": [status.to_dict()],
    }
    return status.ok, payload
