"""SQL guard (SPEC §10.1) — the first of three read-only layers (parser → read-only connection → read-only files).

Rules: exactly one statement; root is SELECT / WITH … SELECT / a set operation of SELECTs; no DDL/DML,
PRAGMA, ATTACH, transactions or dangerous functions; tables must be in the database's allowlist; bounded
recursive CTEs only; no unconditioned cross joins over large tables; LIMIT ≤ 1000 on the outermost query.

Allowed SQL is returned unchanged except for the LIMIT (appended on its own line so a trailing line
comment can't swallow it). This module is a locked guardrail: nothing here comes from the agent profile.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

ROW_LIMIT = 1000
CROSS_JOIN_ROW_LIMIT = 10_000
RECURSIVE_DEPTH_LIMIT = 1000

FORBIDDEN_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
    "sqlite_compileoption_get",
    "sqlite_compileoption_used",
    "zeroblob",
    "randomblob",
    "sqlite_offset",
    "unlikely_sleep",
}
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, n, None)
        for n in (
            "Insert",
            "Update",
            "Delete",
            "Drop",
            "Create",
            "Alter",
            "AlterTable",
            "Command",
            "Pragma",
            "Attach",
            "Detach",
            "Transaction",
            "Commit",
            "Rollback",
            "Merge",
            "Use",
            "Set",
            "Copy",
            "Analyze",
            "TruncateTable",
            "LoadData",
            "Into",
        )
    )
    if isinstance(t, type)
)
# Belt and braces for statements sqlglot may parse loosely: keyword at a statement position.
STATEMENT_KEYWORDS = re.compile(
    r"(^|;)\s*(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace|reindex|begin|commit|rollback|savepoint|release|analyze)\b",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""
    tables: list[str] = field(default_factory=list)
    limited: bool = False  # LIMIT added or lowered
    unlimited_sql: str = ""  # original query without the added LIMIT (used for COUNT(*) when cheap)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "tables": self.tables, "limited": self.limited}


def _reject(reason: str) -> GuardResult:
    return GuardResult(False, reason=reason)


def _strip_trailing(sql: str) -> str:
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _blank_strings(sql: str) -> str:
    """Remove string literals and quoted identifiers so keyword scans don't trip on data."""
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]", "''", sql)


