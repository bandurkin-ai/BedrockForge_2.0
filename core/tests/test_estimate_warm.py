"""The warm-up of the estimation kernels (Э18, 18.17; lesson 17.39б): after `warm()` on a cold `NUMBA_CACHE_DIR` the
first estimate of a portion — every method, a point and a block, the columns of the cache read-only, the centres of a
portion a read-only slice, `local`, `sizes`, `exclude`, threads — adds no signature to any kernel; the cold
compilation takes ≤ 30 s, from the cache — seconds. Each run is a fresh process (the compiled signatures of this
process would hide a gap in the warm-up)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.geostat.estimate import warm as ew

COLD_LIMIT_S = 30.0
CACHED_LIMIT_S = 8.0

SCRIPT = r"""
import json, sys, time
import numpy as np
from bf_core.geostat.estimate import cov, indicator, kernel, kernel_block, krige, methods, search, search_kernel
from bf_core.geostat.estimate import warm as ew

MODS = (cov, krige, search, search_kernel, methods, indicator, kernel, kernel_block)


def sigs():
    out = {}
    for m in MODS:
        for name, obj in vars(m).items():
            if hasattr(obj, "signatures") and hasattr(obj, "py_func"):
                out[f"{m.__name__.rsplit('.', 1)[-1]}.{name}"] = sorted(str(s) for s in obj.signatures)
    return out


def frozen(a):
    a = np.array(a)
    a.setflags(write=False)
    return a


def battery():
    from bf_core.geostat.estimate.cov import BlockCov, cov_model
    from bf_core.geostat.estimate.kernel import Portion, compile_row, estimate, run
    from bf_core.geostat.estimate.samples import prepare
    from bf_core.geostat.estimate.spec import parse_estimation
    from bf_core.geostat.variogram.model import parse_model

    rng = np.random.default_rng(7)
    n = 400
    origin = np.array([512000.0, 6789000.0, 250.0])
    xyz = rng.uniform(-60.0, 60.0, (n, 3)) + origin
    hole = rng.integers(0, 25, n).astype(np.int32)
    dom = np.zeros(n, np.int16)
    dom[::9] = 1
    z = rng.lognormal(0.5, 0.8, n)
    w = rng.uniform(0.5, 1.5, n)
    vg = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.6, "a1": 50, "a2": 35, "a3": 20, "ang1": 30},
                                    {"type": "exponential", "c": 0.3, "a1": 90, "a2": 60, "a3": 30, "ang1": 30}]}
    bc = BlockCov(cov_model(parse_model(vg)))
    cache_xyz = frozen(np.column_stack([rng.uniform(-50, 50, 3000) + origin[0], rng.uniform(-50, 50, 3000) + origin[1],
                                        rng.uniform(-30, 30, 3000) + origin[2]]))  # a cached column: read-only
    sizes_all = frozen(np.tile([[10.0, 10.0, 5.0]], (3000, 1)))
    sizes_mix = frozen(np.where(np.arange(3000)[:, None] % 4 == 0, [[5.0, 5.0, 2.5]], [[10.0, 10.0, 5.0]]))
    act = frozen(rng.uniform(0, 1, 3000) > 0.1)
    for ro in (True, False):
        s = prepare(xyz, z, None, w, hole, [f"h{i}" for i in range(25)], dom, np.arange(n, dtype=np.int64),
                    np.zeros((n, 2)), origin=origin)
        if ro:
            for name in ("xyz", "local", "z", "raw", "w", "hole", "dom", "row_id", "span", "merged"):
                getattr(s, name).setflags(write=False)
        for kind in ("nn", "idw", "sk", "ok", "ik"):
            for disc in ((1, 1, 1), (3, 3, 2)):
                for passes in ([{"factor": 1, "n_min": 4, "n_max": 24, "n_hole": 6, "n_oct": 4}],
                               [{"factor": 1, "n_min": 4, "n_max": 16}, {"factor": 3, "n_min": 2, "n_max": 24}]):
                    body = {"model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "disc": list(disc),
                            "method": {"kind": kind, "power": 2.5,
                                       "ik": {"thresholds": [0.8, 1.6, 3.0], "models": "median"}},
                            "search": {"from": "manual", "r1": 80.0, "r2": 60.0, "r3": 30.0, "ang1": 30.0,
                                       "passes": passes}}
                    if kind in ("sk", "ok", "ik"):
                        body["variogram"] = {"model_id": 1, "snapshot": vg}
                    if kind == "ik" and disc == (3, 3, 2):  # a model per threshold
                        body["method"]["ik"]["models"] = "per"
                        body["variograms"] = [{"model_id": 1, "snapshot": vg} for _ in range(3)]
                    spec = parse_estimation(body)
                    rk = compile_row(spec, s, block_size=(10.0, 10.0, 5.0), rotation_deg=30.0, blockcov=bc)
                    for lo in (0, 700):
                        c = cache_xyz[lo:lo + 500]  # a read-only slice, as the job takes the portion
                        estimate(rk, c, active=act[lo:lo + 500])
                        estimate(rk, c, sizes=sizes_mix[lo:lo + 500], active=act[lo:lo + 500])
                        estimate(rk, frozen(c - origin), local=True)
                        estimate(rk, c[:1])
                    estimate(rk, cache_xyz[:0])  # an empty portion
                    estimate(rk, np.full((5, 3), np.nan))  # nothing to do: the centres are not finite
                    estimate(rk, cache_xyz[:40], exclude=("sample", np.arange(40)))
                    estimate(rk, cache_xyz[:40], exclude=("hole", np.arange(40) % 25))
                    portions = [Portion(cache_xyz[i:i + 300], sizes_all[i:i + 300], act[i:i + 300])
                                for i in range(0, 1200, 300)]
                    for _ in run(rk, portions, threads=2):
                        pass
                    for _ in run(rk, (p.centres for p in portions), threads=1):
                        pass
                    kernel_block.estimate_block(rk, cache_xyz[5])


