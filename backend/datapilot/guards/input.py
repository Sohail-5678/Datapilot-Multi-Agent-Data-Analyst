"""Input guard (SPEC §10.2): Groq Llama Prompt Guard 2 + heuristics.

SQL injection in a question ("'; DROP TABLE …") is harmless by design (SQL guard + read-only engine),
so it is flagged, not blocked. Prompt injection aimed at the agents is blocked when the classifier and
the heuristics agree, and flagged (stricter reminder in prompts) when only one fires.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from datapilot.config import get_settings
from datapilot.llm import quota
from datapilot.llm.providers import prompt_guard_score

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|prompts?|rules|guardrails?|system)\b", re.I | re.S)),
    ("role_override", re.compile(r"\b(you are now|act as|pretend to be|new (system )?prompt|developer mode|DAN\b|jailbreak)", re.I)),
    ("system_tag", re.compile(r"(<\|?(system|im_start)\|?>|\[/?(system|inst)\]|^\s*system\s*:)", re.I | re.M)),
    ("exfiltrate_prompt", re.compile(r"\b(reveal|print|show|repeat|leak)\b.{0,30}\b(system prompt|your (instructions|prompt)|hidden prompt|api key|secret)", re.I | re.S)),
    ("write_sql", re.compile(r"\b(drop|delete|truncate|alter|insert|update)\s+(table|from|into|database)\b", re.I)),
    ("attach_pragma", re.compile(r"\b(attach\s+database|pragma\s+\w+|load_extension)\b", re.I)),
]
_cache: dict[str, InputVerdict] = {}


@dataclass
class InputVerdict:
    action: str  # allow | flag | block
    score: float | None = None
    source: str = "heuristic"
    patterns: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {"action": self.action, "score": self.score, "source": self.source, "patterns": self.patterns}


def heuristics(text: str) -> list[str]:
    return [name for name, rx in _PATTERNS if rx.search(text)]


async def check_input(text: str) -> InputVerdict:
    s = get_settings()
    key = hashlib.sha256(text.encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    patterns = heuristics(text)
    score = None
    if s.groq_ready and quota.available("guard"):
        score = await prompt_guard_score(text)
        if score is not None:
            quota.record("guard", s.guard_model, 0)
    injection = [p for p in patterns if p not in ("write_sql", "attach_pragma")]
    model_flag = score is not None and score >= s.guard_threshold
    if injection and (model_flag or score is None and len(injection) >= 2):
        v = InputVerdict("block", score, "prompt_guard" if score is not None else "heuristic", patterns,
                         "This looks like an attempt to change the assistant's instructions, so it wasn't run.")
    elif injection or model_flag or patterns:
        v = InputVerdict("flag", score, "prompt_guard" if score is not None else "heuristic", patterns)
    else:
        v = InputVerdict("allow", score, "prompt_guard" if score is not None else "heuristic", patterns)
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = v
    return v
