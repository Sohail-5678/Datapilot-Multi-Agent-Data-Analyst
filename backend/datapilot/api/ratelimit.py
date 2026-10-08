"""In-memory rate limits (SPEC §10.4). Render runs one instance, so process memory is the source of truth.

- Questions per user: guest 10/hour · 30/day, user 30/hour · 100/day, admin effectively unlimited.
- Requests per IP: token bucket (RATE_IP_PER_MIN).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from datapilot.api.errors import ApiError
from datapilot.config import get_settings

_questions: dict[str, deque[float]] = defaultdict(deque)
_buckets: dict[str, tuple[float, float]] = {}


def check_question(user_key: str, role: str) -> dict:
    per_hour, per_day = get_settings().rate_limits.get(role, (10, 30))
    now = time.time()
    q = _questions[user_key]
    while q and q[0] < now - 86_400:
        q.popleft()
    last_hour = sum(1 for t in q if t > now - 3600)
    if last_hour >= per_hour:
        oldest = next(t for t in q if t > now - 3600)
        raise ApiError(
            429,
            "rate_limited",
            f"You've asked {per_hour} questions in the last hour. Try again soon.",
            {"retry_after": int(oldest + 3600 - now) + 1},
        )
    if len(q) >= per_day:
        raise ApiError(
            429,
            "rate_limited",
            f"Daily limit of {per_day} questions reached for this account.",
            {"retry_after": int(q[0] + 86_400 - now) + 1},
        )
    q.append(now)
    return {"hour_left": per_hour - last_hour - 1, "day_left": per_day - len(q)}


def check_ip(ip: str) -> None:
    rate = get_settings().rate_ip_per_min / 60.0
    cap = float(get_settings().rate_ip_per_min)
    now = time.monotonic()
    tokens, ts = _buckets.get(ip, (cap, now))
    tokens = min(cap, tokens + (now - ts) * rate)
    if tokens < 1:
        raise ApiError(429, "rate_limited", "Too many requests from this address.", {"retry_after": 5})
    _buckets[ip] = (tokens - 1, now)
    if len(_buckets) > 20_000:
        _buckets.clear()


def reset_for_tests() -> None:
    _questions.clear()
    _buckets.clear()