t0 = time.perf_counter()
took = ew.warm()
warm_s = time.perf_counter() - t0
after_warm = sigs()
t1 = time.perf_counter()
battery()
first_s = time.perf_counter() - t1
after = sigs()
extra = {k: [s for s in v if s not in after_warm.get(k, [])] for k, v in after.items() if v != after_warm.get(k, [])}
print("RESULT" + json.dumps({"warm_s": warm_s, "first_s": first_s, "took": took, "extra": extra,
                             "n_kernels": len(after_warm), "parts": list(ew.PARTS)}))
"""


def _run(cache: Path, tmp: Path) -> dict[str, Any]:
    script = tmp / "probe_estimate_warm.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=900, check=False)  # noqa: S603
    assert p.returncode == 0, p.stderr[-3000:]
    line = next(ln for ln in p.stdout.splitlines() if ln.startswith("RESULT"))
    return json.loads(line[len("RESULT") :])  # type: ignore[no-any-return]


def test_cold_then_cached_adds_no_signature(tmp_path: Path) -> None:
    cache = tmp_path / "numba"
    cold = _run(cache, tmp_path)
    print(f"\ncold: warm {cold['warm_s']:.1f} s, first estimate {cold['first_s']:.2f} s, took {cold['took']}")
    assert cold["extra"] == {}, f"the first estimate adds signatures: {cold['extra']}"
    assert cold["n_kernels"] >= 8
    assert set(cold["took"]) == set(cold["parts"]), "a part of the warm-up failed"
    assert cold["warm_s"] <= COLD_LIMIT_S
    cached = _run(cache, tmp_path)
    print(f"cached: warm {cached['warm_s']:.1f} s, first estimate {cached['first_s']:.2f} s")
    assert cached["extra"] == {}
    assert cached["warm_s"] <= CACHED_LIMIT_S


def test_a_failing_part_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("x")

    parts = {"a": lambda: ran.append("a"), "b": boom, "c": lambda: ran.append("c")}
    monkeypatch.setattr(ew, "PARTS", parts)
    took = ew.warm()
    assert ran == ["a", "c"]
    assert set(took) == {"a", "c"}
    assert all(v >= 0.0 for v in took.values())
