"""Э19, 19.21: the warm-up of the numba kernels of the implicit model (`model/warm.py`; ADR 0026, «numba: cache=True»;
lesson 17.39б). After `warm()` on a cold `NUMBA_CACHE_DIR` the real calls of the stage — other sizes, the arrays of the
patches, the query points and the intervals of a hole read-only and writable, every kernel, the global matrix and the
matrices of the patches, threads, several bands — add no signature to any `@njit` kernel of `model/*`; the cold
compilation takes ≤ 30 s, from the cache — seconds. Each run is a fresh process (the signatures compiled in this
process would hide a gap in the warm-up)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.model import warm as mw

COLD_LIMIT_S = 30.0
CACHED_LIMIT_S = 8.0
CORE = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import json, sys, time
import numpy as np
from bf_core.model import data_kernels, field_kernel, kernels, veins_kernels
from bf_core.model import warm as mw

MODS = (kernels, field_kernel, data_kernels, veins_kernels)


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


def fibonacci(n, r):
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def battery():
    from bf_core.composites import intervals as iv
    from bf_core.drillholes.desurvey import Collars, desurvey
    from bf_core.geo.iso import isosurface
    from bf_core.model import smoothing
    from bf_core.model.data_holes import domain_points
    from bf_core.model.evaluate import BAND, PatchField, evaluate
    from bf_core.model.octree import build_patches
    from bf_core.model.solve import solve_patches
    from bf_core.model.spec import parse_model
    from bf_core.model.veins import build_vein

    rng = np.random.default_rng(11)
    org = np.array([512_345.678, 6_789_012.345, 250.0])  # UTM-sized coordinates: the model works around the origin
    n = 300
    pts = np.concatenate([fibonacci(n, 50.0), fibonacci(n // 2, 25.0), fibonacci(n // 2, 75.0)]) + org
    f = np.concatenate([np.zeros(n), np.full(n // 2, -25.0), np.full(n // 2, 25.0)])
    order = np.lexsort((f, pts[:, 2], pts[:, 1], pts[:, 0]))
    pts, f = np.ascontiguousarray(pts[order]), np.ascontiguousarray(f[order])
    spec = parse_model({"boundary": {"box": [-1e6, -1e6, -1e6, 1e6, 1e6, 1e6]}, "smoothing": {"mode": "auto"}})
    a_glob = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.1], [0.0, 0.1, 1.6]])
    q_big = np.ascontiguousarray(rng.uniform(-80.0, 80.0, (3 * BAND + 17, 3)) + org)  # several bands, the tree path
    for ro in (True, False):
        p, v = (frozen(pts), frozen(f)) if ro else (pts, f)
        for matrix in (None, a_glob):
            ps = build_patches(p, n_patch=150, n_min=60, matrix=matrix)  # frozen arrays
            for kind in ("linear", "cubic", "wendland"):
                radius = 90.0 if kind == "wendland" else None
                mats = None if matrix is not None else [a_glob] * ps.n_patches
                eta = smoothing.eta_for_patches(p, None, ps, spec, radius=radius or 90.0, matrices=mats)
                sols = solve_patches(p, v, ps, kind=kind, radius=radius, eta=eta, threads=3, matrices=mats)
                fld = PatchField(ps, sols, matrices=mats, threads=3)
                for q in (frozen(q_big), q_big, frozen(q_big[:5]), q_big[:1]):
                    fld(q)
                    fld.terms(q)
                evaluate(ps, sols, frozen(q_big[:50]), matrices=mats)
        ps = build_patches(p, n_patch=150, n_min=60)
        fld = PatchField(ps, solve_patches(p, v, ps, kind="cubic"))
        isosurface(lambda q: fld(q), org - 70.0, org + 70.0, 5.0, threads=2)
        # the holes: other numbers of holes and rows, the table read-only (a cache) and writable
        nh = 7
        hole = np.repeat(np.arange(nh), 6)
        frm = np.tile(np.arange(6) * 8.0, nh)
        to = frm + 8.0
        lines = iv.build_lines(hole, frm, to)
        if ro:
            import dataclasses

            cols = {k: frozen(getattr(lines, k)) for k in ("holes", "starts", "frm", "to")}
            lines = dataclasses.replace(lines, **cols)
        traj = desurvey(Collars(np.arange(nh), 100.0 + 25.0 * np.arange(nh), np.full(nh, 200.0), np.full(nh, 500.0),
                                np.full(nh, 60.0)), n_holes=nh)
        state = (np.arange(len(frm)) % 3 != 0).astype(np.int8)
        domain_points(lines, state, traj, np.arange(nh), d_max=12.0, step=3.0, group=2, threads=2)
        # a vein: more points, a polygon
        u, w = np.meshgrid(np.linspace(0.0, 60.0, 9), np.linspace(0.0, 40.0, 7), indexing="ij")
        uv = np.column_stack([u.ravel(), w.ravel()])
        base = np.column_stack([uv, 0.2 * uv[:, 0] + rng.normal(0, 0.05, len(uv))]) + org
        hang, foot = base + [0.0, 0.0, 1.5], base - [0.0, 0.0, 1.5]
        if ro:
            hang, foot = frozen(hang), frozen(foot)
        build_vein(hang, foot, {"bound": "hull", "offset": 1.0, "t_min": 0.2}, 3.0, threads=2)


t0 = time.perf_counter()
took = mw.warm()
warm_s = time.perf_counter() - t0
after_warm = sigs()
t1 = time.perf_counter()
battery()
first_s = time.perf_counter() - t1
after = sigs()
extra = {k: [s for s in v if s not in after_warm.get(k, [])] for k, v in after.items() if v != after_warm.get(k, [])}
print("RESULT" + json.dumps({"warm_s": warm_s, "first_s": first_s, "took": took, "extra": extra,
                             "n_kernels": len(after_warm), "parts": list(mw.PARTS),
                             "n_sigs": {k: len(v) for k, v in after_warm.items()}}))
"""

