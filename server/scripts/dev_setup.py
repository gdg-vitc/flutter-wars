"""Development/test harness setup.

Creates ONLY the infra probe table used by `/db/roundtrip` and the tests. This is
not production schema and must never enter the Alembic migration history.

Usage:  uv run python scripts/dev_setup.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.infra.db import engine_for, resolve_target  # noqa: E402
from app.infra.infra_probe import ensure_probe_schema  # noqa: E402
from app.infra.settings import ConfigError, load_settings  # noqa: E402


def main() -> int:
    settings = load_settings()
    try:
        target = resolve_target(None, settings)
    except ConfigError as exc:
        print(f"configuration error: {exc}")
        return 1
    with engine_for(target, settings) as engine:
        ensure_probe_schema(engine)
    print(f"infra probe table ready (source={target.source}, {target.redacted()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
