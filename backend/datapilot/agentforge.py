"""AgentForge export (SPEC §18.1, §S.2): trace.v1 batches posted in the background, never blocking a request.
Thumbs feedback is forwarded the same way. No-op until AGENTFORGE_URL and AGENTFORGE_KEY are set."""

from __future__ import annotations

import asyncio
import logging

import httpx

from datapilot.config import get_settings

log = logging.getLogger(__name__)
_queue: list[dict] = []
_task: asyncio.Task | None = None
BATCH = 20


def enabled() -> bool:
    s = get_settings()
    return bool(s.agentforge_url and s.agentforge_key)


def submit_trace(trace: dict) -> None:
    if not enabled():
        return
    _queue.append(trace)
    _kick()


def submit_feedback(run_id: str, thumbs: int, comment: str | None) -> None:
    if not enabled():
        return
    asyncio.get_running_loop().create_task(
        _post("/v1/feedback", {"agent": "datapilot", "trace_id": run_id, "thumbs": thumbs, "comment": comment})
    )


def _kick() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_flush())


async def _flush() -> None:
    await asyncio.sleep(2)  # small batching window
    while _queue:
        batch = _queue[:BATCH]
        del _queue[:BATCH]
        for attempt in range(3):
            if await _post("/v1/traces", {"traces": batch}):
                break
            await asyncio.sleep(2 * (attempt + 1))


async def _post(path: str, body: dict) -> bool:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(
                f"{s.agentforge_url.rstrip('/')}{path}", json=body, headers={"X-AgentForge-Key": s.agentforge_key}
            )
            return r.status_code < 300
    except httpx.HTTPError as e:
        log.info("agentforge export failed: %s", e)
        return False
