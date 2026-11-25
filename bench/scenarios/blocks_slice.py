"""The slice of a block model by a plane: the raster of BFA1 `blocks_slice` (ТЗ 8.7.6; Т8.7-10; ADR 0023 «Замеры»: ci —
2 млн блоков ≤ 0,3 с, full — 20 млн ≤ 1 с).

The model of `tools/gen/blockmodel.py` with sub-blocks (`rows` rows; AU, DENSITY, F2, F3, SM, DOM), the route as a
function (`viz_routes.slice_`) in a process that has read nothing yet; the colour AU:
  z_s        a plane ⟂ Z through the middle of the model (a layer of parents: the file is read by the groups of it)
  x_s        a plane ⟂ X (every row of the file is looked at)
  oblique_s  a plane with the normal (1, 1, 0.3) — the raster `sampled` of the box of the section
  filter_s   `z` under the restrictor SM > 0.78;  again_s — `z` once more (the columns are in the cache)
  <name>_px  the pixels of the raster;  <name>_kb — the answer
What is checked (a failure stops the run): the header (the kind `blocks_slice`, the size of the raster = the length
of `value` and `m`); of the planes ⟂ axes — for 24 pixels with a block the value is that of the route «block under a
point» (`viz_routes.at`: AU of the block at the centre of the pixel, to float32); the pixels outside the model are
`m = 0`; the filter leaves fewer pixels with a block than the whole slice; the answer `again` is the same bytes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

RESTRICT = {"cond": {"field": "SM", "op": ">", "value": 0.78}}
SAMPLES = 24


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=int(params.get("fields", 4))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def pixel_point(head: dict[str, Any], i: int, j: int) -> list[float]:
    """The centre of pixel (i, j) in the project axes."""
    f = head["frame"]
    o, u, v = (np.asarray(f[k], dtype=np.float64) for k in ("origin", "u", "v"))
    return [float(x) for x in o + u * (i + 0.5) * f["du"] + v * (j + 0.5) * f["dv"]]


def check_pixels(
    ctx: bc.Ctx, mid: int, head: dict[str, Any], arrays: dict[str, np.ndarray], normal: list[float]
) -> int:
    """The value of 24 pixels with a block against the route of the point; the number of pixels with a block."""
    from bf_core.blocks import viz_routes

    w, h = int(head["width"]), int(head["height"])
    value, m = arrays["value"], arrays["m"]
    assert value.shape == m.shape == (w * h,), (value.shape, w, h)
    hit = np.flatnonzero(m == 1)
    assert len(hit), "the slice has no block"
    for flat in np.random.default_rng(3).choice(hit, size=min(SAMPLES, len(hit)), replace=False):
        j, i = divmod(int(flat), w)
        at = viz_routes.at(ctx.svc, mid, {"point": pixel_point(head, i, j), "normal": normal, "fields": ["AU"]})
        assert at["found"] and np.float32(at["values"]["AU"]) == value[flat], (i, j, at, value[flat])
    return len(hit)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import viz_routes
    from bf_core.viz import binary

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    grid = ctx.model(mid).grid
    lo = np.asarray(grid.extent()[:3])
    hi = np.asarray(grid.extent()[3:])
    mid_point = [float(x) for x in (lo + hi) / 2.0 + np.array([0.7, 0.9, 0.6])]  # off the faces of the blocks
    info: dict[str, Any] = {"rows": meta["rows"], "parents": meta["parents"]}
    planes = {"z": ([0.0, 0.0, 1.0], None), "x": ([1.0, 0.0, 0.0], None), "oblique": ([1.0, 1.0, 0.3], None),
              "filter": ([0.0, 0.0, 1.0], RESTRICT), "again": ([0.0, 0.0, 1.0], None)}  # fmt: skip
    try:
        got: dict[str, tuple[dict[str, Any], dict[str, np.ndarray], bytes]] = {}
        for key, (normal, restrict) in planes.items():
            body: dict[str, Any] = {"plane": {"origin": mid_point, "normal": normal}, "field": "AU"}
            if restrict:
                body["restrict"] = restrict
            t0 = time.perf_counter()
            data = bytes(viz_routes.slice_(ctx.svc, mid, body).body)
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
            head, arrays = binary.decode(data)
            got[key] = (head, arrays, data)
            info[f"{key}_px"] = int(head["width"]) * int(head["height"])
            info[f"{key}_kb"] = round(len(data) / 1024.0, 1)
            assert head["kind"] == "blocks_slice" and head["model"] == mid and head["field"] == "AU", head
        shown = {k: check_pixels(ctx, mid, got[k][0], got[k][1], planes[k][0]) for k in ("z", "x")}
        assert got["again"][2] == got["z"][2], "the slice once more is another answer"
        filtered = int((got["filter"][1]["m"] == 1).sum())
        assert 0 < filtered < shown["z"], (filtered, shown["z"])
        info.update(z_blocks=shown["z"], x_blocks=shown["x"], filter_blocks=filtered)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
