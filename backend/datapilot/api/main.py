"""FastAPI app (SPEC §12): databases, threads, ask (SSE), human checkpoints, sandbox intake, user SQL, runs."""

from __future__ import annotations

import asyncio
import json
import logging
import resource
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from datapilot import agentforge, persist
from datapilot.api import ratelimit
from datapilot.api.auth import Principal, current_principal
from datapilot.api.errors import ApiError, api_error_handler
from datapilot.api.runs import get_handle, llm_status, resolve, start_run, stream
from datapilot.config import BACKEND_ROOT, get_settings
from datapilot.db.session import init_db, is_sqlite
from datapilot.guards.pii import mask_value
from datapilot.index import cache as sql_cache
from datapilot.index.catalog import get_database, load_catalog
from datapilot.index.schema_index import build_index, ensure_embeddings, get_index
from datapilot.index.value_index import build_value_index, index_stats
from datapilot.sql.executor import execute_readonly
from datapilot.sql.explain import estimate_cost
from datapilot.sql.guard import guard_sql

log = logging.getLogger("datapilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STARTED = time.time()
_ready = {"indexes": False}


async def _warm() -> None:
    t0 = time.perf_counter()
    for db in load_catalog().values():
        await asyncio.to_thread(build_value_index, db)
        build_index(db)
    _ready["indexes"] = True
    log.info("indexes ready in %.1f s: %s", time.perf_counter() - t0, index_stats())
    await sql_cache.warm()
    removed = await persist.apply_retention()
    if removed:
        log.info("retention removed %d old runs", removed)
    for db in load_catalog().values():  # embeddings last: optional and network-bound
        await ensure_embeddings(db)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    await asyncio.to_thread(init_db)
    await asyncio.to_thread(load_catalog)
    task = asyncio.create_task(_warm())
    yield
    task.cancel()


app = FastAPI(title="DataPilot API", version="1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_exception_handler(ApiError, api_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
)


@app.middleware("http")
async def guard_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path.startswith("/v1/"):
        ip = (
            (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?"))
            .split(",")[0]
            .strip()
        )
        try:
            ratelimit.check_ip(ip)
        except ApiError as e:
            return await api_error_handler(request, e)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store")
    return resp


# ---------------------------------------------------------------- health & status


@app.get("/healthz")
async def healthz() -> dict:
    cat = load_catalog()
    return {
        "status": "ok",
        "db": "sqlite" if is_sqlite() else "postgres",
        "dbs_loaded": len(cat),
        "indexes_ready": _ready["indexes"],
        "value_index_mb": index_stats().get("_total_mb", 0),
        "rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (1e6 if __import__("sys").platform == "darwin" else 1e3),
            1,
        ),
        "uptime_s": int(time.time() - STARTED),
        "version": get_settings().git_sha[:12],
    }


@app.get("/v1/status")
async def status(_p: Principal = Depends(current_principal)) -> dict:
    return {"llm": llm_status(), "indexes_ready": _ready["indexes"], "agentforge": agentforge.enabled()}


# ---------------------------------------------------------------- databases


def _db_or_404(db_id: str):  # type: ignore[no-untyped-def]
    db = get_database(db_id)
    if db is None:
        raise ApiError(404, "not_found", f"Unknown database '{db_id}'.")
    return db


@app.get("/v1/databases")
async def databases(_p: Principal = Depends(current_principal)) -> dict:
    return {
        "databases": [
            {
                "db_id": db.db_id,
                "title": db.title,
                "subtitle": db.subtitle,
                "description": db.description,
                "tables": len(db.tables),
                "columns": sum(len(t.columns) for t in db.tables.values()),
                "rows": db.total_rows,
                "source": db.source,
                "source_url": db.source_url,
                "license": db.license,
                "examples": db.examples,
            }
            for db in load_catalog().values()
        ]
    }


def _erd(db) -> str:  # type: ignore[no-untyped-def]
    lines = ["erDiagram"]
    g = get_index(db).graph
    for a, b, d in g.edges(data=True):
        lines.append(f'  {a} ||--o{{ {b} : "{"inferred" if d.get("inferred") else "fk"}"')
    return "\n".join(lines)


@app.get("/v1/databases/{db_id}/schema")
async def schema(db_id: str, _p: Principal = Depends(current_principal)) -> dict:
    db = _db_or_404(db_id)
    tables = []
    for t in db.tables.values():
        tables.append(
            {
                "name": t.name,
                "rows": t.row_count,
                "description": t.description,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "pk": c.pk,
                        "fk": next(
                            (
                                {"table": f.ref_table, "column": f.ref_column}
                                for f in t.foreign_keys
                                if f.column == c.name
                            ),
                            None,
                        ),
                        "description": c.description,
                        "value_description": c.value_description,
                        "samples": [mask_value(x) for x in c.samples] if c.pii else c.samples,
                        "pii": c.pii,
                    }
                    for c in t.columns
                ],
            }
        )
    return {
        "db_id": db.db_id,
        "title": db.title,
        "description": db.description,
        "tables": tables,
        "erd": _erd(db),
        "examples": db.examples,
        "source": db.source,
        "license": db.license,
    }


