"""The DataPilot agent team as a LangGraph StateGraph (SPEC §6.3).

input_guard → planner ⇄ clarify (interrupt) → schema_linker → fan-out (Send ×k) → sql_candidate → vote
  vote → more candidates (adaptive k) | confirm (interrupt) | verify
  verify → analyst → sandbox_call (interrupt) → analyst (fix ≤ 1) → advance
  verify → advance → schema_linker (next plan step) | analyst (analysis-only step) | chart
chart → narrator → output_guard → respond → END
Any stop (budget, quota, cancel) jumps to the narrator with what is verified so far.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from datapilot.agents.analyst import analyst, route_analysis, sandbox_call
from datapilot.agents.chart import chart
from datapilot.agents.narrator import narrator, output_guard
from datapilot.agents.planner import clarify, input_guard, planner
from datapilot.agents.schema_linker import schema_linker
from datapilot.agents.sql_agent import sql_candidate
from datapilot.agents.state import RESET, DPState, ctx_of
from datapilot.agents.verifier import confirm, fan_out, route_vote, verify, vote


def _stopped(state: DPState) -> str | None:
    if state.get("stop"):
        return "narrator" if state.get("step_results") else "respond"
    return None


def route_guard(state: DPState) -> str:
    return "respond" if state.get("status") == "blocked" else "planner"


def route_planner(state: DPState) -> str:
    if s := _stopped(state):
        return s
    if not state.get("plan") and (state.get("plan_meta") or {}).get("options"):
        return "clarify"
    return "schema_linker"


def route_clarify(state: DPState) -> str:
    return _stopped(state) or "planner"


def route_confirm(state: DPState) -> str:
    return _stopped(state) or "vote"


def route_verify(state: DPState) -> str:
    if s := _stopped(state):
        return s
    step = (state.get("plan") or [{}])[state.get("step_idx", 0)] if state.get("plan") else {}
    last = (state.get("step_results") or [{}])[-1]
    if step.get("needs_analysis") and last.get("ok"):
        return "analyst"
    return "advance"


async def advance(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    idx = state.get("step_idx", 0) + 1
    ctx.send("step", {"node": "advance", "status": "done", "label": f"Step {idx} of {len(state.get('plan') or [])} done", "step_idx": idx - 1})
    return {"step_idx": idx, "candidates": RESET, "analysis_attempts": 0, "confirmed": False}


def route_advance(state: DPState) -> str:
    if s := _stopped(state):
        return s
    plan = state.get("plan") or []
    idx = state.get("step_idx", 0)
    if idx < len(plan):
        step = plan[idx]
        if step.get("needs_sql", True):
            return "schema_linker"
        if step.get("needs_analysis"):
            return "analyst"
        return "advance"
    return "chart"


async def respond(state: DPState, config: RunnableConfig) -> dict:
    stop = state.get("stop")
    if state.get("status") == "blocked":
        status = "blocked"
    elif stop in ("budget_exceeded", "quota_exhausted", "cancelled", "needs_human"):
        status = stop
    elif not any(r.get("ok") for r in state.get("step_results") or []):
        status = "failure"
    else:
        status = "success"
    update: dict = {"status": status}
    if not state.get("answer"):
        from datapilot.agents.narrator import STOP_MESSAGES

        update["answer"] = STOP_MESSAGES.get(stop or "", state.get("answer") or "No answer was produced.")
    return update


def build_graph() -> StateGraph:
    g = StateGraph(DPState)
    g.add_node("input_guard", input_guard)
    g.add_node("planner", planner)
    g.add_node("clarify", clarify)
    g.add_node("schema_linker", schema_linker)
    g.add_node("sql_candidate", sql_candidate)
    g.add_node("vote", vote)
    g.add_node("confirm", confirm)
    g.add_node("verify", verify)
    g.add_node("analyst", analyst)
    g.add_node("sandbox_call", sandbox_call)
    g.add_node("advance", advance)
    g.add_node("chart", chart)
    g.add_node("narrator", narrator)
    g.add_node("output_guard", output_guard)
    g.add_node("respond", respond)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", route_guard, ["planner", "respond"])
    g.add_conditional_edges("planner", route_planner, ["clarify", "schema_linker", "narrator", "respond"])
    g.add_conditional_edges("clarify", route_clarify, ["planner", "narrator", "respond"])
    g.add_conditional_edges("schema_linker", fan_out, ["sql_candidate", "narrator", "respond"])
    g.add_edge("sql_candidate", "vote")
    g.add_conditional_edges("vote", route_vote, ["sql_candidate", "confirm", "verify", "narrator", "respond"])
    g.add_conditional_edges("confirm", route_confirm, ["vote", "narrator", "respond"])
    g.add_conditional_edges("verify", route_verify, ["analyst", "advance", "narrator", "respond"])
    g.add_conditional_edges("analyst", route_analysis, ["sandbox_call", "analyst", "advance", "narrator", "respond"])
    g.add_conditional_edges("sandbox_call", route_analysis, ["analyst", "advance", "narrator", "respond"])
    g.add_conditional_edges("advance", route_advance, ["schema_linker", "analyst", "advance", "chart", "narrator", "respond"])
    g.add_edge("chart", "narrator")
    g.add_edge("narrator", "output_guard")
    g.add_edge("output_guard", "respond")
    g.add_edge("respond", END)
    return g


_saver = InMemorySaver()


@lru_cache
def compiled_graph():  # type: ignore[no-untyped-def]
    """One compiled graph per process. The in-memory checkpointer holds paused runs (clarify, confirm,
    sandbox); Render runs a single instance, and paused runs expire after HUMAN_WAIT_S."""
    return build_graph().compile(checkpointer=_saver)


def forget_run(run_id: str) -> None:
    try:
        _saver.delete_thread(run_id)
    except Exception:  # noqa: BLE001
        pass
