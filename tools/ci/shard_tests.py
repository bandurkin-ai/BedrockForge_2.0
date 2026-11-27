"""Split the pytest files of bf-core into N CI shards by weight, and prove that nothing is lost.

  python tools/ci/shard_tests.py --shard 1 --of 4        # files of shard 1, one per line (relative to core/)
  python tools/ci/shard_tests.py --summary --of 4        # estimated minutes per shard
  python tools/ci/shard_tests.py --check --of 4          # completeness: needs the core environment (pytest)
  python tools/ci/shard_tests.py --update-weights junit.xml [--scale-to-minutes 89]

Run `--check` from the core environment: `cd core && uv run python ../tools/ci/shard_tests.py --check --of 4`.

How it works. Every test file (`test_*.py`, `*_test.py` under core/tests, like pytest's own defaults) gets a weight:
seconds from tools/ci/test_weights.json, or, for a file that is not in the table yet, (number of `def test_`)
x the average seconds per test of the table. Files are dealt to the shards by the greedy longest-first rule
(tools/ci/shardlib.py), so the result is deterministic and a NEW test file lands in some shard on its own.
The table only balances the shards; a stale table never loses a test.

`--check` (1) collects the whole suite (`pytest --collect-only -q`), (2) checks that the shards are a partition of
the test files and that every collected file is in a shard, (3) collects every shard separately and checks that the
union of the node ids of the shards is exactly the node ids of the whole suite: no loss, no duplicate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import shardlib  # noqa: E402

ROOT = HERE.parents[1]
CORE = ROOT / "core"
TESTS = CORE / "tests"
WEIGHTS = HERE / "test_weights.json"
DEF_TEST = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.M)
SKIP_DIRS = {"__pycache__", "node_modules", "build", "dist", "venv", "CVS", "_darcs"}  # pytest's norecursedirs


def discover() -> list[str]:
    """Test files like pytest finds them under core/tests, as posix paths relative to core/."""
    found = []
    for pattern in ("test_*.py", "*_test.py"):
        for p in TESTS.rglob(pattern):
            rel = p.relative_to(CORE)
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            found.append(rel.as_posix())
    return sorted(set(found))


def load_table(path: Path = WEIGHTS) -> tuple[dict[str, float], float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data["files"].items()}, float(data["seconds_per_test"])


def weights_of(files: list[str], table: dict[str, float], per_test: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for f in files:
        if f in table:
            out[f] = table[f]
        else:
            n = len(DEF_TEST.findall((CORE / f).read_text(encoding="utf-8", errors="replace")))
            out[f] = max(n, 1) * per_test
    return out


def plan(n: int, files: list[str] | None = None) -> tuple[list[list[str]], dict[str, float]]:
    files = discover() if files is None else files
    table, per_test = load_table()
    w = weights_of(files, table, per_test)
    return shardlib.split(w, n), w


def collect(files: list[str] | None = None) -> list[str]:
    """Node ids from `pytest --collect-only -q` (all tests, or only the given files)."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *(files or [])]
    r = subprocess.run(cmd, cwd=CORE, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"pytest --collect-only failed (exit {r.returncode}):\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
    return [ln.strip() for ln in r.stdout.splitlines() if "::" in ln and not ln.startswith(" ")]


def check(n: int, per_shard: bool) -> int:
    errors: list[str] = []
    files = discover()
    shards, w = plan(n, files)
    flat = [f for s in shards for f in s]
    if sorted(flat) != files:
        dup = [f for f, c in Counter(flat).items() if c > 1]
        miss = sorted(set(files) - set(flat))
        errors.append(f"shards are not a partition of the test files: duplicated {dup}, missing {miss}")
    if any(not s for s in shards):
        errors.append("an empty shard: fewer test files than shards")

    all_ids = collect()
    collected_files = {i.split("::", 1)[0] for i in all_ids}
    outside = sorted(collected_files - set(flat))
    if outside:
        errors.append(f"collected test files that are in no shard: {outside}")
    print(f"whole suite: {len(all_ids)} tests in {len(collected_files)} files ({len(files)} test_*.py files on disk)")

    if per_shard:
        got: Counter[str] = Counter()
        for i, s in enumerate(shards, 1):
            ids = collect(s)
            dup = [x for x, c in Counter(ids).items() if c > 1]
            if dup:
                errors.append(f"shard {i}: node ids repeated inside the shard: {dup[:5]}")
            got.update(ids)
            print(f"shard {i}/{n}: {len(s)} files, {len(ids)} tests")
        want = Counter(all_ids)
        lost, extra = want - got, got - want
        if lost:
            errors.append(f"tests lost by the shards: {len(lost)}, e.g. {sorted(lost)[:5]}")
        if extra:
            errors.append(f"tests run more than once or not in the whole suite: {len(extra)}, e.g. {sorted(extra)[:5]}")
        if not lost and not extra:
            print(f"OK: the union of {n} shards == the whole suite ({sum(got.values())} tests), no loss, no duplicate")
    if errors:
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        return 1
    print("OK: the shards are a partition of the test files")
    return 0


def summary(n: int) -> None:
    shards, w = plan(n)
    for i, (s, load) in enumerate(zip(shards, shardlib.loads(w, shards), strict=True), 1):
        print(f"shard {i}/{n}: {len(s):3d} files, estimated {load / 60:5.1f} min")


def module_file(classname: str) -> str | None:
    parts = classname.split(".")
    for k in range(len(parts), 0, -1):
        cand = "/".join(parts[:k]) + ".py"
        if (CORE / cand).is_file():
            return cand
    return None


def update_weights(junit: list[Path], scale_min: float | None) -> None:
    """Rebuild the table from pytest --junitxml files (seconds per file; several files are summed)."""
    sec: dict[str, float] = defaultdict(float)
    tests: Counter[str] = Counter()
    for xml in junit:
        for tc in ET.parse(xml).getroot().iter("testcase"):
            f = module_file(tc.get("classname", ""))
            if f:
                sec[f] += float(tc.get("time", 0))
                tests[f] += 1
    total = sum(sec.values())
    k = scale_min * 60 / total if scale_min else 1.0
    table = {f: round(s * k, 1) for f, s in sorted(sec.items())}
    per_test = round(total * k / sum(tests.values()), 3)
    out = {"_comment": "seconds per test file (pytest --junitxml, scaled to CI); balances shards only, "
           "see tools/ci/shard_tests.py --update-weights", "seconds_per_test": per_test, "files": table}
    WEIGHTS.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{WEIGHTS}: {len(table)} files, {sum(table.values()) / 60:.1f} min, {per_test} s per test")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shard_tests")
    ap.add_argument("--of", type=int, default=4, help="number of shards")
    ap.add_argument("--shard", type=int, help="print the files of this shard (1-based)")
    ap.add_argument("--check", action="store_true", help="prove the shards lose and repeat nothing")
    ap.add_argument("--no-per-shard", action="store_true", help="--check: partition only, no per-shard collect")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--update-weights", nargs="+", type=Path, metavar="JUNIT_XML")
    ap.add_argument("--scale-to-minutes", type=float)
    a = ap.parse_args(argv)
    if a.update_weights:
        update_weights(a.update_weights, a.scale_to_minutes)
        return 0
    if a.check:
        return check(a.of, not a.no_per_shard)
    if a.summary:
        summary(a.of)
        return 0
    if a.shard:
        if not 1 <= a.shard <= a.of:
            ap.error("--shard must be in 1..--of")
        shards, _ = plan(a.of)
        print("\n".join(shards[a.shard - 1]))
        return 0
    ap.error("give --shard, --check, --summary or --update-weights")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
