"""The preview of the variograms — `POST /variography/preview` (ADR 0022 «Замеры», решения 14, 24; Д-14: «≤ 1 с»;
the target is the same in ci and full: a sample of ≤ 20 000 points, 3 axes and along the holes).

The data and the request — those of `variogram_axes` (the composites of 2 m of AU, the snapshot ready; the axes
of (30°, −10°, 0°), L = 15 m × 20 lags, along the holes 2 m × 20). The function of the route (`routes.preview`
in a HEAVY slot of `stats.routes_charts`, one thread), as the window sends it while the model is edited: the
first request reads the table (the columns of the process are empty), the next `shown` turn the axes by 15° each
and find the columns of the process (`stats.source`):
  preview_first_s   the first request — what the window waits for when it opens (`limits`: 1 s);
  preview_s, preview_max_s   the median and the slowest of the next ones (`limits`: 1 s);
  first_load_s, first_sample_s, first_plan_s, first_kernel_s, first_dh_s   the stages of the first one:
                  `points.load` (the read, the trajectories), `points.sample`, `pairs.prepare` (the grid of the
                  sample), `pairs.run` (the kernel of the axes), `downhole.calculate` (along the holes over the
                  sample);  load_s, sample_s, plan_s, kernel_s, dh_s — their medians over the next ones;
  sampled_n, of   the points of the sample (`sampled.n`, 20 000) and all the points (`sampled.of`);
  checks          the pair checks of the sample (the axes of the first request).
What is checked (a failure stops the run): `key` is null, `sampled` {n: 20 000, of, seed} when there are more
points, `counts` are those of all the points; 3 axes × 20 lags; the same request twice — the same answer.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

from . import _samples
from . import variogram_axes as axes

SPOTS = ("load", "sample", "plan", "kernel", "dh")


def ask(svc: Any, request: dict[str, Any]) -> tuple[dict[str, Any], float, dict[str, float], int]:
    """`routes.preview` with clocks inside: (the answer, its seconds, the clocks, the pair checks of the axes)."""
    from bf_core.geostat.variogram import downhole, experimental, pairs, points, routes

    with _samples.captured(experimental, "calculate") as calls, _samples.stages(
        (points, "load", "load"), (points, "sample", "sample"), (pairs, "prepare", "plan"), (pairs, "run", "kernel"),
        (downhole, "calculate", "dh"),
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        got = routes.preview(svc, request)
        seconds = time.perf_counter() - t0
    return got, seconds, took, int(calls[0][2].checks)


def turned(meta: dict[str, Any], k: int) -> dict[str, Any]:
    return axes.body(meta, {"axes": {**axes.ANGLES, "ang1": (axes.ANGLES["ang1"] + 15.0 * k) % 360.0}})


def prepare(params: dict[str, Any], work: Path) -> None:
    axes.build(work, params)


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        svc = _samples.service(engine, folder)
        for k in range(2):
            ask(svc, turned(meta, k))

    axes.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.variogram import spec

    shown = int(params.get("shown", 10))
    meta, engine, folder = _samples.opened(work)
    try:
        svc = _samples.service(engine, folder)
        first, first_s, first_took, checks = ask(svc, axes.body(meta, {}))
        same = ask(svc, axes.body(meta, {}))[0]
        later = [ask(svc, turned(meta, k + 1)) for k in range(shown)]
    finally:
        engine.dispose()
    c, sampled = first["counts"], first["sampled"]
    assert first["key"] is None and c["rows"] == meta["composite_rows"] and same == first
    if c["used"] > spec.PREVIEW_MAX:
        assert sampled == {"n": spec.PREVIEW_MAX, "of": c["used"], "seed": axes.SEED}, sampled
    assert len(first["directions"]) == 3 and all(len(d["n"]) == axes.NLAG for d in first["directions"])
    assert len(first["downhole"]["n"]) == axes.DH_NLAG
    seconds = [s for _got, s, _took, _n in later]
    return {
        "preview_first_s": round(first_s, 4),
        "preview_s": round(statistics.median(seconds), 4),
        "preview_max_s": round(max(seconds), 4),
        **{f"first_{k}_s": round(first_took.get(k, 0.0), 4) for k in SPOTS},
        **{f"{k}_s": round(statistics.median(took.get(k, 0.0) for _g, _s, took, _n in later), 4) for k in SPOTS},
        "sampled_n": sampled["n"] if sampled else c["used"],
        "of": c["used"],
        "checks": checks,
        "shown": shown,
        "composites": meta["composite_rows"],
    }
