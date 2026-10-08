"""Verified-SQL cache (SPEC §5.4). High-confidence or thumbs-up answers are stored; a near-identical new
question on the same DB (cosine ≥ 0.95 with embeddings, exact normalized match without) reuses the SQL as
ONE candidate — it still runs and is voted on, never trusted blindly. Entries also feed few-shot examples.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import select

from datapilot.config import get_settings
from datapilot.db.schema import sql_cache
from datapilot.db.session import run_db
from datapilot.index.schema_index import embed_query

log = logging.getLogger(__name__)
COSINE_MIN = 0.95
_recent: dict[str, list[dict]] = defaultdict(list)
_loaded = False


def normalize(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%$.-]", " ", q.lower())).strip()


async def warm() -> None:
    global _loaded
    if _loaded:
        return

    def load(conn):  # type: ignore[no-untyped-def]
        return conn.execute(
            select(sql_cache.c.db_id, sql_cache.c.question_norm, sql_cache.c.sql).order_by(sql_cache.c.created_at.desc()).limit(500)
        ).all()

    try:
        for r in await run_db(load):
            if len(_recent[r.db_id]) < 50:
                _recent[r.db_id].append({"question": r.question_norm, "sql": r.sql})
    except Exception as e:  # noqa: BLE001
        log.warning("sql cache warm failed: %s", e)
    _loaded = True


def recent_examples(db_id: str) -> list[dict]:
    return list(_recent.get(db_id, []))


async def lookup(db_id: str, question: str) -> str | None:
    qn = normalize(question)
    vec = await embed_query(qn) if get_settings().gemini_ready else None

    def find(conn):  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(sql_cache.c.id, sql_cache.c.question_norm, sql_cache.c.sql, sql_cache.c.embedding).where(
                sql_cache.c.db_id == db_id
            )
        ).all()
        best, best_sim = None, 0.0
        for r in rows:
            if r.question_norm == qn:
                return r
            if vec is not None and r.embedding:
                sim = sum(a * b for a, b in zip(vec, r.embedding, strict=False))
                if sim > best_sim:
                    best, best_sim = r, sim
        return best if best_sim >= COSINE_MIN else None

    try:
        row = await run_db(find)
    except Exception as e:  # noqa: BLE001
        log.warning("sql cache lookup failed: %s", e)
        return None
    if row is None:
        return None

    def bump(conn):  # type: ignore[no-untyped-def]
        conn.execute(sql_cache.update().where(sql_cache.c.id == row.id).values(hits=sql_cache.c.hits + 1))

    await run_db(bump)
    return str(row.sql)


async def store(db_id: str, question: str, sql: str, result_hash: str, source: str, profile_version: str) -> None:
    qn = normalize(question)
    vec = await embed_query(qn) if get_settings().gemini_ready else None

    def put(conn):  # type: ignore[no-untyped-def]
        exists = conn.execute(
            select(sql_cache.c.id).where((sql_cache.c.db_id == db_id) & (sql_cache.c.question_norm == qn))
        ).first()
        if exists:
            conn.execute(
                sql_cache.update().where(sql_cache.c.id == exists.id).values(sql=sql, result_hash=result_hash, source=source)
            )
        else:
            conn.execute(
                sql_cache.insert().values(
                    db_id=db_id, question_norm=qn, embedding=vec, sql=sql, result_hash=result_hash, source=source,
                    profile_version=profile_version,
                )
            )

    try:
        await run_db(put)
        lst = _recent[db_id]
        lst[:] = [e for e in lst if e["question"] != qn]
        lst.insert(0, {"question": qn, "sql": sql})
        del lst[50:]
    except Exception as e:  # noqa: BLE001
        log.warning("sql cache store failed: %s", e)
