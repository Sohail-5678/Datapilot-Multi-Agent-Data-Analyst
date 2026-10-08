"""SQL guard (SPEC §10.1, §13.3): 60 malicious/edge statements are rejected; 60 valid SELECTs over the real
demo schemas are allowed and returned unchanged except for the LIMIT line."""

from __future__ import annotations

import pytest
import sqlglot

from datapilot.sql.executor import execute_readonly
from datapilot.sql.guard import ROW_LIMIT, guard_sql


def _guard(catalog, db_id: str, sql: str):
    db = catalog[db_id]
    return guard_sql(sql, db.table_names, {n: t.row_count for n, t in db.tables.items()})


C, S, SC, F1, EF = "chinook", "superhero", "student_club", "formula_1", "european_football_2"

MALICIOUS = [
    # DDL / DML
    pytest.param(C, "DROP TABLE Track", id="drop_table"),
    pytest.param(C, "DROP TABLE IF EXISTS Track", id="drop_table_if_exists"),
    pytest.param(C, "DELETE FROM Track", id="delete"),
    pytest.param(C, "INSERT INTO Genre (Name) VALUES ('Polka')", id="insert"),
    pytest.param(C, "INSERT INTO Genre SELECT * FROM Genre", id="insert_select"),
    pytest.param(C, "UPDATE Track SET UnitPrice = 0", id="update"),
    pytest.param(C, "ALTER TABLE Track ADD COLUMN pwned INTEGER", id="alter_add_column"),
    pytest.param(C, "CREATE TABLE copy AS SELECT * FROM Customer", id="create_table_as"),
    pytest.param(C, "CREATE INDEX ix_pwn ON Track (Name)", id="create_index"),
    pytest.param(C, "CREATE VIEW v AS SELECT * FROM Track", id="create_view"),
    pytest.param(C, "CREATE TRIGGER t AFTER INSERT ON Genre BEGIN DELETE FROM Track; END", id="create_trigger"),
    pytest.param(C, "CREATE TEMP TABLE t AS SELECT * FROM Customer", id="create_temp_table"),
    pytest.param(C, "REPLACE INTO Genre (GenreId, Name) VALUES (1, 'x')", id="replace_into"),
    pytest.param(C, "INSERT OR REPLACE INTO Genre VALUES (1, 'x')", id="insert_or_replace"),
    pytest.param(C, "DeLeTe FrOm Genre", id="mixed_case_delete"),
    pytest.param(C, "  \n\t DROP TABLE Genre", id="leading_whitespace_drop"),
    # PRAGMA / ATTACH / DETACH / VACUUM / maintenance
    pytest.param(C, "PRAGMA table_info(Track)", id="pragma_table_info"),
    pytest.param(C, "PRAGMA writable_schema = ON", id="pragma_writable_schema"),
    pytest.param(C, "pragma query_only = off", id="pragma_query_only_off"),
    pytest.param(C, "SELECT * FROM pragma_table_info('Track')", id="pragma_table_valued_function"),
    pytest.param(C, "ATTACH DATABASE '/tmp/evil.db' AS evil", id="attach"),
    pytest.param(C, "DETACH DATABASE evil", id="detach"),
    pytest.param(C, "VACUUM", id="vacuum"),
    pytest.param(C, "VACUUM INTO '/tmp/copy.db'", id="vacuum_into"),
    pytest.param(C, "REINDEX Track", id="reindex"),
    pytest.param(C, "ANALYZE", id="analyze"),
    pytest.param(C, "EXPLAIN SELECT * FROM Genre", id="explain"),
    # transactions
    pytest.param(C, "BEGIN TRANSACTION", id="begin"),
    pytest.param(C, "COMMIT", id="commit"),
    pytest.param(C, "ROLLBACK", id="rollback"),
    pytest.param(C, "SAVEPOINT s1", id="savepoint"),
    # multi-statement and comment tricks
    pytest.param(C, "SELECT 1; DROP TABLE Track", id="stacked_drop"),
    pytest.param(C, "SELECT * FROM Track; SELECT * FROM Album", id="two_selects"),
    pytest.param(C, "SELECT * FROM Genre;; DELETE FROM Genre", id="double_semicolon"),
    pytest.param(C, "SELECT * FROM Genre -- harmless\n; DROP TABLE Genre", id="line_comment_then_statement"),
    pytest.param(C, "SELECT * FROM Genre /* note */; DELETE FROM Genre", id="block_comment_then_statement"),
    pytest.param(C, "/* SELECT */ DELETE FROM Genre", id="comment_disguises_delete"),
    pytest.param(C, "-- SELECT * FROM Genre\nDELETE FROM Genre", id="line_comment_disguises_delete"),
    pytest.param(C, "SELECT * FROM Genre WHERE Name = '--'; DROP TABLE Genre", id="dashes_in_string_then_drop"),
    pytest.param(C, "SELECT '/*', 1; DROP TABLE Genre --*/", id="string_opens_block_comment"),
    # unicode tricks
    pytest.param(C, "ＤＲＯＰ TABLE Genre", id="fullwidth_keyword"),
    pytest.param(C, "SELECT * FROM Genre； DROP TABLE Genre", id="fullwidth_semicolon"),
    pytest.param(C, "SELECT * FROM Genre; DROP TABLE Genre", id="greek_question_mark_semicolon"),
    pytest.param(C, "DR​OP TABLE Genre", id="zero_width_space_in_keyword"),
    pytest.param(C, "SELECT * FROM Genre\x00; DROP TABLE Genre", id="null_byte"),
    # dangerous functions
    pytest.param(C, "SELECT load_extension('/tmp/evil.so')", id="load_extension"),
    pytest.param(C, "SELECT readfile('/etc/passwd')", id="readfile"),
    pytest.param(C, "SELECT writefile('/tmp/x', Name) FROM Genre", id="writefile"),
    pytest.param(C, "SELECT Name FROM Genre WHERE LOAD_EXTENSION('x') IS NULL", id="load_extension_in_where"),
    # system tables and other schemas
    pytest.param(C, "SELECT * FROM sqlite_master", id="sqlite_master"),
    pytest.param(C, "SELECT sql FROM sqlite_schema", id="sqlite_schema"),
    pytest.param(C, "SELECT * FROM main.sqlite_master", id="main_sqlite_master"),
    pytest.param(C, "SELECT Name, 1 FROM Genre UNION SELECT name, sql FROM sqlite_master", id="union_sqlite_master"),
    pytest.param(C, "SELECT * FROM evil.Track", id="other_schema"),
    # unbounded recursion
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r",
        id="unbounded_recursive_cte",
    ),
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 10 OR n > 0) SELECT count(*) FROM r",
        id="recursive_bound_defeated_by_or",
    ),
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 WHERE 1 < 2 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r",
        id="recursive_bound_only_in_anchor",
    ),
    # unconditioned cross joins of big tables
    pytest.param(F1, "SELECT COUNT(*) FROM lapTimes CROSS JOIN results", id="cross_join_big_tables"),
    pytest.param(F1, "SELECT * FROM lapTimes JOIN results", id="join_without_on_big_tables"),
    pytest.param(F1, "SELECT * FROM lapTimes l, results r WHERE l.raceId = 5", id="comma_join_where_not_linking"),
]

