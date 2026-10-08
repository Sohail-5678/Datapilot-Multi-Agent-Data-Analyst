"""Value linking (SPEC §5.3 step 4, §7.1, §13.3): literals in 30 hand-made questions are linked to the right
table.column by the in-memory value index; the index stays cheap to build."""

from __future__ import annotations

import sqlite3
import time
import tracemalloc

import pytest

from datapilot.index.value_index import build_value_index, link_values, suggest_values

# (db, question, stored value, acceptable table.column homes of that value)
CASES = [
    ("chinook", "How many tracks by AC/DC?", "AC/DC", {"Artist.Name"}),
    ("chinook", "List the albums by iron maden", "Iron Maiden", {"Artist.Name"}),  # lowercase + typo
    ("chinook", "How many tracks are in the Rock genre?", "Rock", {"Genre.Name"}),
    ("chinook", "Total sales to customers in Brazil", "Brazil", {"Customer.Country", "Invoice.BillingCountry"}),
    (
        "chinook",
        "How many tracks use the media type Protected AAC audio file?",
        "Protected AAC audio file",
        {"MediaType.Name"},
    ),
    ("chinook", "Which tracks are on the Grunge playlist?", "Grunge", {"Playlist.Name"}),
    ("chinook", "How many employees are a Sales Support Agent?", "Sales Support Agent", {"Employee.Title"}),
    ("superhero", "How many heroes are published by marvel comics?", "Marvel Comics", {"publisher.publisher_name"}),
    ("superhero", "How many superheroes have the power Flight?", "Flight", {"superpower.power_name"}),
    ("superhero", "Count the heroes of the race Mutant", "Mutant", {"race.race"}),
    ("superhero", "What is the full name of Spider-Man?", "Spider-Man", {"superhero.superhero_name"}),
    ("superhero", "Average Intelligence attribute of heroes", "Intelligence", {"attribute.attribute_name"}),
    ("superhero", "List heroes with Blond hair", "Blond", {"colour.colour"}),
    (
        "formula_1",
        "How many races were held at Monaco?",
        "Monaco",
        {"circuits.country", "circuits.name", "circuits.location"},
    ),
    ("formula_1", "How many wins does Lewis Hamilton have?", "Hamilton", {"drivers.surname"}),
    ("formula_1", "Total points scored by Ferrari", "Ferrari", {"constructors.name"}),
    ("formula_1", "Who won the Australian Grand Prix in 2009?", "Australian Grand Prix", {"races.name"}),
    ("formula_1", "How many races took place at Silverstone Circuit?", "Silverstone Circuit", {"circuits.name"}),
    ("formula_1", "How many results ended with the status Disqualified?", "Disqualified", {"status.status"}),
    ("student_club", "How many members are majoring in Business?", "Business", {"major.major_name"}),
    ("student_club", "How much was spent on the Advertisement category?", "Advertisement", {"budget.category"}),
    ("student_club", "Which majors belong to the College of Engineering?", "College of Engineering", {"major.college"}),
    ("student_club", "Who holds the position Vice President?", "Vice President", {"member.position"}),
    ("student_club", "How much income came from Dues?", "Dues", {"income.source"}),
    ("student_club", "How many people attended the Women's Soccer event?", "Women's Soccer", {"event.event_name"}),
    (
        "european_football_2",
        "How many matches were played in the Italy Serie A league?",
        "Italy Serie A",
        {"League.name"},
    ),
    ("european_football_2", "What is the height of Lionel Mesi?", "Lionel Messi", {"Player.player_name"}),  # typo
    ("european_football_2", "List the team attributes of FC Barcelona", "FC Barcelona", {"Team.team_long_name"}),
    ("european_football_2", "How many leagues are in Spain?", "Spain", {"Country.name"}),
    (
        "european_football_2",
        "Teams whose build-up play speed class is Fast",
        "Fast",
        {"Team_Attributes.buildUpPlaySpeedClass"},
    ),
]


def test_thirty_cases():
    assert len(CASES) == 30
    assert len({q for _, q, _, _ in CASES}) == 30


@pytest.mark.parametrize(("db_id", "question", "value", "homes"), CASES, ids=[c[1][:40] for c in CASES])
def test_literal_exists_in_real_data(catalog, db_id, question, value, homes):
    db = catalog[db_id]
    conn = sqlite3.connect(f"file:{db.path}?mode=ro&immutable=1", uri=True)
    try:
        found = {}
        for home in homes:
            table, column = home.split(".")
            assert db.table(table) is not None and db.table(table).column(column) is not None, home
            found[home] = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = ?', (value,)).fetchone()[0]
    finally:
        conn.close()
    assert any(found.values()), f"{value!r} not found in {homes}: {found}"


def test_value_links_hit_the_right_column(catalog):
    hits, misses = 0, []
    for db_id, question, value, homes in CASES:
        links = link_values(catalog[db_id], question)
        if any(f"{v.table}.{v.column}" in homes and v.value.lower() == value.lower() for v in links):
            hits += 1
        else:
            misses.append((question, [(f"{v.table}.{v.column}", v.value, v.score) for v in links[:4]]))
    assert hits >= 25, f"only {hits}/30 linked; misses: {misses}"


def test_link_shape_and_scores(catalog):
    links = link_values(catalog["chinook"], "How many tracks are in the Rock genre?")
    top = links[0]
    assert (top.table, top.column, top.value) == ("Genre", "Name", "Rock")
    assert top.score == 100.0 and top.matched.lower() == "rock"
    assert set(top.as_dict()) == {"table", "column", "value", "matched", "score"}
    assert all(v.score >= 88 for v in links)
    assert len(link_values(catalog["chinook"], "How many tracks are there " * 20, limit=3)) <= 3


def test_quoted_literal_is_linked(catalog):
    links = link_values(catalog["chinook"], 'How long is the track "Hallowed Be Thy Name"?')
    assert any((v.table, v.column, v.value) == ("Track", "Name", "Hallowed Be Thy Name") for v in links)


def test_suggest_values_for_repair_hints(catalog):
    assert suggest_values(catalog["chinook"], "Genre", "Name", "rok")[0] == "Rock"


def test_build_time_and_memory_stay_modest(catalog):
    total_bytes = 0
    for db in catalog.values():
        t0 = time.perf_counter()
        idx = build_value_index(db)
        wall_ms = (time.perf_counter() - t0) * 1000
        assert idx.columns_indexed > 0 and len(idx.choices) == len(idx.owners) > 0
        assert idx.build_ms < 5000 and wall_ms < 5000, f"{db.db_id}: {wall_ms:.0f} ms"
        total_bytes += idx.approx_bytes
    assert total_bytes < 64e6, f"value index ≈ {total_bytes / 1e6:.1f} MB"

    # measured, not estimated: the biggest index (formula_1) allocates well under 64 MB
    tracemalloc.start()
    try:
        build_value_index(catalog["formula_1"])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64e6, f"peak {peak / 1e6:.1f} MB"


def test_linking_is_fast(catalog):
    db = catalog["formula_1"]
    link_values(db, "warm up")
    t0 = time.perf_counter()
    link_values(db, "How many points did Lewis Hamilton score for McLaren at the Monaco Grand Prix in 2008?")
    assert time.perf_counter() - t0 < 2.0
