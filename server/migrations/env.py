import importlib
import os
import pkgutil

from alembic import context
from sqlalchemy import create_engine, pool
from sqlmodel import SQLModel

import app.core._temp_models  # noqa: F401  TEMP
import app.modules

# Import every app/modules/<name>/models.py so SQLModel.metadata knows all tables.
# Auto-discovery: a new module (ours or another team's) is picked up without editing this file.
for _m in pkgutil.iter_modules(app.modules.__path__):
    try:
        importlib.import_module(f"app.modules.{_m.name}.models")
    except ModuleNotFoundError as e:
        if e.name != f"app.modules.{_m.name}.models":
            raise  # a real import error inside models.py must not be hidden

target_metadata = SQLModel.metadata

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_dev")


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
