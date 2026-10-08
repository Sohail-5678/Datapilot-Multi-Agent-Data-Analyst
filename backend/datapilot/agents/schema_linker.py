"""Schema linker (SPEC §7.1): retrieval + value links + join paths + Flash-Lite pruning + similar examples."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from datapilot.agents.state import DPState, current_step, ctx_of, database_for, tag
from datapilot.index import cache as sql_cache
from datapilot.index.catalog import Database
from datapilot.index.schema_index import embed_query, ensure_embeddings, join_paths, rank_columns, render_schema
from datapilot.index.value_index import link_values
from datapilot.llm.router import LLMUnavailable, complete_model
from datapilot.tracing import BudgetExceeded

FULL_SCHEMA_COLUMNS = 90  # small schemas go to the SQL agents whole (pruning would only lose context)


class PrunedTable(BaseModel):
    name: str
    columns: list[str] = Field(default_factory=list)
    reason: str = ""


class PruneOut(BaseModel):
    tables: list[PrunedTable] = Field(default_factory=list)


def _names_only(db: Database, tables: list[str]) -> str:
    lines = []
    for name in tables:
        t = db.table(name)
        if t is None:
            continue
        cols = []
        for c in t.columns:
            d = f" ({c.description[:60]})" if c.description else ""
            cols.append(f"{c.name}{d}")
        lines.append(f"{t.name}: " + "; ".join(cols))
    return "\n".join(lines)


def similar_examples(ctx, db: Database, question: str, n: int) -> list[dict]:  # type: ignore[no-untyped-def]
    pool: list[dict] = list(ctx.flags.get("fewshot_pool") or [])
    pool += [e for e in (ctx.profile.few_shots or []) if e.get("db_id") in (None, db.db_id)]
    pool += sql_cache.recent_examples(db.db_id)
    if not pool or n <= 0:
        return []
    scored = sorted(pool, key=lambda e: -fuzz.token_set_ratio(question, e.get("question") or e.get("input") or ""))
    seen, out = set(), []
    for e in scored:
        q = e.get("question") or e.get("input")
        sql = e.get("sql") or e.get("output")
        if not q or not sql or q in seen or q.strip().lower() == question.strip().lower():
            continue
        seen.add(q)
        out.append({"question": q, "sql": sql, "evidence": e.get("evidence")})
        if len(out) >= n:
            break
    return out


async def schema_linker(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    prof = ctx.profile
    step = current_step(state)
    question = state["question"]
    goal = step.get("goal") or question
    clar = state.get("clarification")
    query = " ".join(x for x in (question, goal, clar) if x)
    linking = ctx.flags.get("linking", True)
    ctx.send("step", {"node": "schema_linker", "status": "running", "label": "Finding relevant tables…"})

    with ctx.span("node", "schema_linker") as sp:
        total_cols = sum(len(t.columns) for t in db.tables.values())
        values = [v.as_dict() for v in link_values(db, query)] if linking else []
        selection: dict[str, list[str]] | None = None
        reasons: dict[str, str] = {}
        joins: list[str] = []
        pruned_by = "full schema"
        if linking and total_cols > FULL_SCHEMA_COLUMNS:
            vec = None
            if await ensure_embeddings(db):
                vec = await embed_query(query)
            ranked = rank_columns(db, query, int(prof.p("schema_top_columns", 25)), vec)
            with ctx.span("retrieval", "schema_search") as rs:
                ctx.set_io(rs, {"query": query}, {"top": ranked[:10], "vector": vec is not None})
            order: list[str] = []
            for t, _c, _s in ranked:
                if t not in order:
                    order.append(t)
            for v in values:
                if v["table"] not in order:
                    order.insert(0, v["table"])
            cand_tables = order[:10]
            try:
                out = await complete_model(
                    ctx,
                    "schema_prune",
                    prof.prompts["schema_prune"],
                    "\n\n".join(
                        [
                            tag("schema", _names_only(db, cand_tables)),
                            tag("values", "\n".join(f"{v['table']}.{v['column']} = {v['value']!r}" for v in values[:8])),
                            tag("evidence", state.get("evidence") or ""),
                            tag("question", query),
                        ]
                    ),
                    PruneOut,
                    kinds=["lite"],
                    max_tokens=500,
                )
                selection = {}
                for t in out.tables[: int(prof.p("max_tables", 8))]:
                    real = db.table(t.name)
                    if real is None:
                        continue
                    cols = [c.name for c in real.columns if c.name.lower() in {x.lower() for x in t.columns}]
                    selection[real.name] = cols
                    reasons[real.name] = t.reason[:120]
                pruned_by = "flash-lite"
            except BudgetExceeded:
                return {"stop": "budget_exceeded"}
            except (LLMUnavailable, ValueError):
                selection = None
            if not selection:  # rules fallback: top tables by vector/BM25 score, top columns + keys
                selection = {}
                budget = int(prof.p("max_columns", 40))
                for t, c, _s in ranked:
                    if len(selection) >= int(prof.p("max_tables", 8)) and t not in selection:
                        continue
                    selection.setdefault(t, [])
                    if budget > 0:
                        selection[t].append(c)
                        budget -= 1
                for v in values:
                    selection.setdefault(v["table"], []).append(v["column"])
                pruned_by = "rules"
            tables, joins = join_paths(db, list(selection))
            for t in tables:
                selection.setdefault(t, [])
        elif linking:
            tables, joins = join_paths(db, [v["table"] for v in values]) if values else ([], [])
        schema_text = render_schema(db, selection, with_samples=linking, with_descriptions=True)
        examples = similar_examples(ctx, db, query, int(prof.p("few_shot_pool", 3))) if linking else []
        cached = await sql_cache.lookup(db.db_id, question) if ctx.flags.get("cache", True) and linking else None
        n_tables = len(selection) if selection else len(db.tables)
        linked = {
            "tables": list(selection) if selection else list(db.tables),
            "selection": selection,
            "reasons": reasons,
            "values": values,
            "joins": joins,
            "examples": examples,
            "schema": schema_text,
            "pruned_by": pruned_by,
            "cached_sql": cached,
        }
        ctx.set_io(sp, {"goal": goal}, {k: v for k, v in linked.items() if k != "schema"})
    label = (f"Schema ready · all {n_tables} tables" if pruned_by == "full schema"
             else f"Finding relevant tables… {n_tables} table{'s' if n_tables != 1 else ''} found")
    if values:
        label += f", {len(values)} value link{'s' if len(values) != 1 else ''}"
    ctx.send("step", {"node": "schema_linker", "status": "done", "label": label,
                      "tables": linked["tables"], "values": values[:6]})
    return {"linked": linked, "candidates": "__reset__"}
