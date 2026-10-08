"""Persistence of runs, spans, results, feedback and threads (SPEC §11). Retention: 30 days."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, desc, select

from datapilot.config import get_settings
from datapilot.db.schema import audit_log, feedback, run_results, run_spans, runs, threads
from datapilot.db.session import run_db

if TYPE_CHECKING:
    from datapilot.api.runs import RunHandle
    from datapilot.runner import RunOutcome

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def create_thread(user_id: str, db_id: str, title: str | None = None) -> str:
    def fn(conn):  # type: ignore[no-untyped-def]
        res = conn.execute(threads.insert().values(user_id=user_id, db_id=db_id, title=title, updated_at=_now()))
        return str(res.inserted_primary_key[0])

    return await run_db(fn)


async def get_thread(thread_id: str) -> Any:
    return await run_db(lambda c: c.execute(select(threads).where(threads.c.id == thread_id)).first())


async def list_threads(user_id: str, limit: int = 30) -> list[dict]:
    def fn(conn):  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(threads).where(threads.c.user_id == user_id).order_by(desc(threads.c.updated_at)).limit(limit)
        ).all()
        return [dict(r._mapping) for r in rows]

    return await run_db(fn)


async def thread_runs(thread_id: str) -> list[dict]:
    def fn(conn):  # type: ignore[no-untyped-def]
        rows = conn.execute(select(runs).where(runs.c.thread_id == thread_id).order_by(runs.c.created_at)).all()
        return [dict(r._mapping) for r in rows]

    return await run_db(fn)


async def thread_history(thread_id: str) -> list[dict]:
    rows = await thread_runs(thread_id)
    return [{"question": r["question"], "sql": r["chosen_sql"]} for r in rows if r["status"] == "success"][-3:]


async def create_run(
    run_id: str, thread_id: str, user_id: str, db_id: str, question: str, profile_version: str
) -> None:
    s = get_settings()

    def fn(conn):  # type: ignore[no-untyped-def]
        conn.execute(
            runs.insert().values(
                id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                db_id=db_id,
                question=question,
                status="running",
                profile_version=profile_version,
                agent_version=s.git_sha,
            )
        )
        title = question if len(question) <= 80 else question[:77] + "…"
        conn.execute(threads.update().where(threads.c.id == thread_id).values(updated_at=_now()))
        t = conn.execute(select(threads.c.title).where(threads.c.id == thread_id)).scalar()
        if not t:
            conn.execute(threads.update().where(threads.c.id == thread_id).values(title=title))

    await run_db(fn)


def _detail(out: RunOutcome) -> dict:
    st = out.state
    steps = []
    for r in st.get("step_results") or []:
        steps.append(
            {
                k: r.get(k)
                for k in (
                    "step_idx",
                    "goal",
                    "sql",
                    "row_count",
                    "truncated",
                    "confidence",
                    "reasons",
                    "data_ref",
                    "candidates",
                    "ok",
                    "columns",
                )
            }
        )
    a = st.get("analysis") or None
    return {
        "plan": st.get("plan") or [],
        "plan_meta": st.get("plan_meta") or {},
        "clarification": st.get("clarification"),
        "steps": steps,
        "analysis": {k: a.get(k) for k in ("ok", "code", "result", "stdout", "error", "duration_ms", "ran_in", "rows")}
        if a
        else None,
        "notes": st.get("notes") or [],
        "input_verdict": st.get("input_verdict"),
        "end_state": out.end_state,
        "metrics": out.trace["metrics"],
    }


async def finish_run(h: RunHandle, out: RunOutcome) -> None:
    st = out.state
    m = out.trace["metrics"]
    results = [r for r in st.get("step_results") or [] if r.get("ok")]
    last = results[-1] if results else None

    def fn(conn):  # type: ignore[no-untyped-def]
        conn.execute(
            runs.update()
            .where(runs.c.id == h.run_id)
            .values(
                status=out.status,
                confidence=last["confidence"] if last else None,
                chosen_sql=last["sql"] if last else None,
                row_count=last["row_count"] if last else None,
                llm_calls=m["llm_calls"],
                tokens_in=m["tokens_in"],
                tokens_out=m["tokens_out"],
                list_price_cost_usd=m["list_price_cost_usd"],
                latency_ms=m["latency_ms"],
                grounding=st.get("grounding"),
                answer=st.get("answer"),
                chart=st.get("chart"),
                detail=_detail(out),
            )
        )
        if out.trace["spans"]:
            conn.execute(run_spans.insert(), [{"run_id": h.run_id, "span": sp} for sp in out.trace["spans"]])
        for r in results:
            conn.execute(
                run_results.insert().values(
                    run_id=h.run_id,
                    data_ref=r["data_ref"],
                    columns=r["columns"],
                    rows=r["rows"][:1000],
                    total_rows=r["row_count"],
                )
            )

    try:
        await run_db(fn)
    except Exception:  # noqa: BLE001
        log.exception("persisting run %s failed", h.run_id)


async def fail_run(run_id: str, error: str) -> None:
    def fn(conn):  # type: ignore[no-untyped-def]
        conn.execute(
            runs.update().where(runs.c.id == run_id).values(status="error", answer=None, detail={"error": error[:500]})
        )

    try:
        await run_db(fn)
    except Exception:  # noqa: BLE001
        log.exception("marking run %s failed", run_id)


async def get_run(run_id: str) -> dict | None:
    def fn(conn):  # type: ignore[no-untyped-def]
        r = conn.execute(select(runs).where(runs.c.id == run_id)).first()
        if not r:
            return None
        spans = (
            conn.execute(select(run_spans.c.span).where(run_spans.c.run_id == run_id).order_by(run_spans.c.id))
            .scalars()
            .all()
        )
        fb = conn.execute(select(feedback).where(feedback.c.run_id == run_id)).first()
        return {**dict(r._mapping), "spans": list(spans), "feedback": dict(fb._mapping) if fb else None}

    return await run_db(fn)


async def get_result(run_id: str, data_ref: str) -> dict | None:
    def fn(conn):  # type: ignore[no-untyped-def]
        r = conn.execute(
            select(run_results).where((run_results.c.run_id == run_id) & (run_results.c.data_ref == data_ref))
        ).first()
        return {"columns": r.columns, "rows": r.rows, "total_rows": r.total_rows} if r else None

    return await run_db(fn)


async def save_feedback(run_id: str, thumbs: int, comment: str | None) -> None:
    def fn(conn):  # type: ignore[no-untyped-def]
        conn.execute(delete(feedback).where(feedback.c.run_id == run_id))
        conn.execute(feedback.insert().values(run_id=run_id, thumbs=thumbs, comment=comment))

    await run_db(fn)


async def audit(actor: str, action: str, target: str, details: dict | None = None) -> None:
    try:
        await run_db(
            lambda c: c.execute(
                audit_log.insert().values(actor=actor, action=action, target=target, details=details or {})
            )
        )
    except Exception:  # noqa: BLE001
        log.warning("audit write failed")


async def apply_retention() -> int:
    cutoff = _now() - timedelta(days=get_settings().retention_days)

    def fn(conn):  # type: ignore[no-untyped-def]
        old = select(runs.c.id).where(runs.c.created_at < cutoff)
        for t in (run_spans, run_results, feedback):
            conn.execute(delete(t).where(t.c.run_id.in_(old)))
        return conn.execute(delete(runs).where(runs.c.created_at < cutoff)).rowcount

    try:
        return int(await run_db(fn) or 0)
    except Exception:  # noqa: BLE001
        log.warning("retention failed")
        return 0
