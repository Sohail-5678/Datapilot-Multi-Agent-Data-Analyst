"""App data model (SPEC §11). Portable SQLAlchemy Core: Neon Postgres in production, SQLite locally.

Deviation from the spec: embeddings are stored as JSON arrays and ranked in Python instead of pgvector
HNSW. The corpora are tiny (≈800 column cards, a few hundred cached questions), so an in-memory cosine is
sub-millisecond, and the same code runs on SQLite in CI and on the eval adapter's throwaway databases.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


ID = BigInteger().with_variant(Integer, "sqlite")

app_users = Table(
    "app_users",
    metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("provider", String(20)),
    Column("provider_user_id", String(200)),
    Column("github_login", String(100)),
    Column("role", String(10)),
    Column("created_at", DateTime(timezone=True), default=_now),
    UniqueConstraint("provider", "provider_user_id"),
)

threads = Table(
    "threads",
    metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("user_id", String(36), ForeignKey("app_users.id"), index=True),
    Column("db_id", String(60)),
    Column("title", Text),
    Column("created_at", DateTime(timezone=True), default=_now),
    Column("updated_at", DateTime(timezone=True), default=_now),
)

runs = Table(
    "runs",
    metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("thread_id", String(36), ForeignKey("threads.id", ondelete="CASCADE"), index=True, nullable=True),
    Column("user_id", String(36), index=True),
    Column("db_id", String(60)),
    Column("question", Text),
    Column("status", String(30)),
    Column("confidence", String(10)),
    Column("chosen_sql", Text),
    Column("row_count", Integer),
    Column("profile_version", String(60)),
    Column("agent_version", String(60)),
    Column("llm_calls", Integer, default=0),
    Column("tokens_in", Integer, default=0),
    Column("tokens_out", Integer, default=0),
    Column("list_price_cost_usd", Numeric(10, 6), default=0),
    Column("latency_ms", Integer),
    Column("grounding", JSON),
    Column("answer", Text),
    Column("chart", JSON),
    Column("detail", JSON),  # plan, candidates, confidence reasons, analysis — the answer card's tabs
    Column("created_at", DateTime(timezone=True), default=_now, index=True),
)

run_spans = Table(
    "run_spans",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), index=True),
    Column("span", JSON),
)

run_results = Table(
    "run_results",
    metadata,
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("data_ref", String(40), primary_key=True),
    Column("columns", JSON),
    Column("rows", JSON),
    Column("total_rows", Integer),
)

feedback = Table(
    "feedback",
    metadata,
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("thumbs", SmallInteger),
    Column("comment", Text),
    Column("created_at", DateTime(timezone=True), default=_now),
)

schema_index = Table(
    "schema_index",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("db_id", String(60), index=True),
    Column("table_name", String(120)),
    Column("column_name", String(120)),
    Column("card", Text),
    Column("is_pii", Boolean, default=False),
    Column("embedding", JSON),
    Column("embed_model", String(80)),
    Column("schema_hash", String(32)),
)

table_docs = Table(
    "table_docs",
    metadata,
    Column("db_id", String(60), primary_key=True),
    Column("table_name", String(120), primary_key=True),
    Column("description", Text),
    Column("schema_hash", String(32)),
)

sql_cache = Table(
    "sql_cache",
    metadata,
    Column("id", String(36), primary_key=True, default=_uuid),
    Column("db_id", String(60), index=True),
    Column("question_norm", Text),
    Column("embedding", JSON),
    Column("sql", Text),
    Column("result_hash", String(32)),
    Column("source", String(20)),
    Column("profile_version", String(60)),
    Column("hits", Integer, default=0),
    Column("created_at", DateTime(timezone=True), default=_now),
)

bench_results = Table(
    "bench_results",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("bench_run", String(80)),
    Column("config", String(20)),
    Column("question_id", String(40)),
    Column("db_id", String(60)),
    Column("difficulty", String(20)),
    Column("ex", Boolean),
    Column("pred_sql", Text),
    Column("llm_calls", Integer),
    Column("tokens", Integer),
    Column("list_price_cost_usd", Numeric(10, 6)),
    Column("latency_ms", Integer),
    Column("created_at", DateTime(timezone=True), default=_now),
)

usage_counters = Table(
    "usage_counters",
    metadata,
    Column("day", Date, primary_key=True),
    Column("model", String(120), primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("requests", Integer, default=0),
    Column("tokens", Integer, default=0),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", ID, primary_key=True, autoincrement=True),
    Column("actor", String(200)),
    Column("action", String(60)),
    Column("target", String(200)),
    Column("details", JSON),
    Column("created_at", DateTime(timezone=True), default=_now),
)

Index("ix_sql_cache_db_q", sql_cache.c.db_id, sql_cache.c.question_norm)
