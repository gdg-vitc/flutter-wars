#!/usr/bin/env sh
# Apply the shared Alembic migration history.
#
# Runs OUTSIDE the Worker (locally or in CI) and targets Neon directly via
# DATABASE_URL -- migrations never go through Hyperdrive and are never run inside
# the Worker. Do not print or commit DATABASE_URL.
#
# Usage:
#   DATABASE_URL=postgresql://... scripts/migrate.sh            # upgrade head
#   DATABASE_URL=postgresql://... scripts/migrate.sh downgrade -1
set -eu

: "${DATABASE_URL:?DATABASE_URL must be set to the target database (Neon)}"

echo "Running migrations against the database in DATABASE_URL (credentials redacted)"
exec uv run alembic upgrade head "$@"
