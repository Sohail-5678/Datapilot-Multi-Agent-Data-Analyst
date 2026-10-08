"""Published paid list prices (USD per 1M tokens, input/output) so cost is measurable even at $0 (SPEC §9.2).

Snapshot from public pricing trackers, Sept–Oct 2026 (Gemini 3 Flash $0.50/$3.00, Gemini 3.1 Flash-Lite
$0.25/$1.50, gemini-embedding-001 $0.15). Groq Qwen 27B is an estimate from Groq's Qwen3-32B list
price. Override with PRICE_TABLE_JSON='{"model-substring": [in, out]}'. Verify before quoting.
"""

from __future__ import annotations

import json
from functools import lru_cache

from datapilot.config import get_settings

DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "flash-lite": (0.25, 1.50),
    "gemini-3-flash": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-flash": (0.50, 3.00),
    "embedding": (0.15, 0.0),
    "qwen": (0.29, 0.59),
    "gpt-oss-120b": (0.15, 0.60),
    "gpt-oss-20b": (0.075, 0.30),
    "prompt-guard": (0.03, 0.03),
}


@lru_cache
def price_table() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    raw = get_settings().price_table_json
    if raw:
        table.update({k: (float(v[0]), float(v[1])) for k, v in json.loads(raw).items()})
    return table


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    m = model.lower()
    for key, (pin, pout) in price_table().items():  # first (most specific) substring wins
        if key in m:
            return (tokens_in * pin + tokens_out * pout) / 1_000_000
    return 0.0
