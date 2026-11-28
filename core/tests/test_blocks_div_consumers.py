"""The consumers of `div` with a division that is not 2^L (ADR 0030 decision 1, подшаг 27.4а): a model with
sub-blocks 5 × 5 × 3 (and 2 × 3 × 5) goes through the geometry, the checks, the shell, the LOD, the slice (raster and
polygons), the point, the regularization, the report, the filling by a wireframe, the swath — and every one gives what
it gives for 2^L.

The references are written out here (boxes by (n + s/div)·d; a brute force over the sub-cells; sums of volumes), not
taken from the code under test."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import checks as ck
from bf_core.blocks import geometry as geo
from bf_core.blocks import lod, pick
from bf_core.blocks import schema as bs
from bf_core.blocks import slice as sl
from bf_core.blocks.fill import columns as fc
from bf_core.blocks.fill import octree as fo
from bf_core.blocks.model import Model
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell import exposed
from bf_core.blocks.slice_poly import section_polygons
from bf_core.stats import swath_frame as sf
from bf_core.stats import swath_sub as ss
from tests.test_blocks_fields_store import (  # noqa: F401  (fixtures)
    bc,
    blocks_router,
    project,
    tmp_clean,
)
from tests.test_blocks_regularize import FIELDS, bp, build, rel, run  # noqa: F401  (fixtures)
from tests.test_blocks_slice_pick import raster_matches
from tests.test_grid_infer_div import compositions
from tests.test_swath_sub import FIELDS as SW_FIELDS
from tests.test_swath_sub import cuboid_of, prj, put, source  # noqa: F401  (fixtures)

DIVS = [(5, 5, 3), (2, 3, 5)]
COUNTS = (6, 5, 4)
SIZE = (10.0, 10.0, 6.0)


def grid_of(div: Any = (5, 5, 3), rotation: float = 30.0, counts: Any = COUNTS) -> GridV2:
    return GridV2.of({"corner": [1000.5, 2000.25, -300.0], "counts": list(counts), "size": list(SIZE),
                      "div": list(div), "rotation": rotation, "order": "F"})  # fmt: skip


def rows_of(grid: GridV2, seed: int = 3, gap: float = 0.2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The rows of a model: every parent absent (a gap), whole or split by a random composition of the division per
    axis (a tensor product of the pieces); in the order of the file (P, sz, sy, sx)."""
    rng = np.random.default_rng(seed)
    div = grid.sub.div
    p, s, w = [], [], []
    for par in range(grid.cells):
        if rng.random() < gap:
            continue
        if rng.random() < 0.35:
            pieces = [((0, 0, 0), div)]
        else:
            axes = [compositions(rng, div[a], 3) for a in range(3)]
            offs = [np.concatenate([[0], np.cumsum(ax)[:-1]]).astype(int) for ax in axes]
            pieces = [((offs[0][i], offs[1][j], offs[2][k]), (axes[0][i], axes[1][j], axes[2][k]))
                      for i, j, k in itertools.product(*(range(len(ax)) for ax in axes))]
        for o, ww in pieces:
            p.append(par)
            s.append(o)
            w.append(ww)
    p, s, w = np.asarray(p, np.uint64), np.asarray(s, np.uint8), np.asarray(w, np.uint8)
    order = bs.sort_rows(p, s)
    return p[order], s[order], w[order]


