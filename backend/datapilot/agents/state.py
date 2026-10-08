"""LangGraph state (SPEC §6.2) and shared helpers for the agent nodes."""

from __future__ import annotations

import re
from string import Template
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig

from datapilot.index.catalog import Database, get_database
from datapilot.tracing import RunCtx

RESET = "__reset__"


def merge_candidates(left: list[dict] | None, right: list[dict] | str | None) -> list[dict]:
    """Parallel candidates append; a candidate with an existing id replaces it; RESET clears (next step)."""
    if right == RESET or right is None and left is None:
        return []
    out = list(left or [])
    for cand in right or []:  # type: ignore[union-attr]
        idx = next((i for i, c in enumerate(out) if c["id"] == cand["id"]), None)
        if idx is None:
            out.append(cand)
        else:
            out[idx] = cand
    return out


def keep_first_stop(left: str | None, right: str | None) -> str | None:
    """Parallel candidates can both hit the budget in the same step; keep the first stop reason."""
    return left or right


class DPState(TypedDict, total=False):
    run_id: str
    thread_id: str
    user_id: str
    db_id: str
    question: str
    evidence: str | None
    history: list[dict]  # earlier Q/A in this thread (short)
    clarification: str | None
    input_verdict: dict
    plan: list[dict]
    plan_meta: dict
    step_idx: int
    linked: dict  # tables, columns, values, joins, examples, schema text
    candidates: Annotated[list[dict], merge_candidates]
    vote: dict
    chosen: dict | None
    step_results: list[dict]
    analysis: dict | None
    analysis_attempts: int
    sandbox: dict | None  # pending sandbox request
    chart: dict | None
    answer: str | None
    grounding: dict | None
    confirmed: bool
    status: str  # success | blocked | needs_human | budget_exceeded | quota_exhausted | error | cancelled
    stop: Annotated[str | None, keep_first_stop]  # set when a budget/quota/cancel stops the pipeline early
    notes: list[str]


def ctx_of(config: RunnableConfig) -> RunCtx:
    return config["configurable"]["ctx"]  # type: ignore[index]


def database_for(ctx: RunCtx) -> Database:
    if ctx.database is not None:
        return ctx.database
    db = get_database(ctx.db_id)
    if db is None:
        raise ValueError(f"unknown database {ctx.db_id}")
    return db


def render(template: str, **values: Any) -> str:
    """Prompts use $placeholders (JSON braces in prompts stay literal)."""
    return Template(template).safe_substitute({k: str(v) for k, v in values.items()})


def tag(name: str, content: str) -> str:
    return f"<{name}>\n{content}\n</{name}>"


def pii_columns(db: Database) -> set[str]:
    return {c.name.lower() for t in db.tables.values() for c in t.columns if c.pii}


def rows_for_prompt(db: Database, columns: list[str], rows: list[list[Any]], limit: int = 30) -> str:
    """Result rows as compact TSV for prompts, with PII columns dropped (SPEC §10.4)."""
    pii = pii_columns(db)
    keep = [i for i, c in enumerate(columns) if c.lower() not in pii]
    head = "\t".join(columns[i] for i in keep)
    lines = [head]
    for r in rows[:limit]:
        lines.append("\t".join(_cell(r[i]) for i in keep))
    if len(rows) > limit:
        lines.append(f"… {len(rows) - limit} more rows")
    if len(keep) < len(columns):
        lines.append(f"(personal-data columns hidden: {', '.join(c for c in columns if c.lower() in pii)})")
    return "\n".join(lines)


def _cell(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.6g}" if abs(v) < 1e15 else str(v)
    s = str(v).replace("\t", " ").replace("\n", " ")
    return s if len(s) <= 80 else s[:77] + "…"


_SQL_FENCE = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    m = _SQL_FENCE.findall(text or "")
    if m:
        return m[-1].strip()
    t = (text or "").strip()
    i = re.search(r"\b(with|select)\b", t, re.IGNORECASE)
    return t[i.start() :].strip() if i else t


_PY_FENCE = re.compile(r"```(?:python|py)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_python(text: str) -> str:
    m = _PY_FENCE.findall(text or "")
    return (m[-1] if m else text or "").strip()


def current_step(state: DPState) -> dict:
    plan = state.get("plan") or []
    idx = state.get("step_idx", 0)
    return plan[idx] if idx < len(plan) else {}


def last_result(state: DPState) -> dict | None:
    results = state.get("step_results") or []
    return results[-1] if results else None


def history_text(state: DPState) -> str:
    h = state.get("history") or []
    if not h:
        return ""
    lines = [f"Q: {x.get('question', '')}\nSQL: {x.get('sql') or '-'}" for x in h[-3:]]
    return tag("conversation", "\n".join(lines))
