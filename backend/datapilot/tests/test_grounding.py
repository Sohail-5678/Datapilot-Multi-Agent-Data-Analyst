"""Output grounding guard (SPEC §8.4, §13.3): invented numbers are removed; numbers derived from the results
(percent change, difference, ratio, sum, share, fraction as percent) are accepted; the narrator gets one
rewrite before a sentence is stripped."""

from __future__ import annotations

import pytest

from datapilot.guards.output_grounding import (
    Allowed,
    allowed_values,
    check_grounding,
    extract_numbers,
    strip_ungrounded,
)
from datapilot.llm import fake

REVENUE = [
    {
        "columns": ["genre", "revenue_2012", "revenue_2011"],
        "rows": [["Rock", 400.0, 350.0], ["Latin", 100.0, 125.0], ["Metal", 75.5, 80.0]],
        "row_count": 3,
    }
]


def allowed(results=REVENUE, analysis=None, question="Which genres made the most revenue in 2012?") -> Allowed:
    return Allowed(allowed_values(results, analysis, question))


def test_extract_numbers_units_and_signs():
    ms = extract_numbers("Sales were $1,234.50 (up 12.5%), then −4.1% in 2011–2012, about 3.2 million and 15K users.")
    vals = [round(m.value, 4) for m in ms]
    assert vals == [1234.5, 12.5, -4.1, 2011.0, 2012.0, 3_200_000.0, 15_000.0]
    assert [m.percent for m in ms][:3] == [False, True, True]
    assert ms[0].decimals == 2


ACCEPTED = [
    pytest.param("Rock earned $400 in 2012.", id="base_value_and_question_year"),
    pytest.param("Rock grew 14.3% from 350 to 400.", id="percent_change"),
    pytest.param("Rock grew about 14% year over year.", id="percent_change_rounded"),
    pytest.param("Latin fell 20% (from 125 to 100).", id="negative_percent_change"),
    pytest.param("Latin fell −20%.", id="signed_percent_change"),
    pytest.param("Rock is up $50 on 2011.", id="difference"),
    pytest.param("Rock made 4 times as much as Latin.", id="ratio"),
    pytest.param("Rock made 1.14x its 2011 revenue.", id="ratio_decimal"),
    pytest.param("Together they made $575.5 in 2012.", id="column_sum"),
    pytest.param("The average genre made 191.83 in 2012.", id="column_mean"),
    pytest.param("Rock alone is 69.5% of 2012 revenue.", id="share_of_total"),
    pytest.param("The top 3 genres are Rock, Latin and Metal.", id="row_count"),
    pytest.param("Rock leads with 401.9.", id="within_half_percent"),
]


def test_tolerance_is_half_a_percent():
    one = Allowed(allowed_values([{"columns": ["v"], "rows": [[4000.0]], "row_count": 1}]))
    assert check_grounding("It is 4019.", one).ok  # 0.475 % off
    assert not check_grounding("It is 4030.", one).ok  # 0.75 % off


@pytest.mark.parametrize("text", ACCEPTED)
def test_derived_numbers_accepted(text):
    res = check_grounding(text, allowed())
    assert res.ok, res.ungrounded


REJECTED = [
    pytest.param("Rock earned $999 in 2012.", "$999", id="invented_value"),
    pytest.param("Rock grew 85% from 2011.", "85%", id="invented_percent"),
    pytest.param("Rock leads with 466.", "466", id="invented_near_value"),
    pytest.param("There are 31,337 genres.", "31,337", id="invented_count"),
    pytest.param("Rock made 2.5 million.", "2.5 million", id="invented_scaled"),
]


@pytest.mark.parametrize(("text", "bad"), REJECTED)
def test_invented_numbers_flagged(text, bad):
    res = check_grounding(text, allowed())
    assert not res.ok
    assert bad in res.ungrounded


def test_fraction_written_as_percent():
    results = [{"columns": ["share_left"], "rows": [[0.256]], "row_count": 1}]
    assert check_grounding("25.6% of snapshots are left-footed.", allowed(results)).ok
    assert check_grounding("About 26% are left-footed.", allowed(results)).ok
    assert not check_grounding("About 40% are left-footed.", allowed(results)).ok


