"""SQL candidates (SPEC §7.2, §7.4): one LangGraph `Send` per strategy, run in parallel.

Each candidate: generate (direct / plan-then-SQL / few-shot / cached) → SQL guard → cost estimate → read-only
execution → repair loop (≤ MAX_REPAIRS, same model chain as the failing strategy). Every sub-step is a span.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from datapilot.agents.state import ctx_of, database_for, extract_sql, rows_for_prompt, tag
from datapilot.config import get_settings
from datapilot.index.catalog import Database
from datapilot.index.value_index import suggest_values
from datapilot.llm.router import LLMUnavailable, chain_for, complete
from datapilot.sql.compare import is_ordered, result_hash
from datapilot.sql.executor import ExecResult, execute_readonly
from datapilot.sql.explain import estimate_cost
from datapilot.sql.guard import GuardResult, guard_sql
from datapilot.tracing import BudgetExceeded, RunCtx

STEP_FOR = {"direct": "sql_direct", "plan_then_sql": "sql_plan", "few_shot": "sql_fewshot"}
DEFAULT_KINDS = {"direct": ["fast", "main"], "plan_then_sql": ["fast", "main"], "few_shot": ["main", "fast"]}
_LITERAL_FILTER = re.compile(r"(=|\blike\b|\bin\s*\()\s*'", re.IGNORECASE)


def task_prompt(payload: dict, strategy: str) -> str:
    linked = payload["linked"]
    parts = [tag("schema", linked["schema"])]
    if linked.get("joins"):
        parts.append("Join paths (foreign keys):\n" + "\n".join(linked["joins"]))
    if linked.get("values"):
        vals = "\n".join(f"{v['table']}.{v['column']} = {v['value']!r}  (matched \"{v['matched']}\")" for v in linked["values"][:10])
        parts.append(tag("values", vals))
    if strategy == "few_shot":
        ex = linked.get("examples") or []
        body = "\n\n".join(
            (f"Question: {e['question']}\n" + (f"Evidence: {e['evidence']}\n" if e.get("evidence") else "") + f"SQL: {e['sql']}")
            for e in ex
        )
        parts.append(tag("examples", body or "(no solved examples for this database yet)"))
    if payload.get("evidence"):
        parts.append(tag("evidence", payload["evidence"]))
    prev = payload.get("previous") or []
    if prev:
        parts.append(
            "Earlier steps of this analysis (reuse their logic if helpful):\n"
            + "\n".join(f"- {p['goal']}\n  SQL: {p['sql']}" for p in prev if p.get("sql"))
        )
    if payload.get("history_sql"):
        parts.append(f"Previous question in this conversation used:\n{payload['history_sql']}")
    task = payload["goal"]
    if payload["question"] != task:
        task = f"{payload['question']}\nThis step: {task}"
    if payload.get("clarification"):
        task += f"\nThe user clarified: {payload['clarification']}"
    parts.append(tag("question", task))
    return "\n\n".join(parts)


async def _generate(ctx: RunCtx, payload: dict, strategy: str, cand: dict) -> str:
    step = STEP_FOR[strategy]
    res = await complete(
        ctx,
        step,
        ctx.profile.prompts[step],
        task_prompt(payload, strategy),
        kinds=DEFAULT_KINDS[strategy],
        temperature=float(ctx.profile.p("temperature", 0.0)),
        max_tokens=900 if strategy == "plan_then_sql" else 600,
        span_attrs={"strategy": strategy},
    )
    cand["model"] = res.model
    return extract_sql(res.text)


async def _repair(ctx: RunCtx, payload: dict, strategy: str, sql: str, problem: str, hints: str) -> str:
    kinds = chain_for(ctx, STEP_FOR.get(strategy, "sql_direct"), DEFAULT_KINDS.get(strategy, ["fast", "main"]))
    prompt = "\n\n".join(
        [task_prompt(payload, "direct"), tag("failed_sql", sql), tag("problem", problem), tag("hints", hints or "none")]
    )
    res = await complete(ctx, "repair", ctx.profile.prompts["repair"], prompt, kinds=kinds, max_tokens=600,
                         span_attrs={"strategy": strategy})
    return extract_sql(res.text)


def _zero_row_hints(db: Database, sql: str, values: list[dict]) -> str:
    hints = ["The query returned 0 rows. Check filters and value spelling (text comparison is case-sensitive)."]
    for lit in re.findall(r"'((?:[^']|'')+)'", sql)[:4]:
        for v in values:
            if v["value"].lower() == lit.lower() and v["value"] != lit:
                hints.append(f"'{lit}' is spelled '{v['value']}' in {v['table']}.{v['column']}.")
        # nearest spellings in the columns this literal is compared against
        for m in re.finditer(r"([\w\"`.]+)\s*(?:=|like)\s*'" + re.escape(lit), sql, re.IGNORECASE):
            col_ref = m.group(1).strip('"`')
            col = col_ref.split(".")[-1].strip('"`')
            for t in db.tables.values():
                c = t.column(col)
                if c is not None and c.is_text and not c.pii:
                    near = suggest_values(db, t.name, c.name, lit, 4)
                    if near:
                        hints.append(f"Closest values in {t.name}.{c.name}: {', '.join(repr(n) for n in near)}")
    return "\n".join(dict.fromkeys(hints))


def _guard(db: Database, sql: str) -> GuardResult:
    return guard_sql(sql, db.table_names, {n: t.row_count for n, t in db.tables.items()})


async def _execute(ctx: RunCtx, db: Database, g: GuardResult) -> ExecResult:
    s = get_settings()
    path = ctx.db_path or db.path
    with ctx.span("tool", "executor") as sp:
        res = await asyncio.to_thread(
            execute_readonly, path, g.sql, timeout_s=s.sql_timeout_s, row_cap=s.row_cap, count_sql=g.unlimited_sql or None
        )
        sp.status = "ok" if res.ok else "error"
        sp.error = res.error
        ctx.set_io(sp, {"sql": g.sql}, {"rows": res.row_count, "ms": res.duration_ms, "truncated": res.truncated})
    return res


def _new_candidate(cid: str, strategy: str) -> dict[str, Any]:
    return {
        "id": cid,
        "strategy": strategy,
        "model": None,
        "sql": None,
        "status": "pending",
        "result_hash": None,
        "row_count": None,
        "error": None,
        "repairs": 0,
        "columns": [],
        "rows": [],
        "truncated": False,
        "duration_ms": 0,
        "cost": None,
        "unlimited_sql": "",
    }


async def sql_candidate(payload: dict, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    s = get_settings()
    strategy = payload["strategy"]
    cand = _new_candidate(payload["cand_id"], strategy)
    max_repairs = s.max_repairs if ctx.flags.get("repair", True) else 0
    ctx.send("candidate", {"id": cand["id"], "strategy": strategy, "status": "running"})
    with ctx.span("node", "sql_agent", strategy=strategy, candidate=cand["id"]) as span:
        try:
            sql = payload["linked"]["cached_sql"] if strategy == "cache" else await _generate(ctx, payload, strategy, cand)
            if strategy == "cache":
                cand["model"] = "verified-cache"
            attempts = 0
            fallback: dict | None = None  # a 0-row result kept in case repairs don't do better
            while True:
                cand["sql"] = sql
                with ctx.span("guard", "sql_guard") as gs:
                    g = _guard(db, sql)
                    gs.status = "ok" if g.ok else "blocked"
                    ctx.set_io(gs, {"sql": sql}, g.as_dict())
                problem, hints = None, ""
                if not g.ok:
                    problem = f"Rejected by the SQL guard: {g.reason}"
                else:
                    cand["sql"] = g.sql
                    cand["unlimited_sql"] = g.unlimited_sql
                    if not ctx.flags.get("allow_expensive") and not payload.get("confirmed"):
                        est = await asyncio.to_thread(
                            estimate_cost, ctx.db_path or db.path, g.sql, {n: t.row_count for n, t in db.tables.items()}
                        )
                        cand["cost"] = {"scanned_rows": est.scanned_rows, "table": est.biggest_table,
                                        "seconds": est.seconds_hint(), "unindexed_join": est.unindexed_join}
                        if est.scanned_rows > s.scan_confirm_rows:
                            cand["status"] = "needs_confirm"
                            break
                    res = await _execute(ctx, db, g)
                    if not res.ok:
                        problem = f"SQLite error: {res.error}"
                        hints = _schema_hint(db, res.error or "")
                    elif res.row_count == 0 and _LITERAL_FILTER.search(g.sql) and attempts < max_repairs:
                        problem = "The query ran but returned 0 rows, which is suspicious for this question."
                        hints = _zero_row_hints(db, g.sql, payload["linked"].get("values") or [])
                        _fill(cand, res, g.sql)
                        fallback, cand["status"] = dict(cand), "pending"
                    else:
                        _fill(cand, res, g.sql)
                        break
                if attempts >= max_repairs:
                    if fallback is not None:
                        cand.update({**fallback, "repairs": attempts})
                    else:
                        cand["status"] = "rejected" if not g.ok else "error"
                        cand["error"] = problem
                    break
                attempts += 1
                cand["repairs"] = attempts
                ctx.send("candidate", {"id": cand["id"], "strategy": strategy, "status": "repairing", "attempt": attempts})
                with ctx.span("node", "repair", attempt=attempts) as rs:
                    ctx.set_io(rs, {"problem": problem, "hints": hints})
                    sql = await _repair(ctx, payload, strategy if strategy != "cache" else "direct", cand["sql"] or sql, problem or "", hints)
        except BudgetExceeded as e:
            cand["status"], cand["error"] = "error", str(e)
            return {"candidates": [cand], "stop": "budget_exceeded"} if not cand.get("rows") else {"candidates": [cand]}
        except LLMUnavailable as e:
            cand["status"], cand["error"] = "error", "No model available: " + str(e)[:200]
            cand["quota"] = e.quota_exhausted
        span.attributes.update({"status": cand["status"], "repairs": cand["repairs"], "rows": cand["row_count"]})
    ctx.send(
        "candidate",
        {"id": cand["id"], "strategy": strategy, "status": cand["status"], "row_count": cand["row_count"],
         "repairs": cand["repairs"], "error": (cand["error"] or "")[:200] or None},
    )
    return {"candidates": [cand]}


def _fill(cand: dict, res: ExecResult, sql: str) -> None:
    cand.update(
        status="ok",
        columns=res.columns,
        rows=res.rows,
        row_count=res.row_count,
        truncated=res.truncated,
        duration_ms=res.duration_ms,
        result_hash=result_hash(res.rows, ordered=is_ordered(sql)),
        error=None,
    )


def _schema_hint(db: Database, error: str) -> str:
    m = re.search(r"no such column: ([\w.\"`]+)", error)
    if m:
        name = m.group(1).split(".")[-1].strip('"`').lower()
        owners = [f"{t.name}.{c.name}" for t in db.tables.values() for c in t.columns if name in c.name.lower()]
        return f"Columns with a similar name: {', '.join(owners[:8])}" if owners else "Use only columns listed in <schema>."
    m = re.search(r"no such table: ([\w.\"`]+)", error)
    if m:
        return f"Tables in this database: {', '.join(db.table_names)}"
    if "ambiguous column" in error:
        return "Qualify the column with its table alias."
    return ""


def preview_for_verifier(db: Database, cand: dict) -> str:
    return rows_for_prompt(db, cand["columns"], cand["rows"], limit=8)
