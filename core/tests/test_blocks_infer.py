"""Roles of the columns and the inference of the grid of a block model (подшаг 17.11; ТЗ 7.31, 8.7.2; ADR 0023
decisions 5–6): the names of Micromine, Datamine and other programs, the parent size, the step, the corner,
`div` ≤ 128, (P, s, w) of every block, the refusal of blocks off the sub-cells with their rows, the check of three
centres of a turned model (1e−6 м), the two passes over chunks, and the owner's model (`tests/data/real/Для BF.zip`:
`.DAT`, `.dm`, `.fbm` of the same 169 070 blocks).

The reference is not the code under test: the synthetic models are built from the cells (i, j, k, s, w) by the
formula of 8.7.2 with `math.cos/sin`, and the cells are compared with the ones inferred from the centres and sizes
alone."""

from __future__ import annotations

import math
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.blocks.grid_infer import CROSSES_PARENT, OFF_GRID, GridHint, GridScan, Unaligned, infer
from bf_core.blocks.schema import GridV2
from bf_core.formats import blocks_roles as br
from bf_core.formats.datamine import read_dm, read_header
from bf_core.formats.micromine import read_dat

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "Для BF.zip"
ORIGIN = (466167.5, 4584527.5, 202.5)
PARENT = (8.0, 8.0, 4.0)
DIV = (8, 4, 4)  # sub-cells 1 × 2 × 1 м


# ------------------------------------------------------------------------------ synthetic models
def octree_cells(counts=(4, 3, 2)) -> list[tuple[int, int, int, tuple, tuple]]:
    """Cells (i, j, k, s, w) of a model with sub-blocks: whole parents, halves, slices, layers, octants, air."""
    out = []
    for i in range(counts[0]):
        for j in range(counts[1]):
            for k in range(counts[2]):
                if (i + j + k) % 5 == 4:
                    continue  # an empty parent
                kind = (i * 7 + j * 3 + k) % 5
                if kind == 0:
                    parts = [((0, 0, 0), DIV)]
                elif kind == 1:
                    parts = [((0, 0, 0), (4, 4, 4)), ((4, 0, 0), (4, 4, 4))]
                elif kind == 2:
                    parts = [((x, y, 0), (1, 1, 4)) for x in range(8) for y in range(4)]
                elif kind == 3:
                    parts = [((0, 0, 0), (8, 4, 2)), ((0, 0, 2), (8, 4, 1)), ((0, 0, 3), (8, 4, 1))]
                else:
                    parts = [((x, y, z), (4, 2, 2)) for x in (0, 4) for y in (0, 2) for z in (0, 2)]
                out += [(i, j, k, s, w) for s, w in parts]
    return out


def project_of(cells, origin=ORIGIN, phi=0.0, parent=PARENT, div=DIV):
    """Centres and sizes of the cells in the project axes: p = O + R_z(φ)·q (8.7.2), written out by hand."""
    c, d = np.empty((len(cells), 3)), np.empty((len(cells), 3))
    cos, sin = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    if phi % 90 == 0:  # exact turns
        cos, sin = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}[int(phi) % 360]
    for r, (i, j, k, s, w) in enumerate(cells):
        q = [(n + (s[a] + w[a] / 2) / div[a]) * parent[a] for a, n in enumerate((i, j, k))]
        c[r] = (origin[0] + cos * q[0] - sin * q[1], origin[1] + sin * q[0] + cos * q[1], origin[2] + q[2])
        d[r] = [w[a] * parent[a] / div[a] for a in range(3)]
    return c, d


def expected_rows(cells, counts=(4, 3, 2)):
    p = np.array([i + counts[0] * (j + counts[1] * k) for i, j, k, _, _ in cells], dtype=np.uint64)
    return p, np.array([s for *_, s, _ in cells], dtype=np.uint8), np.array([w for *_, w in cells], dtype=np.uint8)


