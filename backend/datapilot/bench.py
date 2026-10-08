"""BIRD Mini-Dev benchmark harness and ablation (SPEC §5.2, §7.5, §13.1–13.2).

    cd backend
    uv run python -m datapilot.bench --config C0|C1|C2|C3|C4 [--subset ../bench/subset_150.jsonl]
        [--split test|val|train|all] [--limit N] [--budget-calls N] [--concurrency 1] [--min-interval-s S]
        [--fake-llm] [--bird-dir PATH] [--resume]
    uv run python -m datapilot.bench --summarize          # rebuild bench/summary.json only

Each question runs through the real agent graph (`runner.run_question`) against the FULL BIRD SQLite file
(Database id `bird:<db_id>`, so indexes never mix with the slimmed demo copies). Predicted SQL (without the
guard's display LIMIT) and gold SQL run on the same file read-only; EX = BIRD's official set comparison.

Configs (all run with the planner, clarify, chart and narrator off and `sql_only=True`, so only SQL-generation
calls are counted; BIRD questions are single-query, the planner is evaluated by AgentForge scenario suites):
  C0 baseline          one LLM call, full schema + evidence, k=1, no linking/repair/verifier/cache
  C1 +linking          + schema pruning, value links, join paths, column samples (k=1)
  C2 +candidates       + k=3 strategies (direct, plan-then-SQL, few-shot) with result-hash voting
  C3 +repair+verifier  + repair loop (≤ 2 per candidate) + LLM verifier
  C4 full+adaptive     + adaptive k (2, third only on disagreement) + verified-SQL cache lookup + few-shot pool
                       from the TRAIN split of the same database (never val/test)

Free-tier behaviour: sequential by default; every real provider request is paced (rolling 60 s window:
Groq 7,000 tokens/min and 25 requests/min, Gemini 8 requests/min by default) and counted. The run stops
cleanly — progress saved after every question — when `--budget-calls` provider requests are used, when the
router reports quota exhaustion, or when providers stay unreachable. `--resume` continues the latest
results file for the same config + subset + split and skips question_ids already scored. Every LLM response
is cached by hash in bench/.llm_cache/ (gitignored), so reruns are free. By default each step uses only its
profile's primary model kind (`--no-pin-models` allows the router's fallbacks); the models used are recorded.

Output: bench/results/{date}_{config}_{split}.json (EX, 95% bootstrap CI, per-difficulty/per-db EX,
tokens, list-price cost, p50/p95 latency, failures with SQL) and a refreshed bench/summary.json (latest real
run per config with n ≥ 20; `--fake-llm` runs are marked fake and never included).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datapilot import evalkit
from datapilot.config import get_settings
from datapilot.evalkit import BENCH_DIR, REPO_ROOT

SCHEMA = "datapilot.bench.v1"
SUMMARY_SCHEMA = "datapilot.bench-summary.v1"
SEED = 13
BOOTSTRAP = 1000
DIFFICULTIES = ("simple", "moderate", "challenging")
SPLIT_PREFERENCE = ("test", "all", "val", "train")

_BASE = {
    "planner": False,
    "clarify": False,
    "chart": False,
    "allow_expensive": True,
    "sql_only": True,
    "grounding_rewrite": False,
}
CONFIGS: dict[str, dict[str, Any]] = {
    "C0": {
        "label": "baseline",
        "description": "One LLM call: full schema with column descriptions + BIRD evidence, one direct candidate. "
        "No linking, voting, repair, verifier or cache.",
        "flags": {
            **_BASE,
            "linking": False,
            "k": 1,
            "adaptive": False,
            "repair": False,
            "verifier": False,
            "cache": False,
        },
        "steps": ["sql_direct"],
    },
    "C1": {
        "label": "+linking",
        "description": "C0 + schema linking: pruned schema, value links, FK join paths and column samples (k=1).",
        "flags": {
            **_BASE,
            "linking": True,
            "k": 1,
            "adaptive": False,
            "repair": False,
            "verifier": False,
            "cache": False,
        },
        "steps": ["sql_direct"],
        "optional_steps": ["schema_prune"],
    },
    "C2": {
        "label": "+candidates",
        "description": "C1 + three candidate strategies (direct, plan-then-SQL, few-shot) with result-hash voting.",
        "flags": {
            **_BASE,
            "linking": True,
            "k": 3,
            "adaptive": False,
            "repair": False,
            "verifier": False,
            "cache": False,
        },
        "steps": ["sql_direct", "sql_plan", "sql_fewshot"],
        "optional_steps": ["schema_prune"],
    },
    "C3": {
        "label": "+repair+verifier",
        "description": "C2 + repair loop (≤ 2 per candidate, exact SQLite error / 0-row hints) + LLM verifier.",
        "flags": {
            **_BASE,
            "linking": True,
            "k": 3,
            "adaptive": False,
            "repair": True,
            "verifier": True,
            "cache": False,
        },
        "steps": ["sql_direct", "sql_plan", "sql_fewshot", "verifier"],
        "optional_steps": ["schema_prune"],
    },
    "C4": {
        "label": "full+adaptive",
        "description": "C3 + adaptive k (two candidates, a third only when they disagree), verified-SQL cache lookup, "
        "and few-shot examples from the TRAIN split of the same database (never val/test).",
        "flags": {**_BASE, "linking": True, "k": 3, "adaptive": True, "repair": True, "verifier": True, "cache": True},
        "fewshot": "train",
        "steps": ["sql_direct", "sql_plan", "sql_fewshot", "verifier"],
        "optional_steps": ["schema_prune"],
    },
}


# ---------------------------------------------------------------- data


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def interleave_by_db(items: list[dict]) -> list[dict]:
    """Deterministic run order that rotates databases, so a --limit prefix (or a partial night) is diverse."""
    by_db: dict[str, list[dict]] = defaultdict(list)
    for it in sorted(items, key=lambda r: int(r["question_id"])):
        by_db[it["db_id"]].append(it)
    out: list[dict] = []
    dbs = sorted(by_db)
    while any(by_db.values()):
        for db in dbs:
            if by_db[db]:
                out.append(by_db[db].pop(0))
    return out


def select_items(subset: list[dict], splits: dict | None, split: str) -> list[dict]:
    if split == "all":
        return interleave_by_db(subset)
    if not splits or split not in splits:
        sys.exit(f"split '{split}' not found in splits.json (use --split all for a file without splits)")
    ids = {int(q) for q in splits[split]}
    return interleave_by_db([r for r in subset if int(r["question_id"]) in ids])


def train_pool(subset: list[dict], splits: dict | None) -> dict[str, list[dict]]:
    """Few-shot pool for C4: TRAIN split only, grouped by database. Never val or test."""
    if not splits or "train" not in splits:
        return {}
    ids = {int(q) for q in splits["train"]}
    test_ids = {int(q) for q in splits.get("test", [])}
    assert not ids & test_ids, "train and test overlap"
    pool: dict[str, list[dict]] = defaultdict(list)
    for r in subset:
        if int(r["question_id"]) in ids:
            pool[r["db_id"]].append(
                {
                    "question_id": r["question_id"],
                    "question": r["question"],
                    "sql": r["gold_sql"],
                    "evidence": r.get("evidence") or None,
                }
            )
    return pool


def agent_version() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"][:12]
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return (sha + ("+dirty" if dirty else "")) or get_settings().git_sha
    except (OSError, subprocess.SubprocessError):
        return get_settings().git_sha


# ---------------------------------------------------------------- stats


def bootstrap_ci(values: list[int], b: int = BOOTSTRAP, seed: int = SEED) -> list[float] | None:
    n = len(values)
    if n == 0:
        return None
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return [round(means[round(0.025 * (b - 1))], 4), round(means[round(0.975 * (b - 1))], 4)]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    pos = pct / 100 * (len(v) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 1)


def _group(qs: list[dict], key: str, order: tuple[str, ...] | None = None) -> dict:
    g: dict[str, list[int]] = defaultdict(list)
    for q in qs:
        g[q[key]].append(int(bool(q["ex"])))
    keys = [k for k in order if k in g] if order else sorted(g)
    return {k: {"n": len(g[k]), "ex": round(sum(g[k]) / len(g[k]), 4)} for k in keys}


def aggregate(qs: list[dict], items_by_id: dict[int, dict]) -> dict:
    n = len(qs)
    exs = [int(bool(q["ex"])) for q in qs]
    models: Counter = Counter()
    for q in qs:
        models.update(q.get("models") or {})
    failures = []
    for q in qs:
        if q["ex"]:
            continue
        it = items_by_id.get(int(q["question_id"]), {})
        failures.append(
            {
                "question_id": q["question_id"],
                "db_id": q["db_id"],
                "difficulty": q["difficulty"],
                "question": it.get("question"),
                "evidence": it.get("evidence"),
                "gold_sql": it.get("gold_sql"),
                "pred_sql": q.get("pred_sql"),
                "status": q.get("status"),
                "error": q.get("error"),
            }
        )
    mean = (lambda key: round(sum(q.get(key) or 0 for q in qs) / n, 6)) if n else (lambda key: None)
    return {
        "n": n,
        "ex": round(sum(exs) / n, 4) if n else None,
        "ex_ci95": bootstrap_ci(exs),
        "correct": sum(exs),
        "per_difficulty": _group(qs, "difficulty", DIFFICULTIES),
        "per_db": _group(qs, "db_id"),
        "llm_calls_per_question": mean("llm_calls"),
        "live_calls_total": sum(q.get("live_calls") or 0 for q in qs),
        "cache_hit_rate": round(
            sum(q.get("cached_calls") or 0 for q in qs) / max(1, sum(q.get("llm_calls") or 0 for q in qs)), 4
        ),
        "tokens_per_question": round(sum(q.get("tokens") or 0 for q in qs) / n, 1) if n else None,
        "cost_per_question_usd": mean("list_price_cost_usd"),
        "cost_total_usd": round(sum(q.get("list_price_cost_usd") or 0 for q in qs), 6),
        "latency_ms": {
            "p50": percentile([q["latency_ms"] for q in qs], 50),
            "p95": percentile([q["latency_ms"] for q in qs], 95),
        },
        "status_counts": dict(Counter(q["status"] for q in qs)),
        "models": dict(models),
        "degraded_questions": sum(1 for q in qs if q.get("degraded")),
        "failures": failures,
    }


# ---------------------------------------------------------------- results files


def results_name(date: str, config: str, split: str, subset: Path, tag: str = "") -> str:
    """{date}_{config}_{split}[_{subset stem}][.fake|.oracle].json"""
    stem = "" if subset.name == "subset_150.jsonl" else f"_{subset.stem}"
    return f"{date}_{config}_{split}{stem}{'.' + tag if tag else ''}.json"


def find_resume(results_dir: Path, config: str, split: str, subset: Path, tag: str = "") -> Path | None:
    hits = sorted(results_dir.glob(results_name("*", config, split, subset, tag)))
    return hits[-1] if hits else None


def markdown_table(res: dict) -> str:
    a = res["aggregate"]
    ci = a.get("ex_ci95") or [None, None]
    pct = lambda v: "—" if v is None else f"{100 * v:.1f}%"  # noqa: E731
    lines = [
        f"### BIRD Mini-Dev · {res['config']} {res['label']} · split `{res['split']}`{' · FAKE LLM' if res['fake'] else ''}",
        "",
        "| n | EX | 95% CI | calls/q | tokens/q | list-price $/q | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|",
        f"| {a['n']}/{res['n_planned']} | {pct(a['ex'])} | {pct(ci[0])}–{pct(ci[1])} | {a['llm_calls_per_question']} | "
        f"{a['tokens_per_question']} | {a['cost_per_question_usd']} | {a['latency_ms']['p50']} | {a['latency_ms']['p95']} |",
        "",
        "| difficulty | n | EX |",
        "|---|---|---|",
        *[f"| {d} | {v['n']} | {pct(v['ex'])} |" for d, v in a["per_difficulty"].items()],
        "",
        f"Stopped: **{res.get('stop_reason') or 'completed'}** · models: {a['models']} · preview: {res['preview']}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- summary.json (read by the web /benchmarks page)


def build_summary(results_dir: Path, summary_path: Path, min_n: int = 20) -> dict:
    eligible: list[dict] = []
    for f in sorted(results_dir.glob("*.json")):
        try:
            res = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if (
            res.get("schema") != SCHEMA
            or res.get("fake")
            or res.get("fake_oracle")
            or res.get("subset") != "subset_150.jsonl"
        ):
            continue
        if "fake-llm" in ((res.get("aggregate") or {}).get("models") or {}):
            continue  # never publish numbers produced by the offline fake model
        if (res.get("aggregate") or {}).get("n", 0) < min_n:
            continue
        res["_file"] = f
        eligible.append(res)
    chosen: dict[str, dict] = {}
    for res in eligible:
        cur = chosen.get(res["config"])
        rank = (
            -SPLIT_PREFERENCE.index(res["split"]) if res["split"] in SPLIT_PREFERENCE else -9,
            res.get("updated_at") or "",
        )
        if cur is None or rank > cur["_rank"]:
            chosen[res["config"]] = {**res, "_rank": rank}
    configs: dict[str, dict] = {}
    for cfg in sorted(chosen):
        r = chosen[cfg]
        a = r["aggregate"]
        try:
            rel = str(r["_file"].resolve().relative_to(summary_path.parent.resolve()))
        except ValueError:
            rel = r["_file"].name
        configs[cfg] = {
            "config": cfg,
            "label": r["label"],
            "description": r["description"],
            "split": r["split"],
            "n": a["n"],
            "n_planned": r["n_planned"],
            "preview": bool(r["preview"]),
            "ex": a["ex"],
            "ex_ci95": a["ex_ci95"],
            "correct": a["correct"],
            "llm_calls_per_question": a["llm_calls_per_question"],
            "tokens_per_question": a["tokens_per_question"],
            "cost_per_question_usd": a["cost_per_question_usd"],
            "latency_p50_ms": a["latency_ms"]["p50"],
            "latency_p95_ms": a["latency_ms"]["p95"],
            "models": a["models"],
            "degraded_questions": a.get("degraded_questions", 0),
            "cache_hit_rate": a.get("cache_hit_rate"),
            "agent_version": r.get("agent_version"),
            "profile_version": r.get("profile_version"),
            "updated_at": r.get("updated_at"),
            "result_file": rel,
        }
    failed: list[dict] = []
    if configs:
        top = max(configs)  # the most complete config that has a result
        for fcase in chosen[top]["aggregate"]["failures"][:10]:
            failed.append(
                {
                    "config": top,
                    **{
                        k: fcase.get(k)
                        for k in (
                            "question_id",
                            "db_id",
                            "difficulty",
                            "question",
                            "evidence",
                            "gold_sql",
                            "pred_sql",
                            "error",
                        )
                    },
                }
            )
    summary = {
        "schema": SUMMARY_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "benchmark": "BIRD Mini-Dev (SQLite), fixed stratified 150-question subset (seed 13; train/val/test 50 each)",
        "metric": "EX — execution accuracy, BIRD official set comparison of predicted vs gold result rows",
        "cost_note": "list_price_cost_usd = tokens × published paid list price; actual spend is $0 (free tiers)",
        "inclusion_rule": f"latest real (non-fake) run per config on subset_150 with n ≥ {min_n}; test split preferred",
        "preview": (not configs) or any(c["preview"] for c in configs.values()),
        "configs": configs,
        "ablation": [
            {
                k: configs[c][k]
                for k in (
                    "config",
                    "label",
                    "split",
                    "n",
                    "preview",
                    "ex",
                    "ex_ci95",
                    "llm_calls_per_question",
                    "tokens_per_question",
                    "cost_per_question_usd",
                    "latency_p50_ms",
                    "latency_p95_ms",
                )
            }
            for c in sorted(configs)
        ],
        "per_difficulty": {c: chosen[c]["aggregate"]["per_difficulty"] for c in sorted(chosen)},
        "per_db": {c: chosen[c]["aggregate"]["per_db"] for c in sorted(chosen)},
        "failed_cases": failed,
    }
    if not configs:
        summary["note"] = "No qualifying benchmark run has been recorded yet."
    evalkit.write_json_atomic(summary_path, summary)
    return summary


# ---------------------------------------------------------------- the run


class Harness:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.cfg = CONFIGS[a.config]
        self.subset_path = Path(a.subset).resolve()
        self.subset = load_jsonl(self.subset_path)
        splits_path = Path(a.splits).resolve() if a.splits else self.subset_path.parent / "splits.json"
        self.splits = json.loads(splits_path.read_text()) if splits_path.exists() else None
        if self.splits is None and self.cfg.get("fewshot"):
            default_splits = BENCH_DIR / "splits.json"
            self.splits = json.loads(default_splits.read_text()) if default_splits.exists() else None
        self.items_by_id = {int(r["question_id"]): r for r in self.subset}
        self.items = select_items(self.subset, self.splits, a.split)
        self.pool: dict[str, list[dict]] = {}
        if self.cfg.get("fewshot") == "train" and self.splits:
            # The train questions live in subset_150 even when running another subset (e.g. regression_40 ⊂ val).
            source = {int(r["question_id"]): r for r in self.subset}
            full = BENCH_DIR / "subset_150.jsonl"
            if full.exists():
                source.update({int(r["question_id"]): r for r in load_jsonl(full)})
            self.pool = train_pool(list(source.values()), self.splits)
            test_ids = {int(q) for q in self.splits.get("test", [])}
            assert not any(int(e["question_id"]) in test_ids for v in self.pool.values() for e in v)
        self.results_dir = Path(a.results_dir).resolve()
        self.stop_reason: str | None = None
        self.warmed: set[str] = set()
        self.last_start = 0.0
        self.consecutive_infra = 0
        self.prev_requests: Counter = Counter()
        self.tag = "oracle" if a.fake_oracle else "fake" if a.fake_llm else ""
        self.session_fake_calls = 0

    # -------------------------------------------------- setup

    def prepare(self) -> None:
        from datapilot.db.session import init_db
        from datapilot.profile import default_profile, load_file

        a = self.a
        if a.fake_llm:
            evalkit.enable_fake_llm()
            if a.fake_oracle:
                self._install_oracle()
        if a.app_db:
            evalkit.use_app_db(a.app_db)
        s = get_settings()
        s.sql_timeout_s = float(a.sql_timeout_s)
        init_db()
        base = load_file(Path(a.profile)) if a.profile else default_profile()
        self.profile = evalkit.pinned_profile(base) if a.pin_models else base
        self.profile_sha = hashlib.sha256(json.dumps(base.raw, sort_keys=True).encode()).hexdigest()[:12]
        if not a.fake_llm:
            need = evalkit.kinds_for_steps(self.profile, self.cfg["steps"])
            missing = {st: k for st, k in need.items() if not evalkit.provider_ready(k)}
            if missing and a.pin_models:
                sys.exit(
                    f"{a.config} needs model kinds {missing} but their provider has no API key configured. "
                    "Add the key, or pass --no-pin-models to let those steps fall back to a configured provider "
                    "(the models used are recorded per question)."
                )
            opt = evalkit.kinds_for_steps(self.profile, self.cfg.get("optional_steps", []))
            for st, k in opt.items():
                if not evalkit.provider_ready(k):
                    print(
                        f"note: {st} model ({k}) not configured — the schema linker uses its rules fallback (recorded as pruned_by=rules)"
                    )
        self.bird_dir = evalkit.resolve_bird_dir(a.bird_dir)
        if self.bird_dir is None:
            sys.exit(
                "BIRD Mini-Dev databases not found. Pass --bird-dir (folder containing dev_databases/) or set BIRD_DIR."
            )
        self.cache = None if a.no_llm_cache or a.fake_llm else evalkit.SqliteResponseCache(Path(a.llm_cache))
        self.pacer = evalkit.install_pacer(
            {
                "groq": (a.groq_rpm, a.groq_tpm),
                "gemini": (a.gemini_rpm, a.gemini_tpm),
            }
        )

    def _install_oracle(self) -> None:
        """Self-test: the fake model answers every SQL step with the gold SQL, so EX should be ~100% unless the
        guard, executor, LIMIT handling or comparison mishandles a valid BIRD query. Results are marked fake."""
        from datapilot.llm import fake

        gold = {r["question"].strip(): r["gold_sql"] for r in self.subset}
        original = fake._sql_for

        def oracle(prompt: str) -> str:
            q = fake._tag(prompt, "question").split("\n")[0].strip()
            return gold.get(q) or original(prompt)

        fake._sql_for = oracle  # type: ignore[assignment]

    def calls_used(self) -> int:
        """Real provider requests this session (retries included); offline fake-model calls in --fake-llm runs."""
        return self.session_fake_calls if self.a.fake_llm else self.pacer.total_requests

    def config_hash(self) -> str:
        spec = {
            "flags": self.cfg["flags"],
            "fewshot": self.cfg.get("fewshot"),
            "train_ids": sorted(int(e["question_id"]) for v in self.pool.values() for e in v),
            "pinned": bool(self.a.pin_models),
            "profile": self.profile_sha,
            "sql_timeout_s": float(self.a.sql_timeout_s),
            "fake": bool(self.a.fake_llm),
            "oracle": bool(self.a.fake_oracle),
        }
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]

    def open_results(self) -> tuple[Path, dict]:
        a = self.a
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        chash = self.config_hash()
        path = None
        if a.resume:
            path = find_resume(self.results_dir, a.config, a.split, self.subset_path, self.tag)
        if path is not None:
            res = json.loads(path.read_text())
            if res.get("config_hash") != chash or res.get("subset_sha") != file_sha(self.subset_path):
                sys.exit(
                    f"--resume: {path.name} was produced with a different config/profile/subset "
                    f"({res.get('config_hash')} vs {chash}); start a fresh run without --resume."
                )
            print(f"resuming {path.name}: {len(res['questions'])} question(s) already scored")
            return path, res
        path = self.results_dir / results_name(date, a.config, a.split, self.subset_path, self.tag)
        if path.exists():
            print(f"note: overwriting {path.name} (use --resume to continue it instead)")
        s = get_settings()
        res = {
            "schema": SCHEMA,
            "benchmark": "BIRD Mini-Dev (SQLite)",
            "config": a.config,
            "label": self.cfg["label"],
            "description": self.cfg["description"],
            "flags": self.cfg["flags"],
            "fewshot_pool": "train split, same database" if self.cfg.get("fewshot") else None,
            "config_hash": chash,
            "subset": self.subset_path.name,
            "subset_sha": file_sha(self.subset_path),
            "split": a.split,
            "n_planned": len(self.items),
            "fake": bool(a.fake_llm),
            "fake_oracle": bool(a.fake_oracle),
            "preview": True,
            "pinned_models": bool(a.pin_models),
            "routing": {
                st: k
                for st, k in evalkit.kinds_for_steps(
                    self.profile, self.cfg["steps"] + self.cfg.get("optional_steps", [])
                ).items()
            },
            "model_ids": {"main": s.main_model, "lite": s.lite_model, "fast": s.fast_model, "alt": s.alt_model or None},
            "providers": {"groq": s.groq_ready, "gemini": s.gemini_ready},
            "sql_timeout_s": float(a.sql_timeout_s),
            "agent_version": agent_version(),
            "profile_version": self.profile.version_label,
            "profile_sha": self.profile_sha,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "updated_at": None,
            "stop_reason": None,
            "questions": [],
        }
        return path, res

    def save(self, path: Path, res: dict) -> None:
        res["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        res["aggregate"] = aggregate(res["questions"], self.items_by_id)
        res["preview"] = res["aggregate"]["n"] < res["n_planned"]
        res["stop_reason"] = self.stop_reason
        res["provider_requests"] = dict(self.prev_requests + self.pacer.requests)  # all sessions of this file
        evalkit.write_json_atomic(path, res)

    # -------------------------------------------------- one question

    async def run_one(self, item: dict) -> dict | str:
        """Returns the scored record, or "quota" / "infra" when the question could not be attempted."""
        from datapilot.runner import run_question
        from datapilot.tracing import RunCtx

        assert self.bird_dir is not None
        db = evalkit.load_bird_database(self.bird_dir, item["db_id"])
        if db.db_id not in self.warmed:
            await evalkit.warm_indexes(db, bool(self.cfg["flags"].get("linking")))
            self.warmed.add(db.db_id)
        flags = dict(self.cfg["flags"])
        if self.cfg.get("fewshot") == "train":
            flags["fewshot_pool"] = [
                {k: e[k] for k in ("question", "sql", "evidence")}
                for e in self.pool.get(item["db_id"], [])
                if int(e["question_id"]) != int(item["question_id"])
            ]
        evidence = (item.get("evidence") or "").strip() or None
        ctx = RunCtx(
            run_id=str(uuid.uuid4()),
            db_id=db.db_id,
            mode="bench",
            case_id=f"dp-bird-{item['question_id']}",
            profile=self.profile,
            flags=flags,
            db_path=db.path,
            database=db,
            evidence=evidence,
            llm_cache=self.cache,
        )

        async def on_interrupt(p: dict) -> Any:
            if p.get("type") == "clarify":
                return (p.get("options") or [""])[0]
            if p.get("type") == "confirm":
                return "run"
            if p.get("type") == "sandbox":
                from datapilot.sandbox_node import run_in_node

                return await run_in_node(p["code"], ctx.data[p["data_ref"]])
            return None

        token = evalkit.CURRENT_CTX.set(ctx)
        error = None
        end_state: dict = {}
        try:
            out = await run_question(ctx, item["question"], on_interrupt=on_interrupt, evidence=evidence)
            state, status, trace, end_state = out.state, out.status, out.trace, out.end_state
        except Exception as e:  # noqa: BLE001 — one broken question must not end the run
            state, status, error = {}, "error", f"{type(e).__name__}: {e}"[:400]
            trace = ctx.trace(status="error", question=item["question"], final_output={}, end_state={})
        finally:
            evalkit.CURRENT_CTX.reset(token)
        if status == "quota_exhausted":
            return "quota"
        if evalkit.is_infra_failure(state) and not evalkit.too_large_for_provider(state):
            return "infra"

        pred = evalkit.predicted_sql(state)
        exr = await asyncio.to_thread(
            evalkit.compute_ex, db.path, pred, item["gold_sql"], pred_timeout_s=max(30.0, float(self.a.sql_timeout_s))
        )
        m = trace.get("metrics") or {}
        stats = evalkit.llm_span_stats(trace)
        last = (state.get("step_results") or [{}])[-1] if state.get("step_results") else {}
        cands = last.get("candidates") or []
        reasons = last.get("reasons") or []
        linked = state.get("linked") or {}
        degraded = []
        if any("verifier unavailable" in r for r in reasons):
            degraded.append("verifier unavailable")
        if any((c.get("error") or "").startswith("No model available") for c in cands) and any(
            c.get("status") == "ok" for c in cands
        ):
            degraded.append("some candidates had no model")
        if not error:
            if not pred:
                error = next((c.get("error") for c in cands if c.get("error")), None) or f"no SQL ({status})"
            elif not exr["ex"]:
                error = exr.get("ex_error")
        return {
            "question_id": item["question_id"],
            "db_id": item["db_id"],
            "difficulty": item["difficulty"],
            "ex": bool(exr["ex"]),
            "pred_sql": pred,
            "status": status,
            "confidence": end_state.get("confidence"),
            "llm_calls": m.get("llm_calls", 0),
            "live_calls": stats["live_calls"],
            "cached_calls": stats["cached_calls"],
            "tokens": (m.get("tokens_in") or 0) + (m.get("tokens_out") or 0),
            "tokens_in": m.get("tokens_in", 0),
            "tokens_out": m.get("tokens_out", 0),
            "list_price_cost_usd": m.get("list_price_cost_usd", 0.0),
            "latency_ms": m.get("latency_ms", 0),
            "error": (str(error)[:400] if error else None),
            "models": stats["models"],
            "pruned_by": linked.get("pruned_by"),
            "cache_hit": any(c.get("strategy") == "cache" for c in cands),
            "candidates": [{k: c.get(k) for k in ("strategy", "status", "repairs", "row_count")} for c in cands],
            "gold_rows": exr.get("gold_rows"),
            "pred_rows": exr.get("pred_rows"),
            "degraded": degraded,
        }

    # -------------------------------------------------- loop

    async def run(self) -> int:
        a = self.a
        self.prepare()
        path, res = self.open_results()
        self.prev_requests = Counter(res.get("provider_requests") or {})
        done = {int(q["question_id"]) for q in res["questions"]}
        # --limit N = the first N questions of the split in run order (a resumed run finishes the same N).
        todo = [it for it in (self.items[: a.limit] if a.limit else self.items) if int(it["question_id"]) not in done]
        print(
            f"{a.config} {self.cfg['label']} · split={a.split} · {len(todo)} to run ({len(done)} done, "
            f"{len(self.items)} in split) · fake={a.fake_llm} · pinned={a.pin_models} → {path}"
        )
        queue: asyncio.Queue = asyncio.Queue()
        for it in todo:
            queue.put_nowait(it)
        total = len(done) + len(todo)
        lock = asyncio.Lock()

        async def worker() -> None:
            while not queue.empty() and self.stop_reason is None:
                if a.budget_calls and self.calls_used() >= a.budget_calls:
                    self.stop_reason = f"budget: {self.calls_used()} {'fake model calls' if a.fake_llm else 'provider requests'} (--budget-calls {a.budget_calls})"
                    return
                it = queue.get_nowait()
                async with lock:
                    gap = a.min_interval_s - (time.monotonic() - self.last_start)
                    if gap > 0:
                        await asyncio.sleep(gap)
                    self.last_start = time.monotonic()
                rec = await self.run_one(it)
                if rec == "infra" and not a.fake_llm:
                    print(f"  q{it['question_id']}: no model reachable — cooling down 65 s and retrying once")
                    await asyncio.sleep(65)
                    rec = await self.run_one(it)
                if rec == "quota":
                    self.stop_reason = "quota: the router reports the free daily quota is used up"
                    return
                if rec == "infra":
                    self.consecutive_infra += 1
                    print(f"  q{it['question_id']}: skipped (providers unreachable); it stays unscored for --resume")
                    if self.consecutive_infra >= 3:
                        self.stop_reason = "providers unreachable for 3 questions in a row"
                    continue
                self.consecutive_infra = 0
                assert isinstance(rec, dict)
                self.session_fake_calls += rec["live_calls"] if a.fake_llm else 0
                res["questions"].append(rec)
                self.save(path, res)
                n = len(res["questions"])
                if not a.quiet:
                    print(
                        f"[{n}/{total}] q{rec['question_id']} {rec['db_id']} {rec['difficulty']:<11} EX={int(rec['ex'])} "
                        f"calls={rec['llm_calls']} (live {rec['live_calls']}) tok={rec['tokens']} {rec['latency_ms'] / 1000:.1f}s "
                        f"{rec['status']}" + (f" · {rec['error'][:90]}" if rec.get("error") and not rec["ex"] else "")
                    )

        await asyncio.gather(*(worker() for _ in range(max(1, a.concurrency))))
        if self.stop_reason is None and not queue.empty():
            self.stop_reason = "stopped"
        self.save(path, res)
        agg = res["aggregate"]
        print(
            f"\n{a.config}: EX {agg['ex']} (95% CI {agg['ex_ci95']}) on n={agg['n']}/{res['n_planned']} · "
            f"calls/q {agg['llm_calls_per_question']} · tokens/q {agg['tokens_per_question']} · "
            f"$/q {agg['cost_per_question_usd']} · p50 {agg['latency_ms']['p50']} ms · p95 {agg['latency_ms']['p95']} ms"
        )
        print(
            f"provider requests this session: {dict(self.pacer.requests)} · paced wait {self.pacer.waited_s:.0f}s"
            f" · stop: {self.stop_reason or 'completed'}"
        )
        self.final = (path, res)
        return 0

    def finish(self) -> None:
        """Post-run bookkeeping (sync, outside the event loop): job summary, optional DB rows, summary.json."""
        a = self.a
        path, res = self.final
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
                fh.write(markdown_table(res) + "\n")
        if a.store_db and not a.fake_llm:
            store_rows(path.stem, res)
        if not a.no_summary:
            summ = build_summary(self.results_dir, Path(a.summary).resolve(), a.min_n_summary)
            print(f"summary: {len(summ['configs'])} config(s) → {a.summary}")
        if self.cache is not None:
            self.cache.close()


def store_rows(run: str, res: dict) -> None:
    """Optional: mirror per-question rows into the app DB's bench_results table (SPEC §11)."""
    from datapilot.db.schema import bench_results
    from datapilot.db.session import run_sync

    def fn(conn):  # type: ignore[no-untyped-def]
        conn.execute(bench_results.delete().where(bench_results.c.bench_run == run))
        conn.execute(
            bench_results.insert(),
            [
                {
                    "bench_run": run,
                    "config": res["config"],
                    "question_id": str(q["question_id"]),
                    "db_id": q["db_id"],
                    "difficulty": q["difficulty"],
                    "ex": q["ex"],
                    "pred_sql": q["pred_sql"],
                    "llm_calls": q["llm_calls"],
                    "tokens": q["tokens"],
                    "list_price_cost_usd": q["list_price_cost_usd"],
                    "latency_ms": q["latency_ms"],
                }
                for q in res["questions"]
            ],
        )

    if res["questions"]:
        run_sync(fn)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m datapilot.bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", choices=sorted(CONFIGS))
    ap.add_argument("--subset", default=str(BENCH_DIR / "subset_150.jsonl"))
    ap.add_argument("--splits", default=None, help="splits.json (default: next to the subset file)")
    ap.add_argument("--split", default="test", choices=["test", "val", "train", "all"])
    ap.add_argument("--limit", type=int, default=0, help="score at most N questions of the split (0 = all)")
    ap.add_argument("--budget-calls", type=int, default=0, help="stop after N real provider requests (0 = no cap)")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--min-interval-s", type=float, default=0.0, help="minimum seconds between question starts")
    ap.add_argument("--groq-tpm", type=int, default=7000)
    ap.add_argument("--groq-rpm", type=int, default=25)
    ap.add_argument("--gemini-tpm", type=int, default=200_000)
    ap.add_argument("--gemini-rpm", type=int, default=8)
    ap.add_argument("--fake-llm", action="store_true")
    ap.add_argument(
        "--fake-oracle", action="store_true", help="with --fake-llm: answer with the gold SQL (plumbing self-test)"
    )
    ap.add_argument(
        "--bird-dir", default=None, help="folder containing dev_databases/ (default: .cache/minidev or bench/.bird)"
    )
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--profile", default=None, help="profile.v1 file (default: profiles/default.json)")
    ap.add_argument("--no-pin-models", dest="pin_models", action="store_false")
    ap.add_argument("--sql-timeout-s", type=float, default=30.0, help="per-query timeout for candidates (BIRD-style)")
    ap.add_argument("--results-dir", default=str(BENCH_DIR / "results"))
    ap.add_argument("--summary", default=str(BENCH_DIR / "summary.json"))
    ap.add_argument("--no-summary", action="store_true")
    ap.add_argument("--min-n-summary", type=int, default=20)
    ap.add_argument("--summarize", action="store_true", help="only rebuild summary.json from the results folder")
    ap.add_argument("--llm-cache", default=str(evalkit.LLM_CACHE_PATH))
    ap.add_argument("--no-llm-cache", action="store_true")
    ap.add_argument("--app-db", default=None, help="override DATABASE_URL for this run (quota ledger, embeddings)")
    ap.add_argument("--store-db", action="store_true", help="also write per-question rows to the bench_results table")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if not a.summarize and not a.config:
        ap.error("--config is required (or use --summarize)")
    if a.fake_oracle and not a.fake_llm:
        ap.error("--fake-oracle requires --fake-llm")
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    if a.summarize:
        summ = build_summary(Path(a.results_dir).resolve(), Path(a.summary).resolve(), a.min_n_summary)
        print(json.dumps(summ["ablation"], indent=1))
        return 0
    h = Harness(a)
    code = asyncio.run(h.run())
    h.finish()
    return code


if __name__ == "__main__":
    sys.exit(main())