def test_analysis_values_and_unit_conversions():
    results = [{"columns": ["Name", "Milliseconds"], "rows": [["Occupation / Precipice", 5286953]], "row_count": 1}]
    a = allowed(results, analysis={"pearson_r": 0.21, "n": 3503})
    assert check_grounding("The correlation is 0.21 across 3,503 tracks.", a).ok
    assert check_grounding("The longest track runs 5,287 s, about 88.1 minutes.", a).ok
    assert not check_grounding("The correlation is 0.85.", a).ok


def test_strip_removes_only_ungrounded_sentences():
    text = "Rock led 2012 with $400. That is 14.3% more than 2011. Analysts expect 777 next year!"
    res = strip_ungrounded(text, allowed())
    assert not res.ok
    assert res.removed == ["Analysts expect 777 next year!"]
    assert res.text == "Rock led 2012 with $400. That is 14.3% more than 2011."
    assert "777" not in res.text


def test_text_without_numbers_is_grounded():
    res = check_grounding("Rock is the top genre.", allowed())
    assert res.ok and res.checked == 0


# ----------------------------------------------------------------------------- the rewrite path in the graph

QUESTION = "Which artists have the most tracks?"  # fake SQL: top artists by track count (Iron Maiden 213)


async def test_rewrite_fixes_an_invented_number(ask):
    fake.script(
        "narrator", "**Iron Maiden** has the most tracks, with 999.", "**Iron Maiden** has the most tracks, with 213."
    )
    r = await ask("chinook", QUESTION)
    g = r.state["grounding"]
    assert r.status == "success"
    assert g["rewritten"] is True and g["ok"] is True and g["removed"] == []
    assert r.answer == "**Iron Maiden** has the most tracks, with 213."
    narrator_prompts = [c["prompt"] for c in fake.calls if c["step"] == "narrator"]
    assert len(narrator_prompts) == 2
    assert "not supported by the data: 999" in narrator_prompts[1]
    assert "<allowed_numbers>" in narrator_prompts[1] and "213" in narrator_prompts[1]
    streamed = "".join(d["text"] for e, d in r.events if e == "token")
    assert streamed == r.answer and "999" not in streamed
    assert r.spans("guard", "output_guard")[0]["status"] == "ok"


async def test_still_ungrounded_after_rewrite_sentence_removed(ask):
    fake.script(
        "narrator",
        "Iron Maiden leads with 213 tracks. That is 98765 more than anyone else.",
        "Iron Maiden leads with 213 tracks. Experts count 4242 fans.",
    )
    r = await ask("chinook", QUESTION)
    g = r.state["grounding"]
    assert g["rewritten"] is True and g["ok"] is False
    assert g["removed"] == ["Experts count 4242 fans."]
    assert r.answer == "Iron Maiden leads with 213 tracks."
    assert ("grounding", g) in r.events
    streamed = "".join(d["text"] for e, d in r.events if e == "token")
    assert "4242" not in streamed and "98765" not in streamed
    assert r.out.end_state["grounding_removed"] == 1
    assert r.spans("guard", "output_guard")[0]["status"] == "blocked"


async def test_everything_ungrounded_falls_back_to_plain_answer(ask):
    fake.script("narrator", "There are 31337 artists.", "There are 4242 artists.")
    r = await ask("chinook", QUESTION)
    assert r.state["grounding"]["ok"] is False
    assert "31337" not in r.answer and "4242" not in r.answer
    assert r.answer.startswith("Here is the verified result")


async def test_no_rewrite_when_disabled(ask):
    fake.script("narrator", "Iron Maiden leads with 213 tracks. It has 555 albums.")
    r = await ask("chinook", QUESTION, flags={"grounding_rewrite": False})
    g = r.state["grounding"]
    assert g["rewritten"] is False and g["removed"] == ["It has 555 albums."]
    assert len([c for c in fake.calls if c["step"] == "narrator"]) == 1


async def test_grounded_first_draft_needs_no_rewrite(ask):
    r = await ask("chinook", QUESTION)
    g = r.state["grounding"]
    assert g == {"checked": 1, "removed": [], "ok": True, "rewritten": False, "ungrounded_first_draft": []}
    assert len([c for c in fake.calls if c["step"] == "narrator"]) == 1
