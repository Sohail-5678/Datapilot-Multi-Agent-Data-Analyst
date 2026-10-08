"""Engine + small async helpers. Queries are short, so sync SQLAlchemy runs in a worker thread."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection

from datapilot.config import get_settings

T = TypeVar("T")


@lru_cache
def get_engine() -> Engine:
    url = get_settings().sqlalchemy_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # Neon pooled endpoint (PgBouncer, transaction mode): no server-side prepared statements.
    return create_engine(
        url,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=280,
        connect_args={"prepare_threshold": None, "connect_timeout": 10},
    )


def is_sqlite() -> bool:
    return get_engine().dialect.name == "sqlite"


def run_sync(fn: Callable[[Connection], T]) -> T:
    with get_engine().begin() as conn:
        return fn(conn)


async def run_db(fn: Callable[[Connection], T]) -> T:
    return await asyncio.to_thread(run_sync, fn)


def init_db() -> None:
    """Create tables if missing (idempotent). Alembic owns schema changes after the first release."""
    from datapilot.db.schema import metadata

    metadata.create_all(get_engine())