def guard_sql(sql: str, allowed_tables: list[str] | set[str], table_rows: dict[str, int] | None = None) -> GuardResult:
    if not isinstance(sql, str) or not sql.strip():
        return _reject("Empty query.")
    if len(sql) > 20_000:
        return _reject("Query is too long.")
    if "\x00" in sql:
        return _reject("Query contains a null byte.")
    normalized = unicodedata.normalize("NFKC", sql)
    if normalized != sql:
        # Full-width or compatibility characters can smuggle keywords past naive filters.
        return _reject("Query contains non-canonical Unicode characters; retype it in plain ASCII.")
    if any(unicodedata.category(ch) in ("Cf", "Co", "Cs") for ch in sql):
        return _reject("Query contains invisible or private-use Unicode characters.")

    body = _strip_trailing(sql)
    scan = _blank_strings(_strip_comments(body))
    if ";" in scan:
        return _reject("Only one statement is allowed.")
    if STATEMENT_KEYWORDS.search(scan):
        return _reject("Only SELECT queries are allowed.")

    try:
        statements = [s for s in sqlglot.parse(body, read="sqlite") if s is not None]
    except (ParseError, TokenError) as e:
        return _reject(f"Could not parse the SQL: {str(e).splitlines()[0][:200]}")
    if len(statements) != 1:
        return _reject("Only one statement is allowed.")
    root = statements[0]

    if not isinstance(root, exp.Select | exp.Union | exp.Intersect | exp.Except | exp.Subquery):
        return _reject(f"Only SELECT queries are allowed (got {root.key.upper()}).")
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject(f"{node.key.upper()} is not allowed.")
        if isinstance(node, exp.Func):
            name = (node.sql_name() if not isinstance(node, exp.Anonymous) else node.name).lower()
            if name in FORBIDDEN_FUNCTIONS:
                return _reject(f"Function {name}() is not allowed.")

    # Tables: everything referenced must be a real table in this DB (CTE names excepted).
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    allowed = {t.lower(): t for t in allowed_tables}
    used: list[str] = []
    for t in root.find_all(exp.Table):
        name = t.name
        if not name:
            return _reject("Table functions are not allowed.")
        if t.args.get("db") or t.args.get("catalog"):
            schema = (t.db or "").lower()
            if schema not in ("main", ""):
                return _reject(f"Schema '{t.db}' is not allowed.")
        low = name.lower()
        if low in cte_names:
            continue
        if low.startswith("sqlite_") or low not in allowed:
            return _reject(f"Unknown or disallowed table '{name}'.")
        if allowed[low] not in used:
            used.append(allowed[low])

    rec = _check_recursive(root)
    if rec:
        return _reject(rec)
    cross = _check_cross_joins(root, table_rows or {})
    if cross:
        return _reject(cross)

    # LIMIT on the outermost query
    limit_node = root.args.get("limit")
    if limit_node is None:
        return GuardResult(True, f"{body}\nLIMIT {ROW_LIMIT}", tables=used, limited=True, unlimited_sql=body)
    value = _int_literal(limit_node.expression if hasattr(limit_node, "expression") else None)
    if value is None or value > ROW_LIMIT:
        capped = root.copy()
        capped.set("limit", None)
        capped = capped.limit(ROW_LIMIT, copy=False) if hasattr(capped, "limit") else capped
        if root.args.get("offset") is not None:
            capped.set("offset", root.args["offset"].copy())
        return GuardResult(True, capped.sql(dialect="sqlite"), tables=used, limited=True, unlimited_sql="")
    return GuardResult(True, body, tables=used, limited=False, unlimited_sql="")


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def _check_recursive(root: exp.Expression) -> str | None:
    for with_ in root.find_all(exp.With):
        if not with_.args.get("recursive"):
            continue
        for cte in with_.expressions:
            inner = cte.this
            bounded = False
            for cmp in inner.find_all(exp.LT, exp.LTE):
                lit = _int_literal(cmp.expression)
                if lit is not None and lit <= RECURSIVE_DEPTH_LIMIT:
                    bounded = True
            if inner.args.get("limit") is not None:
                lit = _int_literal(inner.args["limit"].expression)
                bounded = bounded or (lit is not None and lit <= RECURSIVE_DEPTH_LIMIT)
            if not bounded:
                return f"Recursive CTEs need an explicit bound ≤ {RECURSIVE_DEPTH_LIMIT} (e.g. WHERE n < 100)."
    return None


def _check_cross_joins(root: exp.Expression, table_rows: dict[str, int]) -> str | None:
    rows = {k.lower(): v for k, v in table_rows.items()}
    for select in root.find_all(exp.Select):
        joins = select.args.get("joins") or []
        from_ = select.args.get("from") or select.args.get("from_")
        base = from_.this if from_ is not None else None
        for j in joins:
            unconditioned = not j.args.get("on") and not j.args.get("using")
            kind = (j.args.get("kind") or "").upper() if isinstance(j.args.get("kind"), str) else ""
            if not unconditioned or kind in ("NATURAL",) or j.args.get("method"):
                continue
            # Comma joins with a WHERE equality are common and fine; only reject if no condition references both.
            where = select.args.get("where")
            right = j.this
            if where is not None and isinstance(right, exp.Table) and _where_links(where, right):
                continue
            sizes = []
            for t in (base, right):
                if isinstance(t, exp.Table):
                    sizes.append(rows.get(t.name.lower(), 0))
            if sizes and max(sizes) > CROSS_JOIN_ROW_LIMIT:
                return "Cross joins without a join condition on large tables are not allowed."
    return None


def _where_links(where: exp.Expression, table: exp.Table) -> bool:
    alias = (table.alias_or_name or "").lower()
    for eq in where.find_all(exp.EQ):
        cols = [c for c in eq.find_all(exp.Column)]
        tables = {(c.table or "").lower() for c in cols}
        if len(cols) >= 2 and alias in tables and len(tables) >= 2:
            return True
    return False
