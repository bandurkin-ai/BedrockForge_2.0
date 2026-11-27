"""Split the scenarios of `bench.run --profile ci` into N CI shards by weight (standard library only).

  python tools/ci/shard_bench.py --shard 2 --of 5     # scenario names of shard 2, space-separated (for --only)
  python tools/ci/shard_bench.py --check --of 5       # partition of bench/budgets.json["ci"], nothing lost
  python tools/ci/shard_bench.py --update-weights bench-ci/head.json

A scenario costs ~ 3 repeats x seconds + a fixed overhead (data preparation, process start, loading the JIT
cache). Seconds come from tools/ci/bench_weights.json (a head.json of a real run); a scenario that is not in the
table yet is weighed by 0.6 x its budget_s. A new scenario in budgets.json lands in some shard on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import shardlib  # noqa: E402

ROOT = HERE.parents[1]
BUDGETS = ROOT / "bench" / "budgets.json"
WEIGHTS = HERE / "bench_weights.json"
REPEAT = 3
OVERHEAD_S = 10.0


def scenarios(profile: str = "ci") -> dict[str, float]:
    """Scenario name -> budget_s, in the order of budgets.json."""
    data = json.loads(BUDGETS.read_text(encoding="utf-8"))[profile]
    return {k: float(v["budget_s"]) for k, v in data.items() if not k.startswith("_")}


def warmed(name: str) -> bool:
    """A scenario gets one unmeasured warm-up run before the measured repeats (numba cache). `*_compile` scenarios
    measure the cold compile themselves and are not warmed."""
    return not name.endswith("_compile")


def weights(profile: str = "ci") -> dict[str, float]:
    known = json.loads(WEIGHTS.read_text(encoding="utf-8"))["seconds"] if WEIGHTS.is_file() else {}
    return {
        name: (REPEAT + (1 if warmed(name) else 0)) * float(known.get(name, 0.6 * budget)) + OVERHEAD_S
        for name, budget in scenarios(profile).items()
    }


def plan(n: int, profile: str = "ci") -> tuple[list[list[str]], dict[str, float]]:
    w = weights(profile)
    return shardlib.split(w, n), w


def check(n: int) -> int:
    shards, _ = plan(n)
    flat = [s for sh in shards for s in sh]
    want = sorted(scenarios())
    errors = []
    if sorted(flat) != want:
        errors.append("shards are not a partition of the scenarios of the ci profile")
    if len(flat) != len(set(flat)):
        errors.append("a scenario is in two shards")
    if any(not s for s in shards):
        errors.append("an empty shard: fewer scenarios than shards")
    if errors:
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        return 1
    print(f"OK: {len(want)} scenarios in {n} shards: " + ", ".join(str(len(s)) for s in shards))
    return 0


def update_weights(head: Path) -> None:
    sc = json.loads(head.read_text(encoding="utf-8"))["scenarios"]
    out = {"_comment": "seconds per scenario (median of a head.json of bench.run --profile ci); balances shards only",
           "seconds": {k: round(v["seconds"], 2) for k, v in sc.items()}}
    WEIGHTS.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{WEIGHTS}: {len(sc)} scenarios")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shard_bench")
    ap.add_argument("--of", type=int, default=5)
    ap.add_argument("--shard", type=int)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--warm", action="store_true", help="with --shard: only the scenarios that are warmed up")
    ap.add_argument("--update-weights", type=Path, metavar="HEAD_JSON")
    a = ap.parse_args(argv)
    if a.update_weights:
        update_weights(a.update_weights)
        return 0
    if a.check:
        return check(a.of)
    shards, w = plan(a.of)
    if a.summary:
        for i, s in enumerate(shards, 1):
            est = shardlib.loads(w, [s])[0] / 60
            print(f"shard {i}/{a.of}: {len(s):3d} scenarios, estimated {est:5.1f} min (head only)")
        return 0
    if a.shard:
        if not 1 <= a.shard <= a.of:
            ap.error("--shard must be in 1..--of")
        names = shards[a.shard - 1]
        print(" ".join(n for n in names if warmed(n)) if a.warm else " ".join(names))
        return 0
    ap.error("give --shard, --check, --summary or --update-weights")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
