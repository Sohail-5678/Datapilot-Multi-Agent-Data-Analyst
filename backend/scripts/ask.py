"""Ask one question from the command line (dev tool). Usage:
uv run python scripts/ask.py chinook "Which 5 genres made the most revenue in 2012?" [--auto run|cancel|pick_first]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from datapilot.db.session import init_db
from datapilot.profile import default_profile
from datapilot.runner import run_question
from datapilot.tracing import RunCtx


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db_id")
    ap.add_argument("question")
    ap.add_argument("--auto", default="run", help="confirm policy: run|cancel; clarify picks the first option")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    init_db()

    def emit(event: str, data: dict) -> None:
        if a.quiet or event == "token":
            return
        if event == "table":
            data = {**data, "rows": data["rows"][:3]}
        print(f"  · {event}: {json.dumps(data, default=str)[:300]}")

    async def on_interrupt(p: dict):  # type: ignore[no-untyped-def]
        print(
            f"  ⏸ interrupt {p['type']}: {json.dumps({k: v for k, v in p.items() if k != 'code'}, default=str)[:300]}"
        )
        if p["type"] == "clarify":
            return p["options"][0]
        if p["type"] == "confirm":
            return a.auto
        if p["type"] == "sandbox":
            from datapilot.sandbox_node import run_in_node

            return await run_in_node(p["code"], ctx.data[p["data_ref"]])
        return None

    ctx = RunCtx(run_id=str(uuid.uuid4()), db_id=a.db_id, profile=default_profile(), emit=emit)
    out = await run_question(ctx, a.question, on_interrupt=on_interrupt)
    print("\nSTATUS:", out.status)
    print("ANSWER:", out.state.get("answer"))
    print("SQL:", out.end_state["chosen_sql"])
    print("CONFIDENCE:", out.end_state["confidence"], "| grounding:", out.state.get("grounding"))
    print("METRICS:", out.trace["metrics"])


if __name__ == "__main__":
    asyncio.run(main())
