"""The outputs of the pit optimiser: the fields PIT and ORE and the z-grids of the shells (ТЗ 8.11.5 «Время»: отчёт и
поверхности 1 млн блоков, 25 оболочек ≤ 30 с; ADR 0028 «Замеры», подшаг 21.9).

A regular model of `rows` blocks of 10 × 10 × 5 m turned by 15° about Z (`blocks_common.regular_model`; the shells are
made here, locally and by numpy: `pit_common` of 21.6 takes this place when it is merged). The shells are `shells`
nested inverted cones (the bigger shell is wider and deeper), the top `AIR` levels are «воздух» (no node, ORE empty),
a tilted relief of 21 × 21 vertices lies over the plan, a quarter of the plan to the east of it is outside its edge.
The timed stages (what the jobs `pit.run` «Запись» and `pit.surfaces` do over a result):
  fields_s    `mine.fields.stage_fields` + `commit_scenario_fields`: one file with PIT (i2) and ORE (i1), a write
              transaction, `source {op: "pit", scenario_id}`
  surfaces_s  `mine.surfaces.z_grids`: ALL the shells in one pass over the mined parents, the relief at the centres
  parquet_s   `ZGrids.to_parquet` — `sidecar:zgrid`
  total_s     the three stages — the budget of the scenario. (The pass over the report of 21.8 joins when it is merged.)
What is checked (a failure stops the run): PIT of every row and ORE (1 / 0 / empty — never 0 for «воздух») against the
shells made by the scenario; the z of every column of every shell against a numpy reference (bottom of the lowest mined
level, otherwise the relief at the turned centre, NaN outside the relief; 1e-9), nesting (a bigger shell never lies
above a smaller one), the parquet round trip bit for bit, 409 `pit_fields_taken` for a second scenario.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

NZ = 100
AIR = 8  # the top levels without nodes
ROTATION = 15.0
SCENARIO = 1
EAST = 700.0  # the east edge of the relief: x − corner_x


def allow_op() -> None:
    """`FIELD_OPS` of `blocks/schema.py` knows "pit" (the line of the orchestrator); until then the scenario adds it."""
    from bf_core.blocks import meta, schema

    if "pit" not in schema.FIELD_OPS:
        schema.FIELD_OPS = (*schema.FIELD_OPS, "pit")
        meta.FIELD_OPS = schema.FIELD_OPS


def load() -> None:
    bc.warm()
    from bf_core.geo import dz_kernels

    dz_kernels.warm()


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}]
        out = bc.regular_model(ctx, int(params.get("rows", 1_000_000)), fields,
                               lambda rng, n: {"AU": rng.lognormal(0.0, 1.0, n)}, nz=NZ, rotation=ROTATION)  # fmt: skip
        ctx.keep(**out)
    finally:
        ctx.close()


def make_shells(counts: list[int], n_shells: int) -> np.ndarray:
    """int16 per parent (P order): the smallest of `n_shells` nested inverted cones that holds it, 0 — none."""
    nx, ny, nz = counts
    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    r = np.sqrt(((i - nx / 2.0) / (nx / 2.0)) ** 2 + ((j - ny / 2.0) / (ny / 2.0)) ** 2)
    top = nz - AIR  # the first level of «воздух»
    pit = np.zeros((nz, ny, nx), dtype=np.int16)
    for n in range(n_shells, 0, -1):  # the largest first: a smaller cone overwrites it
        depth = top * (0.15 + 0.8 * n / n_shells)  # levels from the top down to the bottom of the cone
        width = 0.2 + 0.78 * n / n_shells
        floor = top - depth * (1.0 - np.minimum(r / width, 1.0))
        pit[(r < width) & (k >= floor) & (k < top)] = n
    return pit.reshape(-1)


def relief_of(grid: Any) -> tuple[Any, Any]:
    """(the index of the relief, z at (x, y)): a tilted plane of 21 × 21 vertices over the middle of the plan."""
    from bf_core.geo import dz_kernels

    x0, y0, x1, y1 = (grid.corner[0] - 400.0, grid.corner[1] - 400.0, grid.corner[0] + EAST, grid.corner[1] + 1400.0)
    top = grid.corner[2] + float(grid.edges(2)[-1])
    a, b, c = 0.05, -0.03, top - 20.0 - 0.05 * x0 + 0.03 * y0

    def z(x: Any, y: Any) -> Any:
        return c + a * x + b * y

    xs, ys = np.linspace(x0, x1, 21), np.linspace(y0, y1, 21)
    gx, gy = np.meshgrid(xs, ys)
    xyz = np.column_stack([gx.ravel(), gy.ravel(), z(gx.ravel(), gy.ravel())])
    tri = []
    for j in range(20):
        for i in range(20):
            v = i + 21 * j
            tri += [[v, v + 1, v + 22], [v, v + 22, v + 21]]
    return dz_kernels.surface_index(xyz, np.array(tri, dtype=np.int32)), z


def reference(grid: Any, pit: np.ndarray, n_shells: int, z_relief: Any) -> np.ndarray:
    """[n_shells, ny·nx] by numpy over the whole array: the lowest level of the pit C_n per column."""
    nx, ny, nz = grid.counts
    pit3 = pit.reshape(nz, ny, nx)
    level = np.arange(nz)[:, None, None]
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny))
    a = np.radians(grid.rotation)
    qx, qy = (ii + 0.5) * grid.size[0], (jj + 0.5) * grid.size[1]
    x = grid.corner[0] + np.cos(a) * qx - np.sin(a) * qy
    y = grid.corner[1] + np.sin(a) * qx + np.cos(a) * qy
    base = z_relief(x, y).reshape(-1)
    base = np.where(x.reshape(-1) > grid.corner[0] + EAST, np.nan, base)  # east of the edge of the relief
    out = np.empty((n_shells, nx * ny))
    for n in range(1, n_shells + 1):
        lowest = np.where((pit3 > 0) & (pit3 <= n), level, nz).min(axis=0).reshape(-1)
        out[n - 1] = np.where(lowest < nz, grid.corner[2] + lowest * grid.size[2], base)
    return out


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.api.errors import ApiError
    from bf_core.db import dialect
    from bf_core.mine import fields as pf
    from bf_core.mine import surfaces as sf

    bc.limit_threads(params)
    allow_op()
    n_shells = int(params.get("shells", 25))
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"], "shells": n_shells}
    try:
        model = ctx.model(mid)
        grid = model.grid
        rows = int(model.rows)
        pit = make_shells(meta["counts"], n_shells)
        assert pit.shape == (rows,) and int(pit.max()) == n_shells, "the shells"
        relief, z_relief = relief_of(grid)
        p = np.arange(rows, dtype=np.uint64)  # the rows are the parents in the order of P
        air = (p // np.uint64(meta["counts"][0] * meta["counts"][1])) >= np.uint64(NZ - AIR)
        node = np.arange(rows, dtype=np.int32)
        node[air] = -1
        ore = np.random.default_rng(7).random(rows) < 0.3
        ore_row = ore.astype(np.uint8)
        clock = bc.Clock(info)

        with clock("total"):
            with clock("fields"):
                smap = pf.ShellMap.from_parents(p, pit)
                staged = pf.stage_fields(ctx.store, model, model.version, "", SCENARIO, smap, node, ore_row)
                try:
                    with dialect.write_tx(ctx.engine) as conn:
                        pf.commit_scenario_fields(conn, ctx.store, staged, scenario_id=SCENARIO)
                finally:
                    staged.discard()
            with clock("surfaces"):
                zg = sf.z_grids(grid, smap, relief, n_shells=n_shells)
            with clock("parquet"):
                data = zg.to_parquet()

        written = ctx.model(mid)
        got = written.columns(["PIT", "ORE"])
        assert np.array_equal(got["PIT"], pit.astype(np.float64)), "PIT"
        want = np.where(air, np.nan, ore.astype(np.float64))
        assert np.array_equal(got["ORE"], want, equal_nan=True), "ORE"
        assert np.isnan(got["ORE"]).sum() == int(air.sum()) > 0, "«воздух» is empty, not 0"
        src = next(f for f in written.meta["fields"] if f["name"] == "PIT")["source"]
        assert src["op"] == "pit" and src["scenario_id"] == SCENARIO, src
        ref = reference(grid, pit, n_shells, z_relief)
        assert zg.shells == tuple(range(1, n_shells + 1)) and zg.z.shape == ref.shape
        assert np.array_equal(np.isnan(zg.z), np.isnan(ref)), "NaN outside the relief"
        assert np.allclose(zg.z, ref, rtol=0.0, atol=1e-9, equal_nan=True), float(np.nanmax(np.abs(zg.z - ref)))
        both = np.isfinite(zg.z[1:]) & np.isfinite(zg.z[:-1])  # a column outside the relief may become mined
        assert (np.diff(zg.z, axis=0)[both] <= 1e-12).all(), "nested"
        back = sf.ZGrids.from_parquet(data)
        assert back.shells == zg.shells and np.array_equal(back.z, zg.z, equal_nan=True), "parquet"
        try:
            model2 = ctx.model(mid)
            pf.stage_fields(ctx.store, model2, model2.version, "", SCENARIO + 1, smap, node, ore_row)
            raise AssertionError("pit_fields_taken expected")
        except ApiError as e:
            assert e.code == "pit_fields_taken", e.code
        info["mined"] = int((pit > 0).sum())
        info["nan_nodes"] = int(np.isnan(zg.z[0]).sum())
        info["parquet_mb"] = round(len(data) / 1e6, 3)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
