"""Merge the per-shard reports of bench.run (head.json / base.json) into one report (standard library only).

  python tools/ci/bench_merge.py --out merged/head.json shard-1/head.json shard-2/head.json ...

Scenarios are united (a name that is in two files is an error); profile and python are taken from the first file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def merge(paths: list[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {"scenarios": {}, "external": {}}
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in ("profile", "python"):
            out.setdefault(k, data.get(k))
        for name, r in data["scenarios"].items():
            if name in out["scenarios"]:
                raise SystemExit(f"scenario {name} is in two shards ({p})")
            out["scenarios"][name] = r
        out["external"].update(data.get("external", {}))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_merge")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("reports", nargs="+", type=Path)
    a = ap.parse_args(argv)
    merged = merge(a.reports)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.out}: {len(merged['scenarios'])} scenarios from {len(a.reports)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
