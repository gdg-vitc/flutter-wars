"""Alembic environment for the one shared migration history (Module M).

Every SQLModel-owning module (A-K and G-J) registers its tables through `app.models`. The URL
comes from DATABASE_URL, or `alembic -x db_url=...`. Migrations run outside the
Worker (locally or in CI) directly against Neon, never through Hyperdrive.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers every table)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = SQLModel.metadata

# Dev/test harness tables that must never enter the migration history.
EXCLUDED_TABLES = frozenset({"infra_probe"})


def include_object(object_, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in EXCLUDED_TABLES)


def _database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("db_url") or os.environ.get(
        "DATABASE_URL", ""
    )
    if not url.strip():
        raise SystemExit(
            "DATABASE_URL is required to run migrations (or pass -x db_url=...). "
            "Migrations target Neon directly and never run inside the Worker."
        )
    return url.strip()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
