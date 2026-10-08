"""Narrator + output grounding guard (SPEC §6.1, §8.4).

The narrator drafts ≤ 120 words from the results only. The guard checks every number; ungrounded → one
rewrite with the list of allowed numbers → still ungrounded → that sentence is removed and the UI is told.
Only the checked text is streamed to the user (as `token` events), so ungrounded numbers never ship.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableConfig

from datapilot.agents.state import DPState, ctx_of, database_for, rows_for_prompt, tag
from datapilot.guards.output_grounding import Allowed, allowed_values, check_grounding, extract_numbers, strip_ungrounded
from datapilot.llm.router import LLMUnavailable, complete
from datapilot.tracing import BudgetExceeded

STOP_MESSAGES = {
    "budget_exceeded": "This question hit the per-question budget, so the answer covers only what was verified so far.",
    "quota_exhausted": "Free AI quota for today is used up; you can still browse benchmarks and past answers.",
    "cancelled": "Cancelled — the query was not run.",
    "needs_human": "Waiting for more detail before running this.",
}


def _results_block(db, state: DPState) -> str:  # type: ignore[no-untyped-def]
    parts = []
    for r in state.get("step_results") or []:
        if not r.get("ok"):
            parts.append(f"Step {r['step_idx'] + 1} ({r['goal']}): no result — {'; '.join(r.get('reasons') or [])}")
            continue
        note = f" (showing 30 of {r['row_count']})" if r["row_count"] > 30 else ""
        parts.append(f"Step {r['step_idx'] + 1}: {r['goal']} — {r['row_count']} rows{note}\n{rows_for_prompt(db, r['columns'], r['rows'], 30)}")
    return "\n\n".join(parts)


def _analysis_block(state: DPState) -> str:
    a = state.get("analysis") or {}
    if a.get("ok"):
        return tag("analysis", json.dumps(a.get("result"), default=str)[:3000])
    if a.get("error"):
        return f"(The analysis step could not run: {str(a['error'])[:200]})"
    return ""


def _fallback_answer(state: DPState) -> str:
    results = [r for r in state.get("step_results") or [] if r.get("ok")]
    if not results:
        return "I couldn't produce a verified query for this question. Try rephrasing it or naming the table you mean."
    r = results[-1]
    if r["row_count"] == 0:
        return "The query ran but returned no rows. The filters may be too narrow; check the SQL tab."
    return f"Here is the verified result ({r['row_count']} row{'s' if r['row_count'] != 1 else ''}); see the table and chart."


async def narrator(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    if state.get("status") == "blocked":
        return {}
    stop = state.get("stop")
    if not any(r.get("ok") for r in state.get("step_results") or []):
        return {"answer": STOP_MESSAGES.get(stop or "", _fallback_answer(state)), "grounding": {"checked": 0, "removed": [], "ok": True}}
    ctx.send("step", {"node": "narrator", "status": "running", "label": "Writing the answer…"})
    prompt = "\n\n".join(
        x
        for x in (
            tag("question", state["question"] + (f"\nThe user clarified: {state['clarification']}" if state.get("clarification") else "")),
            tag("rows", _results_block(db, state)),
            _analysis_block(state),
        )
        if x
    )
    with ctx.span("node", "narrator") as sp:
        try:
            res = await complete(ctx, "narrator", ctx.profile.prompts["narrator"], prompt, kinds=["main", "fast"], temperature=0.2, max_tokens=500)
            draft = res.text.strip()
        except (BudgetExceeded, LLMUnavailable) as e:
            draft = _fallback_answer(state)
            sp.attributes["fallback"] = type(e).__name__
        ctx.set_io(sp, None, {"chars": len(draft)})
    return {"answer": draft, "notes": [*(state.get("notes") or []), *( [STOP_MESSAGES[stop]] if stop in STOP_MESSAGES else [])]}


async def output_guard(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    answer = state.get("answer") or ""
    results = [{"columns": r["columns"], "rows": r["rows"], "row_count": r["row_count"]} for r in state.get("step_results") or [] if r.get("ok")]
    analysis = (state.get("analysis") or {}).get("result")
    rewritten = False
    with ctx.span("guard", "output_guard") as sp:
        allowed = Allowed(allowed_values(results, analysis, state["question"] + " " + (state.get("clarification") or "")))
        res = check_grounding(answer, allowed)
        removed: list[str] = []
        if not res.ok and ctx.flags.get("grounding_rewrite", True):
            base = sorted({abs(m.value) for r in results for row in r["rows"][:30] for m in extract_numbers(json.dumps(row, default=str))})[:80]
            prompt = "\n\n".join(
                [
                    tag("question", state["question"]),
                    tag("rows", _results_block(db, state)),
                    _analysis_block(state),
                    tag("draft", answer),
                    f"These numbers in the draft are not supported by the data: {', '.join(res.ungrounded)}. Remove or correct them.",
                    tag("allowed_numbers", ", ".join(f"{v:g}" for v in base)),
                ]
            )
            try:
                fixed = await complete(ctx, "narrator", ctx.profile.prompts["narrator"], prompt, kinds=["main", "fast"], max_tokens=500)
                answer, rewritten = fixed.text.strip(), True
                res = check_grounding(answer, allowed)
            except (BudgetExceeded, LLMUnavailable):
                pass
        if not res.ok:
            stripped = strip_ungrounded(answer, allowed)
            answer, removed = stripped.text, stripped.removed
            if not answer:
                answer = _fallback_answer(state)
        grounding = {"checked": res.checked, "removed": removed, "ok": not removed, "rewritten": rewritten,
                     "ungrounded_first_draft": res.ungrounded if not rewritten else None}
        sp.status = "ok" if not removed else "blocked"
        ctx.set_io(sp, None, grounding)
    ctx.send("grounding", grounding)
    words = answer.split(" ")
    for i in range(0, len(words), 6):
        ctx.send("token", {"text": " ".join(words[i : i + 6]) + (" " if i + 6 < len(words) else "")})
    return {"answer": answer, "grounding": grounding}
