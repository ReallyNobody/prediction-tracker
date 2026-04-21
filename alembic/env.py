"""Alembic environment.

Wired to use ``rmn_dashboard`` for the database URL and metadata, so the same
configuration powers migrations in dev (SQLite) and production (Postgres on
Render) with no hand-editing.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure ``src/`` is on the import path when running ``alembic`` from the
# project root, even before the package is ``pip install -e .`` installed.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rmn_dashboard.config import settings  # noqa: E402
from rmn_dashboard.database import normalize_database_url  # noqa: E402
from rmn_dashboard.models import (
    Base,  # noqa: E402  — importing the package ensures all models register
)

config = context.config

# Inject the runtime DATABASE_URL — overrides alembic.ini's placeholder.
# Normalize Render/Heroku-style ``postgres://`` URLs so SQLAlchemy 2.x accepts them.
config.set_main_option("sqlalchemy.url", normalize_database_url(settings.database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run migrations in-process."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # ``render_as_batch`` makes ALTER TABLE migrations work on SQLite,
            # which otherwise can't ALTER columns. No-op on Postgres.
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
