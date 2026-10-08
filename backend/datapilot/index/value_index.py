"""Value index (SPEC §5.3 step 4): distinct text values per column, fuzzy-matched to question n-grams.

Links literals in a question ("Lewis Hamilton", "Rock", "Marvel Comics") to the columns that contain
them, so the SQL agent filters on the right column with the right spelling. Built in memory at
startup; columns with too many distinct values or long free text are skipped to stay inside 512 MB.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from datapilot.index.catalog import Database, connect_ro

log = logging.getLogger(__name__)

MAX_DISTINCT = 50_000
MAX_AVG_LEN = 60
MIN_SCORE = 88
STOPWORDS = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "many",
        "me",
        "most",
        "much",
        "of",
        "on",
        "or",
        "show",
        "the",
        "their",
        "them",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "each",
        "per",
        "vs",
        "versus",
        "than",
        "top",
        "best",
        "worst",
        "average",
        "total",
        "number",
        "count",
        "list",
        "give",
        "tell",
        "find",
        "all",
        "any",
        "between",
        "during",
        "over",
        "under",
    ]
)


@dataclass
class ValueLink:
    table: str
    column: str
    value: str
    matched: str
    score: float

    def as_dict(self) -> dict:
        return {
            "table": self.table,
            "column": self.column,
            "value": self.value,
            "matched": self.matched,
            "score": round(self.score, 1),
        }


@dataclass
class DbValues:
    choices: list[str] = field(default_factory=list)  # lowercased values
    owners: list[tuple[str, str, str]] = field(default_factory=list)  # (table, column, original value)
    columns_indexed: int = 0
    build_ms: int = 0
    approx_bytes: int = 0


_index: dict[str, DbValues] = {}


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def build_value_index(db: Database) -> DbValues:
    start = time.perf_counter()
    out = DbValues()
    conn = connect_ro(db.path)
    try:
        for table in db.tables.values():
            for col in table.columns:
                if col.pii or not col.is_text or col.name.lower().endswith(("id", "url", "_api_id")):
                    continue
                try:
                    n, avg = conn.execute(
                        f"SELECT COUNT(DISTINCT {_qi(col.name)}), AVG(LENGTH({_qi(col.name)})) FROM {_qi(table.name)}"
                    ).fetchone()
                except sqlite3.Error:
                    continue
                col.distinct = int(n or 0)
                if not n or n > MAX_DISTINCT or (avg or 0) > MAX_AVG_LEN:
                    continue
                for (v,) in conn.execute(
                    f"SELECT DISTINCT {_qi(col.name)} FROM {_qi(table.name)} WHERE {_qi(col.name)} IS NOT NULL"
                ):
                    if not isinstance(v, str):
                        continue
                    s = v.strip()
                    if len(s) < 2 or len(s) > 120:
                        continue
                    out.choices.append(s.lower())
                    out.owners.append((table.name, col.name, s))
                    out.approx_bytes += 2 * len(s) + 120
                out.columns_indexed += 1
    finally:
        conn.close()
    out.build_ms = int((time.perf_counter() - start) * 1000)
    log.info(
        "value index %s: %d values in %d columns, %d ms", db.db_id, len(out.choices), out.columns_indexed, out.build_ms
    )
    _index[db.db_id] = out
    return out


def get_values(db: Database) -> DbValues:
    return _index.get(db.db_id) or build_value_index(db)


def index_stats() -> dict:
    return {
        k: {"values": len(v.choices), "columns": v.columns_indexed, "build_ms": v.build_ms} for k, v in _index.items()
    } | {"_total_mb": round(sum(v.approx_bytes for v in _index.values()) / 1e6, 1)}


def _ngrams(question: str) -> list[str]:
    quoted = re.findall(r"[\"“']([^\"”']{2,80})[\"”']", question)
    words = re.findall(r"[\w][\w.&'/-]*", question)
    grams: list[str] = []
    for n in (4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            chunk = words[i : i + n]
            if n == 1 and (chunk[0].lower() in STOPWORDS or len(chunk[0]) < 3 or chunk[0].isdigit()):
                continue
            if chunk[0].lower() in STOPWORDS and chunk[-1].lower() in STOPWORDS:
                continue
            grams.append(" ".join(chunk))
    return quoted + grams


def link_values(db: Database, question: str, limit: int = 12) -> list[ValueLink]:
    idx = get_values(db)
    if not idx.choices:
        return []
    found: dict[tuple[str, str, str], ValueLink] = {}
    for gram in _ngrams(question):
        g = gram.lower()
        for _choice, score, i in process.extract(g, idx.choices, scorer=fuzz.ratio, score_cutoff=MIN_SCORE, limit=5):
            table, column, value = idx.owners[i]
            key = (table, column, value)
            # Prefer longer n-grams for the same value (more specific match)
            if key not in found or score > found[key].score:
                found[key] = ValueLink(table, column, value, gram, float(score))
    links = sorted(found.values(), key=lambda v: (-v.score, -len(v.matched)))
    # Drop single-word matches that are contained in a longer exact match on the same column
    pruned: list[ValueLink] = []
    for v in links:
        if any(
            p.column == v.column and p.table == v.table and v.value.lower() in p.value.lower() and p.value != v.value
            for p in pruned
        ):
            continue
        pruned.append(v)
    return pruned[:limit]


def suggest_values(db: Database, table: str, column: str, text: str, limit: int = 5) -> list[str]:
    """Closest spellings in one column, for the repair loop's '0 rows' hint."""
    idx = get_values(db)
    cands = [(c, i) for i, c in enumerate(idx.choices) if idx.owners[i][0] == table and idx.owners[i][1] == column]
    if not cands:
        return []
    res = process.extract(text.lower(), [c for c, _ in cands], scorer=fuzz.WRatio, limit=limit)
    return [idx.owners[cands[r[2]][1]][2] for r in res]
