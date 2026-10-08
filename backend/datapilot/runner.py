"""Run driver shared by the API, the eval adapter and the benchmark harness.

Runs the graph until it finishes or pauses on an `interrupt()` (clarify, confirm, sandbox), asks the caller's
`on_interrupt` handler for the resume value — a human in the UI, the browser sandbox, or an eval policy — and
resumes with `Command(resume=…)`. Time spent waiting on humans/the browser doesn't count toward the 90 s budget.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from datapilot.agents.graph import compiled_graph, forget_run
from datapilot.tracing import RunCtx

InterruptHandler = Callable[[dict], Awaitable[Any]]


@dataclass
class RunOutcome:
    state: dict
    status: str
    trace: dict
    end_state: dict
    final_output: dict
    clarified: bool
    confirmed: bool
    sandbox_used: bool


async def run_question(
    ctx: RunCtx,
    question: str,
    *,
    on_interrupt: InterruptHandler,
    evidence: str | None = None,
    history: list[dict] | None = None,
    thread_id: str | None = None,
) -> RunOutcome:
    graph = compiled_graph()
    config = {"configurable": {"thread_id": ctx.run_id, "ctx": ctx}, "recursion_limit": 80}
    inp: Any = {
        "run_id": ctx.run_id,
        "thread_id": thread_id or ctx.run_id,
        "user_id": ctx.user_id,
        "db_id": ctx.db_id,
        "question": question,
        "evidence": evidence,
        "history": history or [],
        "step_results": [],
        "notes": [],
        "status": "running",
    }
    clarified = confirmed = sandbox_used = False
    try:
        while True:
            await graph.ainvoke(inp, config)
            snap = await graph.aget_state(config)
            pending = [i for task in snap.tasks for i in (task.interrupts or ())]
            if not pending:
                state = dict(snap.values)
                break
            payload = dict(pending[0].value)
            kind = payload.get("type")
            clarified |= kind == "clarify"
            confirmed |= kind == "confirm"
            sandbox_used |= kind == "sandbox"
            t0 = time.perf_counter()
            try:
                resume = await on_interrupt(payload)
            finally:
                ctx.budget.waited_s += time.perf_counter() - t0
            # LangGraph can't resume with None (it crashes); "" means "no answer" to every checkpoint node.
            inp = Command(resume="" if resume is None else resume)
    finally:
        forget_run(ctx.run_id)
    return finalize(ctx, question, state, clarified, confirmed, sandbox_used)


def finalize(
    ctx: RunCtx, question: str, state: dict, clarified: bool, confirmed: bool, sandbox_used: bool
) -> RunOutcome:
    status = state.get("status") or "error"
    results = state.get("step_results") or []
    last_ok = next((r for r in reversed(results) if r.get("ok")), None)
    grounding = state.get("grounding") or {}
    end_state = {
        "chosen_sql": last_ok["sql"] if last_ok else None,
        "result_hash": (state.get("chosen") or {}).get("result_hash"),
        "row_count": last_ok["row_count"] if last_ok else 0,
        "confidence": last_ok["confidence"] if last_ok else None,
        "grounding_removed": len(grounding.get("removed") or []),
        "sandbox_used": sandbox_used,
        "clarified": clarified,
        "confirmed": confirmed,
    }
    final_output = {
        "answer": state.get("answer"),
        "sql": last_ok["sql"] if last_ok else None,
        "chart": (state.get("chart") or {}).get("spec"),
        "plan": [s.get("goal") for s in state.get("plan") or []],
    }
    trace_status = {
        "success": "success",
        "failure": "failure",
        "blocked": "blocked",
        "budget_exceeded": "budget_exceeded",
        "needs_human": "needs_human",
        "cancelled": "needs_human",
        "quota_exhausted": "error",
    }.get(status, "error")
    trace = ctx.trace(status=trace_status, question=question, final_output=final_output, end_state=end_state)
    return RunOutcome(state, status, trace, end_state, final_output, clarified, confirmed, sandbox_used)
