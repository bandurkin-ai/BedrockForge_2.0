"""The shells of grades: the job `implicit.build` of a group of type `shell` with three thresholds (ADR 0026 «Замеры»;
ТЗ 8.9.4: 3 порога × 200 тыс. композитов ≤ 5 мин на 4 ядрах; подшаги 19.12, 19.14).

A project of `implicit_common` with the table «Пробы» (AU, a lognormal field with a smooth trend and a nugget, 3 % of
the samples unassayed: no point, never a zero; `holes` holes, ≈ 99 samples of 2 m each) and a model of one group
`shell`: the numeric mode, the linear transform, the thresholds at the quantiles 0,70 / 0,90 / 0,97 of the grades
(`meta["thresholds"]`), the step `h` (not given — diagonal / 200); `model.job.run_build`.
  job_s       the whole job (the time of ТЗ);  data_s, patches_s, iso_s, bodies_s, check_s, write_s — its stages
  points      the grades of the model (one point to a sample with a grade);  shells — the bodies built
  vol_<k>     the volume of the shell k;  triangles — of all the outputs;  nest_s — the check below (not the time)
What is checked (a failure stops the run): a body for every threshold; the volumes fall with the threshold (Т8.9-6);
the shells are nested: the vertices of the shell k lie inside the shell k − 1 or on its surface (the signed distance of
`geo.sdf` to the lower body at the vertices of the upper one, at most `NEST` metres: the job cuts the shells by
booleans, so a vertex is either inside or on the cut); every body has a positive volume."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import implicit_common as ic
from .implicit_domains import stages_of

NEST = 1e-6  # m
QUANTILES = (0.7, 0.9, 0.97)


def prepare(params: dict[str, Any], work: Path) -> None:
    sc = ic.Scene.of(params)
    meta = ic.write_project(work, sc, assays=True, thresholds=QUANTILES)
    ctx = bc.Ctx.open(work)
    try:
        over: dict[str, Any] = {}
        if params.get("n_patch"):
            over["pou"] = {"n_patch": int(params["n_patch"]), "n_min": int(params.get("n_min", 100))}
        mid = ic.create_model(ctx, meta, "Оболочки", [ic.shell(meta, 1)], h=params.get("h"), **over)
    finally:
        ctx.close()
    meta["model"] = mid
    ic.keep(work, meta)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def nested(ctx: Any, outputs: list[dict[str, Any]]) -> list[float]:
    """Every shell against the one below it: the largest signed distance (m) of its vertices to the lower body."""
    from bf_core.db import dialect
    from bf_core.geo import sdf
    from bf_core.wireframes import store as wfs

    bodies = sorted((o for o in outputs if o["kind"] == "body"), key=lambda o: o["threshold"])
    worst: list[float] = []
    with dialect.read_tx(ctx.engine) as conn:
        meshes = [wfs.read_wireframe(conn, ctx.store, o["wireframe_id"]) for o in bodies]
    for (lv, lt), (uv, _ut) in zip(meshes, meshes[1:], strict=False):
        d = sdf.signed_distance(lv, lt, uv, reach=1.0)
        worst.append(float(np.max(d)))
    return worst


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ic.limit_threads(params)
    meta = ic.read_meta(work)
    ctx = bc.Ctx.open(work)
    try:
        seconds, out = ic.seconds_of(lambda: ic.run_job(ctx, int(meta["model"]), threads=threads))
        t0 = time.perf_counter()
        worst = nested(ctx, out["outputs"])
        nest_s = time.perf_counter() - t0
    finally:
        ctx.close()
    bodies = sorted((o for o in out["outputs"] if o["kind"] == "body"), key=lambda o: o["threshold"])
    s = out["stats"]
    assert out["state"] == "ready", out["state"]
    assert [o["threshold"] for o in bodies] == list(meta["thresholds"]), [o["threshold"] for o in bodies]
    vol = [float(o["volume"]) for o in bodies]
    assert all(v > 0 for v in vol) and all(a > b for a, b in zip(vol, vol[1:], strict=False)), f"volumes {vol}"
    assert all(w <= NEST for w in worst), f"a shell leaves the shell below it by {max(worst):.2e} m"
    info: dict[str, Any] = {"threads": threads, "holes": meta["holes"], "samples": meta["samples"],
                            "job_s": round(seconds, 3), "points": s["points"], "patches": s["patches"],
                            "shells": len(bodies), "triangles": sum(o["triangles"] for o in out["outputs"]),
                            "nest_s": round(nest_s, 3), "nest_worst": max(worst) if worst else None,
                            "max_residual": s["max_residual"], "cond_max": s["cond_max"],
                            "process_mb": ic.peak_mb(), "us_point": round(1e6 * seconds / max(s["points"], 1), 1)}
    info.update({f"vol_{k + 1}": round(v, 1) for k, v in enumerate(vol)})
    info.update(stages_of(out))
    return info