#: the kernels that numba compiles for a call from Python (the others are called by them: with the compiled caller
#: loaded from the cache they carry no signature of their own)
ENTRY = (
    "kernels._gram", "kernels._cross", "kernels._phi_array", "field_kernel.field_band", "field_kernel.count_terms",
    "data_kernels.domain_strip", "veins_kernels._gram", "veins_kernels._evaluate",
)  # fmt: skip


def _run(cache: Path, tmp: Path) -> dict[str, Any]:
    script = tmp / "probe_model_warm.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONPATH": str(CORE)}
    cmd = [sys.executable, str(script)]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900, check=False)  # noqa: S603
    assert p.returncode == 0, p.stderr[-3000:]
    line = next(ln for ln in p.stdout.splitlines() if ln.startswith("RESULT"))
    return json.loads(line[len("RESULT") :])  # type: ignore[no-any-return]


def test_cold_then_cached_adds_no_signature(tmp_path: Path) -> None:
    cache = tmp_path / "numba"
    cold = _run(cache, tmp_path)
    print(f"\ncold: warm {cold['warm_s']:.1f} s {cold['took']}, real calls {cold['first_s']:.2f} s")
    assert cold["extra"] == {}, f"the real calls add signatures: {cold['extra']}"
    assert set(cold["took"]) == set(cold["parts"]), "a part of the warm-up failed"
    assert cold["n_kernels"] >= 13
    assert all(cold["n_sigs"][k] >= 1 for k in ENTRY), cold["n_sigs"]
    assert cold["warm_s"] <= COLD_LIMIT_S
    cached = _run(cache, tmp_path)
    print(f"cached: warm {cached['warm_s']:.1f} s, real calls {cached['first_s']:.2f} s")
    assert cached["extra"] == {}
    assert set(cached["took"]) == set(cached["parts"])
    assert cached["warm_s"] <= CACHED_LIMIT_S


def test_import_does_not_load_numba(tmp_path: Path) -> None:
    code = "import sys; import bf_core.model.warm; assert 'numba' not in sys.modules, 'numba loaded'"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False,  # noqa: S603
                       env={**os.environ, "PYTHONPATH": str(CORE)}, cwd=tmp_path)  # fmt: skip
    assert p.returncode == 0, p.stderr[-2000:]


def test_a_failing_part_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(mw, "PARTS", {"a": lambda: ran.append("a"), "b": boom, "c": lambda: ran.append("c")})
    took = mw.warm()
    assert ran == ["a", "c"]
    assert set(took) == {"a", "c"}
    assert all(v >= 0.0 for v in took.values())