def check_octree(phi=0.0, hint=None, centres_in="project", origin=ORIGIN, tol=1e-6):
    cells = octree_cells()
    c, d = project_of(cells, origin, phi)
    res = infer(c, d, hint or GridHint(rotation=phi), centres_in=centres_in)
    p, s, w = expected_rows(cells)
    assert res.grid.counts == (4, 3, 2) and res.grid.sub.div == DIV and res.grid.size == PARENT
    assert np.abs(np.array(res.grid.corner) - origin).max() <= tol
    assert np.array_equal(res.p, p) and np.array_equal(res.s, s) and np.array_equal(res.w, w)
    assert res.check3.ok and res.check3.dist <= tol and res.valid is None and res.dropped == 0
    return res, c, d


# ------------------------------------------------------------------------------ roles of columns
def test_roles_of_micromine_datamine_and_other_programs():
    cases = {
        ("EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", "CU"): ("EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL"),
        ("XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU"): ("XC", "YC", "ZC", "XINC", "YINC", "ZINC"),
        ("X_CENTRE", "Y_CENTRE", "Z_CENTRE", "DIM_X", "DIM_Y", "DIM_Z"): (
            "X_CENTRE",
            "Y_CENTRE",
            "Z_CENTRE",
            "DIM_X",
            "DIM_Y",
            "DIM_Z",
        ),  # fmt: skip
        ("Centroid X", "Centroid Y", "Centroid Z", "Size X", "Size Y", "Size Z"): (
            "Centroid X",
            "Centroid Y",
            "Centroid Z",
            "Size X",
            "Size Y",
            "Size Z",
        ),  # fmt: skip
        ("XCENTRE", "YCENTRE", "ZCENTRE", "XLENGTH", "YLENGTH", "ZLENGTH"): (
            "XCENTRE",
            "YCENTRE",
            "ZCENTRE",
            "XLENGTH",
            "YLENGTH",
            "ZLENGTH",
        ),  # fmt: skip
        ("x", "y", "z", "dx", "dy", "dz", "rock"): ("x", "y", "z", "dx", "dy", "dz"),
    }
    for names, wanted in cases.items():
        roles = br.find_roles(names)
        assert tuple(roles[k] for k in br.GEOMETRY) == wanted, names
    # no sizes: a regular model; a size named after the centre by Micromine is not mistaken for a centre
    roles = br.find_roles(["EAST", "NORTH", "RL", "AU"])
    assert roles["x"] == "EAST" and roles["dx"] is None and roles["dz"] is None
    assert br.find_roles(["_EAST", "_NORTH", "_RL"])["x"] is None


def test_block_model_or_a_table_of_holes():
    assert br.looks_like_block_model(["X", "Y", "Z", "_X", "_Y", "_Z", "AU"])
    assert br.looks_like_block_model(["EAST", "NORTH", "RL", "AU", "CU"])  # a regular model: centres only
    assert not br.looks_like_block_model(["BHID", "FROM", "TO", "X", "Y", "Z", "AU"])
    assert not br.looks_like_block_model(["X", "Y", "Z"])
    assert not br.looks_like_block_model(["A", "B"])


def test_column_roles_of_a_datamine_file_and_the_grid_constants():
    names = ["IJK", "XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU", "FRAC", "XMORIG", "YMORIG", "ZMORIG", "NX", "NY",
             "NZ", "ANGLE1", "ROTAXIS1", "ROCK"]  # fmt: skip
    r = br.classify(names)
    assert r.has_centre and r.has_size and r.ijk == "IJK" and r.frac == "FRAC"
    assert r.origin == {"x": "XMORIG", "y": "YMORIG", "z": "ZMORIG"} and r.counts == {"x": "NX", "y": "NY", "z": "NZ"}
    assert r.turns == [("ANGLE1", "ROTAXIS1")]
    assert r.fields == ["AU", "FRAC", "ROCK"]  # FRAC is a data field with the role «frac», not a skipped column
    assert {"IJK", "XMORIG", "ANGLE1", "ROTAXIS1", "XC", "NZ"} <= set(r.skip) and r.as_dict()["ijk"] == "IJK"
    assert br.classify([*names, "X0", "Y0", "Z0"]).fields == ["AU", "FRAC", "ROCK"]  # the copies of the origin
    # the owner's DAT: nothing of the grid, every other column is a field
    real = br.classify(["EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", "Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"])
    assert real.fields == ["Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"] and real.ijk is None


