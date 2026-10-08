"""Shared plumbing for the benchmark harness (`datapilot.bench`) and the AgentForge eval adapter
(`datapilot.eval_adapter`): BIRD file layout, execution match on a read-only file, the persistent LLM
response cache, free-tier request pacing, guard reports from trace spans, and run-wide switches.

Nothing here changes agent behaviour; it only wires the existing RunCtx hooks (`llm_cache`, `db_path`,
`database`, `flags`) and wraps the provider clients to count and pace real HTTP requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import sqlite3
import threading
import time
from collections import Counter, deque
from collections.abc import Iterator, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datapilot.config import BACKEND_ROOT, get_settings
from datapilot.index.catalog import Database, load_database
from datapilot.profile import Profile
from datapilot.sql.compare import execution_match
from datapilot.sql.executor import ExecResult, execute_readonly

REPO_ROOT = BACKEND_ROOT.parent
BENCH_DIR = REPO_ROOT / "bench"
LLM_CACHE_PATH = BENCH_DIR / ".llm_cache" / "responses.sqlite"
EX_ROW_CAP = 500_000  # BIRD fetches every row; this only guards memory
GOLD_TIMEOUT_S = 60.0
PRED_TIMEOUT_S = 30.0


# ---------------------------------------------------------------- run-wide switches


def enable_fake_llm() -> None:
    """Switch the router to the deterministic fake model (must run before the first LLM call)."""
    os.environ["FAKE_LLM"] = "true"
    get_settings.cache_clear()


def use_app_db(url: str) -> None:
    """Point the app data store (quota ledger, schema embeddings, SQL cache) at `url` for this process."""
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    from datapilot.db.session import get_engine

    get_engine.cache_clear()


def pinned_profile(profile: Profile) -> Profile:
    """Same prompts/params, but every routed step uses only its primary model kind (no fallback), so a
    benchmark config is measured on one model per step. Also disables the router's `alt` last resort."""
    p = copy.deepcopy(profile)
    p.routing = {
        step: {"primary": (r if isinstance(r, str) else r.get("primary")), "fallback": []}
        for step, r in p.routing.items()
    }
    get_settings().alt_model = ""
    return p


def kinds_for_steps(profile: Profile, steps: list[str]) -> dict[str, str]:
    out = {}
    for step in steps:
        r = profile.routing.get(step)
        if isinstance(r, dict):
            out[step] = r.get("primary", "")
        elif isinstance(r, str):
            out[step] = r
    return out


def provider_ready(kind: str) -> bool:
    s = get_settings()
    if s.fake_llm:
        return True
    return s.gemini_ready if kind in ("main", "lite") else s.groq_ready


# ---------------------------------------------------------------- BIRD files


def resolve_bird_dir(arg: str | os.PathLike | None = None) -> Path | None:
    """Find the folder that holds `dev_databases/` (and `mini_dev_sqlite.json`).

    Accepts the MINIDEV folder itself or any parent (the official zip nests `minidev/MINIDEV`)."""
    cands: list[Path] = []
    if arg:
        cands.append(Path(arg))
    if os.environ.get("BIRD_DIR"):
        cands.append(Path(os.environ["BIRD_DIR"]))
    cands += [REPO_ROOT / ".cache" / "minidev", BENCH_DIR / ".bird"]
    for c in cands:
        c = c.expanduser().resolve()
        if (c / "dev_databases").is_dir():
            return c
        if c.is_dir():
            for hit in sorted(c.glob("**/dev_databases")):
                if hit.is_dir() and "__MACOSX" not in hit.parts:
                    return hit.parent
        if arg and c == Path(arg).expanduser().resolve():
            break  # an explicit --bird-dir that doesn't exist is an error, not a fallback
    return None


def bird_paths(bird_dir: Path, db_id: str) -> tuple[Path, Path]:
    folder = bird_dir / "dev_databases" / db_id
    return folder / f"{db_id}.sqlite", folder / "database_description"


_bird_dbs: dict[str, Database] = {}


def load_bird_database(bird_dir: Path, db_id: str) -> Database:
    """The FULL BIRD file as a catalog Database. Its db_id is `bird:<db_id>` so the schema/value index
    caches (keyed by db_id) never mix with the slimmed demo copies of the same database."""
    key = f"bird:{db_id}"
    if key not in _bird_dbs:
        path, desc = bird_paths(bird_dir, db_id)
        if not path.exists():
            raise FileNotFoundError(f"BIRD database not found: {path}")
        _bird_dbs[key] = load_database(key, path, desc, {"title": db_id, "source": "BIRD Mini-Dev (CC BY-SA 4.0)"})
    return _bird_dbs[key]


