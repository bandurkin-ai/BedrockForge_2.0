"""The leaf «каркас» of a filter (подшаг 17.20; ТЗ 8.7.5; ADR 0023 §8; the point of extension of `restrict.py`).

    restrict.LEAVES["wireframe"] = build          # done by importing this module

The rule {wireframe: {id, side: inside | outside, by: frac | centre, m}} gives every block the share f ∈ [0; 1] of it
that is inside (or outside) the body:
  * `by: "centre"` — the parity of the vertical ray through the centre of the row (`columns.inside_points`): 0 or 1;
  * `by: "frac"` — the share of the m³ points of the box of the row that are in the body (`frac.box_codes` of the
    fill of 8.7.3: the same rays, shifted by (√2·1e−6; √3·1e−6)·the sub-cell), m = 1…16;
  * `side: "outside"` — 1 − f (a point is in the body or it is not).
The body is turned into the local axes of the model (`grid.to_local`: −O, R_z(−φ)) and must be closed and consistent
(step 1 of 8.7.3: 422 `wireframe_open` / `wireframe_orientation` with `param: "<path>.id"`); a grid of a variable
step — 422 `variable_grid`. A body that the project has lost, or a leaf without an id — share 0 and a warning, like the
surface (`restrict._unavailable`). Rows whose box is off the box of the body are 0 without a ray (outside: 1).
Memory: a chunk of rows of the restrictor (2²⁰) and the XY index of the body; the result is the same bit for bit with
any number of threads. A cache of shares by (sha of the rows, sha of the body, m) — of ADR §8 — is not built.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry, restrict
from bf_core.blocks.fill import columns as fc
from bf_core.blocks.fill import frac
from bf_core.blocks.model import Chunk
from bf_core.blocks.restrict import Build, Weights
from bf_core.composites import strips

MAX_LEAF_THREADS = 4  # a filter runs beside the display and the other jobs


def _threads() -> int:
    return min(strips.default_threads(), MAX_LEAF_THREADS)


def _wire_error(e: ApiError, path: str) -> ApiError:
    """The refusal of a body with `param` of the rule: the id of its leaf."""
    e.extra = {**e.extra, "param": f"{path}.id"}
    return e


def build(b: Build, body: Mapping[str, Any], path: str) -> Weights:
    """The builder of the leaf «каркас» for `restrict.LEAVES` (module docstring)."""
    from bf_core.wireframes import store as wf

    oid = body.get("id")
    if oid is None:
        return restrict._unavailable(b, "wireframe", body)  # noqa: SLF001
    grid = b.model.grid
    grid.regular("Правило каркаса")
    try:
        info = wf.wireframe_info(b.conn, int(oid))
        xyz, tri = wf.read_wireframe(b.conn, b.store, int(oid))
    except ApiError as e:
        if e.code != "wireframe_not_found":
            raise
        return restrict._unavailable(b, "wireframe", body)  # noqa: SLF001
    name = str(info["name"])
    try:
        mesh = fc.Mesh.of(grid, grid.to_local(xyz), tri, name, stats=info.get("stats") or None)
    except ApiError as e:
        raise _wire_error(e, path) from None
    del xyz, tri
    inside = body["side"] == "inside"
    centre = body.get("by", "frac") == "centre"
    m = int(body.get("m") or 4)
    if mesh.index.ncx == 0:
        b.warn(f"Каркас «{name}» не пересекает сетку модели в плане: внутри него блоков нет.")
    bodies = None if centre else frac.Bodies.of(grid, [mesh])
    threads = _threads()
    lo = np.asarray(mesh.lo)
    hi = np.asarray(mesh.hi)

    def run(chunk: Chunk) -> np.ndarray:
        boxes = geometry.row_boxes(grid, chunk.p, chunk.s, chunk.w)  # local, n × 6
        near = (boxes[:, :3] <= hi).all(axis=1) & (boxes[:, 3:] >= lo).all(axis=1)
        share = np.zeros(chunk.n, dtype=np.float64)  # the share inside; a box off the body — none of it
        at = np.flatnonzero(near)
        if len(at):
            if centre:
                c = (boxes[at, :3] + boxes[at, 3:]) * 0.5
                share[at] = fc.inside_points(mesh, c, threads=threads)
            else:
                assert bodies is not None
                code, part = frac.box_codes(bodies, boxes[at], m=m, mode="inside", threads=threads)
                share[at] = np.where(code == 0, part, 0.0)  # code −1 (no point inside): FRAC NaN
        return share if inside else 1.0 - share

    return run


restrict.LEAVES["wireframe"] = build
