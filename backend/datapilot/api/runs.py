"""Run manager: each question runs as a background task that writes events to a replayable log.

- `POST /threads/{id}/ask` starts a run and streams its events (SSE) from the beginning.
- `GET /runs/{id}/events?after=N` re-attaches (Vercel functions have a max duration; the browser reconnects).
- Interrupts (clarify, confirm, sandbox) emit a `*_request` event and wait on a future that the
  `/clarify`, `/confirm` and `/sandbox_result` routes resolve. The sandbox waits 60 s, humans 15 min.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from datapilot import agentforge, persist
from datapilot.config import get_settings
from datapilot.llm import quota
from datapilot.profile import active_profile
from datapilot.runner import RunOutcome, run_question
from datapilot.tracing import RunCtx

log = logging.getLogger(__name__)
HEARTBEAT_S = 15.0
KEEP_FINISHED_S = 600.0
EVENT_NAMES = {
    "clarify": "clarify_request",
    "confirm": "confirm_request",
    "sandbox": "sandbox_request",
}


@dataclass
class RunHandle:
    run_id: str
    thread_id: str
    owner: str
    db_id: str
    question: str
    ctx: RunCtx
    events: list[tuple[int, str, dict]] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    pending: dict | None = None
    waiter: asyncio.Future | None = None
    done: bool = False
    finished_at: float = 0.0
    task: asyncio.Task | None = None
    outcome: RunOutcome | None = None
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)

    def emit(self, event: str, data: dict) -> None:
        """Thread-safe: sync routing functions run in LangGraph's executor threads."""
        self.loop.call_soon_threadsafe(self._append, event, data)

    def _append(self, event: str, data: dict) -> None:
        self.events.append((len(self.events) + 1, event, data))
        self.loop.create_task(self._notify())

    async def _notify(self) -> None:
        async with self.cond:
            self.cond.notify_all()


_runs: dict[str, RunHandle] = {}


def get_handle(run_id: str) -> RunHandle | None:
    _gc()
    return _runs.get(run_id)


def _gc() -> None:
    now = time.monotonic()
    for rid in [r for r, h in _runs.items() if h.done and now - h.finished_at > KEEP_FINISHED_S]:
        _runs.pop(rid, None)


def active_count() -> int:
    return sum(1 for h in _runs.values() if not h.done)


async def start_run(*, thread_id: str, owner: str, db_id: str, question: str, history: list[dict]) -> RunHandle:
    run_id = str(uuid.uuid4())
    profile = await active_profile()
    ctx = RunCtx(run_id=run_id, db_id=db_id, user_id=owner, profile=profile)
    h = RunHandle(run_id, thread_id, owner, db_id, question, ctx)
    ctx.emit = h.emit
    _runs[run_id] = h
    await persist.create_run(run_id, thread_id, owner, db_id, question, profile.version_label)
    h.emit("run", {"run_id": run_id, "thread_id": thread_id, "db_id": db_id, "question": question,
                   "fake_llm": get_settings().fake_llm, "profile_version": profile.version_label})
    h.task = asyncio.create_task(_drive(h, history))
    return h


async def _drive(h: RunHandle, history: list[dict]) -> None:
    s = get_settings()

    async def on_interrupt(payload: dict) -> Any:
        kind = payload.get("type", "")
        loop = asyncio.get_running_loop()
        h.pending = payload
        h.waiter = loop.create_future()
        data = {k: v for k, v in payload.items() if k != "type"}
        if kind == "sandbox":
            data["data_url"] = f"/v1/runs/{h.run_id}/data/{payload['data_ref']}"
            data["timeout_ms"] = 10_000
        h.emit(EVENT_NAMES.get(kind, kind), data)
        timeout = s.sandbox_wait_s if kind == "sandbox" else s.human_wait_s
        try:
            return await asyncio.wait_for(h.waiter, timeout)
        except TimeoutError:
            if kind == "sandbox":
                h.emit("step", {"node": "sandbox_call", "status": "error", "label": "The browser sandbox didn't answer in time — continuing without the analysis"})
                return {"ok": False, "error": "The browser sandbox did not return a result within 60 s.", "ran_in": "browser"}
            return None if kind == "clarify" else "cancel"
        finally:
            h.pending, h.waiter = None, None

    try:
        out = await run_question(h.ctx, h.question, on_interrupt=on_interrupt, history=history, thread_id=h.thread_id)
        h.outcome = out
        st = out.state
        await persist.finish_run(h, out)
        if st.get("status") == "blocked":
            h.emit("error", {"code": "blocked", "message": st.get("answer") or "Blocked by the input guard."})
        elif out.status == "quota_exhausted":
            h.emit("error", {"code": "quota_exhausted", "message": "Free AI quota for today is used up; you can still browse benchmarks and past answers."})
        elif out.status == "budget_exceeded":
            h.emit("error", {"code": "budget_exceeded", "message": "This question reached its budget; the answer covers what was verified so far.", "fatal": False})
        h.emit("done", {"run_id": h.run_id, "status": out.status, "answer": st.get("answer"), "confidence": out.end_state["confidence"],
                        "metrics": out.trace["metrics"], "notes": st.get("notes") or [], "grounding": st.get("grounding")})
        agentforge.submit_trace(out.trace)
    except Exception as e:  # noqa: BLE001 — the stream must always end with a terminal event
        log.exception("run %s failed", h.run_id)
        h.emit("error", {"code": "internal", "message": "Something went wrong while answering. Please try again."})
        h.emit("done", {"run_id": h.run_id, "status": "error", "error": str(e)[:200]})
        await persist.fail_run(h.run_id, str(e))
    finally:
        h.done = True
        h.finished_at = time.monotonic()
        await h._notify()


def resolve(h: RunHandle, kind: str, value: Any) -> bool:
    if not h.pending or h.pending.get("type") != kind or h.waiter is None or h.waiter.done():
        return False
    h.waiter.set_result(value)
    return True


def _sse(seq: int, event: str, data: dict) -> str:
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data, default=str, separators=(',', ':'))}\n\n"


async def stream(h: RunHandle, after: int = 0) -> AsyncIterator[str]:
    """SSE generator: replay events after `after`, then follow live; heartbeat comment every 15 s."""
    sent = after
    yield "retry: 3000\n\n"
    while True:
        while sent < len(h.events):
            seq, event, data = h.events[sent]
            sent += 1
            yield _sse(seq, event, data)
        if h.done:
            return
        async with h.cond:
            try:
                await asyncio.wait_for(h.cond.wait_for(lambda: len(h.events) > sent or h.done), HEARTBEAT_S)
            except TimeoutError:
                yield ": heartbeat\n\n"


def llm_status() -> dict:
    s = get_settings()
    return {
        "fake_llm": s.fake_llm,
        "gemini": s.gemini_ready,
        "groq": s.groq_ready,
        "available": s.llm_available and not quota.all_exhausted(),
        "quota": quota.snapshot(),
    }