async def warm_indexes(db: Database, linking: bool) -> None:
    """Build the value index and schema index (and embeddings when Gemini is configured) before the clock
    starts, so a one-off index build is not charged to the first question's latency/budget."""
    from datapilot.index.schema_index import ensure_embeddings, get_index
    from datapilot.index.value_index import get_values

    get_index(db)
    if linking:
        await asyncio.to_thread(get_values, db)
        with contextlib.suppress(Exception):  # lexical ranking still works without embeddings
            await ensure_embeddings(db)


def forget_indexes(db_id: str) -> None:
    from datapilot.index import schema_index, value_index

    schema_index._indexes.pop(db_id, None)
    value_index._index.pop(db_id, None)


# ---------------------------------------------------------------- execution match


def run_for_ex(path: Path, sql: str, timeout_s: float) -> ExecResult:
    return execute_readonly(path, sql, timeout_s=timeout_s, row_cap=EX_ROW_CAP)


def compute_ex(
    path: Path,
    pred_sql: str | None,
    gold_sql: str,
    *,
    pred_timeout_s: float = PRED_TIMEOUT_S,
    gold_timeout_s: float = GOLD_TIMEOUT_S,
) -> dict:
    """BIRD EX: run predicted and gold SQL on the same read-only file; set comparison of the rows."""
    gold = run_for_ex(path, gold_sql, gold_timeout_s)
    out: dict[str, Any] = {
        "ex": False,
        "gold_ok": gold.ok,
        "gold_rows": len(gold.rows) if gold.ok else None,
        "pred_rows": None,
        "ex_error": None,
    }
    if not gold.ok:
        out["ex_error"] = f"gold SQL failed: {gold.error}"
        return out
    if not pred_sql:
        out["ex_error"] = "no predicted SQL"
        return out
    pred = run_for_ex(path, pred_sql, pred_timeout_s)
    if not pred.ok:
        out["ex_error"] = f"predicted SQL failed: {pred.error}"
        return out
    out["pred_rows"] = len(pred.rows)
    out["ex"] = execution_match(pred.rows, gold.rows)
    return out


def predicted_sql(state: dict) -> str | None:
    """The SQL the agent chose for its last successful step, without the LIMIT the guard appended
    (the guard only adds a display cap; BIRD compares full result sets)."""
    last_ok = next((r for r in reversed(state.get("step_results") or []) if r.get("ok")), None)
    if not last_ok:
        return None
    return last_ok.get("unlimited_sql") or last_ok.get("sql")


# ---------------------------------------------------------------- persistent LLM response cache