# ---------------------------------------------------------------- threads & ask


class NewThread(BaseModel):
    db_id: str


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


async def _own_thread(thread_id: str, p: Principal):  # type: ignore[no-untyped-def]
    t = await persist.get_thread(thread_id)
    if t is None or (t.user_id != p.user_id and not p.is_admin):
        raise ApiError(404, "not_found", "Conversation not found.")
    return t


@app.post("/v1/threads")
async def new_thread(body: NewThread, p: Principal = Depends(current_principal)) -> dict:
    _db_or_404(body.db_id)
    return {"thread_id": await persist.create_thread(p.user_id, body.db_id)}


@app.get("/v1/threads")
async def my_threads(p: Principal = Depends(current_principal)) -> dict:
    return {"threads": await persist.list_threads(p.user_id)}


@app.get("/v1/threads/{thread_id}")
async def thread(thread_id: str, p: Principal = Depends(current_principal)) -> dict:
    t = await _own_thread(thread_id, p)
    return {"thread": dict(t._mapping), "runs": await persist.thread_runs(thread_id)}


def _sse_response(gen) -> StreamingResponse:  # type: ignore[no-untyped-def]
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/v1/threads/{thread_id}/ask")
async def ask(thread_id: str, body: Ask, p: Principal = Depends(current_principal)) -> StreamingResponse:
    t = await _own_thread(thread_id, p)
    s = get_settings()
    if not s.llm_available:
        raise ApiError(
            503,
            "quota_exhausted",
            "No AI provider is configured on the server yet; you can still browse databases and benchmarks.",
        )
    status = llm_status()
    if not status["available"]:
        raise ApiError(
            503,
            "quota_exhausted",
            "Free AI quota for today is used up; you can still browse benchmarks and past answers.",
        )
    ratelimit.check_question(p.user_id, p.role)
    history = await persist.thread_history(thread_id)
    h = await start_run(
        thread_id=thread_id, owner=p.user_id, db_id=t.db_id, question=body.question.strip(), history=history
    )
    return _sse_response(stream(h, 0))


async def _own_run(run_id: str, p: Principal):  # type: ignore[no-untyped-def]
    h = get_handle(run_id)
    if h is not None:
        if h.owner != p.user_id and not p.is_admin:
            raise ApiError(404, "not_found", "Run not found.")
        return h
    return None


@app.get("/v1/runs/{run_id}/events")
async def events(
    run_id: str, after: int = Query(default=0, ge=0), request: Request = None, p: Principal = Depends(current_principal)
) -> StreamingResponse:  # type: ignore[assignment]
    h = await _own_run(run_id, p)
    if h is None:
        raise ApiError(404, "not_found", "This run is no longer live; open it from the conversation instead.")
    last = request.headers.get("last-event-id") if request else None
    start = max(after, int(last)) if last and last.isdigit() else after
    return _sse_response(stream(h, start))


class Clarify(BaseModel):
    choice: str = Field(min_length=1, max_length=300)


class Confirm(BaseModel):
    action: Literal["run", "cancel", "narrow"]


@app.post("/v1/runs/{run_id}/clarify")
async def clarify(run_id: str, body: Clarify, p: Principal = Depends(current_principal)) -> dict:
    h = await _own_run(run_id, p)
    if h is None or not resolve(h, "clarify", body.choice):
        raise ApiError(409, "not_waiting", "This run isn't waiting for a clarification.")
    return {"ok": True}


@app.post("/v1/runs/{run_id}/confirm")
async def confirm(run_id: str, body: Confirm, p: Principal = Depends(current_principal)) -> dict:
    h = await _own_run(run_id, p)
    if h is None or not resolve(h, "confirm", body.action):
        raise ApiError(409, "not_waiting", "This run isn't waiting for a confirmation.")
    await persist.audit(p.user_id, "confirm_query", run_id, {"action": body.action})
    return {"ok": True}


