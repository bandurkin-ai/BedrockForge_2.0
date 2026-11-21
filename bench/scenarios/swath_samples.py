"""A swath plot of samples beside a block model — what the window «Swath plot» asks for the series of a drillhole
set (ТЗ 7.30, Т7.30-2; ADR 0019 «Замеры»: 2 ряда проб, один — «внутри блоков»; ci — 500 тыс. проб и 2 млн ячеек,
ориентир ≤ 1 с; full — 5 млн проб и 20 млн ячеек, ориентир ≤ 4 с).

The set of `_samples` and the model of `_charts.model` (the same corner as the holes: the model covers a part of
the field in plan and the holes whole in depth, a part of the samples lies outside it). Three series: AU of the
ore cells of the model by volume, AU of «Пробы» by length in the cuboid, the same samples inside the ore
cells. The route `POST /stats/swath` as a function (`_charts.ask`), the box turned by three angles (azimuth
35°, dip −10°); the columns of the model are in the cache of the process before the clock (решение 18;
`model_s` — the request that read them, a line of the report):
  first_s    from the snapshot of the table: `first_read_s` — the columns of «Пробы», `first_traj_s` — the
             trajectories of the set, `first_points_s` — the middles of the intervals on them, `first_select_s`
             — the rules of the selection, `first_bounds_s` — the extents of the three series, `first_cells_s`
             — the bit map of the ore cells, `first_strips_s` — the swaths of the two series of samples,
             `first_model_s` — those of the model series;
  again_s    the same once more: the columns and the middles are in the cache (the recalculation);
  turn_s     the box turned to azimuth 50°;
  x_s        the swaths along X — for the check.
`peak_mb` — the peak of the process (VmHWM). What is checked (a failure stops the run), against the columns and
the middles by numpy: along X the samples of the second series are those with a value and a middle inside the
cuboid of the answer, of the third — those of them whose middle lies in an ore cell (the cell found from the
coordinates, the zone read from the model); the sums of the lengths and the means weighted by length of both
(1e-9); the rest is counted (`outside_box`, `outside_blocks`, `no_position`); the turned box holds no more
samples inside the blocks than there are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "traj": ("traj",), "points": ("points",), "select": ("select",), "bounds": ("bounds",),
          "cells": ("cells",), "strips": ("strips",), "model": ("model",)}  # fmt: skip
FIELD = "AU"


def spots() -> _charts.Spots:
    from bf_core.composites import coords
    from bf_core.composites import source as lines
    from bf_core.stats import select, source, swath, swath_blocks

    return ((source, "_read_lines", "read"), (lines, "trajectories", "traj"), (coords, "midpoints", "points"),
            (select, "select_values", "select"), (swath, "sample_bounds", "bounds"),
            (swath_blocks, "block_bounds", "bounds"), (swath, "cells_of", "cells"),
            (swath, "sample_series", "strips"), (swath_blocks, "block_series", "model"))  # fmt: skip


def model_series(meta: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "blocks", "model_id": meta["model_id"], "field": "AU", "weight": "volume", "density": None,
            "filter": {"field": "ZONE", "labels": [_charts.ORE]}}  # fmt: skip


def settings(meta: dict[str, Any], samples: bool = True, **how: Any) -> dict[str, Any]:
    base = {"kind": "samples", "set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD,
            "weight": "length", "weight_field": None, "negatives": "keep", "top_cut": None}  # fmt: skip
    series = [model_series(meta)]
    if samples:
        series += [{**base, "within": "box", "within_series": None}, {**base, "within": "blocks", "within_series": 0}]
    return {"direction": "angles", "azimuth": 35.0, "dip": -10.0, "roll": 0.0, "width": None, "anchor": None,
            "limits": None, "series": series, **how}  # fmt: skip


def check(first: dict[str, Any], along_x: dict[str, Any], meta: dict[str, Any], cols: Any, points: Any,
          zone: Any) -> None:  # fmt: skip
    import numpy as np

    v, w = cols.number(FIELD), cols.number("length")
    taken = np.isfinite(v) & (w > 0.0)
    placed = np.isfinite(points).all(axis=1)
    model, box, inside = along_x["series"]
    assert all(s["error"] is None for s in along_x["series"]) and inside["within"] == 0 and box["within"] == "box"
    # in the cuboid of the answer: the frame and the limits it names, the tolerance of a side as the kernel's
    lim = along_x["limits"]
    ok = taken & placed
    for axis in "uvw":
        t = points @ np.array(along_x["frame"][axis])
        lo, hi = lim[axis]
        tol = 1e-9 * max(1.0, abs(lo), abs(hi))
        with np.errstate(invalid="ignore"):
            ok &= (t >= lo - tol) & (t <= hi + tol)
    counts = box["counts"]
    assert box["total"]["count"] == int(ok.sum()) == counts["used"] == sum(box["count"]), (counts, int(ok.sum()))
    assert counts["no_position"] == int((taken & ~placed).sum()) > 0 and counts["selected"] == int(taken.sum())
    assert counts["outside_box"] == int((taken & placed).sum()) - int(ok.sum()) > 0, counts
    assert _samples.close(box["total"]["weight"], float(w[ok].sum()), 1e-9), box["total"]
    assert _samples.close(box["total"]["mean"], float((v[ok] * w[ok]).sum() / w[ok].sum()), 1e-9), box["total"]
    # inside the ore cells: the cell of a middle from its coordinates
    nx, ny, nz = meta["grid"]
    with np.errstate(invalid="ignore"):
        ijk = [np.floor((points[:, k] - _charts.CORNER[k]) / _charts.SIZE[k]) for k in range(3)]
        in_grid = placed & (ijk[0] >= 0) & (ijk[0] < nx) & (ijk[1] >= 0) & (ijk[1] < ny) & (ijk[2] >= 0) & (ijk[2] < nz)
    cell = np.zeros(cols.n, dtype=np.int64)
    cell[in_grid] = (ijk[0][in_grid] + nx * (ijk[1][in_grid] + ny * ijk[2][in_grid])).astype(np.int64)
    ore = taken & in_grid & (np.asarray(zone)[cell] == 0)
    counts = inside["counts"]
    assert inside["total"]["count"] == int(ore.sum()) == counts["used"] > 0, (counts, int(ore.sum()))
    assert counts["outside_blocks"] > 0 and counts["used"] < box["counts"]["used"], counts  # Т7.30-2
    assert _samples.close(inside["total"]["mean"], float((v[ore] * w[ore]).sum() / w[ore].sum()), 1e-9)
    assert model["total"]["count"] == meta["ore_cells"] and "deviation" in box and "deviation" in inside
    turned = first["series"]
    assert all(s["error"] is None for s in turned) and 0 < turned[2]["total"]["count"] <= int(ore.sum())
    assert turned[1]["total"]["count"] >= turned[2]["total"]["count"] and first["count"] == len(turned[1]["mean"])


def more(params: dict[str, Any]) -> Any:
    return lambda meta, engine, folder: _charts.model(meta, engine, folder, params["grid"])


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params, more=more(params))


def load() -> None:
    from bf_core.stats import swath, swath_blocks

    def small(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        project = _charts.project_of(engine, folder)
        for how in ({"samples": False}, {}, {"direction": "x"}):
            _charts.ask(project, "swath", settings(meta, **how))

    swath.warm()  # as the step `stats` of the warm-up: both lookups of the cells, both types of `idx`
    swath_blocks.warm()
    _samples.warm(small, fields=3, more=more({"grid": (12, 10, 30)}))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.drillholes import trajectory
    from bf_core.stats import blocks_cache, source

    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "cells": meta["cells"]}
    try:
        project = _charts.project_of(engine, folder)
        _charts.forget(models=True)
        with trajectory._lock:
            trajectory._cache.clear()  # those of the warm-up are of another project; none of this one yet
        _charts.measure(project, "swath", settings(meta, samples=False), "model", info, cold=False, again=False)
        body = settings(meta)
        first = _charts.measure(project, "swath", body, "first", info, spots(), STAGES)
        _charts.measure(project, "swath", settings(meta, azimuth=50.0), "turn", info, cold=False, again=False)
        along_x = _charts.measure(project, "swath", settings(meta, direction="x"), "x", info, cold=False, again=False)
        info["swath_mb"] = _samples.peak_mb()
        info["cache_mb"] = round((source.COLUMNS.size + blocks_cache.MODELS.size) / 2**20, 1)
        cols = _charts.columns(project, meta["set_id"], [FIELD, "length"], meta["samples"])
        with dialect.read_tx(engine) as conn:
            m = blocks_cache.model_of(conn, meta["model_id"])
        zone = blocks_cache.columns(project, m, ["ZONE"]).codes("ZONE")
        check(first, along_x, meta, cols, source.midpoints(project, cols), zone)
    finally:
        engine.dispose()
    info["swaths"] = first["count"]
    info["in_box"] = first["series"][1]["total"]["count"]
    info["in_blocks"] = first["series"][2]["total"]["count"]
    info["peak_mb"] = _samples.peak_mb()
    return info
