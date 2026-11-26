"""The automatic smoothing «Авто» (Д-25; ADR 0026 решение 11а «Замеры»; ТЗ 7.35 Д-25, 8.9.4: накладные «Авто» ≤ 10 %
времени решения; Т8.9-14…16; подшаг 19.10).

Three clouds of `points` points of the scene of `implicit_common`, the linear kernel, the patches N_уч = 1000:
  clean   the domain 0 (the top layer): the points of ONE contact, well spread, no pair of points near each other — the
          data of the target of 8.9.4 («невырожденные»);
  wells   the domain 1 (a layer between two contacts): the data of real holes, where the points ±D from the upper
          contact and from the lower one meet at a few centimetres, with nearly equal values (the screen of 19.10
          sees a small distance between the points and measures the patch);
  twins   the clean cloud (first `degenerate` points) where 2 % of the points have a twin 1e−4 m away, another value.
For each, in `info` (`clean_*`, `wells_*`, `twins_*`):
  eta_manual_s   `smoothing.eta_for_patches` in the mode «Вручную» (η = s·h̄: no work on the patches);
  eta_s          `smoothing.smooth_patches` in the mode «Авто» (the screen of every patch, the measurement of the ones
                 it lets through);  eta_1thread_s — the same on one thread;
  solve_s        `solve.solve_patches` with the η that «Авто» gave;  overhead — (eta_s − eta_manual_s) / solve_s: the
                 figure of the target (≤ 0,10) — the limit is on `clean_overhead`;  measured — the patches measured
What is checked (a failure stops the run): on the clean cloud η is the η₀ of «Вручную» for every patch (Т8.9-15: «Авто»
adds nothing to a patch whose condition number is within the target); the η of 1 and of `threads` threads are the
same bits (Т8.9-16); every patch measured has cond₂ ≤ 1,05·target (Т8.9-14); the twins have patches smoothed and the
system is solved with them."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import implicit_common as ic
from .implicit_solve import DELTA, N_MIN, N_PATCH

FILE = "points.npz"
TARGET = 1e8  # the default cond₂ of 8.9.4
TOL = 1.05  # Т8.9-14
TWINS = 0.02  # the share of points with a twin in the degenerate cloud
APART = 1e-4  # m


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    n = int(params["points"])
    out: dict[str, Any] = {"box": np.array(sc.box)}
    for tag, domain in (("clean", 0), ("wells", 1)):
        xyz, f = sc.domain_points(domain, threads=2)
        assert xyz.shape[0] >= n, f"the scene has {xyz.shape[0]} points of the domain {domain}, {n} wanted: more holes"
        out[f"{tag}_xyz"], out[f"{tag}_f"] = np.ascontiguousarray(xyz[:n]), np.ascontiguousarray(f[:n])
    np.savez(work / FILE, **out)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def spec(mode: str, box: Any) -> Any:
    from bf_core.model.spec import parse_model

    return parse_model({"boundary": {"box": [float(v) for v in box]}, "smoothing": {"mode": mode}})


def twins(xyz: np.ndarray, f: np.ndarray, count: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """The first `count` points of the canonical order and, for `TWINS` of them, a twin `APART` m away with another
    value (the pair a degenerate contact is: two holes that name one place two rocks)."""
    rng = np.random.default_rng(seed)
    a, v = xyz[:count], f[:count]
    pick = np.flatnonzero(rng.random(count) < TWINS)
    dx = rng.normal(0.0, 1.0, (pick.size, 3))
    dx *= APART / np.linalg.norm(dx, axis=1, keepdims=True)
    b, w = np.concatenate([a, a[pick] + dx]), np.concatenate([v, v[pick] + 0.5])
    order = np.lexsort((w, b[:, 2], b[:, 1], b[:, 0]))
    return np.ascontiguousarray(b[order]), np.ascontiguousarray(w[order])


def measure(xyz: np.ndarray, f: np.ndarray, box: Any, threads: int) -> dict[str, Any]:
    from bf_core.model import octree, smoothing
    from bf_core.model.solve import solve_patches

    ps = octree.build_patches(xyz, n_patch=N_PATCH, n_min=N_MIN, delta=DELTA)
    manual, auto = spec("manual", box), spec("auto", box)
    t0 = time.perf_counter()
    eta0 = smoothing.eta_for_patches(xyz, None, ps, manual, threads=threads)
    t1 = time.perf_counter()
    res = smoothing.smooth_patches(xyz, ps, auto, threads=threads)
    t2 = time.perf_counter()
    again = smoothing.smooth_patches(xyz, ps, auto, threads=1)
    t3 = time.perf_counter()
    assert res.eta.tobytes() == again.eta.tobytes(), "the η of «Авто» differs between 1 and several threads"
    solve_patches(xyz, f, ps, kind="linear", eta=list(res.eta), threads=threads)
    t4 = time.perf_counter()
    cond = res.cond[~np.isnan(res.cond)]
    assert cond.size == 0 or float(cond.max()) <= TOL * TARGET, f"cond₂ {cond.max():.3e} > {TOL}·{TARGET:.0e} (Т8.9-14)"
    solve_s = t4 - t3
    return {"points": int(xyz.shape[0]), "patches": int(ps.n_patches), "eta0": eta0, "eta": res.eta,
            "measured": int(res.exact.sum()), "smoothed": int((res.eta > eta0).sum()),
            "eta_manual_s": t1 - t0, "eta_s": t2 - t1, "eta_1thread_s": t3 - t2, "solve_s": solve_s,
            "overhead": max((t2 - t1) - (t1 - t0), 0.0) / solve_s,
            "cond_max": float(cond.max()) if cond.size else 0.0}  # fmt: skip


def shown(tag: str, got: dict[str, Any]) -> dict[str, Any]:
    keys = ("points", "patches", "measured", "smoothed", "cond_max")
    out: dict[str, Any] = {f"{tag}_{k}": got[k] for k in keys}
    out.update({f"{tag}_{k}": round(got[k], 3) for k in ("eta_manual_s", "eta_s", "eta_1thread_s", "solve_s")})
    out[f"{tag}_overhead"] = round(got["overhead"], 4)
    out[f"{tag}_eta_max"] = float(got["eta"].max())
    return out


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ic.limit_threads(params)
    with np.load(work / FILE) as z:
        box = z["box"]
        clean_xyz, clean_f, wells_xyz, wells_f = z["clean_xyz"], z["clean_f"], z["wells_xyz"], z["wells_f"]
    clean = measure(clean_xyz, clean_f, box, threads)
    assert np.array_equal(clean["eta"], clean["eta0"]), "«Авто» added η to a patch that needs none (Т8.9-15)"
    info: dict[str, Any] = {"threads": threads, **shown("clean", clean)}
    info["wells_note"] = "points ±D from two contacts of a hole meet within centimetres"
    info.update(shown("wells", measure(wells_xyz, wells_f, box, threads)))
    n_deg = min(int(params.get("degenerate", 0)), clean_xyz.shape[0])
    if n_deg:
        dx, df = twins(clean_xyz, clean_f, n_deg)
        deg = measure(dx, df, box, threads)
        assert deg["smoothed"] > 0, "the cloud with twins has no patch that «Авто» smoothed"
        info.update(shown("twins", deg))
    info["process_mb"] = ic.peak_mb()
    return info