EXTRA_REJECTED = [
    pytest.param(C, "", id="empty"),
    pytest.param(C, " ; ; ", id="only_semicolons"),
    pytest.param(C, "SELECT " + "1 + " * 6000 + "1", id="too_long"),
    pytest.param(C, "please drop the track table", id="natural_language"),
    pytest.param(C, "CREATE TABLE pwned (a INTEGER)", id="create_table"),
    pytest.param(C, "ALTER TABLE Track RENAME TO Track_old", id="alter_rename"),
    pytest.param(
        C,
        "INSERT INTO Genre (GenreId, Name) VALUES (1, 'x') ON CONFLICT(GenreId) DO UPDATE SET Name = 'y'",
        id="upsert",
    ),
    pytest.param(C, "RELEASE SAVEPOINT s1", id="release_savepoint"),
    pytest.param(C, "ATTACH '/tmp/evil.db' AS evil", id="attach_without_database_keyword"),
    pytest.param(C, "SELECT * FROM Genre /* ; */ ; DROP TABLE Genre", id="semicolon_inside_block_comment"),
    pytest.param(C, "SELECT/**/1;/**/DROP/**/TABLE/**/Genre", id="comments_as_whitespace"),
    pytest.param(C, "SELECT * FROM Genre", id="no_break_spaces"),
    pytest.param(C, "DEL‍ETE FROM Genre", id="zero_width_joiner_in_keyword"),
    pytest.param(C, "SELECT Name FROM Genre WHERE Name = '‮abc'", id="bidi_override"),
    pytest.param(C, "SELECT zeroblob(1000000000)", id="zeroblob"),
    pytest.param(C, "SELECT hex(randomblob(500000000))", id="randomblob"),
    pytest.param(C, "SELECT edit('x', 'vi')", id="edit"),
    pytest.param(C, "SELECT * FROM sqlite_temp_master", id="sqlite_temp_master"),
    pytest.param(C, 'SELECT * FROM "sqlite_master"', id="quoted_sqlite_master"),
    pytest.param(C, "SELECT (SELECT sql FROM sqlite_master LIMIT 1) AS s", id="scalar_subquery_sqlite_master"),
    pytest.param(C, "WITH t AS (SELECT * FROM sqlite_master) SELECT * FROM t", id="cte_over_sqlite_master"),
    pytest.param(C, "SELECT * FROM temp.Genre", id="temp_schema"),
    pytest.param(C, "SELECT * FROM secret_tokens", id="unknown_table"),
    pytest.param(C, "SELECT * FROM superhero", id="table_from_another_database"),
    pytest.param(C, "SELECT * FROM json_each('[1,2,3]')", id="json_each_table_function"),
    pytest.param(C, "SELECT * FROM generate_series(1, 1000000000)", id="generate_series"),
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 100000000) SELECT count(*) FROM r",
        id="recursive_bound_too_high",
    ),
    pytest.param(
        C,
        "SELECT * FROM Genre WHERE GenreId IN (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r)",
        id="unbounded_recursion_in_subquery",
    ),
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n + 0 < 10 OR 1) SELECT count(*) FROM r",
        id="recursive_bound_or_true",
    ),
    pytest.param(
        C,
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1, n < 5 FROM r) SELECT count(*) FROM r",
        id="recursive_comparison_in_select_list",
    ),
    pytest.param(EF, "SELECT COUNT(*) FROM Player_Attributes a, Player_Attributes b", id="comma_self_join_big_table"),
    pytest.param(F1, "SELECT * FROM lapTimes l LEFT JOIN results r ON 1 = 1", id="join_on_constant"),
    pytest.param(F1, "SELECT * FROM lapTimes l JOIN results r ON TRUE", id="join_on_true"),
    pytest.param(
        F1,
        "SELECT * FROM lapTimes l, results r WHERE l.raceId = r.raceId OR 1 = 1",
        id="comma_join_link_defeated_by_or",
    ),
    pytest.param(C, "SELECT * INTO backup FROM Genre", id="select_into"),
    pytest.param(C, "WITH x AS (DELETE FROM Genre RETURNING *) SELECT * FROM x", id="cte_with_delete"),
]


