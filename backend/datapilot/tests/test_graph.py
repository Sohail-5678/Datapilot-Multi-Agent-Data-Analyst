"""Agent graph paths with the fake LLM (SPEC §6.3, §6.4, §7.3, §7.4, §13.3): success, clarify, confirm, repair,
vote tie, adaptive k, budget stop, provider fallback, sandbox, and the trace.v1 shape."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from sqlalchemy import delete

from datapilot.db.schema import usage_counters
from datapilot.db.session import run_sync
from datapilot.llm import fake, quota, router
from datapilot.llm.providers import Completion, ProviderError
from datapilot.llm.router import LLMUnavailable
from datapilot.profile import default_profile
from datapilot.tracing import Budget, BudgetExceeded, RunCtx

ARTISTS = "Which artists have the most tracks?"  # fake SQL: top 10 artists by track count
FOOT = "Average overall rating by preferred foot"  # fake SQL: full scan of Player_Attributes (183,978 rows)
CORRELATION = "What is the correlation between track length and price?"  # 2-step plan with an analysis step


def sql_reply(sql: str) -> str:
    return f"```sql\n{sql}\n```"


def calls(step: str) -> list[str]:
    return [c["prompt"] for c in fake.calls if c["step"] == step]


# ----------------------------------------------------------------------------- plain success


async def test_plain_success_with_high_confidence(ask):
    r = await ask("chinook", ARTISTS)
    assert r.status == "success"
    assert r.confidence == "High"
    cands = r.candidates()
    assert set(cands) == {"c1-direct", "c1-plan_then_sql"}  # adaptive k: two agree → no third strategy
    assert {c["status"] for c in cands.values()} == {"ok"}
    assert len({c["result_hash"] for c in cands.values()}) == 1
    assert calls("sql_fewshot") == []
    assert r.out.end_state["chosen_sql"].endswith("LIMIT 10")
    assert r.out.end_state["row_count"] == 10
    assert "Iron Maiden" in r.answer and "213" in r.answer
    assert r.state["grounding"]["ok"] is True
    names = r.event_names()
    for e in ("plan", "step", "candidate", "chosen", "table", "chart", "grounding", "token"):
        assert e in names, e
    assert names.index("plan") < names.index("chosen") < names.index("grounding")
    chosen = next(d for e, d in r.events if e == "chosen")
    assert chosen["confidence"] == "High" and chosen["agree"] == 2 and chosen["reasons"][0] == "2 of 2 candidates agree"
    assert r.interrupts == []
    assert not (r.out.clarified or r.out.confirmed or r.out.sandbox_used)


# ----------------------------------------------------------------------------- clarify


async def test_clarify_then_resume_with_choice(ask):
    r = await ask(
        "european_football_2",
        "Who are the best players?",
        answers={"clarify": lambda p: p["options"][0], "confirm": "run"},
    )
    assert [i["type"] for i in r.interrupts] == ["clarify", "confirm"]
    clarify = r.interrupts[0]
    assert clarify["question"] and 2 <= len(clarify["options"]) <= 4
    assert r.state["clarification"] == "Highest overall rating"
    planner_prompts = calls("planner")
    assert len(planner_prompts) == 2
    assert "The user already clarified: Highest overall rating" in planner_prompts[1]
    assert r.status == "success" and r.out.clarified
    assert "overall_rating" in r.out.end_state["chosen_sql"]
    assert r.spans("human", "clarify")
    assert r.out.trace["end_state"]["clarified"] is True


async def test_clarify_with_free_text_choice(ask):
    r = await ask(
        "european_football_2",
        "Who are the best players?",
        answers={"clarify": "players with the best overall rating", "confirm": "run"},
    )
    assert r.state["clarification"] == "players with the best overall rating"
    assert r.status == "success"


async def test_clarify_without_answer_stops(ask):
    r = await ask("european_football_2", "Who are the best players?", answers={"clarify": None})
    assert r.status == "needs_human"
    assert r.out.trace["status"] == "needs_human"
    assert r.answer == "Waiting for more detail before running this."
    assert calls("sql_direct") == []


# ----------------------------------------------------------------------------- confirm (expensive query)


async def test_confirm_run_executes_the_full_scan(ask):
    r = await ask("european_football_2", FOOT, answers={"confirm": "run"})
    assert [i["type"] for i in r.interrupts] == ["confirm"]
    c = r.interrupts[0]
    assert c["scanned_rows"] == 183_978 and c["table"] == "Player_Attributes"
    assert "Player_Attributes" in c["sql"] and c["seconds"].endswith(" s")
    assert r.status == "success" and r.out.confirmed
    assert r.confidence == "High"
    assert r.out.end_state["row_count"] == 2  # left / right
    human = r.spans("human", "confirm")[0]
    executor = r.spans("tool", "executor")
    assert executor and all(s["started_at"] >= human["started_at"] for s in executor)


async def test_confirm_cancel_runs_nothing(ask):
    r = await ask("european_football_2", FOOT, answers={"confirm": "cancel"})
    assert r.status == "cancelled"
    assert r.out.trace["status"] == "needs_human"
    assert r.answer == "Cancelled — the query was not run."
    assert r.spans("tool", "executor") == []  # the expensive SQL never ran
    assert r.out.end_state["chosen_sql"] is None


async def test_confirm_narrow_asks_for_a_filter(ask):
    r = await ask("european_football_2", FOOT, answers={"confirm": "narrow"})
    assert r.status == "needs_human"
    assert any("narrow it down" in n for n in r.state["notes"])
    assert r.spans("tool", "executor") == []


async def test_cheap_query_on_big_table_needs_no_confirmation(ask):
    cheap = "SELECT overall_rating, potential FROM Player_Attributes WHERE id = 1"
    fake.script("sql_direct", sql_reply(cheap))
    fake.script("sql_plan", sql_reply(cheap))
    r = await ask("european_football_2", "What are the ratings of attribute row 1?")
    assert r.interrupts == []
    assert r.status == "success" and r.out.end_state["row_count"] == 1


async def test_aliased_full_scan_still_asks_for_confirmation(ask):
    aliased = (
        "SELECT pa.preferred_foot, AVG(pa.overall_rating) AS avg FROM Player_Attributes pa GROUP BY pa.preferred_foot"
    )
    fake.script("sql_direct", sql_reply(aliased))
    fake.script("sql_plan", sql_reply(aliased))
    r = await ask("european_football_2", "Average rating per foot?", answers={"confirm": "cancel"})
    assert [i["type"] for i in r.interrupts] == ["confirm"]
    assert r.interrupts[0]["scanned_rows"] == 183_978


# ----------------------------------------------------------------------------- repair loop


async def test_repair_fixes_a_broken_candidate(ask):
    fake.script("sql_direct", sql_reply("SELECT Nam FROM Artist"))  # broken; the repair step writes the fixed SQL
    r = await ask("chinook", ARTISTS)
    cands = r.candidates()
    assert cands["c1-direct"]["repairs"] == 1 and cands["c1-direct"]["status"] == "ok"
    assert cands["c1-plan_then_sql"]["repairs"] == 0
    repair_prompts = calls("repair")
    assert len(repair_prompts) == 1
    assert "no such column: Nam" in repair_prompts[0]
    assert "<failed_sql>\nSELECT Nam FROM Artist" in repair_prompts[0]
    assert "Columns with a similar name: " in repair_prompts[0] and "Artist.Name" in repair_prompts[0]
    assert r.status == "success" and r.confidence == "High"
    assert len(r.spans("node", "repair")) == 1
    assert any(d.get("status") == "repairing" for e, d in r.events if e == "candidate")


async def test_zero_row_result_is_repaired_with_value_hints(ask):
    wrong = "SELECT t.Name FROM Track t JOIN Genre g ON g.GenreId = t.GenreId WHERE g.Name = 'rock'"
    right = "SELECT t.Name FROM Track t JOIN Genre g ON g.GenreId = t.GenreId WHERE g.Name = 'Rock'"
    fake.script("sql_direct", sql_reply(wrong))
    fake.script("repair", sql_reply(right))
    fake.script("sql_plan", sql_reply(right))
    r = await ask("chinook", "List the tracks in the Rock genre")
    direct = r.candidates()["c1-direct"]
    assert direct["repairs"] == 1 and direct["row_count"] == 1297
    hint = calls("repair")[0]
    assert "returned 0 rows" in hint
    assert "'rock' is spelled 'Rock' in Genre.Name." in hint
    assert r.confidence == "High"


async def test_repairs_are_capped_and_the_vote_moves_on(ask, settings):
    fake.script("sql_direct", sql_reply("SELECT Nme FROM Artist"))
    fake.script("repair", sql_reply("SELECT Nme2 FROM Artist"), sql_reply("SELECT Nme3 FROM Artist"))
    r = await ask("chinook", ARTISTS)
    cands = r.candidates()
    assert cands["c1-direct"]["status"] == "error"
    assert cands["c1-direct"]["repairs"] == settings.max_repairs == 2
    assert "no such column: Nme3" in cands["c1-direct"]["error"]
    # only one candidate succeeded, so adaptive k ran the third strategy, which agrees with plan-then-SQL
    assert cands["c1-few_shot"]["status"] == "ok"
    assert r.status == "success" and r.confidence == "High"


# ----------------------------------------------------------------------------- voting


async def test_vote_tie_is_broken_by_the_verifier(ask):
    fake.script("sql_direct", sql_reply("SELECT Name FROM Genre WHERE GenreId = 1"))
    fake.script("sql_plan", sql_reply("SELECT Name FROM Genre WHERE GenreId = 2"))
    fake.script(
        "verifier", json.dumps({"chosen": "c1-plan_then_sql", "passes": True, "issues": [], "reason": "Id 2 is asked."})
    )
    r = await ask("chinook", "Which genre has id 2?", flags={"adaptive": False, "k": 2})
    assert r.status == "success"
    assert "GenreId = 2" in r.out.end_state["chosen_sql"]
    assert r.confidence == "Medium"  # one candidate in the winning group + verifier pass
    reasons = r.state["step_results"][0]["reasons"]
    assert reasons[0] == "1 of 2 candidates agree" and "Id 2 is asked." in reasons
    vprompt = calls("verifier")[0]
    assert "Candidate c1-direct" in vprompt and "Candidate c1-plan_then_sql" in vprompt
    assert "Jazz" in r.answer


async def test_verifier_concern_gives_low_confidence(ask):
    fake.script(
        "verifier", json.dumps({"chosen": "c1-direct", "passes": False, "issues": ["filter missing"], "reason": ""})
    )
    r = await ask("chinook", ARTISTS)
    assert r.confidence == "Low"
    assert any("filter missing" in x for x in r.state["step_results"][0]["reasons"])


async def test_adaptive_k_runs_third_strategy_when_two_disagree(ask):
    fake.script("sql_direct", sql_reply("SELECT COUNT(*) AS n FROM Genre"))
    fake.script("sql_plan", sql_reply("SELECT COUNT(*) AS n FROM Album"))
    fake.script("sql_fewshot", sql_reply("SELECT COUNT(*) AS n FROM Genre"))
    r = await ask("chinook", "How many genres are there?")
    cands = r.candidates()
    assert set(cands) == {"c1-direct", "c1-plan_then_sql", "c1-few_shot"}
    assert len(calls("sql_fewshot")) == 1
    assert any("running a third strategy" in d.get("label", "") for e, d in r.events if e == "step")
    assert r.confidence == "High"  # direct + few-shot agree
    assert r.state["step_results"][0]["rows"] == [[25]]
    vote = r.spans("node", "vote")
    assert len(vote) == 2 and vote[0]["output_redacted"]["decision"] == "more"


# ----------------------------------------------------------------------------- budgets


async def test_budget_stop_from_settings(ask, monkeypatch, settings):
    monkeypatch.setattr(settings, "question_call_budget", 2)  # planner + one candidate
    r = await ask("chinook", ARTISTS)
    assert r.ctx.budget.max_calls == 2
    assert r.status == "budget_exceeded"
    assert r.out.trace["status"] == "budget_exceeded"
    assert "budget" in r.answer
    assert r.out.trace["metrics"]["llm_calls"] == 2
    over = [c for c in r.state["candidates"] if c["status"] == "error"]
    assert over and "budget reached" in over[0]["error"].lower()


async def test_budget_hit_by_parallel_candidates_together(ask):
    # planner uses the only call; both candidates then hit the budget in the same super-step
    r = await ask("chinook", ARTISTS, budget=Budget(max_calls=1, max_tokens=60_000, max_wall_s=90))
    assert r.status == "budget_exceeded"
    assert {c["status"] for c in r.candidates().values()} <= {"error"}


async def test_token_and_wall_clock_budgets(ask):
    tokens = await ask("chinook", ARTISTS, budget=Budget(max_calls=25, max_tokens=1, max_wall_s=90))
    assert tokens.status == "budget_exceeded"
    wall = await ask("chinook", ARTISTS, budget=Budget(max_calls=25, max_tokens=60_000, max_wall_s=0.0))
    assert wall.status == "budget_exceeded"
    assert len(calls("planner")) == 1  # the wall-clock run stopped before its planner call


def test_budget_check_rules():
    b = Budget(max_calls=2, max_tokens=100, max_wall_s=90)
    b.check()
    b.calls = 2
    with pytest.raises(BudgetExceeded, match="2 LLM calls"):
        b.check()
    b.calls, b.tokens = 0, 100
    with pytest.raises(BudgetExceeded, match="100 tokens"):
        b.check()
    b.tokens, b.waited_s = 0, -1000.0
    with pytest.raises(BudgetExceeded, match="90 s"):
        b.check()


# ----------------------------------------------------------------------------- provider fallback


@pytest.fixture
def live_router(monkeypatch, settings):
    """Router with 'real' providers configured but every provider function replaced by a local fake."""
    monkeypatch.setattr(settings, "fake_llm", False)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
    log: list[tuple[str, str]] = []
    behaviour: dict[str, object] = {}

    def make(provider: str):
        async def call(model, system, prompt, *, json_mode, temperature, max_tokens):
            log.append((provider, model))
            b = behaviour.get(provider)
            if isinstance(b, Exception):
                raise b
            return Completion(f"{provider} says hi", provider, model, 10, 3)

        return call

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(router, "gemini_generate", make("gemini"))
    monkeypatch.setattr(router, "groq_chat", make("groq"))
    monkeypatch.setattr(router.asyncio, "sleep", no_sleep)
    run_sync(lambda c: c.execute(delete(usage_counters)))
    quota.reset_for_tests()
    yield settings, log, behaviour
    run_sync(lambda c: c.execute(delete(usage_counters)))


def _ctx() -> RunCtx:
    # No profile routing: these tests exercise the router's chain logic with explicit `kinds`.
    return RunCtx(run_id="router-test", db_id="chinook", profile=dataclasses.replace(default_profile(), routing={}))


async def test_fallback_when_primary_provider_unconfigured(live_router):
    settings, log, _ = live_router
    settings.groq_api_key = "test-key-not-real"  # Gemini (primary for the planner) has no key
    ctx = _ctx()
    res = await router.complete(ctx, "planner", "sys", "prompt", kinds=["main", "fast"])
    assert res.provider == "groq" and res.model == settings.fast_model
    assert log == [("groq", settings.fast_model)]
    span = [s for s in ctx.spans if s.kind == "llm"][0]
    assert span.provider == "groq" and span.attributes["model_kind"] == "fast"
    assert ctx.llm_calls == 1 and ctx.tokens_in == 10


async def test_overloaded_provider_falls_back_at_once_and_cools_down(live_router):
    settings, log, behaviour = live_router
    settings.gemini_api_key, settings.groq_api_key = "test-key-not-real", "test-key-not-real"
    behaviour["gemini"] = ProviderError("gemini", 503, "overloaded")
    res = await router.complete(_ctx(), "planner", "sys", "prompt", kinds=["main", "fast"])
    assert res.provider == "groq"
    assert [p for p, _ in log] == ["gemini", "groq"]  # no retries against an overloaded model when a fallback exists
    log.clear()
    behaviour.pop("gemini")
    await router.complete(_ctx(), "narrator", "sys", "prompt", kinds=["main", "fast"])
    assert [p for p, _ in log] == ["groq"]  # Gemini is cooling down


async def test_overloaded_last_resort_is_still_retried(live_router):
    settings, log, behaviour = live_router
    settings.gemini_api_key, settings.alt_model = "test-key-not-real", ""
    behaviour["gemini"] = ProviderError("gemini", 503, "overloaded")
    with pytest.raises(LLMUnavailable):
        await router.complete(_ctx(), "planner", "sys", "prompt", kinds=["main"])
    assert [p for p, _ in log] == ["gemini"] * (router.MAX_RETRIES + 1)


async def test_slow_success_cools_provider_down(live_router, monkeypatch):
    settings, log, _ = live_router
    settings.gemini_api_key, settings.groq_api_key = "test-key-not-real", "test-key-not-real"
    clock = iter([100.0, 130.0])  # the Gemini call "takes" 30 s
    real = router.time.monotonic
    monkeypatch.setattr(router.time, "monotonic", lambda: next(clock, None) or real())
    res = await router.complete(_ctx(), "planner", "sys", "prompt", kinds=["main", "fast"])
    assert res.provider == "gemini"
    monkeypatch.setattr(router.time, "monotonic", real)
    log.clear()
    await router.complete(_ctx(), "planner", "sys", "prompt", kinds=["main", "fast"])
    assert [p for p, _ in log] == ["groq"]


async def test_daily_cap_skips_straight_to_fallback(live_router):
    settings, log, behaviour = live_router
    settings.gemini_api_key, settings.groq_api_key = "test-key-not-real", "test-key-not-real"
    behaviour["gemini"] = ProviderError("gemini", 429, "Quota exceeded: requests per day")
    res = await router.complete(_ctx(), "narrator", "sys", "prompt", kinds=["main", "fast"])
    assert res.provider == "groq"
    assert [p for p, _ in log] == ["gemini", "groq"]
    assert not quota.available("main")  # no more Gemini today


async def test_circuit_breaker_opens_after_repeated_failures(live_router):
    settings, log, behaviour = live_router
    settings.gemini_api_key, settings.groq_api_key = "test-key-not-real", "test-key-not-real"
    behaviour["gemini"] = ProviderError("gemini", 500, "boom")
    await router.complete(_ctx(), "planner", "s", "p", kinds=["main", "fast"])  # 3 failures
    await router.complete(_ctx(), "planner", "s", "p", kinds=["main", "fast"])  # 3 more → open
    log.clear()
    await router.complete(_ctx(), "planner", "s", "p", kinds=["main", "fast"])
    assert log == [("groq", settings.fast_model)]  # Gemini skipped while the breaker is open


async def test_all_providers_down_raises_unavailable(live_router):
    settings, _, behaviour = live_router
    settings.gemini_api_key, settings.groq_api_key = "test-key-not-real", "test-key-not-real"
    behaviour["gemini"] = ProviderError("gemini", 400, "bad request")
    behaviour["groq"] = ProviderError("groq", 400, "bad request")
    with pytest.raises(LLMUnavailable) as e:
        await router.complete(_ctx(), "planner", "s", "p", kinds=["main", "fast"])
    assert "gemini 400" in str(e.value) and "groq 400" in str(e.value)


async def test_nothing_configured_raises_unavailable(live_router):
    with pytest.raises(LLMUnavailable, match="not configured"):
        await router.complete(_ctx(), "planner", "s", "p", kinds=["main", "fast"])


def test_routing_chain_comes_from_the_profile(settings):
    ctx = RunCtx(run_id="router-test", db_id="chinook", profile=default_profile())
    assert router.chain_for(ctx, "planner", ["fast"]) == ["lite", "fast", "main"]
    assert router.chain_for(ctx, "sql_direct", ["main"]) == ["fast", "main"]
    assert router.chain_for(None, "planner", ["lite"]) == ["lite"]


async def test_planner_outage_falls_back_to_single_step_plan(ask):
    fake.script("planner", "__raise_unavailable__")
    r = await ask("chinook", ARTISTS)
    assert r.state["plan_meta"]["source"] == "fallback"
    assert [s["goal"] for s in r.state["plan"]] == [ARTISTS]
    assert r.status == "success"


async def test_schema_prune_outage_falls_back_to_rules(ask):
    fake.script("schema_prune", "__raise_unavailable__")
    cheap = "SELECT team_long_name FROM Team WHERE team_long_name = 'FC Barcelona'"
    fake.script("sql_direct", sql_reply(cheap))
    fake.script("sql_plan", sql_reply(cheap))
    r = await ask("european_football_2", "Show the team FC Barcelona")
    assert r.state["linked"]["pruned_by"] == "rules"
    assert "Team" in r.state["linked"]["tables"]
    assert r.status == "success"


async def test_narrator_outage_gives_plain_verified_answer(ask):
    fake.script("narrator", "__raise_unavailable__")
    r = await ask("chinook", ARTISTS)
    assert r.status == "success"
    assert r.answer == "Here is the verified result (10 rows); see the table and chart."


async def test_all_sql_models_down_is_a_failure_not_a_crash(ask):
    for step in ("sql_direct", "sql_plan", "sql_fewshot"):
        fake.script(step, "__raise_unavailable__")
    r = await ask("chinook", ARTISTS)
    assert r.status == "failure"
    assert r.out.trace["status"] == "failure"
    assert {c["status"] for c in r.candidates().values()} == {"error"}
    assert "couldn't produce a verified query" in r.answer


async def test_quota_exhausted_stops_the_run(ask):
    def exhausted(_system, _prompt):
        raise LLMUnavailable("daily budget reached", quota_exhausted=True)

    fake.script("planner", exhausted)
    r = await ask("chinook", ARTISTS)
    assert r.status == "quota_exhausted"
    assert r.out.trace["status"] == "error"
    assert "quota" in r.answer.lower()


# ----------------------------------------------------------------------------- sandbox


async def test_sandbox_path_with_browser_result(ask):
    def browser(p: dict) -> dict:
        return {
            "ok": True,
            "result": {"pearson_r": 0.21, "n": 3503},
            "stdout": "ok",
            "duration_ms": 140,
            "ran_in": "browser",
        }

    r = await ask("chinook", CORRELATION, answers={"sandbox": browser})
    assert [i["type"] for i in r.interrupts] == ["sandbox"]
    req = r.interrupts[0]
    assert "result" in req["code"] and req["request_id"]
    full = r.ctx.data[req["data_ref"]]
    assert req["rows"] == len(full["rows"]) == 3503  # analysis data is not capped at the 1,000 display rows
    assert len(r.state["plan"]) == 2 and r.state["plan"][1]["needs_analysis"]
    a = r.state["analysis"]
    assert a["ok"] and a["result"] == {"pearson_r": 0.21, "n": 3503} and a["ran_in"] == "browser"
    assert r.status == "success" and r.out.sandbox_used
    assert "0.21" in r.answer and r.state["grounding"]["ok"]
    sp = r.spans("sandbox", "sandbox_call")[0]
    assert sp["status"] == "ok" and sp["duration_ms"] == 140 and sp["attributes"]["ran_in"] == "browser"


async def test_sandbox_timeout_retries_once_then_answers_without_analysis(ask):
    timeout = {"ok": False, "error": "The browser sandbox did not return a result within 60 s.", "ran_in": "browser"}
    r = await ask("chinook", CORRELATION, answers={"sandbox": timeout})
    assert [i["type"] for i in r.interrupts] == ["sandbox", "sandbox"]  # one fix attempt, then skip
    analyst_prompts = calls("analyst")
    assert len(analyst_prompts) == 2 and "<previous_attempt>" in analyst_prompts[1]
    assert "within 60 s" in analyst_prompts[1]
    assert r.state["analysis"]["ok"] is False
    assert all(s["status"] == "error" for s in r.spans("sandbox", "sandbox_call"))
    assert "The analysis step could not run" in calls("narrator")[0]
    assert r.status == "success"  # the SQL step is still answered


async def test_forbidden_analysis_code_never_reaches_the_sandbox(ask):
    fake.script("analyst", "```python\nimport os\nresult = os.listdir('/')\n```")
    sent: list[str] = []

    def browser(p: dict) -> dict:
        sent.append(p["code"])
        return {"ok": True, "result": {"pearson_r": 0.21, "n": 3503}, "duration_ms": 5}

    r = await ask("chinook", CORRELATION, answers={"sandbox": browser})
    assert len(sent) == 1 and "import os" not in sent[0]
    assert len(calls("analyst")) == 2
    assert "Not allowed in the sandbox" in calls("analyst")[1]
    assert r.state["analysis"]["ok"]


# ----------------------------------------------------------------------------- trace.v1


SPAN_KINDS = {"node", "llm", "tool", "guard", "retrieval", "human", "sandbox"}
TRACE_STATUSES = {"success", "failure", "error", "blocked", "needs_human", "budget_exceeded"}


async def test_trace_v1_shape(ask):
    r = await ask("chinook", ARTISTS + " (contact me at someone@example.com)")
    t = r.out.trace
    assert t["contract_version"] == "trace.v1"
    assert set(t) >= {
        "trace_id",
        "agent",
        "agent_version",
        "profile_version",
        "mode",
        "case_id",
        "started_at",
        "ended_at",
        "status",
        "input",
        "final_output",
        "end_state",
        "spans",
        "metrics",
        "feedback",
    }
    assert t["agent"] == "datapilot" and t["profile_version"] == "datapilot@2" and t["mode"] == "eval"
    assert t["status"] in TRACE_STATUSES and t["status"] == "success"
    assert t["trace_id"] == r.ctx.run_id and t["started_at"] <= t["ended_at"]
    assert "someone@example.com" not in t["input"]["question"] and "[email]" in t["input"]["question"]
    assert t["input"]["db_id"] == "chinook"
    assert set(t["end_state"]) == {
        "chosen_sql",
        "result_hash",
        "row_count",
        "confidence",
        "grounding_removed",
        "sandbox_used",
        "clarified",
        "confirmed",
    }
    assert set(t["final_output"]) == {"answer", "sql", "chart", "plan"}
    assert t["final_output"]["chart"]["mark"] in {"bar", "line", "point", "area", "arc"}
    assert t["metrics"].keys() == {
        "llm_calls",
        "tool_calls",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "list_price_cost_usd",
    }
    assert t["metrics"]["llm_calls"] == len([s for s in t["spans"] if s["kind"] == "llm"]) > 0
    assert t["metrics"]["tool_calls"] == len([s for s in t["spans"] if s["kind"] == "tool"]) > 0
    assert t["metrics"]["list_price_cost_usd"] == 0.0  # the fake model is free
    assert t["feedback"] == {"thumbs": None, "comment": None}
    ids = {s["span_id"] for s in t["spans"]}
    assert len(ids) == len(t["spans"])
    for s in t["spans"]:
        assert {
            "span_id",
            "parent_id",
            "kind",
            "name",
            "status",
            "started_at",
            "duration_ms",
            "provider",
            "model",
            "tokens_in",
            "tokens_out",
            "error",
            "input_redacted",
            "output_redacted",
            "attributes",
        } <= set(s)
        assert s["kind"] in SPAN_KINDS
        assert s["status"] in {"ok", "error", "blocked"}
        assert s["parent_id"] is None or s["parent_id"] in ids
        assert isinstance(s["duration_ms"], int) and s["duration_ms"] >= 0
        if s["kind"] == "llm":
            assert s["provider"] == "fake" and s["model"] == "fake-llm" and s["tokens_in"] > 0
    names = {s["name"] for s in t["spans"]}
    assert {
        "input_guard",
        "planner",
        "schema_linker",
        "sql_agent",
        "sql_guard",
        "executor",
        "vote",
        "verifier",
        "narrator",
        "output_guard",
    } <= names
    # LLM calls made inside a candidate are children of that candidate's sql_agent span
    agents = {s["span_id"] for s in t["spans"] if s["name"] == "sql_agent"}
    assert any(s["parent_id"] in agents for s in t["spans"] if s["kind"] == "llm")
    json.dumps(t)  # serializable as-is


async def test_live_high_confidence_answer_feeds_the_sql_cache(ask):
    from datapilot.index import cache

    r = await ask("chinook", ARTISTS, mode="live")
    assert r.confidence == "High"
    for _ in range(50):  # the store runs as a background task
        if cache.recent_examples("chinook"):
            break
        await asyncio.sleep(0.02)
    assert cache.recent_examples("chinook")[0]["sql"] == r.out.end_state["chosen_sql"]
    again = await ask("chinook", ARTISTS)
    assert "c1-cache" in again.candidates()  # reused as one candidate, still executed and voted on
    assert again.candidates()["c1-cache"]["status"] == "ok"
