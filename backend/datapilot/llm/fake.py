"""Deterministic fake LLM for tests, CI, Playwright e2e and offline development (FAKE_LLM=true).

Tests can script exact replies per step with `script(step, reply_or_callable)`; otherwise built-in
defaults cover the demo questions so every graph path (clarify, confirm, sandbox, repair, voting) runs
without any provider. The UI labels runs made with it ("offline fake model").
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Callable

from datapilot.llm.providers import Completion

Reply = str | Callable[[str, str], str]
_scripts: dict[str, deque[Reply]] = defaultdict(deque)
calls: list[dict] = []


def script(step: str, *replies: Reply) -> None:
    _scripts[step].extend(replies)


def reset() -> None:
    _scripts.clear()
    calls.clear()


def _tag(prompt: str, name: str) -> str:
    m = re.search(rf"<{name}>\n?(.*?)\n?</{name}>", prompt, re.DOTALL)
    return m.group(1).strip() if m else ""


DEMO_SQL: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"genres?.*revenue|revenue.*genres?", re.I),
        """WITH g AS (
  SELECT ge.Name AS genre,
         SUM(CASE WHEN strftime('%Y', i.InvoiceDate) = '2012' THEN il.UnitPrice * il.Quantity ELSE 0 END) AS revenue_2012,
         SUM(CASE WHEN strftime('%Y', i.InvoiceDate) = '2011' THEN il.UnitPrice * il.Quantity ELSE 0 END) AS revenue_2011
  FROM InvoiceLine il
  JOIN Invoice i ON i.InvoiceId = il.InvoiceId
  JOIN Track t ON t.TrackId = il.TrackId
  JOIN Genre ge ON ge.GenreId = t.GenreId
  GROUP BY ge.Name
)
SELECT genre, ROUND(revenue_2012, 2) AS revenue_2012, ROUND(revenue_2011, 2) AS revenue_2011,
       ROUND(100.0 * (revenue_2012 - revenue_2011) / revenue_2011, 1) AS pct_change