def boxes_by_formula(grid: GridV2, p: np.ndarray, s: np.ndarray, w: np.ndarray) -> np.ndarray:
    """(lo, hi) in the local axes: (n + s/div)·d, (n + (s + w)/div)·d — written out."""
    nx, ny = grid.counts[0], grid.counts[1]
    ijk = np.column_stack([p % nx, (p // nx) % ny, p // (nx * ny)]).astype(float)
    div, d = np.asarray(grid.sub.div, float), np.asarray(grid.size)
    return np.hstack([(ijk + s / div) * d, (ijk + (s + w) / div) * d])


# --- the geometry and the checks ------------------------------------------------------------


@pytest.mark.parametrize("div", DIVS)
def test_boxes_centres_and_volumes_follow_the_division(div) -> None:
    grid = grid_of(div)
    p, s, w = rows_of(grid)
    assert len(p) > grid.cells * 0.8 and grid.sub.levels is None and grid.sub.sub
    want = boxes_by_formula(grid, p, s, w)
    assert np.allclose(geo.row_boxes(grid, p, s, w), want, rtol=0, atol=1e-9)
    vol = geo.row_volumes(grid, p, s, w)
    assert np.allclose(vol, np.prod(want[:, 3:] - want[:, :3], axis=1), rtol=1e-12, atol=0)
    parts = float(np.prod(SIZE))
    assert float(vol.sum()) == pytest.approx(parts * len(np.unique(p)), rel=1e-12)  # a parent is covered exactly
    centre_local = (want[:, :3] + want[:, 3:]) / 2
    assert np.allclose(geo.centres(grid, p, s, w), grid.to_project(centre_local), rtol=0, atol=1e-9)


@pytest.mark.parametrize("div", DIVS)
def test_the_checks_see_no_overlap_and_find_a_planted_one(div) -> None:
    grid = grid_of(div)
    p, s, w = rows_of(grid)
    report = ck.check_arrays(grid, p, s, w)
    assert report.ok and report.checks["overlaps"]["n"] == 0 and report.checks["duplicates"] == 0
    assert report.checks["volume"] == pytest.approx(float(geo.row_volumes(grid, p, s, w).sum()), rel=1e-12)
    # a block planted over a whole parent: an overlap, found
    whole = np.flatnonzero((w == np.asarray(div)).all(axis=1))[0]
    p2 = np.insert(p, whole + 1, p[whole])
    s2 = np.insert(s, whole + 1, [div[0] - 1, div[1] - 1, div[2] - 1], axis=0).astype(np.uint8)
    w2 = np.insert(w, whole + 1, [1, 1, 1], axis=0).astype(np.uint8)
    bad = ck.check_arrays(grid, p2, s2, w2)
    assert bad.checks["overlaps"]["n"] >= 1


# --- the shell, the LOD ------------------------------------------------------------


def shell_reference(grid: GridV2, p: np.ndarray, s: np.ndarray, w: np.ndarray) -> int:
    """The faces the rule «full neighbour» leaves: a face is hidden when the parent behind it is full (its rows cover
    it exactly) — counted block by block, parent by parent."""
    div = np.asarray(grid.sub.div)
    nx, ny, nz = grid.counts
    cover = {}
    for q, b in zip(p.tolist(), w.tolist(), strict=True):
        cover[q] = cover.get(q, 0) + int(np.prod(b))
    full = {q for q, c in cover.items() if c == int(np.prod(div))}
    n = 0
    for q, a, b in zip(p.tolist(), s.tolist(), w.tolist(), strict=True):
        ijk = np.array([q % nx, (q // nx) % ny, q // (nx * ny)])
        for axis in range(3):
            for side in (0, 1):
                inside = (a[axis] > 0) if side == 0 else (a[axis] + b[axis] < div[axis])
                if inside:
                    hidden = q in full
                else:
                    nb = ijk.copy()
                    nb[axis] += -1 if side == 0 else 1
                    ok = 0 <= nb[axis] < [nx, ny, nz][axis]
                    hidden = ok and int(nb[0] + nx * (nb[1] + ny * nb[2])) in full
                n += 0 if hidden else 1
    return n


@pytest.mark.parametrize("div", DIVS)
def test_the_shell_has_the_faces_the_rule_leaves(div) -> None:
    grid = grid_of(div, rotation=0.0)
    p, s, w = rows_of(grid, seed=4)
    passed = np.ones(len(p), dtype=bool)
    f = exposed(grid, p, s, w, passed)
    assert len(f) == shell_reference(grid, p, s, w)
    assert len(exposed(grid, p, s, w, passed, shell=False)) == 6 * len(p)
    # every face lies on a plane of the lattice: g (plane) is a whole number of sub-cells
    assert f.g.dtype.kind == "i" and (f.g[:, 0] >= 0).all()


def test_the_lod_coarsens_a_model_with_a_division_of_five() -> None:
    grid = grid_of((5, 5, 3), rotation=0.0, counts=(12, 10, 8))
    p, s, w = rows_of(grid, seed=5, gap=0.3)
    passed = np.ones(len(p), dtype=bool)
    full = lod.view(grid, p, s, w, passed, limit=10_000_000)
    assert full.lod == 0 and not full.simplified
    small = lod.view(grid, p, s, w, passed, limit=400)
    assert small.lod > 0 and small.simplified and 0 < len(small.faces) <= small.faces_raw


# --- the slice, the polygons, the point ------------------------------------------------------------


@pytest.mark.parametrize("div", DIVS)
def test_the_raster_of_every_axis_is_the_block_under_each_pixel(div) -> None:
    grid = grid_of(div)
    p, s, w = rows_of(grid, seed=6)
    rng = np.random.default_rng(7)
    au = rng.lognormal(0.0, 1.0, len(p))
    au[rng.random(len(p)) < 0.1] = np.nan
    passed = rng.random(len(p)) < 0.85
    c, t = np.cos(np.radians(30.0)), np.sin(np.radians(30.0))
    for origin, normal in (([1010.0, 2010.0, -290.3], [0, 0, 1]), ([1011.7, 2018.2, -290.0], [c, t, 0]),
                           ([1013.1, 2016.4, -290.0], [-t, c, 0])):  # fmt: skip
        pl = sl.plane_of(grid, origin, normal)
        r = sl.slice_rows(grid, pl, p, s, w, passed, au, "number", sub=True)
        assert r.layout.kind == "subcells" and r.m.any()
        raster_matches(grid, p, s, w, r, passed, au)


@pytest.mark.parametrize("div", DIVS)
def test_the_section_polygons_have_the_exact_area_of_the_blocks_the_plane_crosses(div) -> None:
    grid = grid_of(div, rotation=0.0)
    p, s, w = rows_of(grid, seed=8)
    z = -300.0 + 2.9  # local z = 2.9, inside a layer of parents and (for div z = 3) inside a sub-cell
    pl = sl.plane_of(grid, [1010.0, 2010.0, z], [0, 0, 1])
    got = section_polygons(grid, pl, p, s, w, None)
    b = boxes_by_formula(grid, p, s, w)
    cut = (b[:, 2] <= 2.9) & (2.9 < b[:, 5])
    area = float(((b[cut, 3] - b[cut, 0]) * (b[cut, 4] - b[cut, 1])).sum())
    assert got.count == int(cut.sum()) and got.total == pytest.approx(area, rel=1e-12)


@pytest.mark.parametrize("div", DIVS)
def test_a_point_in_every_block_finds_that_block(div) -> None:
    grid = grid_of(div, rotation=30.0)
    p, s, w = rows_of(grid, seed=9)
    b = boxes_by_formula(grid, p, s, w)
    inside = grid.to_project(b[:, :3] * 0.7 + b[:, 3:] * 0.3)  # a point 30 % into the block, not on a face
    row = geo.locate_rows(p, s, w, grid, inside)
    assert (row == np.arange(len(p))).all()
    assert pick.__name__  # the module of the point is the one that locates rows by these boxes


# --- the stored model: regularization, report, swath --------------------------------------------------------


def stored(bc_: Any, div: Any = (5, 5, 3), seed: int = 3) -> tuple[Model, np.ndarray]:
    """A committed model of the rows (the fields of the regularization test) and its volumes."""
    grid = {**grid_of(div).to_meta(), "counts": list(COUNTS)}
    g = grid_of(div)
    p, s, w = rows_of(g, seed)
    rng = np.random.default_rng(seed + 100)
    n = len(p)
    values = {"AU": rng.lognormal(0.0, 0.5, n), "CU": rng.random(n).astype(np.float32).astype(np.float64),
              "SG": rng.normal(2.7, 0.1, n), "DOM": rng.integers(0, 3, n).astype(np.int32),
              "FRAC": rng.uniform(0.3, 1.0, n)}  # fmt: skip
    model = build(bc_, grid, FIELDS, p, values, s, w)
    return model, geo.row_volumes(g, p, s, w, values["FRAC"])


@pytest.mark.parametrize("size", [(10.0, 10.0, 6.0), (4.0, 5.0, 3.0), (7.0, 7.0, 7.0)])
def test_regularization_keeps_volume_tonnage_and_metal(bp, size) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    src, _vol = stored(bp)
    assert src.grid.sub.div == (5, 5, 3)
    grades = [{"field": "AU", "weight": "tonnes"}, {"field": "CU", "weight": "volume"}]
    dst, reply = run(bp, src, size, grades=grades, density="SG", categories=["DOM"])
    before, after = reply["totals"]["before"], reply["totals"]["after"]
    assert rel(before["volume"], after["volume"]) < 1e-9
    assert rel(before["tonnes"], after["tonnes"]) < 1e-9
    assert rel(before["metal"]["AU"], after["metal"]["AU"]) < 1e-9
    assert dst.grid.sub.sub is False
    cols = src.columns(["SG", "AU", "FRAC"])
    v = src.volume()
    assert rel(float(np.sum(v * cols["SG"])), before["tonnes"]) < 1e-12
    assert rel(float(np.sum(v * cols["SG"] * cols["AU"])), before["metal"]["AU"]) < 1e-12


def test_the_report_counts_the_tonnes_of_a_model_with_a_division(bp) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.blocks import report as rp

    src, vol = stored(bp)
    pr = project(bp)
    out = rp.run_report(pr.engine, pr.folder, {"model_id": src.id, "grades": ["AU"], "density": "SG",
                                               "cutoff_field": "AU", "cutoffs": [0]})  # fmt: skip
    cols = src.columns(["SG"])
    want = float(np.sum(vol * cols["SG"]))
    row = next(r for r in out["rows"] if r["group"] is None)
    assert row["tonnes"] == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("div", DIVS)
def test_the_swath_of_a_model_with_a_division_equals_its_subcells(prj, div) -> None:  # noqa: F811
    from bf_core.stats import swath as sw

    g = grid_of(div, rotation=30.0)
    p, s, w = rows_of(g, seed=11, gap=0.4)
    rng = np.random.default_rng(12)
    n = len(p)
    rows = {"p": p, "s": s.astype(np.int64), "w": w.astype(np.int64), "au": rng.lognormal(0.0, 1.0, n),
            "dens": rng.uniform(1.8, 3.2, n), "dom": rng.integers(0, 3, n).astype(np.int32)}  # fmt: skip
    meta = g.to_meta()
    sub_id = put(prj, meta, rows, "Субблоки")
    # the same model cut into its sub-cells: a regular model of the same axes
    nxs, nys = COUNTS[0] * div[0], COUNTS[1] * div[1]
    cells, au, dens, dom = [], [], [], []
    for r in range(n):
        q, sr, wr = int(p[r]), s[r].astype(np.int64), w[r].astype(np.int64)
        i, j, k = q % COUNTS[0], (q // COUNTS[0]) % COUNTS[1], q // (COUNTS[0] * COUNTS[1])
        for c, b, a in itertools.product(range(wr[2]), range(wr[1]), range(wr[0])):
            x, y, z = i * div[0] + sr[0] + a, j * div[1] + sr[1] + b, k * div[2] + sr[2] + c
            cells.append(x + nxs * (y + nys * z))
            au.append(rows["au"][r])
            dens.append(rows["dens"][r])
            dom.append(rows["dom"][r])
    order = np.argsort(cells)
    cut = {"p": np.asarray(cells, np.uint64)[order], "au": np.asarray(au)[order], "dens": np.asarray(dens)[order],
           "dom": np.asarray(dom, np.int32)[order]}  # fmt: skip
    cut_grid = {**meta, "div": [1, 1, 1], "counts": [nxs, nys, COUNTS[2] * div[2]],
                "size": [SIZE[0] / div[0], SIZE[1] / div[1], SIZE[2] / div[2]]}
    cut_id = put(prj, cut_grid, cut, "Подъячейки", sub=False)
    a, b = source(prj, sub_id), source(prj, cut_id)
    f = sf.frame(37.0, 0.0, 0.0)
    ca = cuboid_of(a, f, 7.0)
    for weight in ("volume", "tonnage"):
        x = ss.series(a, f, ca, field="AU", weight=weight, density="DENS")
        y = ss.series(b, f, ca, field="AU", weight=weight, density="DENS")
        assert np.allclose(x.weight, y.weight, rtol=1e-9, atol=0), weight
        assert np.allclose(x.mean, y.mean, rtol=1e-9, atol=0, equal_nan=True), weight
    assert SW_FIELDS and sw is not None


# --- the filling by a wireframe ------------------------------------------------------------


@pytest.mark.parametrize("div", DIVS)
def test_the_filling_cuts_the_boundary_parents_into_the_division_and_keeps_every_volume(div) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.gen.mesh import uv_sphere

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [6, 6, 6], "size": [10.0, 10.0, 10.0],
                      "div": list(div), "rotation": 0.0})
    v, t = uv_sphere(3000, radius=21.0, centre=(0.0, 0.0, 0.0))
    mesh = fc.Mesh.of_project(grid, v + np.asarray((31.0, 29.0, 30.0)), t, "Шар")
    vin, vout = [
        float(geo.row_volumes(grid, r.p, r.s, r.w, r.frac).sum())
        for r in (fo.fill_grid(grid, [mesh], mode=m, m=3) for m in ("inside", "outside"))
    ]
    assert abs((vin + vout) / 216_000.0 - 1.0) < 1e-12
    rows = fo.fill_grid(grid, [mesh], mode="inside", m=3)
    assert bs.ascending(rows.p, rows.s) and (rows.s.astype(int) + rows.w <= np.asarray(div)).all()
    assert (rows.w < np.asarray(div)).any()  # parents on the boundary are cut into sub-blocks of the division
