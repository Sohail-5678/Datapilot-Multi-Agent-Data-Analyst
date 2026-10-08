"""Execution match (BIRD's official set comparison, SPEC §7.5) and the order-aware result hash (§7.3)."""

from __future__ import annotations

import pytest

from datapilot.sql.compare import execution_match, is_ordered, result_hash
from datapilot.sql.executor import execute_readonly

# BIRD evaluation.py: `res = 1 if set(predicted_res) == set(ground_truth_res) else 0` on raw fetchall() rows.
EX_FIXTURES = [
    pytest.param([(1, "a"), (2, "b")], [(2, "b"), (1, "a")], True, id="row_order_ignored"),
    pytest.param([(1,), (1,), (2,)], [(1,), (2,)], True, id="duplicates_ignored"),
    pytest.param([(1,)], [(1,), (1,), (1,)], True, id="duplicates_ignored_other_side"),
    pytest.param([], [], True, id="both_empty"),
    pytest.param([(None,)], [(None,)], True, id="nulls_equal"),
    pytest.param([(1,)], [(1.0,)], True, id="int_equals_float"),
    pytest.param([[1, "a"], [2, "b"]], [(1, "a"), (2, "b")], True, id="lists_vs_tuples"),
    pytest.param([(1, "a")], [(1, "b")], False, id="different_value"),
    pytest.param([(1, "a")], [("a", 1)], False, id="column_order_matters"),
    pytest.param([(1,)], [(1,), (2,)], False, id="missing_row"),
    pytest.param([(1,), (2,), (3,)], [(1,), (2,)], False, id="extra_row"),
    pytest.param([], [(1,)], False, id="empty_vs_rows"),
    pytest.param([(1, 2)], [(1,)], False, id="extra_column"),
    pytest.param([("1",)], [(1,)], False, id="string_vs_number"),
    pytest.param([(0.1 + 0.2,)], [(0.3,)], False, id="raw_floats_no_rounding"),
    pytest.param([(None,)], [(0,)], False, id="null_vs_zero"),
]


@pytest.mark.parametrize(("pred", "gold", "match"), EX_FIXTURES)
def test_execution_match_fixtures(pred, gold, match):
    assert execution_match(pred, gold) is match
    assert execution_match(gold, pred) is match  # symmetric


BIRD_STYLE = [
    # (db, gold SQL, predicted SQL, EX)
    pytest.param(
        "formula_1",
        "SELECT COUNT(raceId) FROM races WHERE year = 2009",
        "SELECT COUNT(*) FROM races r WHERE r.year = 2009",
        True,
        id="count_star_vs_count_column",
    ),
    pytest.param(
        "superhero",
        "SELECT T3.power_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
        "INNER JOIN superpower AS T3 ON T2.power_id = T3.id WHERE T1.superhero_name = '3-D Man'",
        "SELECT sp.power_name FROM superpower sp WHERE sp.id IN (SELECT hp.power_id FROM hero_power hp "
        "JOIN superhero s ON s.id = hp.hero_id WHERE s.superhero_name = '3-D Man') ORDER BY sp.power_name DESC",
        True,
        id="different_shape_and_order_same_set",
    ),
    pytest.param(
        "superhero",
        "SELECT DISTINCT T2.colour FROM superhero AS T1 INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id "
        "WHERE T1.publisher_id = 4",
        "SELECT c.colour FROM superhero s JOIN colour c ON c.id = s.eye_colour_id WHERE s.publisher_id = 4",
        True,
        id="missing_distinct_is_still_a_match",
    ),
    pytest.param(
        "superhero",
        "SELECT T3.power_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
        "INNER JOIN superpower AS T3 ON T2.power_id = T3.id WHERE T1.superhero_name = '3-D Man'",
        "SELECT T3.power_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
        "INNER JOIN superpower AS T3 ON T2.power_id = T3.id WHERE T1.superhero_name = 'A-Bomb'",
        False,
        id="wrong_filter",
    ),
    pytest.param(
        "chinook",
        "SELECT Name FROM Genre WHERE GenreId <= 3",
        "SELECT GenreId, Name FROM Genre WHERE GenreId <= 3",
        False,
        id="extra_column_selected",
    ),
]


