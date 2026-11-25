"""The swath plot of a block model with sub-blocks and a turn (ТЗ 7.30, Т7.30-6 «пересчёт ≤ 2 с и ≤ 1 ГБ на 20 млн
ячеек»; 7.31, 7.35 Д-01, Д-02; ADR 0023 решение 21; Э13 asked ≤ 0,3 с on a model without sub-blocks).

Three models in the project of the scenario, `rows` rows each: `plain` — a regular model with no turn (AU, DENSITY, the
category ZONE «руда» at AU ≥ 0.5 — the model of Э13 and its path `swath_blocks`), `sub` — the model of
`tools/gen/blockmodel.py` with sub-blocks (25 % of the parents split into eight; AU, DENSITY, SM) and `turned` — the
same turned by 30° about Z (the path `swath_sub`: a block is cut between the windows by the share of its volume). The
route `POST /stats/swath` as a function (`routes_swath.swath_of`) for the box turned by azimuth 35° and dip −10°
(no axis of the model is its own), two series — AU by volume, AU by tonnage (DENSITY) — and, for the third request, a
third series under a filter (`plain`: the category ZONE = руда, the others: the stored filter `SM > 0.78`):
  <model>_first_s   the first build: the columns are read from the file of the model (the caches are empty)
  <model>_again_s   the same request once more (the columns are in the cache: what a handle of the box asks)
  <model>_turn_s    the box turned to azimuth 50°;  <model>_filter_s — the third request with the third series
  <model>_n         the swaths of the answer
What is checked (a failure stops the run): the answer once more is the same bit by bit; the total means of the series
are those of the rows by numpy — AU by volume (Σ AU·V / Σ V) and by tonnage (Σ AU·V·ρ / Σ V·ρ), 1e-9 (`blocks_cache`
reads the columns; the volume of a row is `geometry.row_volumes`); the filtered series has fewer blocks than the first.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

ORE = "руда"
LEVEL = 0.78
MODELS = ("plain", "sub", "turned")


def plain_fields() -> list[dict[str, Any]]:
    return [{"name": "AU", "dtype": "f8", "unit": "г/т"}, {"name": "DENSITY", "dtype": "f8"},
            {"name": "ZONE", "dtype": "cat", "labels": [ORE, "порода"]}]  # fmt: skip


def plain_values(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    au = rng.lognormal(-1.0, 1.0, n)
    return {"AU": au, "DENSITY": rng.normal(2.7, 0.05, n), "ZONE": np.where(au >= 0.5, 0, 1).astype(np.int32)}


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.blocks.filters import store as filters
    from bf_core.db import dialect

    rows = int(params["rows"])
    ctx = bc.Ctx.create(work)
    try:
        made = {"plain": bc.regular_model(ctx, rows, plain_fields(), plain_values, name="plain"),
                "sub": bc.sub_model(ctx, rows, fields=2, name="sub"),
                "turned": bc.sub_model(ctx, rows, fields=2, rotation=30.0, name="turned", seed=2)}  # fmt: skip
        with dialect.write_tx(ctx.engine) as conn:
            fid = filters.create(conn, {"name": "Тело", "expr": f"SM > {LEVEL}"})
        ctx.keep(filter_id=fid, **{k: v for k, v in made.items()})
    finally:
        ctx.close()


def load() -> None:
    from bf_core.stats import swath_blocks, swath_sub

    bc.warm()
    swath_blocks.warm()  # as the step `stats` of the warm-up: both paths, the regular model and the sub-blocks
    swath_sub.warm()


def series(model: str, meta: dict[str, Any], third: bool) -> list[dict[str, Any]]:
    mid = meta[model]["model_id"]
    base = {"kind": "blocks", "model_id": mid, "field": "AU", "filter": None}
    out = [{**base, "weight": "volume", "density": None}, {**base, "weight": "tonnage", "density": "DENSITY"}]
    if third:
        extra = ({"filter": {"field": "ZONE", "labels": [ORE]}} if model == "plain"
                 else {"filter_id": meta["filter_id"]})  # fmt: skip
        out.append({**base, "weight": "tonnage", "density": "DENSITY", **extra})
    return out


def body(model: str, meta: dict[str, Any], third: bool = False, azimuth: float = 35.0) -> dict[str, Any]:
    return {"v": 1, "direction": "angles", "azimuth": azimuth, "dip": -10.0, "roll": 0.0, "width": None,
            "anchor": None, "limits": None, "series": series(model, meta, third)}  # fmt: skip


def digest(answer: Any) -> str:
    return hashlib.sha256(json.dumps(answer, sort_keys=True, allow_nan=False).encode()).hexdigest()


def reference(ctx: bc.Ctx, mid: int, weighted: str | None) -> float:
    """Σ AU·V / Σ V (`weighted` None) or Σ AU·V·ρ / Σ V·ρ over the rows of the model, by numpy."""
    from bf_core.blocks import geometry as geo

    model = ctx.model(mid)
    top: list[float] = []
    bottom: list[float] = []
    for ch in model.iter(["AU", "DENSITY"], bc.PORTION):
        v = geo.row_volumes(model.grid, ch.p, ch.s, ch.w, None) if model.sub else np.full(ch.n, 500.0)
        t = v if weighted is None else v * ch.cols["DENSITY"]
        top.append(math.fsum((ch.cols["AU"] * t).tolist()))
        bottom.append(math.fsum(t.tolist()))
    return math.fsum(top) / math.fsum(bottom)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.stats import blocks_cache, routes_swath
    from bf_core.stats import swath_sub as ss

    threads = bc.limit_threads(params)
    routes_swath.THREADS = max(1, min(4, threads))
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    info: dict[str, Any] = {"rows": int(params["rows"]), "threads": routes_swath.THREADS}
    try:
        for model in MODELS:
            blocks_cache.MODELS.clear()
            ss.MASKS.clear()
            first = timed(ctx, body(model, meta), info, f"{model}_first")
            again = timed(ctx, body(model, meta), info, f"{model}_again")
            timed(ctx, body(model, meta, azimuth=50.0), info, f"{model}_turn")
            filtered = timed(ctx, body(model, meta, third=True), info, f"{model}_filter")
            assert digest(again) == digest(first), f"{model}: the swath from the cached columns differs"
            info[f"{model}_n"] = first["count"]
            for k, weighted in enumerate((None, "tonnage")):
                got = first["series"][k]["total"]["mean"]
                want = reference(ctx, meta[model]["model_id"], weighted)
                assert abs(got - want) <= 1e-9 * abs(want), (model, k, got, want)
            rich, whole = filtered["series"][2], filtered["series"][1]
            assert 0 < rich["total"]["count"] < whole["total"]["count"], (model, rich["total"], whole["total"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info


def timed(ctx: bc.Ctx, request: dict[str, Any], info: dict[str, Any], key: str) -> dict[str, Any]:
    from bf_core.stats import routes_swath

    t0 = time.perf_counter()
    answer = routes_swath.swath_of(ctx.project, request)
    info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
    assert all(s["error"] is None for s in answer["series"]), [s["error"] for s in answer["series"]]
    return answer
