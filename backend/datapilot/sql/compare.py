"""Result comparison: BIRD execution match and order-aware result hashing (SPEC §7.3, §7.5)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

FLOAT_DP = 6


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        r = round(v, FLOAT_DP)
        return int(r) if r.is_integer() else r
    if isinstance(v, bool):
        return int(v)
    return v


def _key(row: list[Any] | tuple[Any, ...]) -> str:
    return json.dumps([_norm(v) for v in row], default=str, separators=(",", ":"))


def is_ordered(sql: str) -> bool:
    """Row order matters only for a top-level ORDER BY + LIMIT (a ranking)."""
    s = re.sub(r"'(?:[^']|'')*'", "''", sql.lower())
    # Keep only the top level: drop every parenthesized group (subqueries, CTE bodies, window specs and
    # function calls such as ORDER BY COUNT(*) DESC), innermost first.
    prev = None
    while prev != s:
        prev, s = s, re.sub(r"\([^()]*\)", " ", s)
    return bool(re.search(r"\border\s+by\b", s) and re.search(r"\blimit\b", s))


def result_hash(rows: list[list[Any]], ordered: bool = False) -> str:
    keys = [_key(r) for r in rows]
    if not ordered:
        keys.sort()
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def execution_match(pred_rows: list[Any], gold_rows: list[Any]) -> bool:
    """BIRD's official EX (evaluation.py): set(predicted rows) == set(gold rows), raw values, no rounding."""
    return {tuple(r) for r in pred_rows} == {tuple(r) for r in gold_rows}