def test_roles_of_data_fields_units_and_empty_codes():
    assert br.field_role("SG") == "density" and br.field_role("Density") == "density"
    assert br.field_role("DENSITY", varying=False) is None  # a constant 2,6 is not offered (7.31)
    assert (
        br.field_role("AU_CUT") == "grade" and br.field_role("Cu") == "grade" and br.field_role("RUN_AU_CUT") == "grade"
    )
    assert br.field_role("FRAC") == "frac" and br.field_role("ALT") is None
    assert br.field_role("ROCK", "category") == "code" and br.field_role("CLASS", "category") == "class"
    assert br.field_role("AU", "category") is None
    assert br.guess_unit("AU") == "г/т" and br.guess_unit("CU_CUT") == "%" and br.guess_unit("AU_ppm") == "ppm"
    assert br.guess_unit("Fe_o_pc") == "%" and br.guess_unit("ALT") is None
    assert br.empty_candidates(np.array([1.0, -99.0, 2.0])) == [-99.0]
    assert br.empty_candidates(np.array([1.0, 2.0])) == [] and br.empty_candidates(np.array(["a"])) == []


def test_micromine_grid_line_centre_or_corner():
    h = br.micromine_grid_line(["466170", "5", "4584530", "5", "205", "5", "CENTRE"])
    assert h is not None and h.corner == pytest.approx(ORIGIN, abs=1e-9) and h.size == (5.0, 5.0, 5.0)
    h = br.micromine_grid_line(["0,5", "10", "0", "10", "0", "2,5", "CORNER"])
    assert h is not None and h.corner == (0.5, 0.0, 0.0) and h.size == (10.0, 10.0, 2.5)
    assert br.micromine_grid_line(["466170", "5", "4584530"]) is None
    assert br.micromine_grid_line(["a", "b", "c", "d", "e", "f"]) is None
    assert br.micromine_grid_line(["0", "-5", "0", "5", "0", "5"]) is None
    assert br.micromine_grid_line(["0", "5", "0", "5", "0", "5", "MIDDLE"]) is None


def test_datamine_constants_to_a_hint():
    const = {"XMORIG": 100.0, "YMORIG": 200.0, "ZMORIG": -5.0, "NX": 4.0, "NY": 3.0, "NZ": 2.0, "X0": 100.0}
    h = br.datamine_hint(const)
    assert h == GridHint(corner=(100.0, 200.0, -5.0), counts=(4, 3, 2))
    h = br.datamine_hint({**const, "XINC": 10.0, "YINC": 10.0, "ZINC": 5.0, "ANGLE1": 30.0, "ROTAXIS1": 3.0})
    assert h is not None and h.size == (10.0, 10.0, 5.0) and h.rotation == 30.0 and "знак" in h.notes[0]
    assert br.datamine_hint({**const, "ANGLE1": -20.0}).rotation == -20.0  # no ROTAXIS1: about Z  # type: ignore[union-attr]
    assert br.datamine_hint({"X0": 1.0, "Y0": 2.0, "Z0": 3.0}).corner == (1.0, 2.0, 3.0)  # type: ignore[union-attr]
    assert br.datamine_hint({"XMORIG": float("nan"), "NX": -1.0e30}) is None
    assert br.datamine_hint({"FOO": 1.0}) is None
    with pytest.raises(ApiError) as e:
        br.datamine_hint({**const, "ANGLE1": 15.0, "ROTAXIS1": 1.0})
    assert e.value.code == "rotation_unsupported" and e.value.hint


# ------------------------------------------------------------------------------ the grid
def test_regular_model_without_sizes_the_step_of_the_centres():
    """BF: a model with centres only — the size is the smallest gap of the distinct centres."""
    c = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 4]], dtype=float)
    res = infer(c)
    assert res.grid.size == (10.0, 10.0, 4.0) and res.grid.corner == (-5.0, -5.0, -2.0)
    assert res.grid.counts == (2, 2, 2) and res.s is None and res.w is None
    assert [int(v) for v in res.p] == [0, 1, 2, 4]
    # a 2D model: one layer — the size along Z is not in the data; a warning, not a silent 1
    flat = infer(np.array([[0, 0, 7], [10, 0, 7], [0, 10, 7]], dtype=float))
    assert flat.grid.size == (10.0, 10.0, 1.0) and flat.grid.counts[2] == 1 and "Z" in flat.warnings[0]


