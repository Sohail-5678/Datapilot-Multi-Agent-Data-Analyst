"""Build the fixed BIRD Mini-Dev benchmark subset and splits (SPEC §5.2, §13.4). Standard library only.

    python bench/build_subset.py [--bird-dir PATH] [--check]

Reads `mini_dev_sqlite.json` (500 questions) and writes, next to this file:
  subset_150.jsonl   150 questions, stratified by (db_id, difficulty), seed 13
  splits.json        train 50 / val 50 / test 50, each stratified the same way (test is never used for tuning)
  regression_40.jsonl 40 questions drawn from `val` (stratified) for the nightly regression run

Determinism: strata are visited in sorted order; within a stratum questions are sorted by question_id and
shuffled with one `random.Random(13)`; per-stratum quotas use largest-remainder rounding with ties broken
by the stratum key. Same input file → byte-identical outputs. `--check` runs every gold query read-only on
the full BIRD SQLite files and fails if any errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SEED = 13
FIELDS = ("question_id", "db_id", "question", "evidence", "gold_sql", "difficulty")
SPLITS = ("train", "val", "test")


def find_minidev(arg: str | None) -> Path:
    cands = [Path(arg)] if arg else [REPO / ".cache" / "minidev", HERE / ".bird"]
    for c in cands:
        c = c.expanduser().resolve()
        if (c / "mini_dev_sqlite.json").exists():
            return c
        hits = sorted(p for p in c.glob("**/mini_dev_sqlite.json") if "__MACOSX" not in p.parts) if c.is_dir() else []
        if hits:
            return hits[0].parent
    sys.exit(f"mini_dev_sqlite.json not found under {', '.join(map(str, cands))} (pass --bird-dir)")


def allocate(counts: dict[tuple[str, str], int], total: int) -> dict[tuple[str, str], int]:
    """Proportional allocation with largest-remainder rounding (deterministic tie-break by key)."""
    n = sum(counts.values())
    raw = {k: total * c / n for k, c in counts.items()}
    alloc = {k: math.floor(v) for k, v in raw.items()}
    rest = total - sum(alloc.values())
    for k in sorted(counts, key=lambda k: (-(raw[k] - alloc[k]), k))[:rest]:
        alloc[k] += 1
    return alloc


def strata(items: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for it in items:
        out[(it["db_id"], it["difficulty"])].append(it)
    return out


def record(q: dict) -> dict:
    return {
        "question_id": q["question_id"],
        "db_id": q["db_id"],
        "question": q["question"],
        "evidence": q.get("evidence") or "",
        "gold_sql": q["SQL"],
        "difficulty": q["difficulty"],
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def check_gold(minidev: Path, rows: list[dict]) -> None:
    bad = 0
    for r in rows:
        db = minidev / "dev_databases" / r["db_id"] / f"{r['db_id']}.sqlite"
        conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            conn.execute(r["gold_sql"]).fetchall()
        except sqlite3.Error as e:
            bad += 1
            print(f"  gold failed q{r['question_id']} ({r['db_id']}): {e}")
        finally:
            conn.close()
    print(f"gold check: {len(rows) - bad}/{len(rows)} gold queries ran")
    if bad:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bird-dir", help="folder containing mini_dev_sqlite.json (or a parent of it)")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--regression", type=int, default=40)
    ap.add_argument("--check", action="store_true", help="execute every selected gold query read-only")
    a = ap.parse_args()

    minidev = find_minidev(a.bird_dir)
    src = minidev / "mini_dev_sqlite.json"
    raw = src.read_bytes()
    questions = json.loads(raw)
    assert len(questions) == 500, f"expected 500 Mini-Dev questions, got {len(questions)}"
    rng = random.Random(SEED)

    # 1) the 150 subset, stratified by (db_id, difficulty)
    by_stratum = strata(questions)
    quotas = allocate({k: len(v) for k, v in by_stratum.items()}, a.n)
    chosen_by_stratum: dict[tuple[str, str], list[dict]] = {}
    for key in sorted(by_stratum):
        pool = sorted(by_stratum[key], key=lambda q: q["question_id"])
        rng.shuffle(pool)
        chosen_by_stratum[key] = [record(q) for q in pool[: quotas[key]]]
    subset = sorted((r for rs in chosen_by_stratum.values() for r in rs), key=lambda r: r["question_id"])
    assert len(subset) == a.n

    # 2) splits: walk strata in sorted order (shuffled order inside) and give each question to the split with
    #    the fewest questions of its (db, difficulty) stratum, then of its difficulty, then of its db, then
    #    overall (ties → train, val, test). Exactly n/3 each; strata, difficulties and dbs balanced.
    size = a.n // 3
    tally: dict[tuple, int] = defaultdict(int)
    split_of: dict[int, str] = {}
    for key in sorted(chosen_by_stratum):
        db_id, diff = key
        for r in chosen_by_stratum[key]:
            open_splits = [s for s in SPLITS if tally[(s,)] < size]
            best = min(open_splits, key=lambda s: (tally[(s, key)], tally[(s, "d", diff)], tally[(s, "db", db_id)],
                                                    tally[(s,)], SPLITS.index(s)))
            split_of[r["question_id"]] = best
            for t in ((best, key), (best, "d", diff), (best, "db", db_id), (best,)):
                tally[t] += 1
    splits = {s: sorted(q for q, sp in split_of.items() if sp == s) for s in SPLITS}
    assert all(len(v) == a.n // 3 for v in splits.values()), {k: len(v) for k, v in splits.items()}

    # 3) nightly regression subset: stratified draw from val
    val_rows = [r for r in subset if split_of[r["question_id"]] == "val"]
    val_strata = strata(val_rows)
    rq = allocate({k: len(v) for k, v in val_strata.items()}, a.regression)
    regression: list[dict] = []
    for key in sorted(val_strata):
        pool = sorted(val_strata[key], key=lambda r: r["question_id"])
        rng.shuffle(pool)
        regression += pool[: rq[key]]
    regression.sort(key=lambda r: r["question_id"])

    if a.check:
        check_gold(minidev, subset)

    write_jsonl(HERE / "subset_150.jsonl", subset)
    write_jsonl(HERE / "regression_40.jsonl", regression)

    def counts(rows: list[dict], field: str) -> dict[str, int]:
        c: dict[str, int] = defaultdict(int)
        for r in rows:
            c[r[field]] += 1
        return dict(sorted(c.items()))

    meta = {
        "source": "BIRD Mini-Dev (SQLite) mini_dev_sqlite.json — CC BY-SA 4.0, https://github.com/bird-bench/mini_dev",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "seed": SEED,
        "stratified_by": ["db_id", "difficulty"],
        "subset": "subset_150.jsonl",
        "regression": "regression_40.jsonl (drawn from val)",
        "rule": "test is never used for tuning: few-shot pools, prompt/profile optimisation and thresholds use train (and val for selection) only",
        "counts": {
            s: {"n": len(ids), "difficulty": counts([r for r in subset if split_of[r["question_id"]] == s], "difficulty"),
                "db_id": counts([r for r in subset if split_of[r["question_id"]] == s], "db_id")}
            for s, ids in splits.items()
        },
        **splits,
        "regression_40": [r["question_id"] for r in regression],
    }
    (HERE / "splits.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"subset: {len(subset)}  difficulty={counts(subset, 'difficulty')}")
    print(f"dbs: {counts(subset, 'db_id')}")
    print("splits:", {s: len(v) for s, v in splits.items()}, " regression:", len(regression))


if __name__ == "__main__":
    main()
