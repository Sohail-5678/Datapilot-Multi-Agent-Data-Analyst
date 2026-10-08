"""Agent profile loader (profile.v1, SPEC §9.1, §9.3, §18.2, §S.3).

Loads the active profile from AgentForge (5-minute cache) when PROFILE_SOURCE=agentforge, else the bundled
default. Locked items (guards, read-only enforcement, SCAN_CONFIRM_ROWS, sandbox limits, grounding, budget
ceilings, PII policy) live in code/config; a profile that tries to set them is rejected.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from datapilot.config import get_settings

log = logging.getLogger(__name__)

REQUIRED_PROMPTS = (
    "planner",
    "schema_prune",
    "sql_direct",
    "sql_plan",
    "sql_fewshot",
    "repair",
    "verifier",
    "analyst",
    "chart",
    "narrator",
)
KINDS = {"main", "lite", "fast", "alt"}
STRATEGIES = {"direct", "plan_then_sql", "few_shot"}
# Keys that must never appear in an optimizable profile (anywhere in params/routing).
LOCKED_KEYS = {
    "scan_confirm_rows",
    "max_repairs",
    "row_cap",
    "sql_timeout_s",
    "sandbox_timeout_s",
    "sandbox_max_rows",
    "question_call_budget",
    "question_token_budget",
    "question_wall_s",
    "guard_threshold",
    "grounding_tolerance",
    "pii_columns",
    "allowed_tables",
}
PARAM_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 1.0),
    "self_consistency_k": (1, 3),
    "few_shot_pool": (0, 6),
    "max_plan_steps": (1, 4),
    "clarify_threshold": (0.0, 1.0),
    "schema_top_columns": (5, 60),
    "max_tables": (1, 8),
    "max_columns": (5, 40),
}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    version: int
    prompts: dict[str, str]
    routing: dict[str, Any]
    params: dict[str, Any]
    few_shots: list[dict] = field(default_factory=list)
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def version_label(self) -> str:
        return f"datapilot@{self.version}"

    def p(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


def _locked_keys_in(obj: Any) -> set[str]:
    """Locked keys at any depth (e.g. params.limits.row_cap), case-insensitive."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in LOCKED_KEYS:
                found.add(k.lower())
            found |= _locked_keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _locked_keys_in(v)
    return found


def validate_profile(data: dict) -> Profile:
    if data.get("contract_version") != "profile.v1" or data.get("agent") != "datapilot":
        raise ProfileError("not a datapilot profile.v1")
    prompts = data.get("prompts") or {}
    missing = [k for k in REQUIRED_PROMPTS if not isinstance(prompts.get(k), str) or not prompts[k].strip()]
    if missing:
        raise ProfileError(f"missing prompts: {missing}")
    params = dict(data.get("params") or {})
    routing = dict(data.get("routing") or {})
    bad = _locked_keys_in(params) | _locked_keys_in(routing)
    if bad:
        raise ProfileError(f"locked fields cannot be set by a profile: {sorted(bad)}")
    for key, (lo, hi) in PARAM_RANGES.items():
        if key in params:
            v = params[key]
            if not isinstance(v, int | float) or isinstance(v, bool) or not lo <= v <= hi:
                raise ProfileError(f"param {key}={v!r} outside [{lo}, {hi}]")
    strategies = params.get("candidate_strategies", ["direct", "plan_then_sql", "few_shot"])
    if not strategies or set(strategies) - STRATEGIES:
        raise ProfileError(f"candidate_strategies must be a subset of {sorted(STRATEGIES)}")
    for step, r in routing.items():
        kinds = (
            [r] if isinstance(r, str) else [r.get("primary"), *r.get("fallback", [])] if isinstance(r, dict) else [None]
        )
        if any(k not in KINDS for k in kinds):
            raise ProfileError(f"routing for {step} uses unknown model kind {kinds}")
    return Profile(
        version=int(data.get("version", 1)),
        prompts=prompts,
        routing=routing,
        params=params,
        few_shots=list(data.get("few_shots") or []),
        tool_descriptions=dict(data.get("tool_descriptions") or {}),
        raw=data,
    )


def load_file(path: Path) -> Profile:
    return validate_profile(json.loads(Path(path).read_text()))


_default: Profile | None = None
_active: tuple[float, Profile] | None = None
CACHE_S = 300


def default_profile() -> Profile:
    global _default
    if _default is None:
        _default = load_file(get_settings().profile_path)
    return _default


async def active_profile() -> Profile:
    """Active profile from AgentForge (cached 5 min) → last good → bundled default."""
    global _active
    s = get_settings()
    if s.profile_source != "agentforge" or not s.agentforge_url:
        return default_profile()
    if _active and _active[0] > time.monotonic():
        return _active[1]
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(
                f"{s.agentforge_url.rstrip('/')}/v1/profiles/datapilot/active",
                headers={"X-AgentForge-Key": s.agentforge_key},
            )
            r.raise_for_status()
            prof = validate_profile(r.json())
        _active = (time.monotonic() + CACHE_S, prof)
        return prof
    except Exception as e:  # noqa: BLE001 — never fail a question because AgentForge is down
        log.warning("active profile unavailable, using %s: %s", "last good" if _active else "default", e)
        if _active:
            _active = (time.monotonic() + 60, _active[1])
            return _active[1]
        return default_profile()