def test_whole_blocks_with_sizes_and_a_micromine_grid_line():
    """BF: the grid line of a Micromine CSV `466170, 5, 4584530, 5, 205, 5, CENTRE` is the first parent's centre."""
    idx = np.array([(i, j, k) for i in range(4) for j in range(3) for k in range(2) if (i + j + k) % 5 != 4])
    idx = idx[np.random.default_rng(3).permutation(len(idx))]
    c = np.array(ORIGIN) + (idx + 0.5) * 5.0
    d = np.full_like(c, 5.0)
    hint = br.micromine_grid_line(["466170", "5", "4584530", "5", "205", "5", "CENTRE"])
    for h in (hint, None):  # from the line, and from the blocks alone
        res = infer(c, d, h)
        assert res.grid.size == (5.0, 5.0, 5.0) and res.grid.sub.div == (1, 1, 1) and res.s is None
        assert res.grid.counts == (4, 3, 2) and np.abs(np.array(res.grid.corner) - ORIGIN).max() <= 1e-9
        assert np.array_equal(res.p, (idx[:, 0] + 4 * (idx[:, 1] + 3 * idx[:, 2])).astype(np.uint64))
        assert res.parents == 19 and not res.inferred.parent_guess and res.warnings == []


def test_octree_subblocks_the_cells_and_div():
    res, _, _ = check_octree()
    assert res.inferred.parent_guess and "самому большому" in res.warnings[0]
    assert res.parents == len({int(v) for v in res.p}) and res.grid.sub.sub
    # the centres computed back by the geometry of 17.2 are the file's (the check of the rows of the store)
    cells = octree_cells()
    c, _ = project_of(cells)
    back = geo.centres(res.grid, res.p, res.s, res.w)
    assert np.abs(back - c).max() <= 1e-9


def test_the_corner_is_where_whole_parents_stand_not_the_lowest_block():
    """BF: a model trimmed to the ore starts with a sub-block in the middle of its parent: the grid is at the whole
    parents, the lowest block is only a part of its parent."""
    whole = [(i, 0, 0, (0, 0, 0), DIV) for i in (1, 2, 3)]
    # the right half of parent 0: the lowest block is 4 м above the corner, the whole parents give the residue
    c, d = project_of([(0, 0, 0, (4, 0, 0), (4, 4, 4)), *whole])
    res = infer(c, d)
    assert res.grid.sub.div == (2, 1, 1) and res.grid.corner == pytest.approx(ORIGIN, abs=1e-9)
    assert res.grid.counts == (4, 1, 1) and res.s[0].tolist() == [1, 0, 0] and res.w[0].tolist() == [1, 1, 1]  # type: ignore[index]
    # the same half block moved into parent −1: it is the lowest block, the grid starts one parent below it
    c[0, 0] -= 8.0
    below = infer(c, d)
    assert below.grid.corner == pytest.approx((ORIGIN[0] - 8.0, ORIGIN[1], ORIGIN[2]), abs=1e-9)
    assert below.grid.counts == (5, 1, 1) and int(below.p[0]) == 0 and below.s[0].tolist() == [1, 0, 0]  # type: ignore[index]


def test_rotation_about_z_the_corner_and_the_cells_are_recovered():
    """Т8.7-4 on the inference: the centres come from p = O + R_z(φ)·q; no corner is given — it is inferred."""
    for phi in (0.0, 30.0, -47.5, 90.0, 180.0, -90.0, 133.3):
        check_octree(phi)
    # the same model with the corner and the size in the hint, and with centres written in the model axes
    check_octree(30.0, GridHint(corner=ORIGIN, size=PARENT, rotation=30.0))
    cells = octree_cells()
    q, d = project_of(cells, (0.0, 0.0, 0.0), 0.0)
    model = q + np.array(ORIGIN)  # model axes: c = O + q
    res = infer(model, d, GridHint(rotation=25.0), centres_in="model")
    p, s, w = expected_rows(cells)
    assert res.grid.rotation == 25.0 and np.abs(np.array(res.grid.corner) - ORIGIN).max() <= 1e-9
    assert np.array_equal(res.p, p) and np.array_equal(res.s, s) and np.array_equal(res.w, w) and res.check3.ok
    again = infer(model, d, GridHint(corner=ORIGIN, rotation=25.0), centres_in="model")
    assert np.array_equal(again.p, p) and again.check3.ok


