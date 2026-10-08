"""Alembic environment: the URL comes from DATABASE_URL (Neon) or the local SQLite fallback."""

from alembic import context

from datapilot.db.schema import metadata
from datapilot.db.session import get_engine

target_metadata = metadata


def run_migrations_online() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