@pytest.mark.parametrize(("db_id", "sql"), MALICIOUS)
def test_malicious_rejected(catalog, db_id, sql):
    res = _guard(catalog, db_id, sql)
    assert not res.ok, f"guard allowed: {sql!r} -> {res.sql!r}"
    assert res.reason
    assert res.sql == ""


@pytest.mark.parametrize(("db_id", "sql"), EXTRA_REJECTED)
def test_more_edge_cases_rejected(catalog, db_id, sql):
    res = _guard(catalog, db_id, sql)
    assert not res.ok, f"guard allowed: {sql!r} -> {res.sql!r}"
    assert res.reason


def test_malicious_list_size():
    assert len(MALICIOUS) == 60


# mode: "append" → no LIMIT, guard adds "\nLIMIT 1000"; "same" → LIMIT ≤ 1000 kept as is; "cap" → LIMIT > 1000 lowered.
VALID = [
    # chinook
    pytest.param(C, "SELECT Name FROM Genre", "append", id="c_simple"),
    pytest.param(C, "SELECT Name FROM Genre;", "append", id="c_trailing_semicolon"),
    pytest.param(C, "select name from genre order by name", "append", id="c_lowercase_names"),
    pytest.param(
        C,
        "SELECT ar.Name AS artist, COUNT(*) AS tracks FROM Track t JOIN Album al ON al.AlbumId = t.AlbumId JOIN Artist ar ON ar.ArtistId = al.ArtistId GROUP BY ar.Name ORDER BY tracks DESC LIMIT 10",
        "same",
        id="c_artist_tracks_top10",
    ),
    pytest.param(
        C,
        """WITH g AS (
  SELECT ge.Name AS genre,
         SUM(CASE WHEN strftime('%Y', i.InvoiceDate) = '2012' THEN il.UnitPrice * il.Quantity ELSE 0 END) AS revenue_2012,
         SUM(CASE WHEN strftime('%Y', i.InvoiceDate) = '2011' THEN il.UnitPrice * il.Quantity ELSE 0 END) AS revenue_2011
  FROM InvoiceLine il
  JOIN Invoice i ON i.InvoiceId = il.InvoiceId
  JOIN Track t ON t.TrackId = il.TrackId
  JOIN Genre ge ON ge.GenreId = t.GenreId
  GROUP BY ge.Name
)
SELECT genre, ROUND(revenue_2012, 2) AS revenue_2012, ROUND(revenue_2011, 2) AS revenue_2011,
       ROUND(100.0 * (revenue_2012 - revenue_2011) / revenue_2011, 1) AS pct_change
FROM g ORDER BY revenue_2012 DESC LIMIT 5""",
        "same",
        id="c_cte_revenue_by_genre",
    ),
    pytest.param(
        C,
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total), 2) AS sales FROM Invoice WHERE strftime('%Y', InvoiceDate) = '2013' GROUP BY month ORDER BY month",
        "append",
        id="c_monthly_sales",
    ),
    pytest.param(
        C,
        "SELECT BillingCountry, COUNT(*) AS invoices FROM Invoice GROUP BY BillingCountry HAVING COUNT(*) > 10",
        "append",
        id="c_having",
    ),
    pytest.param(
        C,
        "SELECT Name, Milliseconds, RANK() OVER (ORDER BY Milliseconds DESC) AS rnk FROM Track LIMIT 1000",
        "same",
        id="c_window_limit_1000",
    ),
    pytest.param(C, "SELECT * FROM Track WHERE Name LIKE '%love%' LIMIT 1001", "cap", id="c_limit_1001_capped"),
    pytest.param(C, "SELECT * FROM PlaylistTrack LIMIT 5000 OFFSET 100", "cap", id="c_limit_offset_capped"),
    pytest.param(
        C, "SELECT Name FROM Track WHERE Name = 'Delete Me; Drop Table Track'", "append", id="c_sql_keywords_in_string"
    ),
    pytest.param(C, "SELECT Name FROM Track WHERE Composer LIKE '%--%'", "append", id="c_dashes_in_string"),
    pytest.param(
        C, "SELECT Name -- the genre name\nFROM Genre -- trailing comment", "append", id="c_trailing_line_comment"
    ),
    pytest.param(
        C, "SELECT Name /* the name */ FROM Genre WHERE Name <> '/* not a comment */'", "append", id="c_block_comments"
    ),
    pytest.param(C, "SELECT g.Name, m.Name FROM Genre g CROSS JOIN MediaType m", "append", id="c_small_cross_join"),
    pytest.param(
        C,
        'SELECT "Name" FROM "Artist" WHERE "ArtistId" IN (SELECT ArtistId FROM Album GROUP BY ArtistId HAVING COUNT(*) > 3)',
        "append",
        id="c_quoted_identifiers_in_subquery",
    ),
    pytest.param(
        C,
        "SELECT e.FirstName, m.FirstName AS manager FROM Employee e LEFT JOIN Employee m ON m.EmployeeId = e.ReportsTo",
        "append",
        id="c_self_join",
    ),
    pytest.param(
        C, "SELECT Name FROM Artist UNION SELECT Name FROM Genre ORDER BY Name", "append", id="c_union_order_by"
    ),
    pytest.param(C, "SELECT Name FROM Artist EXCEPT SELECT Composer FROM Track", "append", id="c_except"),
    pytest.param(
        C,
        "SELECT CASE WHEN UnitPrice > 1 THEN 'video' ELSE 'audio' END AS kind, COUNT(*) FROM Track GROUP BY kind",
        "append",
        id="c_case_when",
    ),
    pytest.param(C, "SELECT Name FROM main.Genre WHERE GenreId BETWEEN 1 AND 5", "append", id="c_main_schema"),
    pytest.param(C, "SELECT REPLACE(Name, ' ', '_') AS slug FROM Genre", "append", id="c_replace_function"),
    pytest.param(
        C,
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 12) SELECT x AS month FROM n",
        "append",
        id="c_bounded_recursive_cte",
    ),
    pytest.param(
        C,
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE 12 > x AND x IS NOT NULL) SELECT x FROM n",
        "append",
        id="c_bounded_recursive_cte_reversed",
    ),
    pytest.param(
        C,
        "SELECT t.Name, g.Name FROM Track t, Genre g WHERE t.GenreId = g.GenreId AND g.Name = 'Jazz'",
        "append",
        id="c_comma_join_linked",
    ),
    pytest.param(C, "SELECT 1 + 1 AS two", "append", id="c_no_table"),
    pytest.param(
        C, "SELECT COUNT(DISTINCT CustomerId) FROM Invoice WHERE Total >= 10;", "append", id="c_count_distinct"
    ),
    pytest.param(C, "SELECT `Name` FROM `MediaType`", "append", id="c_backticks"),
    pytest.param(C, "SELECT [Name] FROM [Playlist]", "append", id="c_brackets"),
    pytest.param(
        C,
        "SELECT Title, (SELECT COUNT(*) FROM Track t WHERE t.AlbumId = a.AlbumId) AS n FROM Album a WHERE EXISTS (SELECT 1 FROM Track t WHERE t.AlbumId = a.AlbumId AND t.Milliseconds > 600000)",
        "append",
        id="c_correlated_exists",
    ),
    pytest.param(C, "SELECT Name FROM Genre LIMIT 10, 5", "same", id="c_limit_offset_comma"),
    pytest.param(
        C,
        "SELECT Name FROM Track WHERE Name GLOB 'A*' AND IFNULL(Composer, '') <> '' LIMIT 20",
        "same",
        id="c_glob_ifnull",
    ),
    pytest.param(C, "SELECT Name FROM Genre WHERE Name = 'It''s'", "append", id="c_escaped_quote"),
    # superhero
    pytest.param(
        S,
        "SELECT p.publisher_name, COUNT(*) AS heroes FROM superhero s JOIN publisher p ON p.id = s.publisher_id GROUP BY p.publisher_name ORDER BY heroes DESC LIMIT 10",
        "same",
        id="s_heroes_per_publisher",
    ),
    pytest.param(
        S,
        "SELECT superhero_name FROM superhero WHERE height_cm > 200 AND weight_kg IS NOT NULL",
        "append",
        id="s_tall_heroes",
    ),
    pytest.param(
        S,
        "SELECT AVG(ha.attribute_value) FROM hero_attribute ha JOIN attribute a ON a.id = ha.attribute_id WHERE a.attribute_name = 'Intelligence'",
        "append",
        id="s_avg_intelligence",
    ),
    pytest.param(
        S,
        "SELECT s.superhero_name FROM superhero s JOIN hero_power hp ON hp.hero_id = s.id JOIN superpower sp ON sp.id = hp.power_id WHERE sp.power_name = 'Flight'",
        "append",
        id="s_flight",
    ),
    pytest.param(
        S,
        "SELECT c.colour, COUNT(*) FROM superhero s JOIN colour c ON c.id = s.eye_colour_id GROUP BY c.colour ORDER BY 2 DESC",
        "append",
        id="s_eye_colours",
    ),
    pytest.param(
        S,
        "SELECT g.gender, ROUND(AVG(s.height_cm), 1) FROM superhero s JOIN gender g ON g.id = s.gender_id GROUP BY g.gender",
        "append",
        id="s_height_by_gender",
    ),
    pytest.param(S, "SELECT r.race FROM race r WHERE r.race LIKE 'H%' ORDER BY r.race LIMIT 3", "same", id="s_races"),
    pytest.param(
        S,
        "SELECT al.alignment, COUNT(*) * 100.0 / (SELECT COUNT(*) FROM superhero) AS pct FROM superhero s JOIN alignment al ON al.id = s.alignment_id GROUP BY al.alignment",
        "append",
        id="s_alignment_share",
    ),
    # student_club
    pytest.param(
        SC,
        "SELECT m.position, j.major_name FROM member m JOIN major j ON j.major_id = m.link_to_major WHERE j.major_name = 'Business'",
        "append",
        id="sc_business_majors",
    ),
    pytest.param(
        SC,
        "SELECT e.event_name, COUNT(a.link_to_member) AS attendees FROM event e LEFT JOIN attendance a ON a.link_to_event = e.event_id GROUP BY e.event_id ORDER BY attendees DESC LIMIT 5",
        "same",
        id="sc_attendance",
    ),
    pytest.param(
        SC,
        "SELECT category, SUM(spent) AS spent, SUM(remaining) AS remaining FROM budget GROUP BY category",
        "append",
        id="sc_budget",
    ),
    pytest.param(
        SC,
        "SELECT SUM(amount) FROM income WHERE source = 'Dues' AND date_received LIKE '2019%'",
        "append",
        id="sc_dues",
    ),
    pytest.param(
        SC,
        "SELECT expense_description, cost FROM expense WHERE approved = 'true' ORDER BY cost DESC",
        "append",
        id="sc_expenses",
    ),
    pytest.param(SC, "SELECT city, county FROM zip_code WHERE state = 'Maryland' LIMIT 50", "same", id="sc_zip_codes"),
    # formula_1
    pytest.param(
        F1,
        "SELECT r.name, r.year FROM races r JOIN circuits c ON c.circuitId = r.circuitId WHERE c.country = 'Monaco' ORDER BY r.year DESC",
        "append",
        id="f1_monaco_races",
    ),
    pytest.param(
        F1,
        "SELECT d.forename || ' ' || d.surname AS driver, COUNT(*) AS wins FROM results res JOIN drivers d ON d.driverId = res.driverId WHERE res.position = 1 GROUP BY d.driverId ORDER BY wins DESC LIMIT 10",
        "same",
        id="f1_most_wins",
    ),
    pytest.param(
        F1,
        "SELECT c.name, SUM(cr.points) FROM constructorResults cr JOIN constructors c ON c.constructorId = cr.constructorId GROUP BY c.name ORDER BY 2 DESC LIMIT 5",
        "same",
        id="f1_constructor_points",
    ),
    pytest.param(F1, "SELECT MIN(milliseconds) FROM lapTimes WHERE raceId = 841", "append", id="f1_fastest_lap"),
    pytest.param(
        F1,
        "SELECT q.q1, q.q2, q.q3 FROM qualifying q WHERE q.raceId = 841 AND q.position <= 3",
        "append",
        id="f1_qualifying",
    ),
    pytest.param(
        F1,
        "SELECT r.raceId, l.lap FROM races r, lapTimes l WHERE l.raceId = r.raceId AND r.year = 2009 AND l.driverId = 1",
        "append",
        id="f1_comma_join_big_table_linked",
    ),
    pytest.param(
        F1, "SELECT year, COUNT(*) FROM races GROUP BY year HAVING COUNT(*) >= 20", "append", id="f1_busy_seasons"
    ),
    # european_football_2
    pytest.param(
        EF, "SELECT p.player_name, p.height FROM Player p WHERE p.player_name = 'Lionel Messi'", "append", id="ef_messi"
    ),
    pytest.param(
        EF,
        "SELECT t.team_long_name, ta.buildUpPlaySpeedClass FROM Team t JOIN Team_Attributes ta ON ta.team_api_id = t.team_api_id WHERE ta.buildUpPlaySpeedClass = 'Fast' LIMIT 100",
        "same",
        id="ef_fast_teams",
    ),
    pytest.param(
        EF,
        "SELECT l.name, COUNT(*) AS matches FROM Match m JOIN League l ON l.id = m.league_id GROUP BY l.name",
        "append",
        id="ef_matches_per_league",
    ),
    pytest.param(
        EF,
        "SELECT c.name AS country, l.name AS league FROM Country c JOIN League l ON l.country_id = c.id",
        "append",
        id="ef_leagues",
    ),
    pytest.param(
        EF,
        "SELECT AVG(home_team_goal - away_team_goal) FROM Match WHERE season = '2015/2016'",
        "append",
        id="ef_home_advantage",
    ),
    pytest.param(
        EF,
        "SELECT preferred_foot, COUNT(*) FROM Player_Attributes WHERE id <= 1000 GROUP BY preferred_foot",
        "append",
        id="ef_pk_range",
    ),
]


