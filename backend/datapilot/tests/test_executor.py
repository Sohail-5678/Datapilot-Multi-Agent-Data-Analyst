"""Read-only executor and EXPLAIN cost estimate (SPEC §10.1 layers 2–3, §4.3, §13.3).

The write attempts run against a throwaway, *writable* copy of chinook so that a regression could never
damage the demo database — and so the refusal is shown to come from the executor, not file permissions.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time

import pytest

from datapilot.sql.executor import ROW_CAP, execute_readonly, open_readonly
from datapilot.sql.explain import estimate_cost
from datapilot.sql.guard import guard_sql


@pytest.fixture(scope="module")
def chinook_copy(catalog, tmp_path_factory):
    dst = tmp_path_factory.mktemp("ro") / "chinook_copy.sqlite"
    shutil.copyfile(catalog["chinook"].path, dst)
    os.chmod(dst, 0o644)
    return dst


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ("Genre", "Track", "Customer")}
    finally:
        conn.close()


WRITES = [
    pytest.param("DELETE FROM Genre", id="delete"),
    pytest.param("DELETE FROM Track WHERE TrackId > 0", id="delete_where"),
    pytest.param("INSERT INTO Genre (Name) VALUES ('Polka')", id="insert"),
    pytest.param("REPLACE INTO Genre (GenreId, Name) VALUES (1, 'x')", id="replace"),
    pytest.param("UPDATE Track SET UnitPrice = 0", id="update"),
    pytest.param("DROP TABLE Genre", id="drop"),
    pytest.param("ALTER TABLE Genre ADD COLUMN pwned INTEGER", id="alter"),
    pytest.param("CREATE TABLE pwned (a INTEGER)", id="create_table"),
    pytest.param("CREATE INDEX ix_pwn ON Track (Name)", id="create_index"),
    pytest.param("CREATE TEMP TABLE t AS SELECT * FROM Customer", id="create_temp_table"),
    pytest.param("PRAGMA writable_schema = ON", id="pragma_writable_schema"),
    pytest.param("PRAGMA query_only = OFF", id="pragma_query_only_off"),
    pytest.param("PRAGMA journal_mode = DELETE", id="pragma_journal_mode"),
    pytest.param("SELECT load_extension('/tmp/evil')", id="load_extension"),
    pytest.param("SELECT 1; DELETE FROM Genre", id="multi_statement"),
    pytest.param("BEGIN", id="begin_transaction"),
]


@pytest.mark.parametrize("sql", WRITES)
def test_executor_refuses_writes_when_guard_is_bypassed(chinook_copy, sql):
    assert os.access(chinook_copy, os.W_OK)  # the file itself is writable: the executor must refuse
    before_hash, before_counts = _digest(chinook_copy), _counts(chinook_copy)
    res = execute_readonly(chinook_copy, sql)
    assert not res.ok
    assert res.error
    assert _digest(chinook_copy) == before_hash
    assert _counts(chinook_copy) == before_counts


def test_executor_refuses_attach_and_vacuum_into(chinook_copy, tmp_path):
    evil, copy = tmp_path / "evil.db", tmp_path / "copy.db"
    for sql in (f"ATTACH DATABASE '{evil}' AS evil", f"VACUUM INTO '{copy}'", "VACUUM", "DETACH DATABASE main"):
        res = execute_readonly(chinook_copy, sql)
        assert not res.ok, sql
    assert not evil.exists()
    assert not copy.exists()


def test_open_readonly_connection_rejects_writes_directly(chinook_copy):
    conn = open_readonly(chinook_copy)
    try:
        with pytest.raises(sqlite3.Error):
            conn.execute("INSERT INTO Genre (Name) VALUES ('x')")
        with pytest.raises(sqlite3.Error):  # the authorizer refuses every PRAGMA, even reads
            conn.execute("PRAGMA query_only")
        with pytest.raises(sqlite3.Error):
            conn.execute("PRAGMA query_only = OFF")
        assert conn.execute("SELECT COUNT(*) FROM Genre").fetchone()[0] == 25
    finally:
        conn.close()


def test_reads_still_work(chinook_copy):
    res = execute_readonly(chinook_copy, "SELECT GenreId, Name FROM Genre ORDER BY GenreId")
    assert res.ok, res.error
    assert res.columns == ["GenreId", "Name"]
    assert res.rows[0] == [1, "Rock"]
    assert res.row_count == 25 and not res.truncated


def test_timeout_aborts_runaway_query(catalog):
    sql = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"
    t0 = time.perf_counter()
    res = execute_readonly(catalog["chinook"].path, sql, timeout_s=0.5)
    elapsed = time.perf_counter() - t0
    assert res.timed_out
    assert not res.ok
    assert "timed out after 0.5 s" in (res.error or "")
    assert 0.4 < elapsed < 3.0


def test_row_cap_and_total_count(catalog):
    db = catalog["chinook"]
    g = guard_sql("SELECT * FROM PlaylistTrack", db.table_names)
    assert g.ok and g.unlimited_sql == "SELECT * FROM PlaylistTrack"
    res = execute_readonly(db.path, g.sql, count_sql=g.unlimited_sql)
    assert res.ok
    assert len(res.rows) == ROW_CAP == 1000
    assert res.total_rows == 8715 == db.tables["PlaylistTrack"].row_count
    assert res.row_count == 8715
    assert res.truncated


def test_row_cap_without_count_query(catalog):
    res = execute_readonly(catalog["chinook"].path, "SELECT * FROM PlaylistTrack")
    assert res.ok and len(res.rows) == 1000 and res.truncated
    assert res.total_rows is None  # unknown without a count query
    small = execute_readonly(
        catalog["chinook"].path, "SELECT * FROM Track", row_cap=10, count_sql="SELECT * FROM Track"
    )
    assert len(small.rows) == 10 and small.total_rows == 3503


def test_small_result_not_truncated(catalog):
    res = execute_readonly(
        catalog["chinook"].path, "SELECT Name FROM MediaType", count_sql="SELECT Name FROM MediaType"
    )
    assert res.ok and res.row_count == 5 and not res.truncated


def test_values_are_json_safe(catalog):
    res = execute_readonly(catalog["chinook"].path, "SELECT CAST(X'0102' AS BLOB) AS b, 1e999 AS inf, NULL AS n")
    assert res.ok
    assert res.rows == [["base64:AQI=", None, None]]


def test_sql_error_is_reported(catalog):
    res = execute_readonly(catalog["chinook"].path, "SELECT NoSuchColumn FROM Genre")
    assert not res.ok and "no such column" in (res.error or "")
    assert not res.timed_out


# ----------------------------------------------------------------------------- EXPLAIN estimate


def _rows(db) -> dict[str, int]:
    return {n: t.row_count for n, t in db.tables.items()}


def test_full_scan_of_player_attributes_needs_confirmation(catalog, settings):
    db = catalog["european_football_2"]
    assert db.tables["Player_Attributes"].row_count == 183_978
    est = estimate_cost(db.path, "SELECT AVG(overall_rating) FROM Player_Attributes", _rows(db))
    assert est.error is None
    assert est.scanned_rows == 183_978
    assert est.scanned_rows > settings.scan_confirm_rows == 100_000
    assert est.biggest_table == "Player_Attributes"
    assert est.seconds_hint().endswith(" s")


def test_aliased_full_scan_is_counted(catalog, settings):
    # SQLite names aliased tables by alias in the plan ("SCAN pa"); the estimate must still see 183,978 rows.
    db = catalog["european_football_2"]
    est = estimate_cost(
        db.path, "SELECT pa.preferred_foot, AVG(pa.overall_rating) FROM Player_Attributes pa GROUP BY 1", _rows(db)
    )
    assert est.scanned_rows == 183_978 > settings.scan_confirm_rows
    assert est.biggest_table == "Player_Attributes"


def test_primary_key_lookup_is_cheap(catalog, settings):
    db = catalog["european_football_2"]
    est = estimate_cost(db.path, "SELECT * FROM Player_Attributes WHERE id = 42", _rows(db))
    assert est.error is None
    assert est.scanned_rows == 0 < settings.scan_confirm_rows
    assert any("PRIMARY KEY" in p for p in est.plan)
    idx = estimate_cost(db.path, "SELECT player_name FROM Player WHERE player_api_id = 505942", _rows(db))
    assert idx.scanned_rows == 0


def test_unindexed_join_detected(catalog):
    db = catalog["chinook"]
    est = estimate_cost(db.path, "SELECT t.Name FROM Track t JOIN Customer c ON c.City = t.Composer", _rows(db))
    assert est.unindexed_join
    assert {s["table"] for s in est.scans} == {"Track", "Customer"}
    assert est.scanned_rows == 3503 + 59


def test_explain_error_is_reported(catalog):
    db = catalog["chinook"]
    est = estimate_cost(db.path, "SELECT nope FROM Genre", _rows(db))
    assert est.error and est.scanned_rows == 0
