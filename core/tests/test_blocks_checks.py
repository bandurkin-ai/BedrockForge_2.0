"""Checks of a block model (подшаг 17.12; ТЗ 7.31, **Т8.7-5**, ADR 0023 decisions 5 and 7): overlaps (exact and by
volume), duplicates, blocks off the grid and sub-blocks across their parent, empty and constant fields, the volume;
the result does not depend on the size of the portions; the stored model."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import checks as ck
from bf_core.blocks import geometry as geo
from bf_core.blocks import schema as bs
from bf_core.blocks.schema import GridV2

WHOLE = ((0, 0, 0), (2, 2, 2))
OCT = [((o & 1, (o >> 1) & 1, o >> 2), (1, 1, 1)) for o in range(8)]  # the eight octants of a parent, div = 2


def grid_of(counts=(4, 3, 2), div=(2, 2, 2), size=(10.0, 20.0, 5.0), rotation=0.0, tensors=None) -> GridV2:
    raw: dict[str, Any] = {"corner": [100.0, 200.0, 50.0], "counts": list(counts), "div": list(div),
                           "rotation": rotation}  # fmt: skip
    raw.update({"tensors": tensors} if tensors else {"size": list(size)})
    return GridV2.of(raw)


def rows_of(*specs: tuple[int, tuple[int, int, int], tuple[int, int, int]], sort: bool = True):
    """(p, s, w) of the rows [(P, s, w)…]; sorted like the files (stable: repeats keep their order)."""
    p = np.array([r[0] for r in specs], dtype=np.uint64)
    s = np.array([r[1] for r in specs], dtype=np.uint8).reshape(-1, 3)
    w = np.array([r[2] for r in specs], dtype=np.uint8).reshape(-1, 3)
    if sort:
        o = bs.sort_rows(p, s)
        p, s, w = p[o], s[o], w[o]
    return p, s, w


def run(grid: GridV2, specs, portion: int = 1 << 20, **kw: Any) -> ck.CheckReport:
    p, s, w = rows_of(*specs)
    return ck.check_arrays(grid, p, s, w, portion=portion, **kw)


def drop_at(report: ck.CheckReport) -> dict[str, Any]:
    return {k: v for k, v in report.checks.items() if k != "at"}


def count(report: ck.CheckReport, code: str) -> int:
    f = report.finding(code)
    return 0 if f is None else f["count"]


# --- Т8.7-5 -------------------------------------------------------------------------------------------------


def random_tiling(grid: GridV2, seed: int):
    """Every parent cut in a random way into disjoint sub-blocks (a random binary split, depth ≤ 3): a tiling."""
    rng = np.random.default_rng(seed)
    div = np.asarray(grid.sub.div)
    out = []

    def cut(p: int, lo: np.ndarray, hi: np.ndarray, depth: int) -> None:
        axes = [a for a in range(3) if hi[a] - lo[a] > 1]
        if depth == 0 or not axes or rng.random() < 0.3:
            out.append((p, tuple(int(v) for v in lo), tuple(int(v) for v in hi - lo)))
            return
        a = axes[int(rng.integers(len(axes)))]
        mid = int(rng.integers(lo[a] + 1, hi[a]))
        h1, l2 = hi.copy(), lo.copy()
        h1[a], l2[a] = mid, mid
        cut(p, lo, h1, depth - 1)
        cut(p, l2, hi, depth - 1)

    for p in range(grid.cells):
        cut(p, np.zeros(3, dtype=np.int64), div.astype(np.int64), 3)
    return out


@pytest.mark.parametrize("div", [(2, 2, 2), (8, 4, 2), (16, 16, 4)])
def test_t8_7_5_tiling_does_not_overlap_and_sums_to_the_parent(div):
    grid = grid_of(div=div, rotation=30.0)
    specs = random_tiling(grid, seed=div[0])
    assert len(specs) > grid.cells  # some parents are split
    p, s, w = rows_of(*specs)
    report = ck.check_arrays(grid, p, s, w)
    assert report.ok and not report.finding("overlaps") and not report.finding("parent_overfull")
    assert report.checks["overlaps"] == {"n": 0, "method": "exact"}
    # Т8.7-5 in float: Σ of the sub-block volumes of every parent ≤ the volume of the parent (1e−12 relative)
    vol = geo.row_volumes(grid, p, s, w)
    per_parent = np.bincount(p.astype(np.intp), weights=vol)
    parent = grid.size[0] * grid.size[1] * grid.size[2]
    assert (per_parent <= parent * (1 + 1e-12)).all()
    assert np.allclose(per_parent, parent, rtol=1e-12, atol=0)  # a tiling fills the parent
    assert report.checks["volume"] == pytest.approx(parent * grid.cells, rel=1e-12)


def test_t8_7_5_one_sub_block_too_many_breaks_both_tests():
    grid = grid_of(counts=(2, 1, 1))
    specs = [(0, *o) for o in OCT] + [(1, *WHOLE), (1, (0, 0, 0), (1, 1, 1))]  # parent 1: whole + an octant
    report = run(grid, specs)
    assert count(report, "overlaps") == 2 and count(report, "parent_overfull") == 1
    f = report.finding("parent_overfull")
    assert f and f["severity"] == ck.ERROR and f["rows"] == [8]
    ov = report.finding("overlaps")
    assert ov and ov["rows"] == [8, 9] and ov["method"] == "exact"
    assert report.checks["overlaps"] == {"n": 2, "method": "exact"}
    assert not report.ok


# --- overlaps: exact ----------------------------------------------------------------------------------------


def test_overlap_exact_touching_is_not_overlapping_but_a_shared_cell_is():
    grid = grid_of(counts=(3, 1, 1), div=(4, 4, 4))
    ok = [(0, (0, 0, 0), (2, 4, 4)), (0, (2, 0, 0), (2, 4, 4))]  # a shared face only
    bad = [(1, (0, 0, 0), (3, 4, 4)), (1, (2, 0, 0), (2, 4, 4))]  # one layer of cells in common
    far = [(2, (0, 0, 0), (1, 1, 1)), (2, (3, 3, 3), (1, 1, 1))]  # a gap between: a corner and a corner
    report = run(grid, ok + bad + far)
    ov = report.finding("overlaps")
    assert ov and ov["count"] == 2 and ov["rows"] == [2, 3]
    assert report.checks["examples"] == [
        {"check": "overlap", "row": 2, "p": 1, "sub": [0, 0, 0, 3, 4, 4], "other_row": 3}
    ]


def test_overlap_exact_sees_what_a_gap_hides_from_the_volume_test():
    grid = grid_of(counts=(1, 1, 1), div=(4, 4, 4))
    # two 2x2x2 boxes sharing one cell (Σ = 16 of 64 cells): a lot of empty room in the parent
    specs = [(0, (0, 0, 0), (2, 2, 2)), (0, (1, 1, 1), (2, 2, 2))]
    exact = run(grid, specs)
    assert count(exact, "overlaps") == 2 and not exact.finding("parent_overfull")
    assert exact.checks["overlaps"]["method"] == "exact"


def test_overlap_nested_and_crossing_in_each_axis():
    grid = grid_of(counts=(1, 1, 1), div=(8, 8, 8))
    for a in range(3):  # overlap in one axis only is not enough; overlap in all three is
        lo = [0, 0, 0]
        hi = [4, 4, 4]
        shifted = [0, 0, 0]
        shifted[a] = 4  # touches along the axis a only
        touching = [(0, tuple(lo), tuple(hi)), (0, tuple(shifted), tuple(hi))]
        assert count(run(grid, touching), "overlaps") == 0
        inside = [(0, (0, 0, 0), (8, 8, 8)), (0, (3, 3, 3), (1, 1, 1))]
        assert count(run(grid, inside), "overlaps") == 2


def test_overlap_in_a_parent_with_many_rows_is_painted():
    grid = grid_of(counts=(2, 1, 1), div=(8, 8, 8))
    # 64 columns of height 4 per parent (more rows than the pair test takes): parent 0 is clean, parent 1 has one
    # more cell on top of the column (3, 3) — Σ = 257 of 512 sub-cells, so only the painting can see it
    cols = [((x, y, 0), (1, 1, 4)) for y in range(8) for x in range(8)]
    specs = [(0, *c) for c in cols] + [(1, *c) for c in cols] + [(1, (3, 3, 2), (1, 1, 1))]
    report = run(grid, specs)
    ov = report.finding("overlaps")
    assert ov and ov["count"] == 2 and report.checks["overlaps"]["method"] == "exact"
    assert ov["rows"] == [64 + 3 * 8 + 3, 128]  # the column (3, 3) of parent 1 and the single cell
    assert not report.finding("parent_overfull")


def test_overlap_painted_parent_over_its_volume_counts_all_its_rows_by_volume():
    grid = grid_of(counts=(1, 1, 1), div=(8, 8, 8))
    cols = [((x, y, 0), (1, 1, 8)) for y in range(8) for x in range(8)]
    specs = [(0, *c) for c in cols] + [(0, (3, 3, 2), (1, 1, 1))]
    report = run(grid, specs)
    assert count(report, "parent_overfull") == 1
    assert count(report, "overlaps") == len(specs)  # too full to paint: every row of the parent
    assert report.checks["overlaps"]["method"] == "volume"


def test_overlap_painted_parent_exact_with_a_gap():
    grid = grid_of(counts=(1, 1, 1), div=(8, 8, 8))
    cols = [((x, y, 0), (1, 1, 4)) for y in range(8) for x in range(8) if (x, y) != (7, 7)]  # 63 columns, z 0…3
    specs = [(0, *c) for c in cols] + [(0, (0, 0, 2), (1, 1, 2))]  # one more on top of the column (0, 0)
    report = run(grid, specs)
    assert not report.finding("parent_overfull")  # Σ = 63·4 + 2 of 512: the volume test alone would pass it
    ov = report.finding("overlaps")
    assert ov and ov["count"] == 2 and report.checks["overlaps"]["method"] == "exact"
    assert ov["rows"] == [0, 63]


# --- overlaps: by volume ------------------------------------------------------------------------------------


def test_overlap_by_volume_when_the_parent_is_too_fine(monkeypatch):
    monkeypatch.setattr(ck, "EXACT_MAX_SUBCELLS", 7)  # a parent of 8 sub-cells is tested by volume
    grid = grid_of(counts=(3, 1, 1))
    full = [(0, *o) for o in OCT]
    over = [(1, *o) for o in OCT] + [(1, (0, 0, 0), (1, 1, 2))]  # Σ = 10 sub-cells of 8
    hidden = [(2, *o) for o in OCT[:3]] + [(2, (0, 0, 0), (1, 1, 2))]  # overlap, but Σ = 5 ≤ 8: unseen by volume
    report = run(grid, full + over + hidden)
    ov = report.finding("overlaps")
    assert ov and ov["method"] == "volume" and ov["count"] == 9 and ov["rows"][0] == 8
    assert report.checks["overlaps"] == {"n": 9, "method": "volume"}
    assert count(report, "parent_overfull") == 1
    assert "по объёму" in ov["message"]
    assert all(r < 17 for r in ov["rows"])  # parent 2 (rows 17…) is not flagged by volume


# --- duplicates ---------------------------------------------------------------------------------------------


def test_duplicates_keep_the_first_row_and_do_not_count_as_overlaps():
    grid = grid_of(counts=(3, 1, 1))
    specs = [(0, *WHOLE), (0, *WHOLE), (1, *OCT[0]), (1, *OCT[1]), (1, *OCT[0]), (1, *OCT[0]), (2, *WHOLE)]
    p, s, w = rows_of(*specs)
    for portion in (1, 2, 3, 100):
        report = ck.check_arrays(grid, p, s, w, portion=portion)
        d = report.finding("duplicates")
        assert d and d["count"] == 3 and d["severity"] == ck.WARNING and d["rows"] == [1, 3, 4]
        assert not report.finding("overlaps")
        assert report.checks["duplicates"] == 3
        assert [e["other_row"] for e in report.checks["examples"] if e["check"] == "duplicate"] == [0, 2, 2]
        # the later copies are not in the volume: parent 0 whole + parent 1 two octants + parent 2 whole
        assert report.checks["volume"] == pytest.approx(10.0 * 20.0 * 5.0 * (1 + 0.25 + 1), rel=1e-12)


def test_duplicates_without_sub_blocks_and_the_mask_of_an_unsorted_file():
    grid = grid_of(counts=(4, 1, 1), div=(1, 1, 1))
    report = ck.check_arrays(grid, np.array([0, 1, 1, 1, 3], dtype=np.uint64))
    assert count(report, "duplicates") == 2 and report.finding("duplicates")["rows"] == [2, 3]
    mask = ck.duplicate_mask(np.array([3, 1, 3, 0, 1, 3], dtype=np.uint64))
    assert mask.tolist() == [False, False, True, False, True, True]  # the first of each stays
    p, s, _ = rows_of((2, (0, 0, 0), (1, 1, 1)), (1, (1, 0, 0), (1, 1, 1)), (2, (0, 0, 0), (1, 1, 1)),
                      (2, (0, 1, 0), (1, 1, 1)), (1, (1, 0, 0), (1, 1, 1)), sort=False)  # fmt: skip
    assert ck.duplicate_mask(p, s).tolist() == [False, False, True, False, True]


# --- outside, crossing, order -------------------------------------------------------------------------------


def test_blocks_off_the_grid_and_sub_blocks_across_the_parent():
    grid = grid_of(counts=(2, 1, 1))
    specs = [(0, *WHOLE), (1, (1, 0, 0), (2, 1, 1)), (1, (0, 0, 0), (1, 1, 1)), (5, *WHOLE), (9, *WHOLE)]
    report = run(grid, specs)
    out, cross = report.finding("outside"), report.finding("crossing")
    assert out and out["count"] == 2 and out["rows"] == [3, 4] and out["severity"] == ck.ERROR
    assert cross and cross["count"] == 1 and cross["rows"] == [2]
    assert {"check": "crossing", "row": 2, "p": 1, "sub": [1, 0, 0, 2, 1, 1]} in report.checks["examples"]
    assert {"check": "outside", "row": 3, "p": 5, "sub": [0, 0, 0, 2, 2, 2]} in report.checks["examples"]
    # the volume counts the blocks that are in the grid and inside their parent
    assert report.checks["volume"] == pytest.approx(10.0 * 20.0 * 5.0 * 1.125)
    assert not report.ok


def test_a_zero_or_too_big_size_is_a_crossing_and_the_rows_must_ascend():
    grid = grid_of(counts=(1, 1, 1))
    report = ck.check_arrays(grid, [0, 0], [[0, 0, 0], [0, 0, 1]], [[0, 1, 1], [2, 2, 2]])
    assert count(report, "crossing") == 2
    with pytest.raises(ApiError) as err:
        ck.check_arrays(grid_of(), np.array([1, 0], dtype=np.uint64))
    assert err.value.status == 422 and err.value.code == "block_model_invalid"
    with pytest.raises(ApiError):  # across two portions
        c = ck.ModelChecker(grid_of())
        c.feed(np.array([3], dtype=np.uint64))
        c.feed(np.array([2], dtype=np.uint64))


# --- portions and big parents -------------------------------------------------------------------------------


def test_result_does_not_depend_on_the_portion_size():
    grid = grid_of(counts=(6, 5, 4), div=(4, 4, 2))
    rng = np.random.default_rng(5)
    specs = []
    for p in range(grid.cells):
        k = int(rng.integers(1, 7))
        for _ in range(k):  # random boxes: overlaps are plenty
            s = [int(rng.integers(0, d)) for d in (4, 4, 2)]
            w = [int(rng.integers(1, d - a + 1)) for a, d in zip(s, (4, 4, 2), strict=True)]
            specs.append((p, tuple(s), tuple(w)))
    p, s, w = rows_of(*specs)
    whole = ck.check_arrays(grid, p, s, w)
    assert count(whole, "overlaps") > 10 and count(whole, "duplicates") >= 0
    for portion in (1, 7, 64):
        part = ck.check_arrays(grid, p, s, w, portion=portion)
        assert part.findings == whole.findings
        assert drop_at(part) == drop_at(whole)


def test_overlap_count_agrees_with_a_brute_force_painting():
    grid = grid_of(counts=(5, 4, 3), div=(8, 8, 8))
    rng = np.random.default_rng(11)
    specs = []
    for p in range(grid.cells):
        for _ in range(int(rng.integers(1, 40))):  # up to 40 rows: the pair test and the painting both run
            s = [int(rng.integers(0, 8)) for _ in range(3)]
            w = [int(rng.integers(1, min(2, 8 - a) + 1)) for a in s]
            specs.append((p, tuple(s), tuple(w)))
    p, s, w = rows_of(*specs)
    keep = ~ck.duplicate_mask(p, s, w)
    report = ck.check_arrays(grid, p, s, w)
    assert report.checks["overlaps"]["method"] == "exact" and not report.finding("parent_overfull")
    want = 0  # painting by hand, parent by parent: a row overlaps if one of its cells is painted by another row
    for q in np.unique(p[keep]):
        sel = np.flatnonzero(keep & (p == q))
        cells = np.zeros((8, 8, 8), dtype=np.int16)
        for r in sel:
            cells[s[r, 0] : s[r, 0] + w[r, 0], s[r, 1] : s[r, 1] + w[r, 1], s[r, 2] : s[r, 2] + w[r, 2]] += 1
        for r in sel:
            box = cells[s[r, 0] : s[r, 0] + w[r, 0], s[r, 1] : s[r, 1] + w[r, 1], s[r, 2] : s[r, 2] + w[r, 2]]
            want += int((box > 1).any())
    assert want > 50 and count(report, "overlaps") == want
    assert count(report, "duplicates") == int((~keep).sum())


# --- fields -------------------------------------------------------------------------------------------------


def test_empty_and_constant_fields():
    grid = grid_of(counts=(6, 1, 1), div=(1, 1, 1))
    p = np.arange(6, dtype=np.uint64)
    nan = np.nan
    values = {
        "AU": np.array([1.0, nan, 3.0, 4.0, 5.0, 6.0]),
        "EMPTY": np.array([nan, np.inf, -np.inf, nan, nan, nan]),  # ±inf is empty too
        "ONE": np.array([2.5, 2.5, nan, 2.5, 2.5, 2.5]),
        "ALONE": np.array([nan, nan, 7.0, nan, nan, nan]),  # a single value is not "constant"
        "ROCK": np.array([1, 1, 1, 1, 1, 1], dtype=np.int32),
        "NOROCK": np.array([-1, -1, -1, -1, -1, -1], dtype=np.int32),  # v1 category: −1 is empty
    }
    kinds = {"ROCK": "category", "NOROCK": "category"}
    fields = [{"name": k, "kind": kinds.get(k, "number")} for k in values]
    for portion in (1, 4, 100):
        report = ck.check_arrays(grid, p, values=values, fields=fields, portion=portion)
        assert report.checks["empty"] == ["EMPTY", "NOROCK"]
        assert report.checks["constant"] == ["ONE", "ROCK"]
        e, c = report.finding("empty_fields"), report.finding("constant_fields")
        assert e and e["fields"] == ["EMPTY", "NOROCK"] and e["severity"] == ck.WARNING and e["count"] == 2
        assert c and c["fields"] == ["ONE", "ROCK"] and c["severity"] == ck.INFO
        assert "EMPTY" in e["message"] and e["hint"]


def test_nullable_columns_and_the_fields_of_other_files():
    grid = grid_of(counts=(3, 1, 1), div=(1, 1, 1))
    obj = np.array([None, None, None], dtype=object)  # a column of nulls
    report = ck.check_arrays(grid, [0, 1, 2], values={"G": obj, "H": np.array([1.0, 2.0, 3.0])})
    assert report.checks["empty"] == ["G"] and report.checks["constant"] == []
    chk = ck.ModelChecker(grid)
    chk.feed(np.array([0, 1, 2], dtype=np.uint64))
    other = [{"name": "A", "stats": {"count": 0}}, {"name": "B", "stats": {"count": 3, "min": 2.0, "max": 2.0}},
             {"name": "C", "stats": {"count": 3, "min": 1.0, "max": 2.0}}]  # fmt: skip
    r = chk.finish(other)
    assert r.checks["empty"] == ["A"] and r.checks["constant"] == ["B"]


# --- volume -------------------------------------------------------------------------------------------------


def test_volume_with_frac_a_gap_is_not_a_zero():
    grid = grid_of(counts=(4, 1, 1), div=(2, 1, 1))
    specs = [(0, (0, 0, 0), (2, 1, 1)), (1, (0, 0, 0), (1, 1, 1)), (1, (1, 0, 0), (1, 1, 1)), (3, (0, 0, 0), (2, 1, 1))]
    p, s, w = rows_of(*specs)
    frac = np.array([1.0, 0.5, np.nan, 0.25])
    report = ck.check_arrays(grid, p, s, w, values={"FRAC": frac}, fields=[{"name": "FRAC", "kind": "number"}],
                             frac="FRAC")  # fmt: skip
    cell = 10.0 * 20.0 * 5.0
    v = report.finding("volume")
    assert v and v["severity"] == ck.INFO and v["count"] == 1  # one block without FRAC
    assert report.checks["volume"] == pytest.approx(cell * (1.0 + 0.5 * 0.5 + 0.25), rel=1e-12)
    assert "Без значения FRAC: 1" in v["message"]
    # without FRAC the volume is the whole block
    whole = ck.check_arrays(grid, p, s, w)
    assert whole.checks["volume"] == pytest.approx(cell * (1.0 + 0.5 + 0.5 + 1.0), rel=1e-12)
    assert whole.finding("volume")["count"] == 0


def test_variable_step_grid_and_an_empty_model():
    grid = grid_of(counts=(3, 1, 1), div=(1, 1, 1), tensors={"x": [1.0, 2.0, 4.0], "y": [10.0], "z": [5.0]})
    report = ck.check_arrays(grid, [0, 1, 1, 2])
    assert report.checks["volume"] == pytest.approx((1 + 2 + 4) * 50.0) and count(report, "duplicates") == 1
    empty = ck.check_arrays(grid_of(), np.empty(0, dtype=np.uint64))
    assert empty.rows == 0 and empty.checks["volume"] == 0.0 and empty.ok
    assert empty.checks["empty"] == [] and empty.checks["overlaps"] == {"n": 0, "method": "exact"}


def test_findings_are_russian_with_a_hint_and_examples_are_capped():
    grid = grid_of(counts=(60, 1, 1))
    specs = [(p, *WHOLE) for p in range(60) for _ in range(2)]  # 60 duplicates
    report = run(grid, specs)
    d = report.finding("duplicates")
    assert d and d["count"] == 60 and len(d["rows"]) == 20 and d["rows"] == list(range(1, 40, 2))
    assert len(report.checks["examples"]) == 20
    for f in report.findings:
        assert set(f) >= {"code", "severity", "message", "hint", "rows", "count"}
        assert f["severity"] in ("error", "warning", "info") and f["message"] and f["hint"]
    assert "Повторяющиеся блоки: 60" in d["message"]
    bs_meta = {"at", "duplicates", "overlaps", "empty", "constant", "volume", "examples"}
    assert set(report.checks) == bs_meta


# --- a stored model -----------------------------------------------------------------------------------------


class Stub:
    def __init__(self, path: Path) -> None:
        self.path = path

    def path_of(self, sha: str) -> Path:
        return self.path


def test_check_cells_reads_the_cells_file(tmp_path: Path):
    grid = grid_of(counts=(3, 1, 1))
    specs = [(0, *WHOLE), (1, *OCT[0]), (1, *OCT[1]), (1, (0, 0, 0), (2, 2, 1)), (2, *WHOLE)]
    p, s, w = rows_of(*specs)
    table = pa.table({"idx": p, **{c: s[:, a] for a, c in enumerate(bs.SUB_COLUMNS[:3])},
                      **{c: w[:, a] for a, c in enumerate(bs.SUB_COLUMNS[3:])},
                      "AU": np.array([1.0, 2.0, 3.0, np.nan, 5.0]), "FRAC": np.array([1.0, 1.0, 1.0, 1.0, 0.5]),
                      "CONST": np.full(5, 9.0)})  # fmt: skip
    path = tmp_path / "cells.parquet"
    pq.write_table(table, path, row_group_size=2)  # three row groups: the parent 1 spans two of them
    meta = {"grid": grid.to_meta(), "sub": True, "fields": [
        {"name": "AU", "kind": "number", "file": "cells"},
        {"name": "FRAC", "kind": "number", "role": "frac", "file": "cells"},
        {"name": "CONST", "kind": "number", "file": "cells"},
        {"name": "CALC", "kind": "number", "file": "fld:0123abcd", "stats": {"count": 0}},
    ]}  # fmt: skip
    report = ck.check_cells(Stub(path), "0" * 64, meta)  # type: ignore[arg-type]
    assert report.rows == 5 and count(report, "overlaps") == 3  # the slab 2x2x1 covers two octants of parent 1
    assert report.checks["constant"] == ["CONST"] and report.checks["empty"] == ["CALC"]
    cell = 10.0 * 20.0 * 5.0
    assert report.checks["volume"] == pytest.approx(cell * (1 + 0.125 + 0.125 + 0.5 + 0.5), rel=1e-12)
    meta["fields"][1]["file"] = "fld:11111111"  # FRAC kept in another file: not read, the volume is unknown
    assert ck.check_cells(Stub(path), "0" * 64, meta).checks["volume"] is None  # type: ignore[arg-type]


# --- runs of one key, the generator -------------------------------------------------------------------------


def test_repeat_in_a_run_of_one_key_with_another_size_in_between():
    grid = grid_of(counts=(2, 1, 1))
    a, b = ((0, 0, 0), (1, 1, 1)), ((0, 0, 0), (1, 1, 2))  # the same offset, two sizes: a b a a a b
    specs = [(0, *a), (0, *b), (0, *a), (1, *a), (1, *a), (1, *b)]
    p, s, w = rows_of(*specs)
    assert ck.duplicate_mask(p, s, w).tolist() == [False, False, True, False, True, False]  # unsorted order too
    for portion in (1, 2, 3, 6):  # the run of a key crosses the border of the portions
        report = ck.check_arrays(grid, p, s, w, portion=portion)
        d = report.finding("duplicates")
        assert d and d["rows"] == [2, 4] and count(report, "overlaps") == 4  # a and b of both parents meet
        assert [e["other_row"] for e in report.checks["examples"] if e["check"] == "duplicate"] == [0, 3]


def test_generator_model_is_clean_and_ordered():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.gen.blockmodel import sub_chunks, sub_model

    n = 120_000
    grid = GridV2.of(sub_model(n))
    chk = ck.ModelChecker(grid, [{"name": "AU", "kind": "number"}])
    total = 0
    for p, s, w, values in sub_chunks(n, chunk_parents=10_000):
        chk.feed(p, s, w, values)
        total += len(p)
    report = chk.finish()
    assert n - 8 < total <= n and report.rows == total
    assert report.ok and not report.finding("duplicates") and report.checks["overlaps"]["n"] == 0
    assert report.checks["volume"] > 0