def test_the_rotation_does_not_depend_on_the_order_of_rows():
    cells = octree_cells()
    c, d = project_of(cells, phi=-33.0)
    order = np.random.default_rng(5).permutation(len(c))
    a, b = infer(c, d, GridHint(rotation=-33.0)), infer(c[order], d[order], GridHint(rotation=-33.0))
    assert np.abs(np.array(a.grid.corner) - np.array(b.grid.corner)).max() <= 1e-6
    assert a.grid.counts == b.grid.counts and a.grid.sub.div == b.grid.sub.div
    assert np.array_equal(a.p[order], b.p) and np.array_equal(a.s[order], b.s)  # type: ignore[index]


# ------------------------------------------------------------------------------ the check of three centres
def test_wrong_rotation_is_refused_with_the_three_rows():
    cells = octree_cells()
    c, d = project_of(cells, phi=30.0)
    n = len(c)
    for hint in (
        GridHint(rotation=20.0),
        GridHint(corner=(ORIGIN[0] + 0.01, ORIGIN[1], ORIGIN[2]), size=PARENT, rotation=30.0),
    ):
        with pytest.raises(ApiError) as e:
            infer(c, d, hint)
        err = e.value
        assert err.status == 422 and err.code == "rotation_mismatch" and err.message and err.hint
        assert err.extra["rows"] == [0, n // 2, n - 1] and err.extra["dist"] > 1e-6
        assert "угол" in err.hint and "1e-6" in err.message
    # centres of a turned model given in the axes of the model while the wizard says «project»
    q, _ = project_of(cells, (0.0, 0.0, 0.0), 0.0)
    with pytest.raises(ApiError) as e:
        infer(q + np.array(ORIGIN), d, GridHint(corner=ORIGIN, size=PARENT, rotation=30.0))
    assert e.value.code == "rotation_mismatch"


def test_check3_reports_without_raising_and_is_exact_for_turns_by_90_degrees():
    res, c, d = check_octree(90.0)
    rows = res.inferred.three_rows(len(c))
    ok = res.inferred.check3(c[rows], d[rows], rows)
    assert ok.ok and ok.dist == 0.0 and ok.rows == [0, len(c) // 2, len(c) - 1] and ok.as_dict()["ok"] is True
    moved = c[rows] + [2e-6, 0, 0]  # 2 µm off: over the tolerance
    bad = res.inferred.check3(moved, d[rows], rows, raise_turned=False)
    assert not bad.ok and bad.dist == pytest.approx(2e-6, rel=1e-3)
    inside = res.inferred.check3(c[rows] + [5e-7, 0, 0], d[rows], rows)
    assert inside.ok
    # an unturned model is not refused by the check (its refusal is blocks_unaligned); the answer is still given
    flat, c0, d0 = check_octree(0.0)
    off = flat.inferred.check3(c0[:3] + [0.4, 0, 0], d0[:3], [0, 1, 2])
    assert not off.ok and off.dist > 0.3
    assert flat.inferred.three_rows(1) == [0] and flat.inferred.three_rows(0) == []


# ------------------------------------------------------------------------------ blocks that cannot be held
def bf_blocks(parent=5.0):
    """BF's model of 5 m parents: halves 2 + 3 m, slices of 1 m, layers 2 + 2 + 1 m — not on 2^L sub-cells."""
    rows = []
    for i in range(4):
        for j in range(3):
            for k in range(2):
                if (i + j + k) % 5 == 4:
                    continue
                lo = np.array(ORIGIN) + parent * np.array([i, j, k])
                kind = (i * 7 + j * 3 + k) % 4
                if kind == 0:
                    parts = [(lo, (5, 5, 5))]
                elif kind == 1:
                    parts = [(lo, (2, 5, 5)), (lo + [2, 0, 0], (3, 5, 5))]
                elif kind == 2:
                    parts = [(lo + [x, 0, 0], (1, 5, 5)) for x in range(5)]
                else:
                    parts = [(lo, (5, 5, 2)), (lo + [0, 0, 2], (5, 5, 2)), (lo + [0, 0, 4], (5, 5, 1))]
                rows += [(*(corner + np.array(size) / 2), *size) for corner, size in parts]
    a = np.array(rows, dtype=float)
    return a[:, :3], a[:, 3:]


def test_subblocks_not_on_sub_cells_are_refused_with_their_rows():
    c, d = bf_blocks()
    with pytest.raises(ApiError) as e:
        infer(c, d)
    err = e.value
    assert err.status == 422 and err.code == "blocks_unaligned" and err.message and err.hint
    assert err.extra["count"] > 0 and err.extra["rows"] == sorted(err.extra["rows"])
    assert len(err.extra["rows"]) == min(20, err.extra["count"]) and all(0 <= r < len(c) for r in err.extra["rows"])
    assert "не по подъячейкам" in err.message and "Строки данных (с 1)" in err.hint
    assert str(err.extra["rows"][0] + 1) in err.hint
    # the same model with the parent size and the division said by the wizard: the same refusal, the rows are real
    with pytest.raises(ApiError) as e2:
        infer(c, d, GridHint(corner=ORIGIN, size=(5.0, 5.0, 5.0), div=(4, 4, 4)))
    bad = e2.value.extra["rows"]
    assert all(d[r, 0] in (2.0, 3.0, 1.0) or d[r, 2] in (2.0, 1.0) for r in bad)  # a block of a non-binary size


def test_blocks_before_the_corner_beyond_the_counts_and_over_the_parent_border():
    cells = octree_cells()
    c, d = project_of(cells)
    # the corner and the counts of the wizard: blocks beyond them are refused, not moved
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(corner=(ORIGIN[0] + 8, ORIGIN[1], ORIGIN[2]), size=PARENT, div=DIV))
    assert e.value.code == "blocks_unaligned" and "за началом или за краем" in e.value.message
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(corner=ORIGIN, size=PARENT, div=DIV, counts=(3, 3, 2)))
    assert e.value.code == "blocks_unaligned" and e.value.extra["count"] > 0
    # a block 4 м wide across the border of two parents (its edges are on the sub-cells, the parent is crossed)
    c2, d2 = c.copy(), d.copy()
    c2[0] = (ORIGIN[0] + 8.0, ORIGIN[1] + 2.0, ORIGIN[2] + 2.0)
    d2[0] = (4.0, 4.0, 4.0)
    with pytest.raises(ApiError) as e:
        infer(c2, d2, GridHint(corner=ORIGIN, size=PARENT, div=DIV))
    assert (
        e.value.code == "blocks_unaligned"
        and "через границу родителя" in e.value.message
        and e.value.extra["rows"] == [0]
    )