FROM g ORDER BY revenue_2012 DESC LIMIT 5""",
    ),
    (re.compile(r"track length|milliseconds.*price|correlation", re.I), "SELECT Milliseconds, UnitPrice FROM Track"),
    (
        re.compile(r"preferred.foot|overall rating", re.I),
        "SELECT preferred_foot, ROUND(AVG(overall_rating), 2) AS avg_overall_rating, COUNT(*) AS snapshots "
        "FROM Player_Attributes WHERE preferred_foot IS NOT NULL GROUP BY preferred_foot ORDER BY avg_overall_rating DESC",
    ),
    (
        re.compile(r"artists?.*tracks|tracks.*artists?", re.I),
        "SELECT ar.Name AS artist, COUNT(*) AS tracks FROM Track t JOIN Album al ON al.AlbumId = t.AlbumId "
        "JOIN Artist ar ON ar.ArtistId = al.ArtistId GROUP BY ar.Name ORDER BY tracks DESC LIMIT 10",
    ),
    (
        re.compile(r"publisher", re.I),
        "SELECT p.publisher_name, COUNT(*) AS heroes FROM superhero s JOIN publisher p ON p.id = s.publisher_id "
        "GROUP BY p.publisher_name ORDER BY heroes DESC LIMIT 10",
    ),
    (
        re.compile(r"monthly sales|sales.*month", re.I),
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total), 2) AS sales FROM Invoice "
        "WHERE strftime('%Y', InvoiceDate) = '2013' GROUP BY month ORDER BY month",
    ),
]


def _sql_for(prompt: str) -> str:
    q = _tag(prompt, "question")
    for rx, sql in DEMO_SQL:
        if rx.search(q):
            return sql
    m = re.search(r"CREATE TABLE \"?([\w ]+?)\"? \(", prompt)
    table = m.group(1) if m else "sqlite_master"
    return f'SELECT COUNT(*) AS n FROM "{table}"'


def _default(step: str, system: str, prompt: str) -> str:
    q = _tag(prompt, "question")
    if step == "planner":
        if re.search(r"\bbest\b", q, re.I) and "already clarified" not in prompt:
            return json.dumps(
                {
                    "needs_clarification": True,
                    "clarify_question": '"Best players" could mean different things. Which one?',
                    "options": ["Highest overall rating", "Highest potential", "Most appearances"],
                    "confidence": 0.4,
                    "steps": [],
                }
            )
        if re.search(r"correlation", q, re.I):
            return json.dumps(
                {
                    "needs_clarification": False,
                    "confidence": 0.9,
                    "steps": [
                        {
                            "goal": "Fetch track length and price for every track",
                            "needs_sql": True,
                            "needs_analysis": False,
                            "needs_chart": False,
                        },
                        {
                            "goal": "Compute the Pearson correlation between length and price",
                            "needs_sql": False,
                            "needs_analysis": True,
                            "needs_chart": False,
                        },
                    ],
                }
            )
        return json.dumps(
            {
                "needs_clarification": False,
                "confidence": 0.9,
                "steps": [{"goal": q.split("\n")[0], "needs_sql": True, "needs_analysis": False, "needs_chart": True}],
            }
        )
    if step == "schema_prune":
        tables = re.findall(r"^([\w]+): (.*)$", _tag(prompt, "schema"), re.M)
        return json.dumps(
            {
                "tables": [
                    {"name": t, "columns": [c.split(" (")[0] for c in cols.split("; ")], "reason": "fake"}
                    for t, cols in tables[:8]
                ]
            }
        )
    if step in ("sql_direct", "sql_plan", "sql_fewshot", "repair"):
        return f"```sql\n{_sql_for(prompt)}\n```"
    if step == "verifier":
        ids = re.findall(r"Candidate (\S+) \(", prompt)
        return json.dumps(
            {
                "chosen": ids[0] if ids else "",
                "passes": True,
                "issues": [],
                "reason": "Columns and filters match the question.",
            }
        )
    if step == "analyst":
        return "```python\nr = data.iloc[:, 0].corr(data.iloc[:, 1])\nresult = {'pearson_r': round(float(r), 4), 'n': int(len(data))}\nprint(result)\n```"
    if step == "chart":
        lines = _tag(prompt, "rows").split("\n")
        header = lines[0].split("\t")
        first = lines[1].split("\t") if len(lines) > 1 else []
        numeric_first = bool(first) and first[0].replace(".", "", 1).lstrip("-").isdigit()
        if len(header) >= 2 and numeric_first:
            return json.dumps(
                {
                    "mark": "point",
                    "encoding": {
                        "x": {"field": header[0], "type": "quantitative"},
                        "y": {"field": header[1], "type": "quantitative"},
                    },
                }
            )
        if len(header) >= 2:
            return json.dumps(
                {
                    "mark": "bar",
                    "encoding": {
                        "y": {"field": header[0], "type": "nominal", "sort": "-x"},
                        "x": {"field": header[1], "type": "quantitative"},
                    },
                }
            )
        return json.dumps({"no_chart": True})
    if step == "narrator":
        analysis = _tag(prompt, "analysis")
        if analysis:
            try:
                res = json.loads(analysis)
                if isinstance(res, dict) and "pearson_r" in res:
                    return f"Track length and price are weakly related: the Pearson correlation is **{res['pearson_r']}** across {res.get('n')} tracks."
            except ValueError:
                pass
        rows = [r for r in _tag(prompt, "rows").split("\n") if r and not r.startswith("Step") and not r.startswith("…")]
        if len(rows) >= 2:
            head, first = rows[0].split("\t"), rows[1].split("\t")
            pairs = ", ".join(f"{h} {v}" for h, v in list(zip(head, first, strict=False))[1:3])
            return f"**{first[0]}** comes first ({pairs})."
        return "The query returned no rows."
    return "{}"


async def fake_complete(step: str, system: str, prompt: str, json_mode: bool) -> Completion:
    calls.append({"step": step, "prompt": prompt})
    queue = _scripts.get(step)
    if queue:
        reply = queue.popleft()
        text = reply(system, prompt) if callable(reply) else reply
    else:
        text = _default(step, system, prompt)
    if text == "__raise_unavailable__":
        from datapilot.llm.router import LLMUnavailable

        raise LLMUnavailable("scripted outage")
    return Completion(text, "fake", "fake-llm", max(1, len(prompt) // 4), max(1, len(text) // 4))