@pytest.mark.parametrize(("db_id", "gold", "pred", "match"), BIRD_STYLE)
def test_execution_match_on_real_results(catalog, db_id, gold, pred, match):
    path = catalog[db_id].path
    g = execute_readonly(path, gold, row_cap=100_000)
    p = execute_readonly(path, pred, row_cap=100_000)
    assert g.ok and p.ok, (g.error, p.error)
    assert g.rows, "gold must return rows for a meaningful fixture"
    assert execution_match(p.rows, g.rows) is match


# ----------------------------------------------------------------------------- result hash


def test_hash_ignores_row_order_by_default():
    a = [[1, "Rock"], [2, "Jazz"], [3, "Metal"]]
    assert result_hash(a) == result_hash(list(reversed(a)))


def test_hash_is_order_sensitive_for_rankings():
    a = [[1, "Rock"], [2, "Jazz"]]
    assert result_hash(a, ordered=True) != result_hash(list(reversed(a)), ordered=True)
    assert result_hash(a, ordered=True) == result_hash([list(r) for r in a], ordered=True)


def test_hash_counts_duplicates_unlike_ex():
    assert result_hash([[1], [1], [2]]) != result_hash([[1], [2]])
    assert execution_match([[1], [1], [2]], [[1], [2]])


def test_hash_rounds_floats_to_six_decimals():
    assert result_hash([[0.1 + 0.2]]) == result_hash([[0.3]])
    assert result_hash([[1.0000001]]) == result_hash([[1.0000002]])
    assert result_hash([[1.000001]]) != result_hash([[1.000002]])


def test_hash_normalizes_ints_floats_and_bools():
    assert result_hash([[2.0, True]]) == result_hash([[2, 1]])


def test_hash_differs_for_different_values_or_column_order():
    assert result_hash([[1, "a"]]) != result_hash([[1, "b"]])
    assert result_hash([[1, "a"]]) != result_hash([["a", 1]])
    assert result_hash([[None]]) == result_hash([[None]])
    assert result_hash([[None]]) != result_hash([[0]])


def test_hash_shape():
    h = result_hash([[1]])
    assert len(h) == 16 and int(h, 16) >= 0
    assert result_hash([]) == result_hash([])


ORDERED = [
    pytest.param("SELECT Name FROM Track ORDER BY Milliseconds DESC LIMIT 5", True, id="order_by_limit"),
    pytest.param("SELECT Name FROM Track ORDER BY Milliseconds DESC\nLIMIT 1000", True, id="guard_appended_limit"),
    pytest.param(
        "SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.Name ORDER BY COUNT(*) DESC LIMIT 5",
        True,
        id="order_by_aggregate_function",
    ),
    pytest.param(
        "SELECT Name FROM Track ORDER BY ROUND(UnitPrice, 2) DESC, LENGTH(Name) LIMIT 3", True, id="order_by_functions"
    ),
    pytest.param("SELECT Name FROM Track\nORDER\n  BY Name\nLIMIT 3", True, id="multiline_order_by"),
    pytest.param("SELECT Name FROM Track ORDER BY Name", False, id="order_by_without_limit"),
    pytest.param("SELECT Name FROM Track LIMIT 5", False, id="limit_without_order_by"),
    pytest.param("SELECT * FROM (SELECT Name FROM Track ORDER BY Name LIMIT 5)", False, id="ranking_only_in_subquery"),
    pytest.param(
        "WITH t AS (SELECT Name FROM Track ORDER BY Name LIMIT 5) SELECT * FROM t\nLIMIT 1000",
        False,
        id="ranking_only_in_cte",
    ),
    pytest.param(
        "SELECT Name, ROW_NUMBER() OVER (ORDER BY Name) FROM Track\nLIMIT 1000", False, id="order_by_only_in_window"
    ),
    pytest.param("SELECT 'order by x limit 5' AS s FROM Track", False, id="keywords_inside_string"),
]


@pytest.mark.parametrize(("sql", "ordered"), ORDERED)
def test_is_ordered(sql, ordered):
    assert is_ordered(sql) is ordered
