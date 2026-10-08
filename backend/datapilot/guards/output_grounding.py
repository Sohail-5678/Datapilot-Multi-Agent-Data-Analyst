"""Output grounding guard (SPEC §8.4) — a locked guardrail.

Every number in the narrator's answer must match a value in the chosen results, the analysis result,
the question itself, or a simple derivation computed here from those values (difference, percent change,
ratio, sum, mean, count, rank), at the precision it was written with. Ungrounded numbers → the narrator
rewrites once with the allowed list → still ungrounded → that sentence is removed.

Precision-aware matching (stricter than the spec's flat 0.5%, deliberately): "$13,469.80" must match a value to
the cent, so a narrator "rounding" 13,469.75 to .80 is caught; honest roundings still pass — "13,470",
"$13.5K", "about 13,000" (≥ 2 significant digits kept), "8.0%" for 7.96%. A flat 0.5% let the cents slip through.
"""

from __future__ import annotations

import bisect
import itertools
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

FLOAT_EPS = 1e-9
MAX_PAIR_VALUES = 120  # pairwise derivations over at most this many distinct values

_NUM = re.compile(
    r"(?<![\w.])(?P<sign>[-−–])?(?P<cur>[$€£])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?(?:%|percent|K\b|M\b|B\b|k\b|million\b|billion\b|thousand\b))?(?![\w])"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*\"(\[])|\n+")


@dataclass
class Mention:
    text: str
    value: float
    decimals: int
    percent: bool
    start: int
    end: int
    unit: float = 1.0  # the precision the number was written with (half of it is the allowed error)


@dataclass
class GroundingResult:
    ok: bool
    checked: int
    ungrounded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    text: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checked": self.checked, "ungrounded": self.ungrounded, "removed": self.removed}


def extract_numbers(text: str) -> list[Mention]:
    out = []
    for m in _NUM.finditer(text):
        raw = m.group("num")
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        suf = (m.group("suf") or "").strip().lower()
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}.get(suf, 1.0)
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if m.group("sign") and m.start("sign") >= 0:
            # Only treat as negative when the sign is attached (−4.1%), not a dash in a range "2011–2012".
            before = text[max(0, m.start() - 1) : m.start()]
            v = -v if (not before or before in " (") else v
        unit = 10.0**-dec * mult
        if dec == 0:
            digits = raw.replace(",", "")
            zeros = len(digits) - len(digits.rstrip("0"))
            significant = len(digits.rstrip("0").lstrip("0"))
            if zeros and significant >= 2:  # "13,000" is 13 thousand, rounded to thousands; "100" stays exact
                unit *= 10.0**zeros
        out.append(Mention(m.group(0), v * mult, dec, suf in ("%", "percent"), m.start(), m.end(), unit))
    return out


def _flatten(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        if math.isfinite(obj):
            out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(m.value for m in extract_numbers(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten(v, out)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _flatten(v, out)


def allowed_values(results: Iterable[dict], analysis: Any = None, question: str = "") -> set[float]:
    """Base values + derivations. `results` items: {"columns": [...], "rows": [[...]], "row_count": int}."""
    base: list[float] = []
    derived: set[float] = set()
    for res in results:
        rows = res.get("rows") or []
        _flatten(rows, base)
        # Numbers in result column names are context the answer may cite ("revenue_2011" → 2011, "top_10" → 10).
        for col in res.get("columns") or []:
            base.extend(float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(col)))
        n = int(res.get("row_count") or len(rows))
        derived.update(float(i) for i in range(0, max(10, min(n, 1000)) + 1))
        # column aggregates
        cols = list(zip(*rows, strict=False)) if rows else []
        for col in cols:
            nums = [
                float(v) for v in col if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
            ]
            if nums:
                total = sum(nums)
                derived.update({total, total / len(nums), max(nums) - min(nums), float(len(nums))})
                if total:
                    derived.update(100.0 * x / total for x in nums[:200])  # share of total
    _flatten(analysis, base)
    _flatten(question, base)
    distinct = sorted(set(base), key=abs, reverse=True)[:MAX_PAIR_VALUES]
    for a, b in itertools.permutations(distinct, 2):
        derived.add(a - b)
        if b:
            derived.add(a / b)
            derived.add(100.0 * (a - b) / abs(b))  # percent change b → a
            derived.add(100.0 * a / b)  # a as a percent of b
    allowed = set(base) | derived
    allowed |= {v * 100 for v in base if -1.5 <= v <= 1.5}  # fractions written as percents
    allowed |= {v / 1000 for v in base} | {v / 60000 for v in base}  # ms → s / min (track lengths)
    allowed |= {abs(v) for v in allowed}
    return allowed


class Allowed:
    """Sorted absolute values for O(log n) tolerance lookups."""

    def __init__(self, values: set[float]):
        self.values = sorted({abs(v) for v in values if math.isfinite(v)})

    def matches(self, m: Mention) -> bool:
        x = abs(m.value)
        tol = m.unit / 2 + FLOAT_EPS * max(1.0, x)
        i = bisect.bisect_left(self.values, x - tol)
        return i < len(self.values) and self.values[i] <= x + tol


def check_grounding(text: str, allowed: set[float] | Allowed) -> GroundingResult:
    allow = allowed if isinstance(allowed, Allowed) else Allowed(allowed)
    mentions = extract_numbers(text)
    bad = [m.text.strip() for m in mentions if not allow.matches(m)]
    return GroundingResult(not bad, len(mentions), bad, text=text)


def strip_ungrounded(text: str, allowed: set[float] | Allowed) -> GroundingResult:
    allowed = allowed if isinstance(allowed, Allowed) else Allowed(allowed)
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    kept, removed, checked = [], [], 0
    for sent in sentences:
        res = check_grounding(sent, allowed)
        checked += res.checked
        if res.ok:
            kept.append(sent.strip())
        else:
            removed.append(sent.strip())
    out = " ".join(kept).strip()
    return GroundingResult(not removed, checked, [], removed, out)