def test_the_list_of_rows_is_cut_at_twenty_and_counts_all():
    n = 45
    c = np.array([[4.0 + 8 * i, 4.0, 4.0] for i in range(n)])
    d = np.full((n, 3), 8.0)
    d[10:] = 3.0  # 35 blocks of a size that no 2^L division of 8 holds with these centres
    c[10:, 0] += 0.3
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(corner=(0, 0, 0), size=(8.0, 8.0, 8.0), div=(1, 1, 1)))
    assert e.value.extra["count"] == 35 and e.value.extra["rows"] == list(range(10, 30))
    assert "и другие" in e.value.hint and "35" in e.value.message


def test_empty_centres_and_sizes_are_dropped_not_turned_to_zero():
    cells = octree_cells()
    c, d = project_of(cells)
    c[3, 1], d[7, 2] = np.nan, 0.0
    res = infer(c, d, GridHint(corner=ORIGIN, size=PARENT, div=DIV))
    assert res.dropped == 2 and res.valid is not None and not res.valid[3] and not res.valid[7]
    assert "пропущены" in res.warnings[-1] and res.parents == len({int(v) for v in res.p[res.valid]})
    assert int(res.p[3]) == 0 and not res.s[3].any()  # type: ignore[index]
    with pytest.raises(ApiError) as e:
        infer(np.full((3, 3), np.nan), np.ones((3, 3)))
    assert e.value.code == "block_model_invalid" and e.value.hint


