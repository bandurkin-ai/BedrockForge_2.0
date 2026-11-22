"""The fit of a model of three nested structures to the axes — `POST /variography/fit` (ADR 0022 «Замеры», решения
12, 24; ТЗ 8.6.4: «подбор 3 структур — ≤ 3 с»; the target is the same in ci and full: 3 axes × 20 lags whatever
the number of points).

`prepare` makes the record of the cache the window fits to: the job `variography.experimental` of
`variogram_axes` (the composites of 2 m of AU, the axes of (30°, −10°, 0°), L = 15 m × 20 lags, along the holes
2 m × 20) on the project of the scenario. The fits — the function of the route (`routes.fit` in a HEAVY slot of
`stats.routes_charts`, the record read by its key), every one from the start of the window (`START`: C0 and
three spherical structures, the angles of the axes):
  fit_n_h_s, fit_n_s, fit_n_g2_s   the fit of the points of the generator (no model under them: the hard
                  case — the starts creep along the bounds) with the weights N / h̄, N and N / γ² (three passes
                  after the first);  fit_s — the slowest of them (`limits`: 3 s);  residual_*, warnings_* — the
                  residual of the answer and its warnings;
  fit_exact_s     `fit.fit_variogram` of the same record whose γ are those of a known model (`TRUTH`: a
                  gaussian, an exponential and a spherical structure, C0 0,1·σ², the sill σ²) at the h̄ of its
                  lags, from a start far from it (the ranges × 1,7 / ÷ 1,7, equal contributions, C0 0,3·σ²);
  restored_error  the largest relative difference of C0, c and a1–a3 of that fit from `TRUTH` (to max(|x|,
                  10⁻³·σ²) — the check of Т8.6-5: `limits` 1e−4); the angles and the types come back bit for bit;
  points          the points of the fit (3 axes, N ≥ min_pairs);  starts — 8 (checked).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import _samples
from . import variogram_axes as axes

KEEP = "fit.json"
RULES = ("n_h", "n", "n_g2")
# the model under the points of `fit_exact_s`, × σ² and m: (type, c, a1, a2, a3) — the ranges grow from a
# structure to the next along every axis and lie inside [L/2; 2·h_max] = [7,5; 615] m
TRUTH = (0.1, (("gaussian", 0.25, 60.0, 50.0, 45.0), ("exponential", 0.35, 150.0, 110.0, 90.0),
               ("spherical", 0.3, 290.0, 200.0, 150.0)))  # fmt: skip
START = (0.2, (("spherical", 0.3, 40.0, 30.0, 15.0), ("spherical", 0.3, 120.0, 80.0, 40.0),
               ("spherical", 0.2, 250.0, 160.0, 80.0)))  # fmt: skip


def model(spec: tuple[float, tuple[tuple[str, float, float, float, float], ...]], sigma2: float) -> dict[str, Any]:
    """The API form of a model: C0 and the contributions × σ², the angles of the axes of the calc."""
    c0, items = spec
    angles = axes.ANGLES
    structures = [{"type": t, "c": c * sigma2, "a1": a1, "a2": a2, "a3": a3, **angles} for t, c, a1, a2, a3 in items]
    return {"type": "semivariogram", "c0": c0 * sigma2, "common_axes": True, "structures": structures}


def on_model(form: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """The form with γ of `truth` at h̄ of every lag with pairs (the rest as it was)."""
    import numpy as np

    from bf_core.geostat.variogram.model import Model, Structure

    m = Model(truth["c0"], tuple(Structure(**s) for s in truth["structures"]), truth["common_axes"])
    rows = []
    for d in form["directions"]:
        h = np.array([np.nan if x is None else x for x in d["h"]], dtype=np.float64)
        g = m.gamma_along(np.nan_to_num(h), d["az"], d["plunge"])
        rows.append({**d, "gamma": [None if x is None else float(v) for x, v in zip(d["h"], g, strict=True)]})
    return {**form, "directions": rows}


def restored(got: dict[str, Any], truth: dict[str, Any], sigma2: float) -> float:
    """The largest relative difference of C0, c, a1–a3 of the answer from the truth; the angles and the types
    must be those of the truth bit for bit."""

    def flat(m: dict[str, Any]) -> list[float]:
        return [m["c0"], *[s[k] for s in m["structures"] for k in ("c", "a1", "a2", "a3")]]

    for s, t in zip(got["structures"], truth["structures"], strict=True):
        assert s["type"] == t["type"] and all(s[k] == t[k] for k in ("ang1", "ang2", "ang3")), (s, t)
    return max(abs(a - b) / max(abs(b), 1e-3 * sigma2) for a, b in zip(flat(got), flat(truth), strict=True))


def fits(svc: Any, key: str, sigma2: float) -> dict[str, Any]:
    """The route `/fit` of the record `key` from `START` with every rule of the weights."""
    from bf_core.geostat.variogram import routes

    out: dict[str, Any] = {}
    start = model(START, sigma2)
    for rule in RULES:
        t0 = time.perf_counter()
        got = routes.fit(svc, {"key": key, "type": "semivariogram", "model": start, "weights": rule})
        out[f"fit_{rule}_s"] = round(time.perf_counter() - t0, 3)
        assert got["starts"] == 8 and len(got["model"]["structures"]) == 3, got
        out[f"residual_{rule}"] = float(got["residual"])
        out[f"warnings_{rule}"] = len(got["warnings"])
        out["points"] = int(got["n"])
    out["fit_s"] = max(out[f"fit_{rule}_s"] for rule in RULES)
    return out


def exact(form: dict[str, Any]) -> dict[str, Any]:
    """`fit.fit_variogram` of the form put on `TRUTH`, from a start far from it."""
    from bf_core.geostat.variogram import fit as vf

    sigma2 = float(form["sigma2"])
    truth = model(TRUTH, sigma2)
    start = {**truth, "c0": 0.3 * sigma2,
             "structures": [{**s, "c": 0.4 * sigma2, "a1": s["a1"] * 1.7, "a2": s["a2"] / 1.7, "a3": s["a3"] * 1.7}
                            for s in truth["structures"]]}  # fmt: skip
    put = on_model(form, truth)
    t0 = time.perf_counter()
    got = vf.fit_variogram(put, start)
    took = time.perf_counter() - t0
    assert got["starts"] == 8 and got["order"] == [0, 1, 2], got
    return {"fit_exact_s": round(took, 3), "restored_error": restored(got["model"], truth, sigma2),
            "residual_exact": float(got["residual"]), "warnings_exact": len(got["warnings"])}  # fmt: skip


def record(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
    """The job of `variogram_axes`: the form and the key of its record in the cache of the project."""
    from bf_core.geostat.variogram import jobs as vj

    out = vj.run_experimental(engine, folder, {"body": axes.body(meta, {}), "rows": meta["composite_rows"]})
    return {"key": out["key"], "sigma2": out["sigma2"]}


def prepare(params: dict[str, Any], work: Path) -> None:
    axes.build(work, params)
    meta, engine, folder = _samples.opened(work)
    try:
        axes.clean(folder)
        kept = record(meta, engine, folder)
    finally:
        engine.dispose()
    (work / KEEP).write_text(json.dumps(kept), encoding="utf-8")


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        from bf_core.geostat.variogram import store

        kept = record(meta, engine, folder)
        fits(_samples.service(engine, folder), kept["key"], kept["sigma2"])
        exact(store.read_result(folder, kept["key"], "key")["result"])

    axes.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.variogram import store

    kept = json.loads((work / KEEP).read_text(encoding="utf-8"))
    meta, engine, folder = _samples.opened(work)
    try:
        out = fits(_samples.service(engine, folder), kept["key"], kept["sigma2"])
    finally:
        engine.dispose()
    form = store.read_result(folder, kept["key"], "key")["result"]
    out.update(exact(form))
    assert out["restored_error"] <= 1e-4, out
    return {**out, "sigma2": round(float(kept["sigma2"]), 6), "composites": meta["composite_rows"]}
