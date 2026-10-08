"""Bridge to the Node.js Pyodide runner (sandbox/runner.mjs) used in eval/CI mode, where no browser exists.
Same lockdown and limits as the browser Web Worker; the API process never runs model-written Python itself."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from datapilot.config import BACKEND_ROOT

RUNNER = Path(os.environ.get("SANDBOX_RUNNER", BACKEND_ROOT.parent / "sandbox" / "runner.mjs"))


def node_available() -> bool:
    return bool(shutil.which("node")) and RUNNER.exists() and (RUNNER.parent / "node_modules" / "pyodide").exists()


async def run_in_node(code: str, data: dict, timeout_ms: int = 10_000) -> dict:
    if not node_available():
        return {"ok": False, "error": "Node Pyodide runner not installed (cd sandbox && npm ci).", "ran_in": "node"}
    payload = json.dumps({"code": code, "data": {"columns": data["columns"], "rows": data["rows"]}, "timeout_ms": timeout_ms})
    proc = await asyncio.create_subprocess_exec(
        "node", str(RUNNER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload.encode()), timeout=timeout_ms / 1000 + 90)
    except TimeoutError:
        proc.kill()
        return {"ok": False, "error": "Sandbox runner did not respond.", "ran_in": "node"}
    try:
        return json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": (err.decode() or "runner failed")[-500:], "ran_in": "node"}
