"""Demo database catalog: tables, columns, keys, BIRD descriptions, row counts, samples (SPEC §5.1, §5.3).

Everything here is read once from the read-only SQLite files at startup and cached in memory.
PII-tagged columns (catalog.json) are masked in samples and never sent to an LLM as sample values.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from datapilot.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False
    notnull: bool = False
    description: str = ""
    value_description: str = ""
    samples: list[str] = field(default_factory=list)
    pii: bool = False
    distinct: int | None = None

    @property
    def is_text(self) -> bool:
        t = self.type.upper()
        return not t or "CHAR" in t or "TEXT" in t or "CLOB" in t


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column]
    row_count: int
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    indexed_columns: set[str] = field(default_factory=set)
    description: str = ""

    def column(self, name: str) -> Column | None:
        low = name.lower()
        return next((c for c in self.columns if c.name.lower() == low), None)


@dataclass
class Database:
    db_id: str
    path: Path
    title: str
    subtitle: str
    description: str
    source: str
    source_url: str
    license: str
    examples: list[str]
    tables: dict[str, Table]
    schema_hash: str

    def table(self, name: str) -> Table | None:
        low = name.lower()
        return next((t for n, t in self.tables.items() if n.lower() == low), None)

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    @property
    def foreign_keys(self) -> list[ForeignKey]:
        return [fk for t in self.tables.values() for fk in t.foreign_keys]

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self.tables.values())


def ro_uri(path: Path) -> str:
    return f"file:{path}?mode=ro&immutable=1"


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ro_uri(path), uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _read_descriptions(folder: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """BIRD database_description/*.csv → {table_lower: {column_lower: (description, value_description)}}."""
    out: dict[str, dict[str, tuple[str, str]]] = {}
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.csv")):
        raw = f.read_bytes()
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        cols: dict[str, tuple[str, str]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("original_column_name") or "").strip()
            if not name:
                continue
            desc = " ".join((row.get("column_description") or "").split())
            friendly = (row.get("column_name") or "").strip()
            if friendly and friendly.lower() != name.lower():
                desc = f"{friendly}: {desc}" if desc else friendly
            value_desc = " ".join((row.get("value_description") or "").split())
            cols[name.lower()] = (desc[:300], value_desc[:300])
        out[f.stem.lower()] = cols
    return out


def load_database(db_id: str, path: Path, descriptions_dir: Path | None = None, meta: dict | None = None) -> Database:
    """Load any SQLite file (demo DBs, BIRD benchmark DBs, eval copies) into the catalog model."""
    meta = meta or {}
    descriptions = _read_descriptions(descriptions_dir) if descriptions_dir else {}
    pii = {t.lower(): {c.lower() for c in cols} for t, cols in (meta.get("pii") or {}).items()}
    conn = connect_ro(path)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        tables: dict[str, Table] = {}
        hasher = hashlib.sha256()
        for name in names:
            cols = []
            for _cid, cname, ctype, notnull, _default, pk in conn.execute(f"PRAGMA table_info({_qi(name)})"):
                d, vd = descriptions.get(name.lower(), {}).get(cname.lower(), ("", ""))
                cols.append(
                    Column(
                        name=cname,
                        type=ctype or "",
                        pk=bool(pk),
                        notnull=bool(notnull),
                        description=d,
                        value_description=vd,
                        pii=cname.lower() in pii.get(name.lower(), set()),
                    )
                )
                hasher.update(f"{name}.{cname}:{ctype}".encode())
            fks = [
                ForeignKey(name, r[3], r[2], r[4] or r[3])
                for r in conn.execute(f"PRAGMA foreign_key_list({_qi(name)})")
            ]
            indexed: set[str] = {c.name for c in cols if c.pk}
            for idx in conn.execute(f"PRAGMA index_list({_qi(name)})"):
                for info in conn.execute(f"PRAGMA index_info({_qi(idx[1])})"):
                    if info[0] == 0 and info[2]:  # leading column only
                        indexed.add(info[2])
            row_count = conn.execute(f"SELECT COUNT(*) FROM {_qi(name)}").fetchone()[0]
            table = Table(name, cols, row_count, fks, indexed)
            _fill_samples(conn, table)
            tables[name] = table
    finally:
        conn.close()
    return Database(
        db_id=db_id,
        path=path,
        title=meta.get("title", db_id),
        subtitle=meta.get("subtitle", ""),
        description=meta.get("description", ""),
        source=meta.get("source", ""),
        source_url=meta.get("source_url", ""),
        license=meta.get("license", ""),
        examples=list(meta.get("examples", [])),
        tables=tables,
        schema_hash=hasher.hexdigest()[:16],
    )


def _fill_samples(conn: sqlite3.Connection, table: Table) -> None:
    for col in table.columns:
        if col.pii:
            continue
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {_qi(col.name)} FROM {_qi(table.name)} "
                f"WHERE {_qi(col.name)} IS NOT NULL AND {_qi(col.name)} != '' LIMIT 3"
            ).fetchall()
        except sqlite3.Error:
            continue
        col.samples = [_short(r[0]) for r in rows]


def _short(v: object) -> str:
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "…"


@lru_cache
def load_catalog(dbs_dir: Path | None = None) -> dict[str, Database]:
    dbs_dir = dbs_dir or get_settings().dbs_dir
    meta_file = dbs_dir / "catalog.json"
    meta_all = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    out: dict[str, Database] = {}
    for db_id, meta in meta_all.items():
        path = dbs_dir / db_id / f"{db_id}.sqlite"
        if not path.exists():
            log.warning("demo database %s missing at %s (run scripts/fetch_dbs.py)", db_id, path)
            continue
        out[db_id] = load_database(db_id, path, dbs_dir / db_id / "descriptions", meta)
    return out


def get_database(db_id: str) -> Database | None:
    return load_catalog().get(db_id)
