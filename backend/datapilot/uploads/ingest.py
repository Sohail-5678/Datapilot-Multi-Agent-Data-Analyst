"""Turn uploaded files into one read-only SQLite database ("Your data").

Supported: CSV / TSV / TXT (delimiter sniffed), Excel .xlsx (every sheet → a table), JSON (array of objects,
or {"data": [...]}), and SQLite (.sqlite/.db — tables only; views and triggers are never copied).

Everything is treated as untrusted input:
- size/row/column/table limits are enforced while parsing (not after), xlsx zip sizes are checked first;
- identifiers are rebuilt from scratch (snake_case, deduplicated, never `sqlite_*`), so no name is ever trusted in SQL;
- uploaded SQLite files are opened read-only with trusted_schema=OFF and read with plain SELECTs;
- columns that look personal (emails, phones, names, addresses…) are tagged PII: masked in the UI, never sent to a
  model as samples or value links (SPEC §10.4).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 120 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLS = 100
MAX_TABLES = 12
MAX_CELL_CHARS = 2000
SQLITE_MAGIC = b"SQLite format 3\x00"
EXTENSIONS = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "xlsx",
    ".json": "json",
    ".sqlite": "sqlite",
    ".db": "sqlite",
    ".sqlite3": "sqlite",
}

_PII_NAME = re.compile(
    r"(e[-_ ]?mail|phone|mobile|cell|fax|ssn|social[-_ ]?sec|passport|license|address|street|postal|zip|"
    r"birth|dob|first[-_ ]?name|last[-_ ]?name|full[-_ ]?name|surname|given[-_ ]?name|^name$|customer[-_ ]?name|"
    r"contact|iban|account[-_ ]?(no|num)|card[-_ ]?(no|num)|tax[-_ ]?id|national[-_ ]?id|ip[-_ ]?addr)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
_PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")
_INT = re.compile(r"^[-+]?\d{1,18}$")
_NUM = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_MONEY = re.compile(r"^[-+]?[$€£¥₹]?\s?\d{1,3}(,\d{3})*(\.\d+)?%?$|^[-+]?[$€£¥₹]?\s?\d+(\.\d+)?%?$")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%b %d %Y",
    "%d %b %Y",
    "%Y-%m",
)
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%Y-%m-%dT%H:%M:%SZ")
RESERVED = {
    "select",
    "from",
    "where",
    "group",
    "order",
    "by",
    "table",
    "index",
    "join",
    "on",
    "limit",
    "offset",
    "union",
    "values",
    "insert",
    "update",
    "delete",
    "drop",
    "create",
    "alter",
    "as",
    "and",
    "or",
    "not",
    "null",
    "is",
    "in",
    "case",
    "when",
    "then",
    "else",
    "end",
    "having",
    "distinct",
    "all",
    "key",
    "primary",
    "default",
    "check",
    "references",
}


class IngestError(ValueError):
    """A user-facing problem with an uploaded file."""


@dataclass
class ParsedTable:
    name: str  # original (sheet / file) name
    columns: list[str]
    rows: list[list[Any]]
    source: str
    truncated: bool = False


@dataclass
class ColumnMeta:
    name: str
    original: str
    type: str
    pii: bool
    nulls: int = 0


@dataclass
class TableMeta:
    name: str
    original: str
    source: str
    rows: int
    columns: list[ColumnMeta] = field(default_factory=list)
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "original": self.original,
            "source": self.source,
            "rows": self.rows,
            "truncated": self.truncated,
            "columns": [c.__dict__ for c in self.columns],
        }


# ----------------------------------------------------------------------------- identifiers


def sanitize_identifier(raw: Any, taken: set[str], fallback: str = "col") -> str:
    s = str(raw or "").strip().lower()
    s = re.sub(r"[^\w]+", "_", s, flags=re.ASCII)
    s = re.sub(r"_+", "_", s).strip("_")[:60] or fallback
    if s[0].isdigit():
        s = f"{fallback}_{s}"
    if s in RESERVED or s.startswith("sqlite_"):
        s = f"{s}_{fallback}"
    base, n = s, 2
    while s in taken:
        s = f"{base}_{n}"
        n += 1
    taken.add(s)
    return s


# ----------------------------------------------------------------------------- parsers


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 with a byte-order mark (Excel "Unicode text")
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _cell(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v[:MAX_CELL_CHARS] if v else None
    return v


def parse_csv(name: str, data: bytes) -> list[ParsedTable]:
    text = _decode(data)
    sample = text[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    header: list[str] | None = None
    rows: list[list[Any]] = []
    truncated = False
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue
        if header is None:
            header = raw[:MAX_COLS]
            continue
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        row = [_cell(c) for c in raw[: len(header)]]
        rows.append(row + [None] * (len(header) - len(row)))
    if not header:
        raise IngestError(f"{name}: the file is empty or has no header row.")
    return [ParsedTable(Path(name).stem, header, rows, name, truncated)]


def parse_xlsx(name: str, data: bytes) -> list[ParsedTable]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNCOMPRESSED:
                raise IngestError(f"{name}: the workbook is too large once unpacked.")
    except zipfile.BadZipFile as e:
        raise IngestError(
            f"{name}: not a valid .xlsx file (old .xls files aren't supported; save as .xlsx or CSV)."
        ) from e
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — openpyxl raises many types for corrupt files
        raise IngestError(f"{name}: couldn't read the workbook ({type(e).__name__}).") from e
    out = []
    try:
        for ws in wb.worksheets:
            header: list[str] | None = None
            rows: list[list[Any]] = []
            truncated = False
            for raw in ws.iter_rows(values_only=True):
                vals = list(raw)
                if not any(v not in (None, "") for v in vals):
                    continue
                if header is None:
                    while vals and vals[-1] in (None, ""):
                        vals.pop()
                    header = [
                        str(v) if v not in (None, "") else f"column_{i + 1}" for i, v in enumerate(vals[:MAX_COLS])
                    ]
                    continue
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                row = [_cell(v) for v in vals[: len(header)]]
                rows.append(row + [None] * (len(header) - len(row)))
            if header:
                out.append(ParsedTable(ws.title, header, rows, f"{name} › {ws.title}", truncated))
    finally:
        wb.close()
    if not out:
        raise IngestError(f"{name}: no sheet with a header row was found.")
    return out


def parse_json(name: str, data: bytes) -> list[ParsedTable]:
    try:
        obj = json.loads(_decode(data))
    except json.JSONDecodeError as e:
        raise IngestError(f"{name}: not valid JSON ({e.msg} at line {e.lineno}).") from e
    if isinstance(obj, dict):
        lists = {k: v for k, v in obj.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)}
        if not lists:
            raise IngestError(
                f"{name}: expected an array of objects (or an object whose values are arrays of objects)."
            )
        items = list(lists.items())
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        items = [(Path(name).stem, obj)]
    else:
        raise IngestError(f'{name}: expected an array of objects like [{{"a": 1}}, …].')
    out = []
    for tname, records in items:
        cols: list[str] = []
        for r in records[:2000]:
            for k in r:
                if k not in cols and len(cols) < MAX_COLS:
                    cols.append(k)
        rows = []
        for r in records[:MAX_ROWS]:
            rows.append([_cell(json.dumps(r.get(c)) if isinstance(r.get(c), dict | list) else r.get(c)) for c in cols])
        out.append(ParsedTable(str(tname), [str(c) for c in cols], rows, name, len(records) > MAX_ROWS))
    return out


def parse_sqlite(name: str, data: bytes) -> list[ParsedTable]:
    if not data.startswith(SQLITE_MAGIC):
        raise IngestError(f"{name}: not a SQLite database file.")
    out: list[ParsedTable] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.sqlite"
        path.write_bytes(data)
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA cell_size_check = ON")
            conn.execute("PRAGMA query_only = ON")
            tables = [
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            ]
            for t in tables[:MAX_TABLES]:
                qt = '"' + t.replace('"', '""') + '"'
                cur = conn.execute(f"SELECT * FROM {qt} LIMIT {MAX_ROWS + 1}")
                cols = [d[0] for d in cur.description][:MAX_COLS]
                raw = cur.fetchall()
                rows = [
                    [_cell(v if not isinstance(v, bytes) else None) for v in r[: len(cols)]] for r in raw[:MAX_ROWS]
                ]
                out.append(ParsedTable(t, cols, rows, f"{name} › {t}", len(raw) > MAX_ROWS))
        except sqlite3.DatabaseError as e:
            raise IngestError(f"{name}: couldn't read the SQLite file ({e}).") from e
        finally:
            conn.close()
    if not out:
        raise IngestError(f"{name}: the database has no tables.")
    return out


def parse_file(name: str, data: bytes) -> list[ParsedTable]:
    ext = Path(name.lower()).suffix
    kind = EXTENSIONS.get(ext)
    if kind is None:
        raise IngestError(f"{name}: unsupported file type. Use CSV, TSV, Excel (.xlsx), JSON or SQLite.")
    if len(data) > MAX_FILE_BYTES:
        raise IngestError(f"{name}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise IngestError(f"{name}: the file is empty.")
    return {"csv": parse_csv, "xlsx": parse_xlsx, "json": parse_json, "sqlite": parse_sqlite}[kind](name, data)


# ----------------------------------------------------------------------------- typing


def _as_number(v: Any) -> int | float | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int | float):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if _INT.match(s):
        return int(s)
    if _NUM.match(s):
        return float(s)
    if _MONEY.match(s):
        pct = s.endswith("%")
        n = float(re.sub(r"[^\d.\-+]", "", s))
        return n / 100 if pct else n
    return None


def _as_date(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") if (v.hour or v.minute or v.second) else v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if not isinstance(v, str) or not (6 <= len(v) <= 25) or not re.search(r"\d", v):
        return None
    s = v.strip()
    for f in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    for f in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, f)
            return d.strftime("%Y-%m") if f == "%Y-%m" else d.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def infer_column(values: list[Any]) -> tuple[str, list[Any]]:
    """Return (SQLite type, converted values). A type wins only if *every* non-empty value fits it."""
    present = [v for v in values if v is not None and v != ""]
    if not present:
        return "TEXT", [None] * len(values)
    nums = [_as_number(v) for v in present]
    if all(n is not None for n in nums):
        all_int = all(isinstance(n, int) for n in nums)
        conv = [None if v is None or v == "" else _as_number(v) for v in values]
        return ("INTEGER" if all_int else "REAL"), conv
    dates = [_as_date(v) for v in present]
    if all(d is not None for d in dates):
        return "DATE", [None if v is None or v == "" else _as_date(v) for v in values]
    return "TEXT", [None if v is None or v == "" else (v if isinstance(v, str) else str(v)) for v in values]


def looks_pii(column: str, values: list[Any]) -> bool:
    if _PII_NAME.search(column):
        return True
    sample = [str(v) for v in values[:500] if isinstance(v, str) and v]
    if len(sample) >= 5:
        emails = sum(1 for v in sample if _EMAIL.match(v))
        phones = sum(1 for v in sample if _PHONE.match(v) and sum(ch.isdigit() for ch in v) >= 9)
        if emails / len(sample) > 0.5 or phones / len(sample) > 0.5:
            return True
    return False


# ----------------------------------------------------------------------------- build


def build_database(tables: list[ParsedTable], path: Path) -> list[TableMeta]:
    if not tables:
        raise IngestError("No tables found in the uploaded files.")
    if len(tables) > MAX_TABLES:
        raise IngestError(f"Too many tables ({len(tables)}); the limit is {MAX_TABLES} per dataset.")
    taken_tables: set[str] = set()
    metas: list[TableMeta] = []
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        for t in tables:
            if not t.columns:
                continue
            tname = sanitize_identifier(t.name, taken_tables, "table")
            taken_cols: set[str] = set()
            cols: list[ColumnMeta] = []
            converted: list[list[Any]] = []
            for i, c in enumerate(t.columns[:MAX_COLS]):
                cname = sanitize_identifier(c, taken_cols, "col")
                values = [r[i] if i < len(r) else None for r in t.rows]
                ctype, conv = infer_column(values)
                cols.append(
                    ColumnMeta(cname, str(c), ctype, looks_pii(f"{c} {cname}", conv), sum(v is None for v in conv))
                )
                converted.append(conv)
            ddl = ", ".join(f'"{c.name}" {"TEXT" if c.type == "DATE" else c.type}' for c in cols)
            conn.execute(f'CREATE TABLE "{tname}" ({ddl})')
            placeholders = ", ".join("?" for _ in cols)
            rows = list(zip(*converted, strict=True)) if converted else []
            conn.executemany(f'INSERT INTO "{tname}" VALUES ({placeholders})', rows)
            metas.append(TableMeta(tname, t.name, t.source, len(rows), cols, t.truncated))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    if not metas:
        raise IngestError("None of the files contained a usable table.")
    return metas


def summarize_files(files: list[tuple[str, bytes]]) -> list[ParsedTable]:
    total = sum(len(d) for _, d in files)
    if not files:
        raise IngestError("Choose at least one file.")
    if total > MAX_TOTAL_BYTES:
        raise IngestError(
            f"Files total {total / 1e6:.1f} MB; the limit is {MAX_TOTAL_BYTES // (1024 * 1024)} MB per dataset."
        )
    tables: list[ParsedTable] = []
    for name, data in files:
        tables.extend(parse_file(name, data))
    return tables
