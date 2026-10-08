"""Test harness: fake LLM only, no provider keys, a throwaway app database and an ES256 test keypair.

Everything here runs before any `datapilot` module calls `get_settings()`, so the settings object the suite
sees never comes from backend/.env or the developer's shell.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

BACKEND = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="datapilot-tests-"))
JWT_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
JWT_PUBLIC_PEM = (
    JWT_PRIVATE_KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)

TEST_ENV = {
    "APP_ENV": "test",
    "FAKE_LLM": "true",
    "GROQ_API_KEY": "",
    "GEMINI_API_KEY": "",
    "ALT_MODEL": "openai/gpt-oss-120b",
    "DATABASE_URL": f"sqlite:///{TMP / 'app.db'}",
    "DBS_DIR": str(BACKEND / "data" / "dbs"),
    "PROFILE_PATH": str(BACKEND / "profiles" / "default.json"),
    "PROFILE_SOURCE": "local",
    "AGENTFORGE_URL": "",
    "AGENTFORGE_KEY": "",
    "EVAL_MODE": "false",
    "PRICE_TABLE_JSON": "",
    "JWT_PUBLIC_KEY": JWT_PUBLIC_PEM,
    "JWT_ISSUER": "datapilot-web",
    "JWT_AUDIENCE": "datapilot-api",
    "ALLOWED_ORIGINS": "http://localhost:3000",
    "SCAN_CONFIRM_ROWS": "100000",
    "MAX_K": "3",
    "MAX_REPAIRS": "2",
    "QUESTION_CALL_BUDGET": "25",
    "QUESTION_TOKEN_BUDGET": "60000",
    "QUESTION_WALL_S": "90",
    "SQL_TIMEOUT_S": "5",
    "SANDBOX_WAIT_S": "60",
    "HUMAN_WAIT_S": "900",
    "GUARD_THRESHOLD": "0.8",
    "RATE_LIMITS_JSON": '{"guest": [10, 30], "user": [30, 100], "admin": [1000, 10000]}',
    "RATE_IP_PER_MIN": "60",
}
os.environ.update(TEST_ENV)

from datapilot import config  # noqa: E402  (env must be set first)

config.Settings.model_config["env_file"] = None  # never read backend/.env in tests
config.get_settings.cache_clear()

from datapilot.guards import input as input_guard  # noqa: E402
from datapilot.llm import fake, providers, quota, router  # noqa: E402


def _no_network(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("Network access to LLM providers is disabled in tests.")


@pytest.fixture(scope="session", autouse=True)
def _test_settings():
    s = config.get_settings()
    assert s.fake_llm, "tests must run with FAKE_LLM=true"
    assert not s.gemini_api_key and not s.groq_api_key, "tests must never see provider keys"
    assert not s.agentforge_url and s.profile_source == "local"
    assert str(TMP) in s.sqlalchemy_url, "tests must use the throwaway app database"
    from datapilot.db.session import get_engine, init_db

    init_db()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(providers, "client", _no_network)  # safety net: no HTTP client for any provider
        yield s
    get_engine().dispose()
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_state():
    fake.reset()
    router.reset_for_tests()
    quota.reset_for_tests()
    input_guard._cache.clear()
    yield
    fake.reset()
    router.reset_for_tests()
    quota.reset_for_tests()
    input_guard._cache.clear()


@pytest.fixture(scope="session")
def catalog():
    from datapilot.index.catalog import load_catalog

    cat = load_catalog()
    assert set(cat) >= {"chinook", "superhero", "student_club", "formula_1", "european_football_2"}
    return cat


@pytest.fixture
def settings():
    return config.get_settings()


@pytest.fixture(scope="session")
def jwt_private_key():
    """Private half of the test keypair (the API is configured with the public half)."""
    return JWT_PRIVATE_KEY


# ----------------------------------------------------------------------------- graph runner


@dataclass
class Asked:
    out: Any  # runner.RunOutcome
    ctx: Any  # tracing.RunCtx
    events: list[tuple[str, dict]] = field(default_factory=list)
    interrupts: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.out.status

    @property
    def state(self) -> dict:
        return self.out.state

    @property
    def answer(self) -> str:
        return self.out.state.get("answer") or ""

    @property
    def confidence(self) -> str | None:
        return self.out.end_state["confidence"]

    def event_names(self) -> list[str]:
        return [e for e, _ in self.events]

    def candidates(self, step: int = 0) -> dict[str, dict]:
        results = self.out.state.get("step_results") or []
        return {c["id"]: c for c in results[step]["candidates"]} if len(results) > step else {}

    def spans(self, kind: str | None = None, name: str | None = None) -> list[dict]:
        return [
            s
            for s in self.out.trace["spans"]
            if (kind is None or s["kind"] == kind) and (name is None or s["name"] == name)
        ]


Answer = Callable[[dict], Any] | dict[str, Any] | None


async def run_ask(
    db_id: str,
    question: str,
    *,
    answers: Answer = None,
    flags: dict | None = None,
    budget: Any = None,
    mode: str = "eval",
) -> Asked:
    """Run one question through the real graph with the fake LLM. `answers` resolves interrupts:
    a dict {interrupt type: value or callable(payload)} or a callable(payload) (sync or async)."""
    from datapilot.profile import default_profile
    from datapilot.runner import run_question
    from datapilot.tracing import RunCtx

    events: list[tuple[str, dict]] = []
    interrupts: list[dict] = []
    ctx = RunCtx(
        run_id=str(uuid.uuid4()),
        db_id=db_id,
        profile=default_profile(),
        emit=lambda e, d: events.append((e, d)),
        mode=mode,
        flags=dict(flags or {}),
    )
    if budget is not None:
        ctx.budget = budget

    async def on_interrupt(payload: dict) -> Any:
        interrupts.append(payload)
        kind = payload["type"]
        if answers is None:
            raise AssertionError(f"unexpected interrupt {kind}: {payload}")
        value = answers.get(kind) if isinstance(answers, dict) else answers
        if isinstance(answers, dict) and kind not in answers:
            raise AssertionError(f"unexpected interrupt {kind}: {payload}")
        if callable(value):
            value = value(payload)
            if isinstance(value, Awaitable):
                value = await value
        return value

    out = await run_question(ctx, question, on_interrupt=on_interrupt)
    return Asked(out, ctx, events, interrupts)


@pytest.fixture
def ask():
    return run_ask


@pytest.fixture(autouse=True)
def clean_sql_cache():
    """The verified-SQL cache feeds later runs (live-mode High answers become a candidate), so every test
    starts and ends with it empty — otherwise test order would change candidate sets."""
    from sqlalchemy import delete

    from datapilot.db.schema import sql_cache as sql_cache_table
    from datapilot.db.session import run_sync
    from datapilot.index import cache

    def wipe() -> None:
        run_sync(lambda c: c.execute(delete(sql_cache_table)))
        cache._recent.clear()

    wipe()
    yield
    wipe()
