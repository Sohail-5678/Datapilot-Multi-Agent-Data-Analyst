"""Query cost estimate from EXPLAIN QUERY PLAN (SPEC §4.3, §10.1 rule 7).

A full `SCAN` of a table counts all its rows; an automatic index (a join without an indexed key)
counts the table too, since SQLite has to build the index by scanning it. Above SCAN_CONFIRM_ROWS the
graph asks the user before running the query.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from datapilot.sql.executor import open_readonly

_SCAN = re.compile(r"^SCAN (?:TABLE )?([\w\"`\[\]]+)", re.IGNORECASE)
_AUTO = re.compile(r"^SEARCH (?:TABLE )?([\w\"`\[\]]+) USING AUTOMATIC", re.IGNORECASE)


@dataclass
class CostEstimate:
    scanned_rows: int = 0
    scans: list[dict] = field(default_factory=list)
    unindexed_join: bool = False
    plan: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def biggest_table(self) -> str | None:
        return max(self.scans, key=lambda s: s["rows"])["table"] if self.scans else None

    def seconds_hint(self) -> str:
        # Rough, measured on Render's 0.1 CPU: ~60k rows/s for a scan with aggregation.
        lo = max(1, round(self.scanned_rows / 60_000))
        return f"{lo}–{lo + 2} s"


def _clean(name: str) -> str:
    return name.strip('"`[]')


def estimate_cost(path: Path, sql: str, table_rows: dict[str, int]) -> CostEstimate:
    rows_by_name = {k.lower(): v for k, v in table_rows.items()}
    est = CostEstimate()
    try:
        conn = open_readonly(path)
    except sqlite3.Error as e:
        est.error = str(e)
        return est
    try:
        plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    except sqlite3.Error as e:
        est.error = str(e)
        return est
    finally:
        conn.close()
    for row in plan:
        detail = str(row[-1])
        est.plan.append(detail)
        m = _SCAN.match(detail)
        auto = _AUTO.match(detail)
        if m and "CONSTANT ROW" not in detail.upper():
            table = _clean(m.group(1))
            n = rows_by_name.get(table.lower(), 0)
            est.scans.append({"table": table, "rows": n, "detail": detail})
            est.scanned_rows += n
        elif auto:
            table = _clean(auto.group(1))
            n = rows_by_name.get(table.lower(), 0)
            est.unindexed_join = True
            est.scans.append({"table": table, "rows": n, "detail": detail})
            est.scanned_rows += n
    return est
