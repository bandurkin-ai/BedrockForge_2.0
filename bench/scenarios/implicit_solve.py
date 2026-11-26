"""The solution of the patches of an implicit model (ADR 0026 «Замеры»; ТЗ 8.9.4: 100 тыс. точек ≤ 60 с и ≤ 3 ГБ на
4 ядрах; решение 9, подшаг 19.4).

The data are the points of one domain of the scene of `implicit_common` as `model.data` makes them (contacts and
±D along holes; the canonical order); the octree of patches (N_уч = 1000, N_min = 100, δ = 0,25), then
`solve.solve_patches` on `params["threads"]` threads with η = 0 (the interpolation; the kernel of `params`):
  patches_s   `octree.build_patches` (the tree, the balls, the hashes);  solve_s — the solution of all the patches (the
              time of ТЗ);  rate — points per second;  field_s — the field at the data points (a check, not the time)
  patch_max   the points of the largest patch;  cond_max — the largest cond₁ of the patches
What is checked (a failure stops the run): the data are reproduced — the field of the solution at the data points
differs from the values by at most `RESIDUAL`·max |f| (Т8.9-3: η = 0, 1e−8); the bits of the solution (λ, c, cond₁) of
1 and of `threads` threads are the same (Т8.9-8) — on `bits` points of the cloud (all of them when `bits` is not
given): a run of 1 thread over 100 тыс. is four times the run of the target."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import implicit_common as ic

RESIDUAL = 1e-8  # Т8.9-3
FILE = "points.npz"
N_PATCH, N_MIN, DELTA = 1000, 100, 0.25  # 8.9.4 defaults


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    xyz, f = sc.domain_points(int(params.get("domain", 1)), threads=2)
    n = int(params["points"])
    assert xyz.shape[0] >= n, f"the scene has {xyz.shape[0]} points of the domain, {n} wanted: more holes"
    xyz, f = np.ascontiguousarray(xyz[:n]), np.ascontiguousarray(f[:n])  # a slab of the canonical order
    np.savez(work / FILE, xyz=xyz, f=f)


def load() -> None:
    from bf_core.model.warm import warm

    warm()  # the step `implicit` of the warm-up of the workers: a kernel it misses is compiled inside the timing


def digest(sols: list[Any]) -> list[bytes]:
    return [b"".join(np.asarray(v).tobytes() for v in (s.points, s.lam, s.poly, s.centre))
            + np.array([s.scale, s.eta, s.cond1], dtype=np.float64).tobytes() for s in sols]  # fmt: skip


def solve(xyz: np.ndarray, f: np.ndarray, kernel: str, threads: int) -> tuple[Any, list[Any], float, float]:
    from bf_core.model import octree
    from bf_core.model.solve import solve_patches

    t0 = time.perf_counter()
    ps = octree.build_patches(xyz, n_patch=N_PATCH, n_min=N_MIN, delta=DELTA)
    t1 = time.perf_counter()
    radius = 3.0 * float(np.median(ps.radii)) if kernel == "wendland" else None
    sols = solve_patches(xyz, f, ps, kind=kernel, radius=radius, eta=0.0, threads=threads)
    return ps, sols, t1 - t0, time.perf_counter() - t1


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.model.evaluate import PatchField

    threads = ic.limit_threads(params)
    kernel = str(params.get("kernel", "linear"))
    with np.load(work / FILE) as z:
        xyz, f = z["xyz"], z["f"]
    ps, sols, patches_s, solve_s = solve(xyz, f, kernel, threads)
    rss = ic.peak_mb()
    t0 = time.perf_counter()
    got = PatchField(ps, sols, threads=threads)(xyz, threads=threads)
    field_s = time.perf_counter() - t0
    scale = float(np.abs(f).max())
    residual = float(np.abs(got - f).max()) / scale
    assert residual <= RESIDUAL, f"the field misses the data by {residual:.2e}·max|f| (> {RESIDUAL:.0e})"
    n_bits = min(int(params.get("bits", xyz.shape[0])), xyz.shape[0])
    one: list[Any] = []
    many: list[Any] = []
    bits_s = 0.0
    if n_bits:
        t1 = time.perf_counter()
        one = digest(solve(xyz[:n_bits], f[:n_bits], kernel, 1)[1])
        bits_s = time.perf_counter() - t1
        many = digest(solve(xyz[:n_bits], f[:n_bits], kernel, max(threads, 2))[1])
        assert one == many, "the solution of the patches differs between 1 and several threads"
    sizes = ps.sizes
    return {"threads": threads, "kernel": kernel, "points": int(xyz.shape[0]), "patches": int(ps.n_patches),
            "patch_max": int(sizes.max()), "patch_mean": round(float(sizes.mean()), 1),
            "patches_s": round(patches_s, 3), "solve_s": round(solve_s, 3),
            "rate": round(xyz.shape[0] / solve_s, 1), "field_s": round(field_s, 3),
            "cond_max": float(max(s.cond1 for s in sols)), "residual": residual, "bits_points": n_bits,
            "bits_patches": len(one), "bits_s": round(bits_s, 3), "process_mb": ic.peak_mb() or 0.0,
            "solve_rss_mb": rss}  # fmt: skip