def test_sizes_that_need_more_than_128_sub_cells_are_refused():
    c = np.array([[500.0, 0.5, 0.5], [1000.5, 0.5, 0.5]])
    d = np.array([[1000.0, 1.0, 1.0], [1.0, 1.0, 1.0]])
    c[0, 0], c[1, 0] = 500.0, 1000.5
    with pytest.raises(ApiError) as e:
        infer(c, d)
    assert e.value.code == "blocks_unaligned"
    with pytest.raises(ApiError) as e:
        GridScan(centres="sky")
    assert e.value.code == "block_model_invalid"


def test_the_grid_over_the_limit_is_refused_by_the_schema():
    c = np.array([[0.5, 0.5, 0.5], [1e6 + 0.5, 1e6 + 0.5, 1e6 + 0.5]])
    with pytest.raises(ApiError) as e:
        infer(c, np.ones((2, 3)))
    assert e.value.code == "block_model_too_large"


# ------------------------------------------------------------------------------ chunks
def test_two_passes_over_chunks_equal_the_arrays_in_memory():
    cells = octree_cells()
    c, d = project_of(cells, phi=21.0)
    hint = GridHint(rotation=21.0)
    whole = infer(c, d, hint)
    scan = GridScan(hint)
    cuts = [0, 7, 30, 31, len(c)]
    for a, b in zip(cuts, cuts[1:], strict=False):
        scan.add(c[a:b], d[a:b])
    found = scan.finish()
    assert found.grid == whole.grid and found.warnings == whole.warnings
    lost = Unaligned()
    parts = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        cr = found.rows_of(c[a:b], d[a:b])
        lost.add(a, cr)
        parts.append(cr)
    assert lost.error() is None and lost.count == 0
    assert np.array_equal(np.concatenate([x.p for x in parts]), whole.p)
    assert np.array_equal(np.concatenate([x.w for x in parts]), whole.w)  # type: ignore[arg-type]
    # the rows named by `Unaligned` are the rows of the file, not of the chunk
    d2 = d.copy()
    d2[40] = (3.0, 3.0, 3.0)
    scan2 = GridScan(GridHint(corner=ORIGIN, size=PARENT, div=DIV, rotation=21.0))
    scan2.add(c[:30], d2[:30])
    scan2.add(c[30:], d2[30:])
    grid2 = scan2.finish()
    lost2 = Unaligned()
    lost2.add(0, grid2.rows_of(c[:30], d2[:30]))
    lost2.add(30, grid2.rows_of(c[30:], d2[30:]))
    assert lost2.count == 1 and lost2.rows == [40]
    with pytest.raises(ApiError):
        lost2.raise_if()
    sized, regular = GridScan(), GridScan()
    sized.add(c[:3], d[:3])
    with pytest.raises(ApiError):
        sized.add(c[3:6], None)
    regular.add(c[:3])
    assert regular.rows == 3
    with pytest.raises(ApiError):
        GridScan().add(c[:3], d[:2])
    with pytest.raises(ApiError):
        GridScan().finish()


def test_rows_of_returns_numbers_the_store_can_use():
    res, _, _ = check_octree(15.0)
    assert res.p.dtype == np.uint64 and res.s is not None and res.s.dtype == np.uint8 and res.w.dtype == np.uint8  # type: ignore[union-attr]
    assert isinstance(res.grid, GridV2) and res.grid.to_meta()["div"] == list(DIV)
    assert res.grid.rotation == 15.0 and int(res.p.max()) < res.grid.cells


