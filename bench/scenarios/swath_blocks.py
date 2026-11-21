"""A swath plot of a block model — what the window «Swath plot» asks while its box is turned (ТЗ 7.30, 7.35 Д-01;
Т7.30-6, ТД01-7: «пересчёт ≤ 2 с и ≤ 1 ГБ на 20 млн ячеек»; ADR 0019 «Замеры»: ci — 2 млн ячеек, ориентир ≤ 0,5 с;
первое построение — строкой отчёта, ориентир ≤ 6 с).

The model of `tools/gen/blockmodel.py` as a regular model of the project (`_charts.model`: AU, DENSITY, the
category ZONE — «руда» at AU ≥ 0,5 г/т); two series of it: AU by volume over all the cells and AU by tonnage
(DENSITY) over the ore cells. The route `POST /stats/swath` as a function (`_charts.ask`), the box turned by
three angles (azimuth 35°, dip −10° — no axis of the model is its own):
  first_s    the first build: the columns `idx`, AU, DENSITY, ZONE are read from the file of the model (one pass
             over its row groups) — `first_read_s`; `first_bounds_s` — the extents of the two series,
             `first_kernel_s` — their swaths; `first_mb` — the peak of the process after it;
  again_s    the same swath once more — the recalculation: the columns are in the cache of the process
             (`cache_mb`); its stages `again_bounds_s`, `again_kernel_s`;
  turn_s     the box turned to azimuth 50° (what a handle of Д-01 asks when it is let go);
  x_s        the swaths along X, one block wide (the default width keeps ≤ 50 swaths);
  t1_s … t8_s  the first swath with 1, 2, 4, 8 threads of a model series; `hashes` — how many different
             answers they gave (1: the same bits with any number of threads — a budget).
`peak_mb` — the peak of the process (VmHWM) over all of it. What is checked (a failure stops the run), against
the columns by numpy: every cell of the model is in the swaths of the first series and every ore cell in those
of the second; their total means are the mean of AU and the mean weighted by the density over the ore cells
(1e-9); the deviation of the second series is counted from the first; along X the swaths are the columns of
blocks (their number is nx, the width — the side of a block).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "bounds": ("bounds",), "kernel": ("kernel",)}
SMALL = {"holes": 24, "per_hole": 60, "litho_per_hole": 5, "fields": 3}  # the set of the project: not read here
THREADS = (1, 2, 4, 8)


def spots() -> _charts.Spots:
    from bf_core.stats import blocks_cache, swath_blocks

    return ((blocks_cache, "_read", "read"), (swath_blocks, "block_bounds", "bounds"),
            (swath_blocks, "block_series", "kernel"))  # fmt: skip


def series(model_id: int) -> list[dict[str, Any]]:
    base = {"kind": "blocks", "model_id": model_id, "field": "AU"}
    return [
        {**base, "weight": "volume", "density": None, "filter": None},
        {**base, "weight": "tonnage", "density": "DENSITY", "filter": {"field": "ZONE", "labels": [_charts.ORE]}},
    ]


def settings(model_id: int, **how: Any) -> dict[str, Any]:
    return {"direction": "angles", "azimuth": 35.0, "dip": -10.0, "roll": 0.0, "width": None, "anchor": None,
            "limits": None, "series": series(model_id), **how}  # fmt: skip


def reference(cols: Any, step: int = 1 << 20) -> tuple[float, float, int]:
    """(the mean of AU, its mean weighted by the density over the ore cells, the number of ore cells) by numpy,
    in portions of the columns: the check adds nothing to the peak of the process."""
    import math

    import numpy as np

    sums: list[list[float]] = [[], [], []]
    ore_cells = 0
    for a in range(0, cols.n, step):
        au, rho = cols.number("AU")[a : a + step], cols.number("DENSITY")[a : a + step]
        ore = np.asarray(cols.codes("ZONE")[a : a + step]) == 0
        ore_cells += int(ore.sum())
        for part, value in zip(sums, (au.sum(), (au[ore] * rho[ore]).sum(), rho[ore].sum()), strict=True):
            part.append(float(value))
    total, metal, tonnes = (math.fsum(part) for part in sums)
    return total / cols.n, metal / tonnes, ore_cells


def check(first: dict[str, Any], along_x: dict[str, Any], meta: dict[str, Any], cols: Any) -> None:
    mean, ore_mean, ore_cells = reference(cols)
    assert cols.n == meta["cells"] and ore_cells == meta["ore_cells"], (cols.n, ore_cells)
    for answer in (first, along_x):
        whole, rich = answer["series"]
        assert whole["error"] is None and rich["error"] is None, (whole["error"], rich["error"])
        assert whole["total"]["count"] == meta["cells"] == sum(whole["count"]), whole["total"]
        assert rich["total"]["count"] == ore_cells == sum(rich["count"]), rich["total"]
        assert whole["counts"]["used"] == meta["cells"], whole["counts"]
        assert rich["counts"]["filtered_out"] == meta["cells"] - ore_cells, rich["counts"]
        assert _samples.close(whole["total"]["mean"], mean, 1e-9), (whole["total"]["mean"], mean)
        assert _samples.close(rich["total"]["mean"], ore_mean, 1e-9), (rich["total"]["mean"], ore_mean)
        assert len(rich["deviation"]) == answer["count"] == len(whole["mean"]) and "deviation" not in whole
        assert rich["weight_name"] == "тоннаж" and whole["weight_name"] == "объём", rich["weight_name"]
    assert first["direction"] == "angles" and first["frame"]["azimuth"] == 35.0 and first["frame"]["dip"] == -10.0
    assert 2 <= first["count"] <= 50 and first["width"] > 0.0, (first["count"], first["width"])
    nx = meta["grid"][0]
    assert along_x["count"] == nx and along_x["width"] == _charts.SIZE[0] == along_x["block"], along_x["width"]
    assert all(n == meta["cells"] // nx for n in along_x["series"][0]["count"]), along_x["series"][0]["count"][:3]


def prepare(params: dict[str, Any], work: Path) -> None:
    grid = params["grid"]
    _samples.build(work, SMALL, more=lambda meta, engine, folder: _charts.model(meta, engine, folder, grid))


def load() -> None:
    from bf_core.stats import swath_blocks

    def small(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        project = _charts.project_of(engine, folder)
        for how in ({}, {"direction": "x"}):
            _charts.ask(project, "swath", settings(meta["model_id"], **how))

    swath_blocks.warm()  # as the step `stats` of the warm-up: both types of `idx`
    _samples.warm(small, fields=3, more=lambda meta, engine, folder: _charts.model(meta, engine, folder, (6, 5, 4)))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.stats import blocks_cache, routes_swath

    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"cells": meta["cells"], "ore_cells": meta["ore_cells"]}
    try:
        project = _charts.project_of(engine, folder)
        model_id = meta["model_id"]
        _charts.forget(models=True)
        body = settings(model_id)
        first = _charts.measure(project, "swath", body, "first", info, spots(), STAGES, cold=False, again=False)
        info["first_mb"] = _samples.peak_mb()
        info["cache_mb"] = round(blocks_cache.MODELS.size / 2**20, 1)
        again = _charts.measure(project, "swath", body, "again", info, spots(), STAGES, cold=False, again=False)
        assert _charts.digest(again) == _charts.digest(first), "the swath from the cached columns differs"
        _charts.measure(project, "swath", settings(model_id, azimuth=50.0), "turn", info, cold=False, again=False)
        along_x = _charts.measure(project, "swath", settings(model_id, direction="x", width=_charts.SIZE[0]), "x",
                                  info, cold=False, again=False)  # fmt: skip
        hashes = {_charts.digest(first)}
        usual = routes_swath.THREADS
        try:
            for threads in THREADS:
                routes_swath.THREADS = threads
                answer, seconds, _ = _charts.ask(project, "swath", body)
                info[f"t{threads}_s"] = round(seconds, 3)
                hashes.add(_charts.digest(answer))
        finally:
            routes_swath.THREADS = usual
        info["hashes"] = len(hashes)
        info["threads"] = usual
        with dialect.read_tx(engine) as conn:
            m = blocks_cache.model_of(conn, model_id)
        check(first, along_x, meta, blocks_cache.columns(project, m, ["AU", "DENSITY", "ZONE"]))
    finally:
        engine.dispose()
    info["swaths"] = first["count"]
    info["peak_mb"] = _samples.peak_mb()
    return info
