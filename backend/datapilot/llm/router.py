"""Model router (SPEC §6.4, §9.2): primary → fallback per step, quota ledger, retries with backoff + jitter,
a per-provider circuit breaker, the per-question budget, list-price cost, and an `llm` span per call.

Kinds: `main` (Gemini Flash), `lite` (Gemini Flash-Lite), `fast` (Groq Qwen 27B) and `alt` (Groq gpt-oss-120b,
a last-resort fallback with a small daily share — §S.1 gives that model's budget mainly to ReturnPilot).
Which kind each step uses comes from the agent profile's `routing`; the model ids come from env vars.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from datapilot.config import get_settings
from datapilot.llm import quota
from datapilot.llm.prices import cost_usd
from datapilot.llm.providers import Completion, ProviderError, gemini_generate, groq_chat
from datapilot.tracing import BudgetExceeded, RunCtx

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

BREAKER_FAILS = 5
BREAKER_OPEN_S = 60.0
MAX_RETRIES = 2


class LLMUnavailable(Exception):
    """Every provider in the chain failed, is unconfigured, or is over its daily quota."""

    def __init__(self, message: str, quota_exhausted: bool = False):
        super().__init__(message)
        self.quota_exhausted = quota_exhausted


@dataclass
class _Breaker:
    fails: int = 0
    open_until: float = 0.0


_breakers: dict[str, _Breaker] = {}


def model_for(kind: str) -> tuple[str, str]:
    s = get_settings()
    return {
        "main": ("gemini", s.main_model),
        "lite": ("gemini", s.lite_model),
        "fast": ("groq", s.fast_model),
        "alt": ("groq", s.alt_model),
    }[kind]


def _configured(provider: str) -> bool:
    s = get_settings()
    return s.gemini_ready if provider == "gemini" else s.groq_ready


def _breaker_open(provider: str) -> bool:
    b = _breakers.get(provider)
    return bool(b and b.open_until > time.monotonic())


def _cool_down(provider: str, why: str) -> None:
    """Overloaded (5xx), timed out or very slow: skip this provider for a while instead of making users wait."""
    secs = get_settings().provider_cooldown_s
    b = _breakers.setdefault(provider, _Breaker())
    b.open_until = max(b.open_until, time.monotonic() + secs)
    log.warning("%s cooling down for %.0f s (%s)", provider, secs, why)


def _note(provider: str, ok: bool) -> None:
    b = _breakers.setdefault(provider, _Breaker())
    if ok:
        b.fails = 0
        return
    b.fails += 1
    if b.fails >= BREAKER_FAILS:
        b.open_until = time.monotonic() + BREAKER_OPEN_S
        b.fails = 0
        log.warning("circuit breaker open for %s (%.0f s)", provider, BREAKER_OPEN_S)


def chain_for(ctx: RunCtx | None, step: str, default: list[str]) -> list[str]:
    routing = getattr(getattr(ctx, "profile", None), "routing", None) or {}
    r = routing.get(step)
    if isinstance(r, dict):
        return [r.get("primary", default[0]), *r.get("fallback", default[1:])]
    if isinstance(r, str):
        return [r, *[k for k in default if k != r]]
    return default


async def complete(
    ctx: RunCtx | None,
    step: str,
    system: str,
    prompt: str,
    *,
    kinds: list[str],
    json_mode: bool = False,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    span_attrs: dict | None = None,
) -> Completion:
    s = get_settings()
    if ctx is not None:
        ctx.budget.check()
    chain = chain_for(ctx, step, kinds)
    if s.alt_model and "alt" not in chain and chain[-1] != "lite":
        chain = [*chain, "alt"]  # last resort for reasoning steps (lite steps fall back to rules instead)

    if s.fake_llm:
        from datapilot.llm.fake import fake_complete

        return await _traced(
            ctx, step, "fake", "fake-llm", lambda: fake_complete(step, system, prompt, json_mode), span_attrs
        )

    errors: list[str] = []
    quota_hit = False
    for pos, kind in enumerate(chain):
        has_fallback = any(_configured(model_for(k)[0]) for k in chain[pos + 1 :] if model_for(k)[1])
        provider, model = model_for(kind)
        if not model:
            continue
        if not _configured(provider):
            errors.append(f"{provider} not configured")
            continue
        if not quota.available(kind):
            quota_hit = True
            errors.append(f"{kind} daily budget reached")
            continue
        if _breaker_open(provider):
            errors.append(f"{provider} circuit open")
            continue
        cache_key = (
            _cache_key(model, system, prompt, json_mode, temperature) if ctx and ctx.llm_cache is not None else None
        )
        if cache_key and cache_key in ctx.llm_cache:  # type: ignore[union-attr]
            hit = ctx.llm_cache[cache_key]  # type: ignore[union-attr]
            return await _traced(
                ctx, step, provider, model, _const(Completion(**hit)), {**(span_attrs or {}), "cached": True}, kind=kind
            )
        fn = gemini_generate if provider == "gemini" else groq_chat
        for attempt in range(MAX_RETRIES + 1):
            t0 = time.monotonic()
            try:
                res = await _traced(
                    ctx,
                    step,
                    provider,
                    model,
                    lambda fn=fn, model=model: fn(
                        model, system, prompt, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens
                    ),
                    {**(span_attrs or {}), "attempt": attempt + 1, "model_kind": kind},
                    kind=kind,
                )
                _note(provider, True)
                if has_fallback and time.monotonic() - t0 > s.slow_call_s:
                    _cool_down(provider, f"slow call {time.monotonic() - t0:.1f} s")
                if cache_key:
                    ctx.llm_cache[cache_key] = res.__dict__  # type: ignore[union-attr]
                return res
            except ProviderError as e:
                errors.append(str(e)[:160])
                if e.daily_cap:
                    quota.mark_exhausted(kind)
                    quota_hit = True
                    break  # daily cap: go straight to the fallback
                if e.status != 429:
                    _note(provider, False)  # rate limits are expected; only real failures trip the breaker
                if has_fallback and e.status in (0, 500, 502, 503, 504):
                    _cool_down(provider, f"HTTP {e.status or 'timeout'}")
                    break  # overloaded or stalled: the fallback answers now instead of a retry later
                if not e.retryable or attempt == MAX_RETRIES:
                    break
                wait = e.retry_after if e.retry_after is not None else 0.6 * 2**attempt
                if wait > 12:
                    break  # long per-minute wait: the fallback model is faster
                if ctx is not None:
                    ctx.budget.check()
                await asyncio.sleep(min(12.0, wait) + random.uniform(0, 0.4))
    raise LLMUnavailable("; ".join(errors) or "no provider available", quota_exhausted=quota_hit)


def _const(c: Completion):  # type: ignore[no-untyped-def]
    async def f() -> Completion:
        return c

    return f


async def _traced(ctx, step, provider, model, call, attrs, kind: str | None = None) -> Completion:  # type: ignore[no-untyped-def]
    if ctx is None:
        res = await call()
        if kind:
            quota.record(kind, model, res.tokens_in + res.tokens_out)
        return res
    with ctx.span("llm", step, **(attrs or {})) as sp:
        sp.provider, sp.model = provider, model
        res = await call()
        sp.tokens_in, sp.tokens_out = res.tokens_in, res.tokens_out
        cached = bool((attrs or {}).get("cached"))
        cost = 0.0 if provider == "fake" else cost_usd(model, res.tokens_in, res.tokens_out)
        sp.attributes["list_price_cost_usd"] = round(cost, 6)
        sp.output_redacted = {"chars": len(res.text)}
        ctx.record_llm(res.tokens_in, res.tokens_out, cost)
        if kind and not cached:
            quota.record(kind, model, res.tokens_in + res.tokens_out)
        return res


def _cache_key(model: str, system: str, prompt: str, json_mode: bool, temperature: float) -> str:
    return hashlib.sha256(json.dumps([model, system, prompt, json_mode, temperature]).encode()).hexdigest()


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> Any:
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            return json.loads(t[start : end + 1])
        raise


async def complete_model(
    ctx: RunCtx | None,
    step: str,
    system: str,
    prompt: str,
    schema: type[M],
    *,
    kinds: list[str],
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> M:
    """JSON output validated with Pydantic; one retry with the validation error (SPEC §9.1)."""
    res = await complete(
        ctx, step, system, prompt, kinds=kinds, json_mode=True, temperature=temperature, max_tokens=max_tokens
    )
    try:
        return schema.model_validate(parse_json(res.text))
    except (ValueError, ValidationError) as e:
        if ctx is not None:
            try:
                ctx.budget.check()
            except BudgetExceeded:
                raise
        fix = (
            f"{prompt}\n\nYour previous reply was not valid for the required JSON schema.\n"
            f"Error: {str(e)[:400]}\nReply again with JSON only."
        )
        res = await complete(
            ctx, step, system, fix, kinds=kinds, json_mode=True, temperature=0.0, max_tokens=max_tokens
        )
        return schema.model_validate(parse_json(res.text))


def reset_for_tests() -> None:
    _breakers.clear()
