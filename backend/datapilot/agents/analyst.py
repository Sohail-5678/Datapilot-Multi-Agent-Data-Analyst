"""Analyst + sandbox checkpoint (SPEC §4.2, §8.1–8.2).

The analyst writes pandas code; the graph pauses with `interrupt()` and the browser runs it in a Pyodide Web
Worker with network APIs removed (eval mode uses the Node Pyodide runner with the same lockdown). The server
never executes model-written Python.
"""

from __future__ import annotations

import asyncio
import re
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from datapilot.agents.state import DPState, current_step, ctx_of, database_for, extract_python, last_result, rows_for_prompt, tag
from datapilot.config import get_settings
from datapilot.llm.router import LLMUnavailable, complete
from datapilot.sql.executor import execute_readonly
from datapilot.tracing import BudgetExceeded

MAX_ATTEMPTS = 2
_FORBIDDEN = re.compile(
    r"\b(import\s+(os|sys|subprocess|socket|requests|urllib|http|pyodide|js|micropip|shutil|pathlib|ctypes)|"
    r"from\s+(os|sys|subprocess|socket|requests|urllib|http|pyodide|js|micropip)\b|__import__|exec\s*\(|eval\s*\(|"
    r"open\s*\(|compile\s*\(|globals\s*\(|getattr\s*\()",
)


def precheck(code: str) -> str | None:
    m = _FORBIDDEN.search(code)
    if m:
        return f"Not allowed in the sandbox: `{m.group(0).strip()}`. Use only pandas/numpy/scipy on `data`."
    if "result" not in code:
        return "The code must assign the final answer to a variable named `result`."
    return None


async def analyst(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    s = get_settings()
    src = last_result(state)
    step = current_step(state)
    if not src or not src.get("ok"):
        return {"analysis": {"ok": False, "skipped": True, "error": "No data to analyze."}}
    ctx.send("step", {"node": "analyst", "status": "running", "label": "Writing the analysis code…"})
    attempts = int(state.get("analysis_attempts") or 0)
    prev = state.get("analysis") or {}

    # Analysis data: the chosen query without the 1,000-row display cap, up to 5,000 rows.
    data_ref = src["data_ref"] + "-full"
    if data_ref not in ctx.data:
        base = src.get("unlimited_sql") or src["sql"]
        full = await asyncio.to_thread(
            execute_readonly, ctx.db_path or db.path, f"{base}\nLIMIT {s.sandbox_max_rows}",
            timeout_s=s.sql_timeout_s, row_cap=s.sandbox_max_rows,
        ) if src.get("unlimited_sql") else None
        if full is not None and full.ok:
            ctx.data[data_ref] = {"columns": full.columns, "rows": full.rows, "total_rows": len(full.rows)}
        else:
            ctx.data[data_ref] = ctx.data.get(src["data_ref"]) or {"columns": src["columns"], "rows": src["rows"]}
    data = ctx.data[data_ref]

    prompt = "\n\n".join(
        x
        for x in (
            tag("question", state["question"] + (f"\nAnalysis goal: {step.get('goal')}" if step.get("goal") else "")),
            f"`data` has {len(data['rows'])} rows and columns: {', '.join(data['columns'])}",
            tag("rows", rows_for_prompt(db, data["columns"], data["rows"], limit=8)),
            (tag("previous_attempt", f"{prev.get('code')}\n\nError:\n{prev.get('error')}") if attempts and prev.get("error") else ""),
        )
        if x
    )
    with ctx.span("node", "analyst", attempt=attempts + 1) as sp:
        try:
            res = await complete(ctx, "analyst", ctx.profile.prompts["analyst"], prompt, kinds=["main", "fast"], max_tokens=700)
        except BudgetExceeded:
            return {"stop": "budget_exceeded"}
        except LLMUnavailable as e:
            return {"analysis": {"ok": False, "skipped": True, "error": f"No model available for analysis ({str(e)[:80]})."}}
        code = extract_python(res.text)
        ctx.set_io(sp, None, {"code": code})
    problem = precheck(code)
    if problem:
        return {"analysis": {"ok": False, "code": code, "error": problem}, "analysis_attempts": attempts + 1, "sandbox": None}
    req = {"request_id": uuid.uuid4().hex[:12], "code": code, "data_ref": data_ref, "rows": len(data["rows"])}
    return {"sandbox": req, "analysis_attempts": attempts + 1}


async def sandbox_call(state: DPState, config: RunnableConfig) -> dict:
    req = state.get("sandbox")
    if not req:
        return {}
    result = interrupt({"type": "sandbox", **req})
    ctx = ctx_of(config)
    with ctx.span("sandbox", "sandbox_call") as sp:
        sp.attributes.update({"ran_in": (result or {}).get("ran_in", "browser"), "request_id": req["request_id"]})
        ok = bool((result or {}).get("ok"))
        sp.status = "ok" if ok else "error"
        sp.error = None if ok else str((result or {}).get("error"))[:300]
        sp.duration_ms = int((result or {}).get("duration_ms") or 0)
        ctx.set_io(sp, {"code": req["code"]}, {"result": (result or {}).get("result"), "stdout": ((result or {}).get("stdout") or "")[:500]})
    analysis = {
        "ok": ok,
        "code": req["code"],
        "result": (result or {}).get("result") if ok else None,
        "stdout": ((result or {}).get("stdout") or "")[:10_000],
        "error": None if ok else (result or {}).get("error") or "Sandbox failed.",
        "duration_ms": (result or {}).get("duration_ms"),
        "ran_in": (result or {}).get("ran_in", "browser"),
        "rows": req["rows"],
    }
    return {"analysis": analysis, "sandbox": None}


def route_analysis(state: DPState) -> str:
    if state.get("stop"):
        return "narrator" if state.get("step_results") else "respond"
    a = state.get("analysis") or {}
    if state.get("sandbox"):
        return "sandbox_call"
    if not a.get("ok") and not a.get("skipped") and int(state.get("analysis_attempts") or 0) < MAX_ATTEMPTS:
        return "analyst"
    return "advance"