@app.get("/v1/runs/{run_id}/data/{data_ref}")
async def run_data(run_id: str, data_ref: str, p: Principal = Depends(current_principal)) -> dict:
    h = await _own_run(run_id, p)
    if h is not None and data_ref in h.ctx.data:
        d = h.ctx.data[data_ref]
        return {"columns": d["columns"], "rows": d["rows"], "total_rows": d.get("total_rows", len(d["rows"]))}
    run = await persist.get_run(run_id)
    if run is None or (run["user_id"] != p.user_id and not p.is_admin):
        raise ApiError(404, "not_found", "Run not found.")
    d = await persist.get_result(run_id, data_ref)
    if d is None:
        raise ApiError(404, "not_found", "Result not found.")
    return d


class SandboxResult(BaseModel):
    request_id: str
    ok: bool
    result: object | None = None
    stdout: str | None = Field(default=None, max_length=12_000)
    error: str | None = Field(default=None, max_length=4000)
    duration_ms: int | None = None


@app.post("/v1/runs/{run_id}/sandbox_result")
async def sandbox_result(run_id: str, body: SandboxResult, p: Principal = Depends(current_principal)) -> dict:
    h = await _own_run(run_id, p)
    if h is None or not h.pending or h.pending.get("request_id") != body.request_id:
        raise ApiError(409, "not_waiting", "This run isn't waiting for that sandbox result.")
    if len(json.dumps(body.result, default=str)) > 50_000:
        body = SandboxResult(
            request_id=body.request_id, ok=False, error="Result larger than 50 KB.", duration_ms=body.duration_ms
        )
    resolve(h, "sandbox", {**body.model_dump(), "ran_in": "browser"})
    return {"ok": True}


class UserSql(BaseModel):
    db_id: str
    sql: str = Field(min_length=1, max_length=20_000)
    confirm: bool = False


@app.post("/v1/sql/execute")
async def user_sql(body: UserSql, p: Principal = Depends(current_principal)) -> dict:
    """User-edited SQL goes through the same guard and runs read-only (SPEC §2.4 'Edit SQL')."""
    db = _db_or_404(body.db_id)
    s = get_settings()
    g = guard_sql(body.sql, db.table_names, {n: t.row_count for n, t in db.tables.items()})
    if not g.ok:
        raise ApiError(422, "sql_rejected", g.reason)
    if not body.confirm:
        est = await asyncio.to_thread(estimate_cost, db.path, g.sql, {n: t.row_count for n, t in db.tables.items()})
        if est.scanned_rows > s.scan_confirm_rows:
            return {
                "needs_confirm": True,
                "scanned_rows": est.scanned_rows,
                "table": est.biggest_table,
                "seconds": est.seconds_hint(),
                "sql": g.sql,
            }
    res = await asyncio.to_thread(
        execute_readonly,
        db.path,
        g.sql,
        timeout_s=s.sql_timeout_s,
        row_cap=s.row_cap,
        count_sql=g.unlimited_sql or None,
    )
    await persist.audit(p.user_id, "user_sql", db.db_id, {"ok": res.ok, "rows": res.row_count})
    if not res.ok:
        raise ApiError(422, "sql_error", res.error or "Query failed.")
    return {
        "sql": g.sql,
        "columns": res.columns,
        "rows": res.rows,
        "row_count": res.row_count,
        "truncated": res.truncated,
        "duration_ms": res.duration_ms,
    }


class Feedback(BaseModel):
    thumbs: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=1000)


@app.post("/v1/runs/{run_id}/feedback")
async def run_feedback(run_id: str, body: Feedback, p: Principal = Depends(current_principal)) -> dict:
    run = await persist.get_run(run_id)
    if run is None or run["user_id"] != p.user_id:
        raise ApiError(404, "not_found", "Run not found.")
    await persist.save_feedback(run_id, body.thumbs, body.comment)
    if body.thumbs == 1 and run.get("chosen_sql") and run.get("status") == "success":
        await sql_cache.store(
            run["db_id"], run["question"], run["chosen_sql"], "", "thumbs_up", run.get("profile_version") or ""
        )
    agentforge.submit_feedback(run_id, body.thumbs, body.comment)
    return {"ok": True}


@app.get("/v1/runs/{run_id}")
async def run_detail(run_id: str, p: Principal = Depends(current_principal)) -> dict:
    run = await persist.get_run(run_id)
    if run is None or (run["user_id"] != p.user_id and not p.is_admin):
        raise ApiError(404, "not_found", "Run not found.")
    h = get_handle(run_id)
    return {**run, "live": bool(h and not h.done)}


@app.get("/v1/benchmarks")
async def benchmarks(_p: Principal = Depends(current_principal)) -> JSONResponse:
    path = Path(get_settings().dbs_dir).parent.parent / "bench_summary.json"
    for candidate in (path, BACKEND_ROOT.parent / "bench" / "summary.json"):
        if candidate.exists():
            return JSONResponse(json.loads(candidate.read_text()))
    return JSONResponse({"available": False, "message": "No benchmark run has been published yet."})
