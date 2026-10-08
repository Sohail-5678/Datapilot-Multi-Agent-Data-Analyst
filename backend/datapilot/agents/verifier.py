"""Fan-out, voting, verification and the expensive-query checkpoint (SPEC §6.4 adaptive k, §7.3, §4.3).

- Adaptive k: start with 2 candidates; if their result hashes match we're done, otherwise run the 3rd and vote.
- Pick the largest agreeing group; ties → the verifier LLM picks. The verifier also checks the chosen SQL.
- Confidence: High (≥ 2 agree + verifier pass), Medium (1 candidate + verifier pass), Low otherwise.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt
from pydantic import BaseModel, Field

from datapilot.agents.sql_agent import _execute, _fill, _guard, preview_for_verifier
from datapilot.agents.state import DPState, ctx_of, current_step, database_for, history_text, tag
from datapilot.config import get_settings
from datapilot.index import cache as sql_cache
from datapilot.llm.router import LLMUnavailable, complete_model
from datapilot.tracing import BudgetExceeded

_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:  # type: ignore[no-untyped-def]
    """Fire-and-forget with a strong reference (asyncio only keeps weak ones)."""
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


class VerifyOut(BaseModel):
    chosen: str = ""
    passes: bool = True
    issues: list[str] = Field(default_factory=list)
    reason: str = ""


def strategies_for(state: DPState, config: RunnableConfig) -> tuple[list[str], int, bool]:
    ctx = ctx_of(config)
    prof = ctx.profile
    s = get_settings()
    strategies = list(prof.p("candidate_strategies", ["direct", "plan_then_sql", "few_shot"]))
    k = min(int(ctx.flags.get("k", prof.p("self_consistency_k", 3))), s.max_k, len(strategies))
    adaptive = bool(ctx.flags.get("adaptive", prof.p("adaptive_k", True)))
    if (state.get("linked") or {}).get("cached_sql"):
        strategies = ["cache", *strategies]
        k = min(k + 1, len(strategies)) if not adaptive else k
    return strategies, max(1, k), adaptive


def _payload(state: DPState, strategy: str, confirmed: bool = False) -> dict:
    step = current_step(state)
    prev = [{"goal": r["goal"], "sql": r.get("sql")} for r in state.get("step_results") or []]
    hist = state.get("history") or []
    return {
        "cand_id": f"c{state.get('step_idx', 0) + 1}-{strategy}",
        "strategy": strategy,
        "question": state["question"],
        "goal": step.get("goal") or state["question"],
        "evidence": state.get("evidence"),
        "clarification": state.get("clarification"),
        "linked": state["linked"],
        "previous": prev,
        "history_sql": hist[-1].get("sql") if hist else None,
        "confirmed": confirmed,
    }


def fan_out(state: DPState, config: RunnableConfig) -> list[Send] | str:
    if state.get("stop"):
        return "narrator" if state.get("step_results") else "respond"
    strategies, k, adaptive = strategies_for(state, config)
    first = strategies[: min(2, k)] if adaptive else strategies[:k]
    ctx = ctx_of(config)
    ctx.send("step", {"node": "sql_agent", "status": "running", "label": f"Running {len(first)} candidate queries…"})
    return [Send("sql_candidate", _payload(state, s)) for s in first]


async def vote(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    cands = state.get("candidates") or []
    ok = [c for c in cands if c["status"] == "ok"]
    groups: dict[str, list[str]] = defaultdict(list)
    for c in ok:
        groups[c["result_hash"]].append(c["id"])
    strategies, k, adaptive = strategies_for(state, config)
    used = {c["strategy"] for c in cands}
    unused = [s for s in strategies if s not in used]
    decision = "verify"
    more: list[str] = []
    if any(c["status"] == "needs_confirm" for c in cands) and not state.get("confirmed"):
        decision = "confirm"
    elif adaptive and len(used) < k and unused and (len(groups) != 1 or len(ok) < 2):
        decision, more = "more", unused[: k - len(used)]
    if any(c.get("quota") for c in cands) and not ok:
        return {"stop": "quota_exhausted", "vote": {"decision": "stop"}}
    with ctx.span("node", "vote") as sp:
        ctx.set_io(sp, None, {"groups": {h: ids for h, ids in groups.items()}, "decision": decision, "more": more})
    if decision == "more":
        ctx.send(
            "step",
            {
                "node": "verifier",
                "status": "running",
                "label": "Candidates disagree — running a third strategy to break the tie…",
            },
        )
    return {"vote": {"decision": decision, "more": more, "groups": dict(groups)}}


def route_vote(state: DPState, config: RunnableConfig) -> list[Send] | str:
    if state.get("stop"):
        return "narrator" if state.get("step_results") else "respond"
    v = state.get("vote") or {}
    if v.get("decision") == "more":
        return [Send("sql_candidate", _payload(state, s)) for s in v["more"]]
    if v.get("decision") == "confirm":
        return "confirm"
    return "verify"


async def confirm(state: DPState, config: RunnableConfig) -> dict:
    """Human checkpoint: 'This will scan about N rows. Run it?' → run / narrow / cancel."""
    cands = [c for c in state.get("candidates") or [] if c["status"] == "needs_confirm"]
    show = max(cands, key=lambda c: (c.get("cost") or {}).get("scanned_rows", 0))
    cost = show.get("cost") or {}
    action = interrupt(
        {
            "type": "confirm",
            "sql": show["sql"],
            "scanned_rows": cost.get("scanned_rows"),
            "table": cost.get("table"),
            "seconds": cost.get("seconds"),
            "unindexed_join": cost.get("unindexed_join"),
        }
    )
    ctx = ctx_of(config)
    db = database_for(ctx)
    with ctx.span("human", "confirm") as sp:
        ctx.set_io(sp, {"scanned_rows": cost.get("scanned_rows")}, {"action": action})
    if action == "cancel":
        return {"stop": "cancelled", "confirmed": False}
    if action != "run":
        return {
            "stop": "needs_human",
            "confirmed": False,
            "notes": [
                *(state.get("notes") or []),
                "Add a filter (a season, team or year) and ask again to narrow it down.",
            ],
        }
    ctx.send("step", {"node": "executor", "status": "running", "label": "Running the confirmed query…"})

    async def run_one(c: dict) -> dict:
        c = dict(c)
        g = _guard(db, c["sql"])
        if not g.ok:
            c.update(status="rejected", error=g.reason)
            return c
        res = await _execute(ctx, db, g)
        if res.ok:
            _fill(c, res, g.sql)
        else:
            c.update(status="error", error=res.error)
        ctx.send(
            "candidate", {"id": c["id"], "strategy": c["strategy"], "status": c["status"], "row_count": c["row_count"]}
        )
        return c

    updated = await asyncio.gather(*(run_one(c) for c in cands))
    return {"candidates": list(updated), "confirmed": True}


async def verify(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    step = current_step(state)
    cands = state.get("candidates") or []
    ok = [c for c in cands if c["status"] == "ok"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in ok:
        groups[c["result_hash"]].append(c)
    ranked = sorted(groups.values(), key=lambda g: (-len(g), -min(1, g[0]["row_count"] or 0)))
    reasons: list[str] = []
    stop = None
    with ctx.span("node", "verifier") as sp:
        if not ranked:
            chosen = None
            reasons.append("No candidate query ran successfully.")
        else:
            top = ranked[0]
            tie = len(ranked) > 1 and len(ranked[1]) == len(top)
            reps = [g[0] for g in ranked] if tie else [top[0]]
            passes, issues = True, []
            pick = top[0]
            if ctx.flags.get("verifier", True):
                try:
                    out = await _llm_verify(ctx, db, state, step, reps)
                    pick = next((c for c in reps if c["id"] == out.chosen), reps[0])
                    passes, issues = out.passes, out.issues
                    if out.reason:
                        reasons.append(out.reason[:200])
                except BudgetExceeded:
                    stop = "budget_exceeded"
                    passes = False
                    issues = ["verifier skipped: budget reached"]
                except (LLMUnavailable, ValueError) as e:
                    passes = False
                    issues = [f"verifier unavailable ({str(e)[:80]})"]
            agree = len(groups[pick["result_hash"]])
            n = len([c for c in cands if c["status"] in ("ok", "error", "rejected")])
            confidence = "High" if agree >= 2 and passes else "Medium" if passes else "Low"
            reasons.insert(0, f"{agree} of {n} candidate{'s' if n != 1 else ''} agree")
            reasons.append("verifier passed" if passes else "verifier raised concerns: " + "; ".join(issues)[:200])
            if pick["repairs"]:
                reasons.append(f"{pick['repairs']} repair{'s' if pick['repairs'] > 1 else ''} used")
            chosen = {**pick, "confidence": confidence, "reasons": reasons, "agree": agree, "of": n, "issues": issues}
        ctx.set_io(
            sp,
            {"groups": len(ranked)},
            {"chosen": chosen and chosen["id"], "confidence": chosen and chosen["confidence"]},
        )

    results = list(state.get("step_results") or [])
    data_ref = f"r{state.get('step_idx', 0) + 1}-{uuid.uuid4().hex[:6]}"
    step_result = {
        "step_idx": state.get("step_idx", 0),
        "goal": step.get("goal") or state["question"],
        "sql": chosen["sql"] if chosen else None,
        "unlimited_sql": chosen.get("unlimited_sql") if chosen else None,
        "columns": chosen["columns"] if chosen else [],
        "rows": chosen["rows"] if chosen else [],
        "row_count": chosen["row_count"] if chosen else 0,
        "truncated": chosen["truncated"] if chosen else False,
        "confidence": chosen["confidence"] if chosen else "Low",
        "reasons": chosen["reasons"] if chosen else reasons,
        "data_ref": data_ref,
        "needs_chart": step.get("needs_chart", False),
        "candidates": [
            {
                k: c.get(k)
                for k in ("id", "strategy", "model", "sql", "status", "row_count", "result_hash", "repairs", "error")
            }
            for c in cands
        ],
        "ok": chosen is not None,
    }
    results.append(step_result)
    ctx.data[data_ref] = {
        "columns": step_result["columns"],
        "rows": step_result["rows"],
        "total_rows": step_result["row_count"],
    }
    if chosen:
        ctx.send(
            "chosen",
            {
                "step_idx": step_result["step_idx"],
                "sql": chosen["sql"],
                "confidence": chosen["confidence"],
                "reasons": chosen["reasons"],
                "agree": chosen["agree"],
                "of": chosen["of"],
            },
        )
        ctx.send(
            "table",
            {
                "data_ref": data_ref,
                "columns": chosen["columns"],
                "rows": chosen["rows"],
                "row_count": chosen["row_count"],
                "truncated": chosen["truncated"],
                "step_idx": step_result["step_idx"],
            },
        )
        if chosen["confidence"] == "High" and ctx.mode == "live":
            _spawn(
                sql_cache.store(
                    db.db_id,
                    state["question"] if len(state.get("plan") or []) == 1 else step_result["goal"],
                    chosen["sql"],
                    chosen["result_hash"],
                    "high_confidence",
                    ctx.profile.version_label,
                )
            )
    update: dict = {"chosen": {k: v for k, v in (chosen or {}).items() if k != "rows"} or None, "step_results": results}
    if stop:
        update["stop"] = stop
    return update


async def _llm_verify(ctx, db, state: DPState, step: dict, reps: list[dict]) -> VerifyOut:  # type: ignore[no-untyped-def]
    blocks = []
    for c in reps:
        blocks.append(
            f"Candidate {c['id']} ({c['row_count']} rows):\n{c['sql']}\nResult preview:\n{preview_for_verifier(db, c)}"
        )
    prompt = "\n\n".join(
        x
        for x in (
            tag("schema", state["linked"]["schema"]),
            tag("evidence", state["evidence"]) if state.get("evidence") else "",
            history_text(state),
            tag(
                "question",
                (
                    state["question"]
                    + ("\nThis step: " + step["goal"] if step.get("goal") and step["goal"] != state["question"] else "")
                    + (f"\nThe user clarified: {state['clarification']}" if state.get("clarification") else "")
                ),
            ),
            tag("candidates", "\n\n".join(blocks)),
            f"Strictness: {ctx.profile.p('verifier_strictness', 'normal')}.",
        )
        if x
    )
    out = await complete_model(
        ctx, "verifier", ctx.profile.prompts["verifier"], prompt, VerifyOut, kinds=["main", "fast"], max_tokens=400
    )
    return out
