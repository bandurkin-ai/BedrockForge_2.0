"""Speed and peak-memory benchmarks (ТЗ 7.33, 10.3).

  python -m bench.run --profile ci [--check] [--repeat 3] [--out bench/out/ci.json]
  python -m bench.run --compare base.json head.json

Each scenario runs in its own process; data are prepared separately (outside the timing).
Regression rule: slower by > 20 % AND by >= 0.25 s against the base on the same runner.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

REL_LIMIT = 0.20
ABS_LIMIT_S = 0.25


def load_budgets(profile: str) -> dict[str, Any]:
    data = json.loads((ROOT / "bench" / "budgets.json").read_text(encoding="utf-8"))
    return data[profile]


def peak_rss_mb() -> float | None:
    # Linux: VmHWM of this process. ru_maxrss survives fork + exec, so a child would report the peak of the
    # parent that prepared the data of earlier scenarios (bench/run.py prepares in-process).
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:  # Windows: measured by the customer smoke scenario
        return None


def child(name: str, params: dict[str, Any], work: Path) -> None:
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()
    mod = importlib.import_module(f"bench.scenarios.{name}")
    if hasattr(mod, "load"):
        mod.load()  # heavy imports and JIT cache load stay outside the timing
    t0 = time.perf_counter()
    info = mod.run(params, work)
    seconds = time.perf_counter() - t0
    print(json.dumps({"seconds": seconds, "peak_mb": peak_rss_mb(), "info": info}))


def run_profile(profile: str, repeat: int, only: list[str] | None) -> dict[str, Any]:
    budgets = load_budgets(profile)
    results: dict[str, Any] = {}
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "core")])}
    for name, spec in budgets.items():
        if only and name not in only:
            continue
        with tempfile.TemporaryDirectory(prefix=f"bf-bench-{name}-") as tmp:
            work = Path(tmp)
            importlib.import_module(f"bench.scenarios.{name}").prepare(
                spec["params"], work
            )
            runs = []
            for _ in range(repeat):
                out = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "bench.run",
                        "--child",
                        name,
                        "--profile",
                        profile,
                        "--work",
                        str(work),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if out.returncode != 0:  # the reason of a failed child goes into the log of the CI (PR #35)
                    print(f"bench: {name} failed (exit {out.returncode}):\n{out.stderr[-4000:]}", file=sys.stderr)
                    out.check_returncode()
                runs.append(json.loads(out.stdout.strip().splitlines()[-1]))
        secs = statistics.median(r["seconds"] for r in runs)
        peaks = [r["peak_mb"] for r in runs if r["peak_mb"] is not None]
        results[name] = {
            "seconds": round(secs, 4),
            "peak_mb": round(max(peaks), 1) if peaks else None,
            "budget_s": spec["budget_s"],
            "budget_mb": spec["budget_mb"],
            "limits": spec.get("limits", {}),
            "info": runs[-1]["info"],
        }
    return {"profile": profile, "python": sys.version.split()[0], "scenarios": results}


def external_results() -> dict[str, Any]:
    """Results written by other checks (the frame of the 3D window from the E2E perf3d): read when present.

    Only the deterministic counters have budgets (`limits`); times go to the report.
    """
    spec = json.loads((ROOT / "bench" / "budgets.json").read_text(encoding="utf-8")).get("external", {})
    out: dict[str, Any] = {}
    for name, e in spec.items():
        if name.startswith("_"):
            continue
        path = ROOT / e["file"]
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        out[name] = {"file": e["file"], "limits": e.get("limits", {}), "report": e.get("report", []), "info": data}
    return out


def check_external(report: dict[str, Any]) -> list[str]:
    errors = []
    for name, r in report.get("external", {}).items():
        for key, limit in r["limits"].items():
            value = r["info"].get(key)
            if value is None or value > limit:
                errors.append(f"{name}: {key} = {value} > budget {limit}")
    return errors


def check_budgets(report: dict[str, Any]) -> list[str]:
    errors = check_external(report)
    for name, r in report["scenarios"].items():
        if r["seconds"] > r["budget_s"]:
            errors.append(f"{name}: {r['seconds']:.3f} s > budget {r['budget_s']} s")
        if r["peak_mb"] is not None and r["peak_mb"] > r["budget_mb"]:
            errors.append(f"{name}: {r['peak_mb']:.0f} MB > budget {r['budget_mb']} MB")
        # stages of one scenario with their own budgets: "limits" {info key: max} (seconds, MB of answers)
        for key, limit in r.get("limits", {}).items():
            value = r.get("info", {}).get(key)
            if value is None or value > limit:
                errors.append(f"{name}: {key} = {value} > budget {limit}")
    return errors


def compare(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    errors = []
    for name, h in head["scenarios"].items():
        b = base.get("scenarios", {}).get(name)
        if not b:
            continue
        delta = h["seconds"] - b["seconds"]
        if delta >= ABS_LIMIT_S and h["seconds"] > b["seconds"] * (1 + REL_LIMIT):
            errors.append(
                f"{name}: {b['seconds']:.3f} s -> {h['seconds']:.3f} s (+{delta:.3f} s)"
            )
    return errors


def table(report: dict[str, Any], base: dict[str, Any] | None = None) -> str:
    lines = [
        "| Сценарий | Время, с | База, с | Память, МБ | Бюджет |",
        "|---|---|---|---|---|",
    ]
    for name, r in report.get("external", {}).items():
        shown = ", ".join(f"{k} {r['info'].get(k)}" for k in [*r["limits"], *r["report"]])
        lines.append(f"| {name} (внешний) | {shown} | — | — | {r['limits']} |")
    for name, r in report["scenarios"].items():
        b = (base or {}).get("scenarios", {}).get(name, {}).get("seconds")
        mem = "—" if r["peak_mb"] is None else f"{r['peak_mb']:.0f}"
        base_s = "—" if b is None else f"{b:.3f}"
        lines.append(
            f"| {name} | {r['seconds']:.3f} | {base_s} | {mem} | {r['budget_s']} с / {r['budget_mb']} МБ |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.run")
    ap.add_argument("--profile", default="ci", choices=["ci", "full"])
    ap.add_argument(
        "--check", action="store_true", help="fail when a budget is exceeded"
    )
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BASE", "HEAD"))
    ap.add_argument("--child")
    ap.add_argument("--work", type=Path)
    a = ap.parse_args(argv)

    if a.child:
        child(a.child, load_budgets(a.profile)[a.child]["params"], a.work)
        return 0
    if a.compare:
        base, head = (json.loads(p.read_text(encoding="utf-8")) for p in a.compare)
        print(table(head, base))
        errors = compare(base, head)
        for e in errors:
            print(f"REGRESSION {e}")
        return 1 if errors else 0

    report = run_profile(a.profile, a.repeat, a.only)
    report["external"] = external_results()
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(table(report))
    if a.check:
        errors = check_budgets(report)
        for e in errors:
            print(f"OVER BUDGET {e}")
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