# ------------------------------------------------------------------------------ the owner's model
@pytest.fixture(scope="module")
def real(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not REAL.is_file():
        pytest.skip("tests/data/real/Для BF.zip is not here")
    folder = tmp_path_factory.mktemp("blocks_infer")
    out: dict[str, Path] = {}
    with zipfile.ZipFile(REAL) as archive:
        for info in archive.infolist():
            suffix = Path(info.filename).suffix.lower()
            out[suffix] = folder / f"model{suffix}"
            with archive.open(info) as src, out[suffix].open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return out


def geometry_of(table, roles: br.ColumnRoles) -> tuple[np.ndarray, np.ndarray]:
    c = np.column_stack([table.column(roles.geometry[a]).values for a in "xyz"])
    d = np.column_stack([table.column(roles.geometry["d" + a]).values for a in "xyz"])
    return c, d


def test_real_dat_the_grid_is_inferred_and_equals_the_datamine_header(real):
    dat = read_dat(real[".dat"])
    roles = br.classify(dat.names)
    assert roles.has_centre and roles.has_size and roles.fields == ["Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"]
    c, d = geometry_of(dat, roles)
    res = infer(c, d)
    assert len(res.p) == 169_070 and res.parents == 169_070 and res.s is None and res.dropped == 0
    assert res.grid.size == (10.0, 10.0, 10.0) and res.grid.counts == (443, 558, 102) and res.grid.rotation == 0.0
    assert res.grid.corner == (23436265.92, 5988122.53, -157.14) and not res.inferred.parent_guess
    assert res.check3.ok and res.check3.dist <= 1e-6 and res.check3.rows == [0, 84_535, 169_069]
    assert br.empty_candidates(dat.column("Fe_m_pc").values) == [-99.0]  # the 90 empty blocks of the analysis
    assert np.abs(c[0] - (23436270.92, 5988227.53, 457.86)).max() <= 1e-9  # the first block of the analysis
    back = geo.centres(res.grid, res.p, None, None)
    assert np.abs(back - c).max() <= 1e-6


@pytest.mark.parametrize("suffix, counts", [(".dm", (443, 558, 102)), (".fbm", (445, 560, 104))])
def test_real_datamine_the_hint_of_the_header_and_the_same_blocks(real, suffix, counts):
    header = read_header(real[suffix])
    hint = br.datamine_hint(header.constants)
    assert hint == GridHint(corner=(23436265.92, 5988122.53, -157.14), counts=counts)
    table = read_dm(real[suffix])
    roles = br.classify(header.names)  # the implicit fields of the dictionary too: the constants of the grid
    assert roles.ijk == "IJK" and roles.origin["x"] == "XMORIG" and roles.counts["z"] == "NZ"
    assert roles.fields == ["Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"] and roles.frac is None
    assert br.classify(table.names).fields == roles.fields  # the same fields without the implicit ones
    c, d = geometry_of(table, roles)
    res = infer(c, d, hint)
    assert res.grid.counts == counts and res.grid.corner == hint.corner and res.grid.size == (10.0, 10.0, 10.0)
    assert len(res.p) == 169_070 and res.parents == 169_070 and res.check3.ok
    # the blocks are the same as in the DAT in every file: the cells (i, j, k), whatever the counts of the grid
    dat = read_dat(real[".dat"])
    c0, d0 = geometry_of(dat, br.classify(dat.names))
    assert np.abs(c - c0).max() == 0.0 and np.abs(d - d0).max() == 0.0
    ref = infer(c0, d0)
    assert np.array_equal(geo.row_ijk(res.grid, res.p)[0], geo.row_ijk(ref.grid, ref.p)[0])
    assert np.array_equal(geo.row_ijk(res.grid, res.p)[2], geo.row_ijk(ref.grid, ref.p)[2])


def test_unaligned_message_keeps_its_commas() -> None:
    """Only the thousands of a number get a space: the commas of the text stay (defect of 17.11)."""
    lost = Unaligned()
    lost.count = 12_345
    lost.by_reason[OFF_GRID] = 12_000
    lost.by_reason[CROSSES_PARENT] = 345
    lost.rows = [0, 5]
    err = lost.error()
    assert err is not None
    assert err.message == (
        "Блоков, которые нельзя уложить в сетку модели: 12 345 "
        "(за началом или за краем сетки — 12 000; через границу родителя — 345)."
    )
    assert "Блоков, которые" in err.message and "," not in err.message.split(":")[1]
