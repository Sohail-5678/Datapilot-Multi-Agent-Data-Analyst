"""Run context: trace.v1 spans, metrics and the per-question budget (SPEC §6.4, §S.2).

Every node, LLM call, guard, tool and sandbox step writes a span. The RunCtx travels with the graph in
`config["configurable"]["ctx"]` (not in the checkpointed state) so it can hold live objects.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from datapilot.config import get_settings
from datapilot.guards.pii import redact_text

# Current span chain. A ContextVar (not a list on RunCtx) so parallel graph branches — each its own asyncio
# task with a copied context — nest their spans correctly instead of under each other.
_SPAN_STACK: ContextVar[tuple[str, ...]] = ContextVar("dp_span_stack", default=())


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BudgetExceeded(Exception):
    def __init__(self, what: str):
        super().__init__(f"Question budget reached ({what}).")
        self.what = what


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    kind: str
    name: str
    started_at: str
    _t0: float
    duration_ms: int = 0
    provider: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "ok"
    error: str | None = None
    input_redacted: dict = field(default_factory=dict)
    output_redacted: dict = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return d


def _redact(obj: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(obj, str):
        s = redact_text(obj)
        return s if len(s) <= 2000 else s[:2000] + "…"
    if isinstance(obj, dict):
        return {str(k): _redact(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list | tuple):
        items = [_redact(v, depth + 1) for v in list(obj)[:20]]
        return items + ([f"… {len(obj) - 20} more"] if len(obj) > 20 else [])
    return obj


@dataclass
class Budget:
    max_calls: int
    max_tokens: int
    max_wall_s: float
    calls: int = 0
    tokens: int = 0
    waited_s: float = 0.0  # time spent waiting for humans / the browser sandbox (not counted)
    t0: float = field(default_factory=time.perf_counter)

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self.t0 - self.waited_s

    def check(self, upcoming_calls: int = 1) -> None:
        if self.calls + upcoming_calls > self.max_calls:
            raise BudgetExceeded(f"{self.max_calls} LLM calls")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"{self.max_tokens:,} tokens")
        if self.elapsed_s > self.max_wall_s:
            raise BudgetExceeded(f"{self.max_wall_s:.0f} s")

    def as_dict(self) -> dict:
        return {
            "calls_used": self.calls,
            "tokens_used": self.tokens,
            "ms_elapsed": int(self.elapsed_s * 1000),
            "limits": {"calls": self.max_calls, "tokens": self.max_tokens, "seconds": self.max_wall_s},
        }


@dataclass
class RunCtx:
    run_id: str
    db_id: str
    user_id: str = "anonymous"
    mode: str = "live"  # live | eval | bench
    case_id: str | None = None
    profile: Any = None
    flags: dict = field(default_factory=dict)  # bench/eval config switches (see agents/config_flags)
    emit: Callable[[str, dict], None] | None = None  # SSE event sink
    db_path: Any = None  # override (eval copies / BIRD files)
    database: Any = None  # catalog.Database override
    evidence: str | None = None
    llm_cache: Any = None  # bench response cache
    data: dict = field(default_factory=dict)  # data_ref -> {columns, rows, total_rows} (table + sandbox data)
    budget: Budget = field(default_factory=lambda: _default_budget())
    spans: list[Span] = field(default_factory=list)
    started_at: str = field(default_factory=now_iso)
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0

    def send(self, event: str, data: dict) -> None:
        if self.emit:
            self.emit(event, data)

    @contextmanager
    def span(self, kind: str, name: str, **attrs: Any) -> Iterator[Span]:
        stack = _SPAN_STACK.get()
        sp = Span(
            span_id=f"s{len(self.spans) + 1}-{uuid.uuid4().hex[:6]}",
            parent_id=stack[-1] if stack else None,
            kind=kind,
            name=name,
            started_at=now_iso(),
            _t0=time.perf_counter(),
            attributes=dict(attrs),
        )
        self.spans.append(sp)
        token = _SPAN_STACK.set((*stack, sp.span_id))
        try:
            yield sp
        except BudgetExceeded as e:
            sp.status, sp.error = "error", str(e)
            raise
        except Exception as e:
            sp.status, sp.error = "error", f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            if not sp.duration_ms:  # a span may report its own duration (the browser sandbox's run time)
                sp.duration_ms = int((time.perf_counter() - sp._t0) * 1000)
            _SPAN_STACK.reset(token)
            if kind == "tool":
                self.tool_calls += 1

    def set_io(self, sp: Span, inp: Any = None, out: Any = None) -> None:
        if inp is not None:
            sp.input_redacted = _redact(inp if isinstance(inp, dict) else {"value": inp})
        if out is not None:
            sp.output_redacted = _redact(out if isinstance(out, dict) else {"value": out})

    def record_llm(self, tokens_in: int, tokens_out: int, cost: float) -> None:
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost
        self.budget.calls += 1
        self.budget.tokens += tokens_in + tokens_out

    def metrics(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": int(self.budget.elapsed_s * 1000),
            "list_price_cost_usd": round(self.cost_usd, 6),
        }

    def trace(
        self, *, status: str, question: str, final_output: dict, end_state: dict, feedback: dict | None = None
    ) -> dict:
        s = get_settings()
        return {
            "contract_version": "trace.v1",
            "trace_id": self.run_id,
            "agent": "datapilot",
            "agent_version": s.git_sha,
            "profile_version": getattr(self.profile, "version_label", "datapilot@1"),
            "mode": "eval" if self.mode in ("eval", "bench") else "live",
            "case_id": self.case_id,
            "started_at": self.started_at,
            "ended_at": now_iso(),
            "status": status,
            "input": {"question": redact_text(question), "db_id": self.db_id},
            "final_output": final_output,
            "end_state": end_state,
            "spans": [sp.to_dict() for sp in self.spans],
            "metrics": self.metrics(),
            "feedback": feedback or {"thumbs": None, "comment": None},
        }


def _default_budget() -> Budget:
    s = get_settings()
    return Budget(s.question_call_budget, s.question_token_budget, s.question_wall_s)
