"""Chart agent (SPEC §8.3): a validated Vega-Lite subset, or an automatic chart picked by rules.

The model never supplies data, transforms, URLs or expressions — the client injects the result rows.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from datapilot.agents.state import DPState, ctx_of, database_for, rows_for_prompt, tag
from datapilot.llm.router import LLMUnavailable, complete, parse_json
from datapilot.tracing import BudgetExceeded

MARKS = {"bar", "line", "point", "area", "arc"}
CHANNELS = {"x", "y", "color", "theta", "tooltip"}
TYPES = {"quantitative", "nominal", "ordinal", "temporal"}
AGGS = {"sum", "mean", "count", "max", "min", "average", "median"}
SORTS = {"-y", "-x", "x", "y", "ascending", "descending", None}
_DATEISH = re.compile(r"(date|day|month|year|time|season|period|week)", re.I)


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def column_kinds(columns: list[str], rows: list[list[Any]]) -> dict[str, str]:
    kinds = {}
    for i, c in enumerate(columns):
        vals = [r[i] for r in rows[:200] if r[i] is not None]
        if vals and all(_is_num(v) for v in vals):
            kinds[c] = "temporal" if _DATEISH.search(c) and all(1900 <= v <= 2100 for v in vals if _is_num(v)) else "quantitative"
        elif vals and all(isinstance(v, str) and re.match(r"^\d{4}(-\d{2})?(-\d{2})?", v) for v in vals):
            kinds[c] = "temporal"
        else:
            kinds[c] = "nominal"
    return kinds


def validate_spec(spec: Any, columns: list[str]) -> dict | None:
    """Strict whitelist validation (mirrored by a zod schema on the web)."""
    if not isinstance(spec, dict) or spec.get("no_chart"):
        return None
    if set(spec) - {"mark", "encoding", "title", "$schema", "description"}:
        return None
    mark = spec.get("mark")
    if isinstance(mark, dict):
        mark = mark.get("type")
    if mark not in MARKS:
        return None
    enc = spec.get("encoding")
    if not isinstance(enc, dict) or not enc or set(enc) - CHANNELS:
        return None
    clean_enc: dict[str, Any] = {}
    for ch, val in enc.items():
        items = val if ch == "tooltip" and isinstance(val, list) else [val]
        clean_items = []
        for it in items:
            if not isinstance(it, dict) or set(it) - {"field", "type", "title", "sort", "aggregate"}:
                return None
            if it.get("field") not in columns or it.get("type") not in TYPES:
                return None
            if "aggregate" in it and it["aggregate"] not in AGGS:
                return None
            if "sort" in it and it["sort"] not in SORTS:
                return None
            if "title" in it and (not isinstance(it["title"], str) or len(it["title"]) > 80):
                return None
            clean_items.append({k: v for k, v in it.items() if v is not None})
        clean_enc[ch] = clean_items if ch == "tooltip" and isinstance(val, list) else clean_items[0]
    if mark == "arc" and "theta" not in clean_enc:
        return None
    if mark != "arc" and not ({"x", "y"} <= set(clean_enc)):
        return None
    title = spec.get("title")
    out = {"mark": mark, "encoding": clean_enc}
    if isinstance(title, str) and len(title) <= 120:
        out["title"] = title
    return out


def auto_chart(columns: list[str], rows: list[list[Any]], title: str | None = None) -> dict | None:
    """Rules fallback: time column → line; one category + one number → bar; two numbers → point."""
    if len(rows) < 2 or len(columns) < 2:
        return None
    kinds = column_kinds(columns, rows)
    nums = [c for c in columns if kinds[c] == "quantitative"]
    times = [c for c in columns if kinds[c] == "temporal"]
    cats = [c for c in columns if kinds[c] == "nominal"]
    if times and nums:
        x, y = times[0], nums[0]
        return {"mark": "line", "encoding": {"x": {"field": x, "type": "temporal" if not all(_is_num(r[columns.index(x)]) for r in rows) else "ordinal"},
                                              "y": {"field": y, "type": "quantitative"},
                                              "tooltip": [{"field": x, "type": "nominal"}, {"field": y, "type": "quantitative"}]},
                **({"title": title} if title else {})}
    if cats and nums and len(rows) <= 60:
        x, y = cats[0], nums[0]
        return {"mark": "bar", "encoding": {"y": {"field": x, "type": "nominal", "sort": "-x"}, "x": {"field": y, "type": "quantitative"},
                                             "tooltip": [{"field": x, "type": "nominal"}, {"field": y, "type": "quantitative"}]},
                **({"title": title} if title else {})}
    if len(nums) >= 2:
        return {"mark": "point", "encoding": {"x": {"field": nums[0], "type": "quantitative"}, "y": {"field": nums[1], "type": "quantitative"},
                                               "tooltip": [{"field": nums[0], "type": "quantitative"}, {"field": nums[1], "type": "quantitative"}]},
                **({"title": title} if title else {})}
    return None


async def chart(state: DPState, config: RunnableConfig) -> dict:
    ctx = ctx_of(config)
    db = database_for(ctx)
    results = [r for r in state.get("step_results") or [] if r.get("ok") and r.get("rows")]
    if not results or not ctx.flags.get("chart", True):
        return {"chart": None}
    src = next((r for r in reversed(results) if r.get("needs_chart")), results[-1])
    cols, rows = src["columns"], src["rows"]
    if len(rows) < 2 or len(cols) < 2:
        return {"chart": None}
    kinds = column_kinds(cols, rows)
    if not any(k == "quantitative" for k in kinds.values()):
        return {"chart": None}
    ctx.send("step", {"node": "chart", "status": "running", "label": "Drawing the chart…"})
    spec, source = None, "rules"
    with ctx.span("node", "chart") as sp:
        try:
            prompt = "\n\n".join(
                [
                    tag("question", state["question"]),
                    "Columns and inferred types: " + ", ".join(f"{c} ({kinds[c]})" for c in cols),
                    tag("rows", rows_for_prompt(db, cols, rows, limit=6)),
                    f"{len(rows)} rows in total.",
                ]
            )
            res = await complete(ctx, "chart", ctx.profile.prompts["chart"], prompt, kinds=["lite"], json_mode=True, max_tokens=400)
            spec = validate_spec(parse_json(res.text), cols)
            source = "llm" if spec else "rules (model spec failed validation)"
        except BudgetExceeded:
            pass
        except (LLMUnavailable, ValueError):
            pass
        if spec is None:
            spec = auto_chart(cols, rows)
            if source == "rules" or spec is None:
                source = "rules" if spec else "none"
        ctx.set_io(sp, None, {"source": source, "spec": spec})
    if spec is None:
        return {"chart": None}
    out = {"spec": spec, "data_ref": src["data_ref"], "source": source, "step_idx": src["step_idx"]}
    ctx.send("chart", out)
    return {"chart": out}
