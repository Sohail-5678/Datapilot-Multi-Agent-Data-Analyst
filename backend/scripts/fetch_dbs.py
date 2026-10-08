"""Download the read-only demo databases (SPEC §5.1, §14.4) and verify their checksums.

Default: the release asset `demo-dbs-v1.tar.gz` of this repo (17 MB; Chinook + four BIRD Mini-Dev databases).
`--from-source` rebuilds the same files from the official sources instead: Chinook (lerocha/chinook-database,
MIT) and BIRD Mini-Dev (CC BY-SA 4.0, ~800 MB download). european_football_2 is slimmed for the 512 MB server by
nulling the Match table's event-XML columns (no BIRD question uses them); its schema is unchanged.
Every file is checked against data/dbs/checksums.json; a mismatch fails the build.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data" / "dbs"
RELEASE_URL = (
    "https://github.com/Sohail-5678/Datapilot-Multi-Agent-Data-Analyst/releases/download/demo-dbs-v1/demo-dbs-v1.tar.gz"
)
CHINOOK_URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
MINIDEV_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
BIRD_DBS = ("superhero", "student_club", "formula_1", "european_football_2")
SLIM_COLUMNS = ("goal", "shoton", "shotoff", "foulcommit", "card", "cross", "corner", "possession")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify() -> bool:
    expected = json.loads((ROOT / "checksums.json").read_text())
    ok = True
    for rel, meta in expected.items():
        p = ROOT / rel
        if not p.exists():
            print(f"missing {rel}")
            ok = False
        elif sha256(p) != meta["sha256"]:
            print(f"checksum mismatch {rel}")
            ok = False
    return ok


def _download(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=600) as r:  # noqa: S310 — fixed https URLs
        return r.read()


def from_release() -> None:
    data = _download(RELEASE_URL)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or not m.name.endswith(".sqlite") or ".." in m.name or m.name.startswith("/"):
                continue
            target = ROOT / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, target.open("wb") as dst:  # type: ignore[union-attr]
                shutil.copyfileobj(src, dst)


def from_source() -> None:
    (ROOT / "chinook").mkdir(parents=True, exist_ok=True)
    (ROOT / "chinook" / "chinook.sqlite").write_bytes(_download(CHINOOK_URL))
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "minidev.zip"
        zpath.write_bytes(_download(MINIDEV_URL))
        with zipfile.ZipFile(zpath) as z:
            for db in BIRD_DBS:
                name = next(n for n in z.namelist() if n.endswith(f"MINIDEV/dev_databases/{db}/{db}.sqlite"))
                target = ROOT / db / f"{db}.sqlite"
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    fb = ROOT / "european_football_2" / "european_football_2.sqlite"
    conn = sqlite3.connect(fb)
    conn.execute("UPDATE Match SET " + ", ".join(f'"{c}" = NULL' for c in SLIM_COLUMNS))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-source", action="store_true")
    ap.add_argument("--check", action="store_true", help="only verify checksums")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if verify() else 1)
    if verify():
        print("demo databases present and verified")
        return
    from_source() if a.from_source else from_release()
    if not verify():
        sys.exit("demo databases failed checksum verification")
    print("demo databases downloaded and verified")


if __name__ == "__main__":
    main()
