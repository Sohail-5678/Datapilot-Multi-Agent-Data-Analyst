"""Thin REST clients for the free LLM tiers (SPEC §S.1): Gemini (AI Studio) and Groq (OpenAI-compatible).

Raw httpx instead of LangChain wrappers keeps the container small (Render free = 512 MB) and makes
token accounting, JSON mode and retries explicit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import httpx

from datapilot.config import get_settings

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(get_settings().llm_timeout_s, connect=8))
    return _client


@dataclass
class Completion:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int


class ProviderError(Exception):
    def __init__(self, provider: str, status: int, message: str, retry_after: float | None = None):
        super().__init__(f"{provider} {status}: {message[:300]}")
        self.provider = provider
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status in (408, 429, 500, 502, 503, 504) or self.status == 0

    @property
    def quota(self) -> bool:
        return self.status == 429

    @property
    def daily_cap(self) -> bool:
        """Daily quota (stop using this model today) vs per-minute limit (wait and retry)."""
        text = str(self).lower()
        return self.status == 429 and ("per day" in text or "perday" in text or "(tpd)" in text or "(rpd)" in text)


def _retry_after(r: httpx.Response) -> float | None:
    v = r.headers.get("retry-after")
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _gemini_thinking(model: str) -> dict:
    if "gemini-3" in model:
        return {"thinkingConfig": {"thinkingLevel": get_settings().gemini_thinking_level}}
    if "2.5" in model:
        return {"thinkingConfig": {"thinkingBudget": 0}}
    return {}


async def gemini_generate(
    model: str, system: str, prompt: str, *, json_mode: bool, temperature: float, max_tokens: int
) -> Completion:
    s = get_settings()
    gen: dict = {"temperature": temperature, "maxOutputTokens": max_tokens, **_gemini_thinking(model)}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"x-goog-api-key": s.gemini_api_key}
    try:
        r = await client().post(url, headers=headers, json=body, timeout=s.gemini_timeout_s)
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in gen:
            gen.pop("thinkingConfig")  # this model doesn't accept the configured thinking level
            r = await client().post(url, headers=headers, json=body, timeout=s.gemini_timeout_s)
    except httpx.HTTPError as e:
        raise ProviderError("gemini", 0, f"{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise ProviderError("gemini", r.status_code, r.text, _retry_after(r))
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise ProviderError("gemini", 422, f"empty response ({reason})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    usage = data.get("usageMetadata") or {}
    out_tokens = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
    return Completion(text, "gemini", model, int(usage.get("promptTokenCount", 0)), out_tokens)


async def groq_chat(
    model: str, system: str, prompt: str, *, json_mode: bool, temperature: float, max_tokens: int
) -> Completion:
    s = get_settings()
    body: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if s.groq_reasoning_effort and ("qwen" in model or "gpt-oss" in model):
        body["reasoning_effort"] = s.groq_reasoning_effort
    headers = {"Authorization": f"Bearer {s.groq_api_key}"}
    try:
        r = await client().post(GROQ_CHAT_URL, headers=headers, json=body)
        if r.status_code == 400 and "reasoning" in r.text.lower():
            body.pop("reasoning_effort", None)  # model doesn't accept this effort level
            r = await client().post(GROQ_CHAT_URL, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise ProviderError("groq", 0, f"{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise ProviderError("groq", r.status_code, r.text, _retry_after(r))
    data = r.json()
    text = _THINK.sub("", data["choices"][0]["message"].get("content") or "").strip()
    usage = data.get("usage") or {}
    return Completion(text, "groq", model, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))


EMBED_BATCH = 40
EMBED_PAUSE_S = 30.0  # free tier counts each text as a request (~100/min): 40 texts every 30 s stays under it


async def gemini_embed(texts: list[str], task: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    import asyncio

    s = get_settings()
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        if i:
            await asyncio.sleep(EMBED_PAUSE_S)
        batch = texts[i : i + EMBED_BATCH]
        body = {
            "requests": [
                {
                    "model": f"models/{s.embed_model}",
                    "content": {"parts": [{"text": t[:2000]}]},
                    "taskType": task,
                    "outputDimensionality": s.embed_dim,
                }
                for t in batch
            ]
        }
        try:
            r = await client().post(
                f"{GEMINI_BASE}/models/{s.embed_model}:batchEmbedContents",
                headers={"x-goog-api-key": s.gemini_api_key},
                json=body,
            )
        except httpx.HTTPError as e:
            raise ProviderError("gemini", 0, str(e)) from e
        if r.status_code != 200:
            raise ProviderError("gemini", r.status_code, r.text, _retry_after(r))
        out.extend(_unit(e["values"]) for e in r.json()["embeddings"])
    return out


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 6) for x in v]


async def prompt_guard_score(text: str) -> float | None:
    """Llama Prompt Guard 2 on Groq → malicious probability (or None when unavailable)."""
    s = get_settings()
    try:
        r = await client().post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {s.groq_api_key}"},
            json={"model": s.guard_model, "messages": [{"role": "user", "content": text[:1800]}]},
            timeout=6,
        )
        if r.status_code != 200:
            return None
        content = str(r.json()["choices"][0]["message"]["content"]).strip().lower()
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    try:
        return max(0.0, min(1.0, float(content)))
    except ValueError:
        pass
    if any(w in content for w in ("malicious", "jailbreak", "injection", "unsafe", "label_1")):
        return 1.0
    if any(w in content for w in ("benign", "safe", "label_0")):
        return 0.0
    return None
