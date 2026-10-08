"""Read-only SQLite executor (SPEC §10.1 layers 2–3).

Even if the guard were bypassed, this refuses writes: the connection is opened with
`mode=ro&immutable=1`, `PRAGMA query_only=ON`, and an authorizer that only allows reads.
The files themselves are read-only in the Docker image. A progress handler aborts queries after the
timeout; results are capped at 1,000 rows (the full count is computed separately when cheap).
"""

from __future__ import annotations

import base64
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datapilot.index.catalog import ro_uri

ROW_CAP = 1000
COUNT_TIMEOUT_S = 1.5

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total_rows: int | None = None
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    timed_out: bool = False

    @property
    def row_count(self) -> int:
        return self.total_rows if self.total_rows is not None else len(self.rows)

    def preview(self, n: int = 5) -> dict:
        return {"columns": self.columns, "rows": self.rows[:n], "row_count": self.row_count}


def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _json_safe(v: Any) -> Any:
    if isinstance(v, bytes):
        return "base64:" + base64.b64encode(v[:64]).decode()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ro_uri(path), uri=True, check_same_thread=False, timeout=1)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA cache_size = -16000")  # ≈16 MB page cache per connection
    conn.set_authorizer(_authorizer)
    return conn


def execute_readonly(
    path: Path,
    sql: str,
    *,
    timeout_s: float = 5.0,
    row_cap: int = ROW_CAP,
    count_sql: str | None = None,
) -> ExecResult:
    """Run one guarded SELECT. `count_sql` is the query without the added LIMIT (for the full row count)."""
    start = time.perf_counter()
    deadline = start + timeout_s
    timed_out = False

    def progress() -> int:
        nonlocal timed_out
        if time.perf_counter() > deadline:
            timed_out = True
            return 1
        return 0

    try:
        conn = open_readonly(path)
    except sqlite3.Error as e:
        return ExecResult(False, error=f"Could not open database: {e}")
    try:
        conn.set_progress_handler(progress, 2000)
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            raw = cur.fetchmany(row_cap + 1)
        except sqlite3.Error as e:
            ms = int((time.perf_counter() - start) * 1000)
            if timed_out:
                return ExecResult(
                    False, error=f"Query timed out after {timeout_s:g} s.", duration_ms=ms, timed_out=True
                )
            return ExecResult(False, error=f"{type(e).__name__}: {e}", duration_ms=ms)
        truncated = len(raw) > row_cap
        rows = [[_json_safe(v) for v in r] for r in raw[:row_cap]]
        total: int | None = len(rows)
        if count_sql and len(rows) >= row_cap:
            total = _count(conn, count_sql)
            truncated = True
        elif truncated:
            total = None
        return ExecResult(
            True,
            columns,
            rows,
            total_rows=total,
            truncated=truncated,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
    finally:
        conn.close()


def _count(conn: sqlite3.Connection, inner_sql: str) -> int | None:
    deadline = time.perf_counter() + COUNT_TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 2000)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM ({inner_sql})").fetchone()[0])
    except sqlite3.Error:
        return None
