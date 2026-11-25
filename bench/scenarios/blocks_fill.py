"""The filling of a prototype with a wireframe: sub-blocks along the border of a body (ТЗ 8.7.3; Т8.7-3; ADR 0023 §6
and «Замеры»: ci — 10⁵ треугольников, 2 млн родителей, L = 3 ≤ 30 с; full — 10⁶ треугольников, 20 млн, L = 3
**≤ 180 с, ≤ 3 ГБ**; 17.17: the stages «Столбцы» and «Граничные» ≤ 3 s and 0.6 GB on 20 млн).

A prototype of `counts` parents of 10 m with three levels of sub-blocks (8 × 8 × 8 sub-cells; `jobs.run_prototype`)
and a closed sphere of `triangles` triangles (`tools/gen/mesh.py::uv_sphere`) of the radius 0.45 of the side of the
grid at its centre. The job `blocks.fill` (`fill.jobs.run_fill`, the body of the handler: the columns of rays, the
parents of the border, the sub-cells with m = 4 points on an axis, the merge of eight equal ones, the file) on the
threads of the params:
  fill_s       the whole job;  its stages: wires_s, columns_s, boundary_s, split_s, merge_s, write_s;
  index_s      `columns_s` + `boundary_s` — the stages of 17.17;  `rows` — the rows made, `parents` — those occupied
What is checked (a failure stops the run): the volume of the rows (Σ of the volume of the row × FRAC, the report of
the job) is that of the polyhedron of the mesh (the divergence theorem) within 0.5 % (Т8.7-3: ±1 %); every parent
of the body is whole (a row of a parent in the middle of the body is not split: rows ≪ parents × 512).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

SIZE = 10.0
LEVELS = [3, 3, 3]
M = 4
RADIUS = 0.45  # of the side of the grid
TOLERANCE = 0.005
STAGES = ("wireframes", "columns", "boundary", "split", "merge", "write", "total")


def polyhedron_volume(v: np.ndarray, t: np.ndarray) -> float:
    """The volume of a closed mesh (outward normals) by the divergence theorem, relative to its first vertex."""
    a, b, c = (v[t[:, i]] - v[0] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.mesh import uv_sphere

    from bf_core.blocks import jobs
    from bf_core.db import dialect
    from bf_core.wireframes import store as wf

    ctx = bc.Ctx.create(work)
    try:
        counts = [int(n) for n in params["counts"]]
        extent = [0.0, 0.0, 0.0, *(n * SIZE for n in counts)]
        proto = jobs.run_prototype(ctx.engine, ctx.folder, {"size": [SIZE] * 3, "extent": extent, "levels": LEVELS})
        centre = tuple(0.5 * n * SIZE for n in counts)
        radius = RADIUS * min(counts) * SIZE
        v, t = uv_sphere(int(params["triangles"]), radius, centre)
        with dialect.write_tx(ctx.engine) as conn:
            wid = wf.put_wireframe(conn, ctx.store, v, t, name="Шар")
        ctx.keep(prototype_id=proto["model_id"], wireframe_id=wid, triangles=len(t), volume=polyhedron_volume(v, t),
                 parents=proto["parents"])  # fmt: skip
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks.fill import jobs as fj
    from bf_core.blocks.fill.spec import parse_fill

    threads = bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    info: dict[str, Any] = {"parents_grid": meta["parents"], "triangles": meta["triangles"], "threads": threads}
    try:
        spec = parse_fill({"prototype_id": meta["prototype_id"], "wireframes": [{"id": meta["wireframe_id"]}], "m": M})
        import time

        t0 = time.perf_counter()
        out = fj.run_fill(ctx.engine, ctx.folder, {"spec": spec.to_params(), "rebuild": None}, threads=threads)
        info["fill_s"] = round(time.perf_counter() - t0, 3)
        stages = dict(out["stages"])
        for key in STAGES:
            if key != "total":
                info[f"{key if key != 'wireframes' else 'wires'}_s"] = round(float(stages.get(key, 0.0)), 3)
        info["index_s"] = round(float(stages.get("columns", 0.0)) + float(stages.get("boundary", 0.0)), 3)
        info.update(rows=out["rows"], parents=out["parents"])
        got = float(sum(out["volume"].values()))
        error = abs(got - meta["volume"]) / meta["volume"]
        info["volume_error"] = float(f"{error:.3e}")
        info["volume"] = got
        assert error <= TOLERANCE, (got, meta["volume"], error)
        assert 0 < out["rows"] < out["parents"] * 512, (out["rows"], out["parents"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
