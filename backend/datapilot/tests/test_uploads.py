"""'Your data' uploads: ingestion (types, sanitizing, limits, PII, untrusted SQLite) and the HTTP flow
(upload → list → schema → ask → delete) with owner checks, consent, per-role limits and scoped upload tokens."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from openpyxl import Workbook
from sqlalchemy import update

from datapilot.api import ratelimit
from datapilot.db.schema import user_datasets
from datapilot.db.session import run_sync
from datapilot.uploads import ingest, registry
from datapilot.uploads.ingest import IngestError, build_database, infer_column, sanitize_identifier, summarize_files

SALES = (
    b"Order ID,Order Date,Customer Email,Region,Amount,Discount\n"
    b'1001,2024-01-05,a@x.com,West,"$1,234.50",5%\n'
    b"1002,01/07/2024,b@y.org,East,$99.99,10%\n"
    b"1003,2024-02-11,c@z.net,West,$450.00,0%\n"
)


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Product", "Units", "Price"])
    ws.append(["Pen", 10, 1.5])
    ws.append(["Book", 3, 12.0])
    ws2 = wb.create_sheet("Stores")
    ws2.append(["Store", "City"])
    ws2.append(["S1", "Austin"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sqlite_with_trigger(tmp_path: Path) -> bytes:
    p = tmp_path / "evil.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    c.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b')")
    c.execute("CREATE VIEW v AS SELECT * FROM items")
    c.execute("CREATE TRIGGER t AFTER INSERT ON items BEGIN DELETE FROM items; END")
    c.commit()
    c.close()
    return p.read_bytes()


# ----------------------------------------------------------------------------- ingestion


def test_csv_types_money_percent_dates_and_pii(tmp_path):
    metas = build_database(summarize_files([("sales.csv", SALES)]), tmp_path / "x.sqlite")
    (t,) = metas
    types = {c.name: (c.type, c.pii) for c in t.columns}
    assert types == {
        "order_id": ("INTEGER", False),
        "order_date": ("DATE", False),
        "customer_email": ("TEXT", True),
        "region": ("TEXT", False),
        "amount": ("REAL", False),
        "discount": ("REAL", False),
    }
    rows = sqlite3.connect(tmp_path / "x.sqlite").execute("SELECT order_date, amount, discount FROM sales").fetchall()
    assert rows[0] == ("2024-01-05", 1234.5, 0.05) and rows[1][0] == "2024-01-07"


def test_xlsx_every_sheet_becomes_a_table(tmp_path):
    metas = build_database(summarize_files([("book.xlsx", _xlsx())]), tmp_path / "x.sqlite")
    assert [(m.name, m.rows) for m in metas] == [("orders", 2), ("stores", 1)]
    assert [c.type for c in metas[0].columns] == ["TEXT", "INTEGER", "REAL"]


def test_json_array_and_object_of_arrays(tmp_path):
    a = json.dumps([{"k": 1, "nested": {"x": 1}}, {"k": 2, "extra": "y"}]).encode()
    b = json.dumps({"people": [{"n": "a"}], "teams": [{"t": "x"}]}).encode()
    metas = build_database(summarize_files([("a.json", a), ("b.json", b)]), tmp_path / "x.sqlite")
    assert [m.name for m in metas] == ["a", "people", "teams"]
    assert [c.name for c in metas[0].columns] == ["k", "nested", "extra"]


def test_uploaded_sqlite_copies_tables_only(tmp_path):
    metas = build_database(summarize_files([("evil.sqlite", _sqlite_with_trigger(tmp_path))]), tmp_path / "x.sqlite")
    assert [m.name for m in metas] == ["items"]
    objs = sqlite3.connect(tmp_path / "x.sqlite").execute("SELECT type, name FROM sqlite_master").fetchall()
    assert objs == [("table", "items")]  # no view, no trigger


def test_hostile_identifiers_are_rebuilt():
    taken: set[str] = set()
    names = [sanitize_identifier(n, taken) for n in ['x"; DROP TABLE t;--', "select", "sqlite_master", "1st", "", "x"]]
    assert names == ["x_drop_table_t", "select_col", "sqlite_master_col", "col_1st", "col", "x"]
    assert all(n.isidentifier() for n in names)


@pytest.mark.parametrize(
    "name,data,match",
    [
        ("virus.exe", b"MZ", "unsupported"),
        ("empty.csv", b"", "empty"),
        ("fake.sqlite", b"hello", "not a SQLite"),
        ("bad.json", b"{nope", "not valid JSON"),
        ("scalar.json", b"[1, 2]", "array of objects"),
        ("old.xlsx", b"PK-but-not-a-zip", "not a valid .xlsx"),
    ],
)
def test_bad_files_are_rejected(name, data, match):
    with pytest.raises(IngestError, match=match):
        summarize_files([(name, data)])


def test_size_and_row_limits(monkeypatch, tmp_path):
    with pytest.raises(IngestError, match="larger than"):
        summarize_files([("big.csv", b"a\n" + b"1\n" * (ingest.MAX_FILE_BYTES // 2 + 1))])
    monkeypatch.setattr(ingest, "MAX_ROWS", 5)
    (t,) = summarize_files([("rows.csv", b"a\n" + b"1\n" * 20)])
    assert len(t.rows) == 5 and t.truncated


def test_type_inference_requires_every_value():
    assert infer_column(["1", "2", None])[0] == "INTEGER"
    assert infer_column(["1", "2.5"])[0] == "REAL"
    assert infer_column(["1", "two"])[0] == "TEXT"
    assert infer_column(["2024-01-01", "2024-02-30"])[0] == "TEXT"  # an impossible date keeps the column TEXT


# ----------------------------------------------------------------------------- HTTP flow


@pytest.fixture(scope="module")
async def client():
    from datapilot.api.main import app

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as c,
    ):
        yield c


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "CACHE_DIR", tmp_path / "uploads")
    registry._loaded.clear()
    ratelimit.reset_for_tests()
    run_sync(lambda c: c.execute(user_datasets.delete()))
    yield
    run_sync(lambda c: c.execute(user_datasets.delete()))


@pytest.fixture
def token(jwt_private_key):
    def make(sub: str = "github:alice", role: str = "user", **extra) -> dict:
        now = int(time.time())
        claims = {
            "sub": sub,
            "role": role,
            "name": "x",
            "iat": now,
            "exp": now + 300,
            "aud": "datapilot-api",
            "iss": "datapilot-web",
            **extra,
        }
        return {"Authorization": f"Bearer {jwt.encode(claims, jwt_private_key, algorithm='ES256')}"}

    return make


async def _upload(client, headers, files, consent=True, title="Sales"):
    return await client.post(
        "/v1/datasets",
        headers=headers,
        data={"title": title, "consent": "true" if consent else "false"},
        files=[("files", (n, d, "application/octet-stream")) for n, d in files],
    )


async def test_upload_list_schema_ask_delete(client, token):
    h = token()
    r = await _upload(client, h, [("sales.csv", SALES), ("book.xlsx", _xlsx())])
    assert r.status_code == 200, r.text
    ds = r.json()
    assert registry.is_upload_id(ds["db_id"]) and ds["tables"] == 3 and ds["rows"] == 6
    assert ds["pii_columns"] == ["sales.customer_email"] and len(ds["examples"]) >= 2

    listed = (await client.get("/v1/databases", headers=h)).json()
    assert [d["db_id"] for d in listed["uploaded"]] == [ds["db_id"]]
    assert all(d["db_id"] != ds["db_id"] for d in listed["databases"])  # demo list unchanged

    sch = (await client.get(f"/v1/databases/{ds['db_id']}/schema", headers=h)).json()
    email = next(
        c for t in sch["tables"] if t["name"] == "sales" for c in t["columns"] if c["name"] == "customer_email"
    )
    assert email["pii"] and all("•" in s for s in email["samples"])  # masked

    tid = (await client.post("/v1/threads", headers=h, json={"db_id": ds["db_id"]})).json()["thread_id"]
    resp = await client.post(f"/v1/threads/{tid}/ask", headers=h, json={"question": "How many sales rows are there?"})
    assert resp.status_code == 200 and "event: done" in resp.text and '"status":"success"' in resp.text

    sql = await client.post(
        "/v1/sql/execute",
        headers=h,
        json={"db_id": ds["db_id"], "sql": "SELECT region, SUM(amount) FROM sales GROUP BY region"},
    )
    assert sql.status_code == 200 and len(sql.json()["rows"]) == 2
    bad = await client.post("/v1/sql/execute", headers=h, json={"db_id": ds["db_id"], "sql": "DELETE FROM sales"})
    assert bad.status_code == 422

    assert (await client.delete(f"/v1/datasets/{ds['db_id']}", headers=h)).status_code == 200
    assert (await client.get(f"/v1/databases/{ds['db_id']}/schema", headers=h)).status_code == 404


async def test_datasets_are_private(client, token):
    ds = (await _upload(client, token("github:alice"), [("sales.csv", SALES)])).json()
    bob = token("github:bob")
    assert (await client.get(f"/v1/databases/{ds['db_id']}/schema", headers=bob)).status_code == 404
    assert (await client.post("/v1/threads", headers=bob, json={"db_id": ds["db_id"]})).status_code == 404
    assert (
        await client.post("/v1/sql/execute", headers=bob, json={"db_id": ds["db_id"], "sql": "SELECT 1"})
    ).status_code == 404
    assert (await client.delete(f"/v1/datasets/{ds['db_id']}", headers=bob)).status_code == 404
    assert (await client.get("/v1/datasets", headers=bob)).json()["datasets"] == []


async def test_consent_and_bad_files(client, token):
    h = token()
    assert (await _upload(client, h, [("sales.csv", SALES)], consent=False)).json()["error"][
        "code"
    ] == "consent_required"
    r = await _upload(client, h, [("notes.pdf", b"%PDF-1.4")])
    assert r.status_code == 422 and "unsupported" in r.json()["error"]["message"]


async def test_guest_can_keep_one_dataset(client, token):
    g = token("guest:abc", "guest")
    assert (await _upload(client, g, [("sales.csv", SALES)])).status_code == 200
    r = await _upload(client, g, [("sales.csv", SALES)])
    assert r.status_code == 422 and "sign in with GitHub" in r.json()["error"]["message"]


async def test_expired_dataset_is_gone(client, token):
    h = token()
    ds = (await _upload(client, h, [("sales.csv", SALES)])).json()
    run_sync(lambda c: c.execute(update(user_datasets).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))))
    registry._loaded.clear()
    assert (await client.get(f"/v1/databases/{ds['db_id']}/schema", headers=h)).status_code == 404


async def test_dataset_survives_a_wiped_disk(client, token):
    h = token()
    ds = (await _upload(client, h, [("sales.csv", SALES)])).json()
    path = registry._cache_path(ds["db_id"])
    path.chmod(0o644)
    path.unlink()  # free-tier restart wipes the local cache
    registry._loaded.clear()
    sch = await client.get(f"/v1/databases/{ds['db_id']}/schema", headers=h)
    assert sch.status_code == 200 and registry._cache_path(ds["db_id"]).exists()


async def test_upload_scoped_token_only_uploads(client, token):
    up = token(scope="upload")
    assert (await _upload(client, up, [("sales.csv", SALES)])).status_code == 200
    assert (await client.get("/v1/threads", headers=up)).status_code == 403
    assert (await client.get("/v1/databases", headers=up)).status_code == 403


async def test_anonymous_cannot_upload(client, token):
    r = await _upload(client, token("anon:public", "guest"), [("sales.csv", SALES)])
    assert r.status_code == 401