class SqliteResponseCache(MutableMapping):
    """Dict-like store for the router's response cache (`RunCtx.llm_cache`): key = sha256 of
    (model, system, prompt, json_mode, temperature), value = the Completion fields. Reruns are free."""

    def __init__(self, path: Path = LLM_CACHE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS responses (k TEXT PRIMARY KEY, v TEXT NOT NULL, created REAL)")
        self._conn.commit()
        self.hits = 0
        self.writes = 0

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM responses WHERE k = ?", (key,)).fetchone() is not None

    def __getitem__(self, key: str) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT v FROM responses WHERE k = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        self.hits += 1
        return json.loads(row[0])

    def __setitem__(self, key: str, value: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)", (key, json.dumps(value), time.time())
            )
            self._conn.commit()
        self.writes += 1

    def __delitem__(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM responses WHERE k = ?", (key,))
            self._conn.commit()

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            keys = [r[0] for r in self._conn.execute("SELECT k FROM responses")]
        return iter(keys)

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------- free-tier pacing + request counting

# The RunCtx of the question/case currently running in this asyncio task (time spent waiting for the
# pacer is credited to its budget like human waits, so free-tier throttling isn't reported as latency).
CURRENT_CTX: ContextVar[Any] = ContextVar("dp_eval_ctx", default=None)


@dataclass
class _Window:
    events: deque = field(default_factory=deque)  # [t, tokens] pairs, last 60 s
    lock: asyncio.Lock | None = None


class Pacer:
    """Rolling 60-second request/token limiter per provider, applied to every real HTTP call the router
    makes (retries included). Also counts provider requests for `--budget-calls`."""

    def __init__(self, limits: dict[str, tuple[int, int]]):
        self.limits = limits  # provider -> (requests/min, tokens/min); 0 = unlimited
        self.windows: dict[str, _Window] = {}
        self.requests: Counter = Counter()
        self.errors: Counter = Counter()
        self.waited_s = 0.0

    @property
    def total_requests(self) -> int:
        return sum(self.requests.values())

    async def acquire(self, provider: str, est_tokens: int) -> list:
        rpm, tpm = self.limits.get(provider, (0, 0))
        w = self.windows.setdefault(provider, _Window())
        if w.lock is None:
            w.lock = asyncio.Lock()
        waited = 0.0
        async with w.lock:
            while True:
                now = time.monotonic()
                while w.events and now - w.events[0][0] >= 60.0:
                    w.events.popleft()
                used_tok = sum(e[1] for e in w.events)
                ok_req = not rpm or len(w.events) + 1 <= rpm
                ok_tok = not tpm or not w.events or used_tok + est_tokens <= tpm
                if ok_req and ok_tok:
                    break
                pause = max(0.05, 60.0 - (now - w.events[0][0]) + 0.05)
                await asyncio.sleep(pause)
                waited += pause
            ev = [time.monotonic(), est_tokens]
            w.events.append(ev)
        if waited:
            self.waited_s += waited
            ctx = CURRENT_CTX.get()
            if ctx is not None:
                ctx.budget.waited_s += waited
        return ev


_PACER: Pacer | None = None


def install_pacer(limits: dict[str, tuple[int, int]]) -> Pacer:
    """Wrap the router's provider functions (idempotent). Works for fake runs too (they never reach it)."""
    global _PACER
    from datapilot.llm import router
    from datapilot.llm.providers import ProviderError, gemini_generate, groq_chat

    _PACER = Pacer(limits)
    pacer = _PACER

    def wrap(provider: str, fn):  # type: ignore[no-untyped-def]
        async def paced(model, system, prompt, *, json_mode, temperature, max_tokens):  # type: ignore[no-untyped-def]
            est = int((len(system) + len(prompt)) / 3.5) + max_tokens
            ev = await pacer.acquire(provider, est)
            pacer.requests[provider] += 1
            try:
                res = await fn(
                    model, system, prompt, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
                )
            except ProviderError:
                pacer.errors[provider] += 1
                raise
            ev[1] = res.tokens_in + res.tokens_out
            return res

        paced.__wrapped__ = fn  # type: ignore[attr-defined]
        return paced

    router.groq_chat = wrap("groq", groq_chat)  # type: ignore[attr-defined]
    router.gemini_generate = wrap("gemini", gemini_generate)  # type: ignore[attr-defined]
    return pacer


# ---------------------------------------------------------------- trace helpers


GUARDS = ("input_guard", "sql_guard", "output_guard")


def guard_report(trace: dict) -> dict:
    """Which guard (if any) blocked something in this run, from the trace spans with status `blocked`."""
    events = []
    for sp in trace.get("spans") or []:
        if sp.get("kind") == "guard" and sp.get("status") == "blocked":
            out = sp.get("output_redacted") or {}
            reason = out.get("reason") or out.get("value") or ""
            if sp.get("name") == "input_guard":
                reason = reason or ",".join(out.get("patterns") or [])
            if sp.get("name") == "output_guard":
                reason = reason or f"removed {len(out.get('removed') or [])} ungrounded sentence(s)"
            events.append({"guard": sp.get("name"), "span_id": sp.get("span_id"), "reason": str(reason)[:300]})
    blocked = list(dict.fromkeys(e["guard"] for e in events))
    input_span = next((sp for sp in trace.get("spans") or [] if sp.get("name") == "input_guard"), None)
    return {
        "blocked_by": blocked,
        "events": events[:20],
        "input_verdict": ((input_span or {}).get("output_redacted") or {}).get("action"),
        "sql_guard_checks": sum(1 for sp in trace.get("spans") or [] if sp.get("name") == "sql_guard"),
    }


def llm_span_stats(trace: dict) -> dict:
    models: Counter = Counter()
    cached = live = 0
    for sp in trace.get("spans") or []:
        if sp.get("kind") != "llm":
            continue
        models[sp.get("model") or "?"] += 1
        if (sp.get("attributes") or {}).get("cached"):
            cached += 1
        else:
            live += 1
    return {"models": dict(models), "cached_calls": cached, "live_calls": live}


def is_infra_failure(state: dict) -> bool:
    """No candidate ran because no model could be reached (rate limit / outage) — not the agent's fault."""
    results = state.get("step_results") or []
    if any(r.get("ok") for r in results):
        return False
    cands = [c for r in results for c in r.get("candidates") or []]
    return bool(cands) and all((c.get("error") or "").startswith("No model available") for c in cands)


def too_large_for_provider(state: dict) -> bool:
    cands = [c for r in state.get("step_results") or [] for c in r.get("candidates") or []]
    return any(("413" in (c.get("error") or "")) or ("too large" in (c.get("error") or "").lower()) for c in cands)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n")
    tmp.replace(path)