@pytest.mark.parametrize(("db_id", "sql", "mode"), VALID)
def test_valid_select_allowed_unchanged_except_limit(catalog, db_id, sql, mode):
    res = _guard(catalog, db_id, sql)
    assert res.ok, res.reason
    body = sql.rstrip(";").strip()
    if mode == "append":
        assert res.sql == body + f"\nLIMIT {ROW_LIMIT}"
        assert res.limited and res.unlimited_sql == body
    elif mode == "same":
        assert res.sql == body
        assert not res.limited
    else:  # cap: the outermost LIMIT is lowered to 1000, nothing else changes semantically
        tree = sqlglot.parse_one(res.sql, read="sqlite")
        assert tree.args["limit"].expression.this == str(ROW_LIMIT)
        expected = sqlglot.parse_one(body, read="sqlite")
        expected.args["limit"].set("expression", sqlglot.exp.Literal.number(ROW_LIMIT))
        assert tree == expected
        assert res.limited
    # and it really is valid SQLite over the real demo schema
    db = catalog[db_id]
    out = execute_readonly(db.path, res.sql, timeout_s=10)
    assert out.ok, out.error
    assert len(out.rows) <= ROW_LIMIT


def test_valid_list_size():
    assert len(VALID) == 60


def test_tables_reported_and_cte_names_ignored(catalog):
    res = _guard(
        catalog,
        C,
        "WITH big AS (SELECT AlbumId FROM Track) SELECT a.Title FROM Album a JOIN big ON big.AlbumId = a.AlbumId",
    )
    assert res.ok
    assert sorted(res.tables) == ["Album", "Track"]  # real tables only, CTE name "big" excluded


def test_capped_limit_keeps_offset(catalog):
    res = _guard(catalog, C, "SELECT Name FROM Track LIMIT 5000 OFFSET 7")
    assert res.ok and res.limited
    out = execute_readonly(catalog[C].path, res.sql)
    ref = execute_readonly(catalog[C].path, "SELECT Name FROM Track LIMIT 1000 OFFSET 7")
    assert out.rows == ref.rows


def test_negative_limit_is_capped(catalog):
    # LIMIT -1 means "no limit" in SQLite
    res = _guard(catalog, C, "SELECT Name FROM Track LIMIT -1")
    assert res.ok and res.limited
    assert len(execute_readonly(catalog[C].path, res.sql).rows) == ROW_LIMIT


def test_cross_join_small_tables_allowed_but_big_rejected(catalog):
    assert _guard(catalog, C, "SELECT * FROM Genre, MediaType").ok
    assert not _guard(catalog, F1, "SELECT * FROM lapTimes, status").ok
