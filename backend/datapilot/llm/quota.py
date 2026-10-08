"""Daily quota ledger per model kind (SPEC §9.2). At 90% of a budget the router falls back; when every
provider is exhausted the app switches to "quota reached" (browse-only) mode.

Counts live in memory and are flushed to `usage_counters` so a restart (or a second process) keeps the
day's totals. Budgets are DataPilot's share of the free tiers (§S.1).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime

from sqlalchemy import select

from datapilot.config import get_settings
from datapilot.db.schema import usage_counters
from datapilot.db.session import run_sync

log = logging.getLogger(__name__)
_lock = threading.Lock()
_counts: dict[tuple[date, str], list[int]] = {}  # (day, kind) -> [requests, tokens]
_loaded: set[date] = set()
THRESHOLD = 0.9


def _today() -> date:
    return datetime.now(UTC).date()


def budgets() -> dict[str, tuple[int, int]]:
    s = get_settings()
    return {
        "main": (s.daily_budget_main_requests, s.daily_budget_main_tokens),
        "lite": (s.daily_budget_lite_requests, s.daily_budget_lite_tokens),
        "fast": (s.daily_budget_fast_requests, s.daily_budget_fast_tokens),
        "alt": (s.daily_budget_alt_requests, s.daily_budget_alt_tokens),
        "guard": (s.daily_budget_guard_requests, 10**9),
        "embed": (s.daily_budget_embed_requests, 10**9),
    }


def _load(day: date) -> None:
    if day in _loaded:
        return
    try:
        rows = run_sync(lambda c: c.execute(select(usage_counters).where(usage_counters.c.day == day)).all())
        for r in rows:
            _counts[(day, r.kind)] = [int(r.requests or 0), int(r.tokens or 0)]
    except Exception as e:  # noqa: BLE001 — the ledger must never break a request
        log.warning("quota load failed: %s", e)
    _loaded.add(day)


def available(kind: str) -> bool:
    day = _today()
    with _lock:
        _load(day)
        req, tok = _counts.get((day, kind), [0, 0])
    max_req, max_tok = budgets().get(kind, (10**9, 10**9))
    return req < THRESHOLD * max_req and tok < THRESHOLD * max_tok


def mark_exhausted(kind: str) -> None:
    """The provider reported its daily cap: stop routing to this kind until tomorrow (UTC)."""
    day = _today()
    max_req, _ = budgets().get(kind, (0, 0))
    with _lock:
        c = _counts.setdefault((day, kind), [0, 0])
        c[0] = max(c[0], max_req)


def record(kind: str, model: str, tokens: int) -> None:
    day = _today()
    with _lock:
        _load(day)
        c = _counts.setdefault((day, kind), [0, 0])
        c[0] += 1
        c[1] += tokens
        req, tok = c
    try:
        _upsert(day, model, kind, req, tok)
    except Exception as e:  # noqa: BLE001
        log.warning("quota flush failed: %s", e)


def _upsert(day: date, model: str, kind: str, req: int, tok: int) -> None:
    def fn(conn):  # type: ignore[no-untyped-def]
        where = (usage_counters.c.day == day) & (usage_counters.c.kind == kind)
        res = conn.execute(usage_counters.update().where(where).values(requests=req, tokens=tok, model=model))
        if res.rowcount == 0:
            conn.execute(usage_counters.insert().values(day=day, model=model, kind=kind, requests=req, tokens=tok))

    run_sync(fn)


def snapshot() -> dict:
    day = _today()
    with _lock:
        _load(day)
        out = {}
        for kind, (max_req, max_tok) in budgets().items():
            req, tok = _counts.get((day, kind), [0, 0])
            out[kind] = {"requests": req, "tokens": tok, "max_requests": max_req, "max_tokens": max_tok}
    return out


def all_exhausted() -> bool:
    s = get_settings()
    if s.fake_llm:
        return False
    kinds = []
    if s.gemini_ready:
        kinds += ["main"]
    if s.groq_ready:
        kinds += ["fast", "alt"] if s.alt_model else ["fast"]
    return bool(kinds) and not any(available(k) for k in kinds)


def reset_for_tests() -> None:
    with _lock:
        _counts.clear()
        _loaded.clear()
