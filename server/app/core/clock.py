from datetime import UTC, datetime


def get_now() -> datetime:
    """Request time. A dependency so tests can pin it."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; Postgres keeps it. Normalise both."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
