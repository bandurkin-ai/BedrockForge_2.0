"""Swath plot: a series of a block model (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5; Э13, 13.11) — means, counts and
weights by swath against a direct numpy count on the centres and sizes of the cells (which the kernel never
builds): volume, tonnage, no weight; the variable step of OMF tensors; the selection by categories; the cuboid
by the cells and by `limits`; an azimuth of 90° as X bit for bit (ТД01-5); a turned frame against centres
turned beforehand (ТД01-6, 1e−9); 1, 2, 4, 8 threads and portions of a file — the same bits (`-m windows`);
2 million cells (Т7.30-6); no dense centres in memory.
"""

from __future__ import annotations

import math
import time
import tracemalloc
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import schema
from bf_core.stats import swath as sw
from bf_core.stats import swath_blocks as sb
from bf_core.stats import swath_frame as sf
from bf_core.stats.weighted import StatsError, pair_sums

CORNER = [512_000.0, 7_101_000.0, -350.0]  # UTM-like
LABELS = ["руда", "порода", "забаланс", "Отвал"]


def bits(a: Any, b: Any) -> bool:
    x, y = np.asarray(a), np.asarray(b)
    return x.shape == y.shape and x.dtype == y.dtype and x.tobytes() == y.tobytes()


def same(a: sw.SeriesResult, b: sw.SeriesResult) -> bool:
    return (
        bits(a.mean, b.mean) and bits(a.weight, b.weight) and bits(a.count, b.count)
        and a.total == b.total and a.counts == b.counts
    )  # fmt: skip


def regular(counts: tuple[int, int, int], size: tuple[float, float, float] = (10.0, 10.0, 5.0)) -> dict[str, Any]:
    return schema.check_grid({"corner": CORNER, "counts": list(counts), "size": list(size)})


def tensors(counts: tuple[int, int, int], seed: int = 2, wild: bool = False) -> dict[str, Any]:
    """The variable step of an OMF tensor grid: fine in the middle, coarse at the sides."""
    rng = np.random.default_rng(seed)
    steps = {}
    for axis, n in zip("xyz", counts, strict=True):
        s = rng.choice([2.5, 5.0, 5.0, 10.0, 20.0], n)
        if wild:  # cells of a centimetre next to cells of a kilometre
            s = s * 10.0 ** rng.integers(-2, 3, n)
        steps[axis] = s.tolist()
    return schema.check_grid({"corner": CORNER, "tensors": steps})


def cells(grid: dict[str, Any], share: float = 0.7, seed: int = 1):
    """The occupied cells of a model as its Parquet columns: `idx` ascending, a grade (empty cells, zeros,
    six orders of magnitude), a density (empty, zero and negative — no tonnage), a category (−1 — empty)."""
    rng = np.random.default_rng(seed)
    total = schema.cell_count(grid)
    idx = np.flatnonzero(rng.random(total) < share).astype(np.uint64)
    n = idx.size
    grade = rng.lognormal(0.0, 1.0, n) * 10.0 ** rng.integers(-3, 4, n)
    grade[rng.integers(0, n, n // 50)] = np.nan
    grade[rng.integers(0, n, n // 70)] = 0.0
    density = rng.normal(2.7, 0.2, n)
    density[rng.integers(0, n, n // 60)] = np.nan
    density[rng.integers(0, n, n // 90)] = 0.0
    density[rng.integers(0, n, n // 110)] = -1.0
    codes = rng.integers(-1, len(LABELS), n).astype(np.int32)
    return idx, grade, density, codes


def geometry(grid: dict[str, Any], idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """What BF kept for every block and BF2 does not: the centres and the sizes of the cells, n×3."""
    ijk = schema.ijk_of(idx, grid["counts"])
    sizes = np.column_stack([np.asarray(schema.steps(grid, a))[n.astype(np.intp)] for a, n in enumerate(ijk)])
    return schema.centers(grid, idx), sizes


def direct_bounds(f: sf.Frame, centres: np.ndarray, sizes: np.ndarray) -> np.ndarray:
    out = np.empty((3, 2))
    for e, axis in enumerate(f.axes):
        t, h = centres @ axis, 0.5 * (sizes @ np.abs(axis))
        out[e] = (t - h).min(), (t + h).max()
    return out


def direct(grid, idx, grade, f, c, weight="volume", density=None, mask=None):
    """The series counted on dense centres, one swath at a time: (Σ w, Σ w·v as `pair_sums` of the rows of the
    swath, the same as plain numpy sums, the number of rows) and the counts."""
    centres, sizes = geometry(grid, idx)
    volume = sizes[:, 0] * sizes[:, 1] * sizes[:, 2]
    w = {"volume": volume, "none": np.ones(len(idx))}[weight] if weight != "tonnage" else volume * density
    taken = np.ones(len(idx), dtype=bool) if mask is None else mask
    has = taken & np.isfinite(grade)
    with np.errstate(invalid="ignore"):
        ok = has & np.isfinite(w) & (w > 0)
    local = sf.to_frame(f, centres)  # the bits of `swath_frame.project`
    inside = ((local >= c.limits[:, 0] - c.tol) & (local <= c.limits[:, 1] + c.tol)).all(axis=1)
    k = np.clip(np.floor((local[:, 0] - c.start) / c.width), 0, c.count - 1).astype(np.int64)
    used = ok & inside
    n = c.count
    pair = np.zeros((n, 2))
    plain = np.zeros((n, 2))
    count = np.zeros(n, dtype=np.int64)
    for s in range(n):
        m = used & (k == s)
        count[s] = m.sum()
        pair[s] = pair_sums(np.ascontiguousarray(w[m]), np.ascontiguousarray(grade[m]))
        plain[s] = w[m].sum(), (w[m] * grade[m]).sum()
    counts = {
        "rows": len(idx), "filtered_out": int((~taken).sum()), "missing": int((taken & ~has).sum()),
        "bad_weight": int((has & ~ok).sum()), "selected": int(ok.sum()), "outside_box": int((ok & ~inside).sum()),
        "used": int(used.sum()),
    }  # fmt: skip
    return pair, plain, count, counts


def check(r: sw.SeriesResult, want: Any, exact: bool = True) -> None:
    """The answer against the direct count: bit for bit `pair_sums` of the rows of a swath when the model is
    one group of rows (`exact`), 1e−12 against plain numpy sums in any case; an empty swath has no mean."""
    pair, plain, count, counts = want
    assert r.counts == counts and (r.count == count).all()
    full = count > 0
    assert np.isnan(r.mean[~full]).all() and (r.weight[~full] == 0.0).all()
    assert np.allclose(r.weight, plain[:, 0], rtol=1e-12, atol=0)
    assert np.allclose(r.mean[full], plain[full, 1] / plain[full, 0], rtol=1e-12, atol=0)
    if exact:
        assert bits(r.weight, pair[:, 0]) and bits(r.mean[full], pair[full, 1] / pair[full, 0])
    assert r.total["count"] == counts["used"] and r.total["weight"] == math.fsum(r.weight.tolist())
    if counts["used"]:
        assert r.total["mean"] == pytest.approx(plain[:, 1].sum() / plain[:, 0].sum(), rel=1e-12)
    else:
        assert r.total["mean"] is None


def build(grid, idx, f, *, codes=None, keep=None, **kwargs) -> sw.Cuboid:
    """The cuboid as the API builds it: around the cells of the series, the width in whole blocks."""
    ext = sb.block_bounds(f, grid, idx, codes=codes, keep=keep)
    kwargs.setdefault("block", sf.block_side(f.u, sf.grid_sides(grid)))
    return sw.cuboid(f, models=[ext], **kwargs)


# --- against a direct count -----------------------------------------------------------------------------------


@pytest.mark.parametrize("angles", [(90.0, 0.0, 0.0), (0.0, 0.0, 0.0), (33.0, 90.0, 0.0), (120.0, -35.0, 20.0)])
def test_block_series_against_a_direct_count_on_200_thousand_cells(angles: tuple[float, float, float]) -> None:
    """Volume, tonnage and no weight on 200 thousand cells (one group of rows): the sums of a swath are
    `pair_sums` of its cells bit for bit — the centre of a cell is a[i] + b[j] + c[k], the bits of
    `swath_frame.project` of its dense centre; plain numpy sums agree to 1e−12."""
    grid = regular((90, 80, 40))
    idx, grade, density, _ = cells(grid, 0.72, seed=3)
    assert 200_000 <= idx.size <= sb.GROUP
    f = sf.frame(*angles)
    c = build(grid, idx, f)
    centres, sizes = geometry(grid, idx)
    scale = np.abs(direct_bounds(f, centres, sizes)).max()
    assert np.allclose(sb.block_bounds(f, grid, idx), direct_bounds(f, centres, sizes), rtol=0, atol=1e-12 * scale)
    assert 10 <= c.count <= sf.DEFAULT_SWATHS and c.width % 5.0 == 0.0 and not c.custom
    for weight in sb.WEIGHTS:
        dens = density if weight == "tonnage" else None
        r = sb.block_series(grid, idx, grade, f, c, weight=weight, density=dens)
        want = direct(grid, idx, grade, f, c, weight, dens)
        check(r, want)
        assert r.counts["outside_box"] == 0 and r.counts["filtered_out"] == 0 and r.counts["missing"] > 2000
        assert (r.counts["bad_weight"] > 3000) == (weight == "tonnage")
        if weight == "none":
            assert (r.weight == r.count).all()
        if weight == "volume":  # a regular step: the weight of a swath is the volume of its cells
            assert np.allclose(r.weight, r.count * 500.0, rtol=1e-12)
    # A narrow swath: a block goes whole into the swath of its centre, a part of the swaths is empty — not 0.
    if angles[:2] == (90.0, 0.0):
        narrow = build(grid, idx, f, width=2.5)
        r = sb.block_series(grid, idx, grade, f, narrow)
        check(r, direct(grid, idx, grade, f, narrow))
        d = r.as_dict()
        assert narrow.count == 360 and d["mean"][1::4] == [None] * 90 and d["count"][1::4] == [0] * 90
        assert all(m is not None for m in d["mean"][2::4]) and sw.width_note(narrow) is not None


@pytest.mark.parametrize("wild", [False, True])
def test_variable_step_of_omf_tensors(wild: bool) -> None:
    """The cells of a tensor grid differ in size: the volume of a cell is dx[i]·dy[j]·dz[k], the extent of the
    series takes the half of every cell; the default width is whole median blocks."""
    grid = tensors((60, 50, 30), wild=wild)
    idx, grade, density, _ = cells(grid, 0.8, seed=5)
    centres, sizes = geometry(grid, idx)
    for angles in ((90.0, 0.0, 0.0), (250.0, 25.0, -60.0)):
        f = sf.frame(*angles)
        want = direct_bounds(f, centres, sizes)
        got = sb.block_bounds(f, grid, idx)
        assert np.allclose(got, want, rtol=0, atol=1e-9 * max(1.0, np.abs(want).max()))
        c = build(grid, idx, f)
        side = sf.block_side(f.u, sf.grid_sides(grid))
        assert c.count <= sf.DEFAULT_SWATHS and c.block == side
        assert c.width / side == pytest.approx(round(c.width / side))
        for weight in sb.WEIGHTS:
            dens = density if weight == "tonnage" else None
            check(sb.block_series(grid, idx, grade, f, c, weight=weight, density=dens),
                  direct(grid, idx, grade, f, c, weight, dens))  # fmt: skip
    volume = sizes[:, 0] * sizes[:, 1] * sizes[:, 2]
    assert volume.max() / volume.min() > (1e6 if wild else 100)
    r = sb.block_series(grid, idx, np.ones(idx.size), sf.frame(90.0), build(grid, idx, sf.frame(90.0)))
    assert r.total["weight"] == pytest.approx(volume.sum(), rel=1e-12) and r.total["mean"] == 1.0


def test_selection_by_categories() -> None:
    """Only the cells whose category is among the chosen labels: the rest — and the cells without a category —
    are counted in `filtered_out`; the cuboid is around the cells taken; samples «inside the blocks» of the
    series see its selection."""
    grid = regular((40, 30, 20), (5.0, 5.0, 2.5))
    idx, grade, density, codes = cells(grid, 0.6, seed=7)
    i = schema.ijk_of(idx, grid["counts"])[0]
    codes[(i < 12) & (codes == 0)] = 1  # no «руда» in the west: the cuboid of the ore is shorter
    f = sf.direction_frame("x")
    keep = sb.keep_of(LABELS, ["руда", "Отвал", "нет такой"])
    assert keep.dtype == np.bool_ and keep.tolist() == [True, False, False, True]
    mask = np.isin(codes, [0, 3])
    c = build(grid, idx, f, codes=codes, keep=keep)
    ore = build(grid, idx, f, codes=codes, keep=sb.keep_of(LABELS, ["руда"]))
    assert c.start == CORNER[0] and ore.start == CORNER[0] + 60.0 and ore.count == 28 and c.count == 40
    centres, sizes = geometry(grid, idx)
    assert np.allclose(sb.block_bounds(f, grid, idx, codes=codes, keep=keep),
                       direct_bounds(f, centres[mask], sizes[mask]), rtol=0, atol=1e-9)  # fmt: skip
    for weight in sb.WEIGHTS:
        dens = density if weight == "tonnage" else None
        r = sb.block_series(grid, idx, grade, f, c, weight=weight, density=dens, codes=codes, keep=keep)
        check(r, direct(grid, idx, grade, f, c, weight, dens, mask))
        assert r.counts["filtered_out"] == int((~mask).sum()) > 0.4 * idx.size
    # The cuboid of the ore: the cells of the other labels are not in it at all, the ore outside it — counted.
    r = sb.block_series(grid, idx, grade, f, ore, codes=codes, keep=keep)
    check(r, direct(grid, idx, grade, f, ore, mask=mask))
    assert r.counts["outside_box"] > 500
    # The cells of the series for the samples «inside the blocks»: only those of the selection.
    taken = sb.selected_idx(idx, codes, keep)
    assert bits(taken, idx[mask]) and bits(sb.selected_idx(idx), idx)
    assert bits(sb.selected_idx(idx.astype(np.uint32), codes, keep), idx[mask].astype(np.uint32))
    inside = sw.points_in_cells(centres, sw.cells_of(grid, taken))
    assert (inside == mask).all()
    # Nothing chosen, nothing left: no extent (NaN — `cuboid` skips the series), every swath is empty.
    nothing = sb.keep_of(LABELS, [])
    assert np.isnan(sb.block_bounds(f, grid, idx, codes=codes, keep=nothing)).all()
    assert np.isnan(sb.block_bounds(f, grid, idx[:0])).all()
    r = sb.block_series(grid, idx, grade, f, c, codes=codes, keep=nothing)
    assert r.as_dict()["mean"] == [None] * c.count and r.counts["filtered_out"] == idx.size and r.counts["used"] == 0
    assert r.total == {"mean": None, "count": 0, "weight": 0.0}
    with pytest.raises(StatsError) as e:
        sw.cuboid(f, models=[sb.block_bounds(f, grid, idx, codes=codes, keep=nothing)])
    assert e.value.code == "no_data"
    # A code beyond the labels of the field is a cell without a category.
    odd = codes.copy()
    odd[:100] = 99
    r = sb.block_series(grid, idx, grade, f, c, codes=odd, keep=keep)
    check(r, direct(grid, idx, grade, f, c, mask=np.isin(odd, [0, 3])))


def test_limits_of_the_comparison_and_an_anchor() -> None:
    """The cuboid given by hand: a cell takes part when its centre is inside (the sides included, 1e−9); the
    rest is counted in `outside_box`."""
    grid = regular((40, 30, 20), (5.0, 5.0, 2.5))
    idx, grade, _, _ = cells(grid, 0.9, seed=9)
    for angles, lim in (
        ((90.0, 0.0, 0.0), [[CORNER[0] + 22.5, CORNER[0] + 147.5], [-CORNER[1] - 100.0, -CORNER[1] - 12.5],
                            [CORNER[2] + 10.0, CORNER[2] + 30.0]]),
        ((310.0, -20.0, 35.0), None),
    ):  # fmt: skip
        f = sf.frame(*angles)
        if lim is None:
            lim = sb.block_bounds(f, grid, idx) + [[40.0, -55.0], [30.0, -25.0], [8.0, -11.0]]
        c = sw.cuboid(f, limits=lim, width=12.5, anchor=3.0)
        assert c.custom
        r = sb.block_series(grid, idx, grade, f, c)
        want = direct(grid, idx, grade, f, c)
        check(r, want)
        assert 0.1 * idx.size < r.counts["outside_box"] < 0.9 * idx.size and r.counts["used"] > 3000
    # Centres on the sides of the cuboid are inside it: X of 22.5 … 147.5 holds the cells 4 … 29.
    f = sf.direction_frame("x")
    c = sw.cuboid(f, limits=[[CORNER[0] + 22.5, CORNER[0] + 147.5], [-1e9, 1e9], [-1e9, 1e9]], width=5.0)
    r = sb.block_series(grid, idx, grade, f, c, weight="none")
    i = schema.ijk_of(idx, grid["counts"])[0].astype(np.int64)
    ok = np.isfinite(grade)
    assert c.count == 25 and r.counts["used"] == int((ok & (i >= 4) & (i <= 29)).sum())
    assert r.count[0] == int((ok & (i == 4)).sum()) and r.count[-1] == int((ok & ((i == 28) | (i == 29))).sum())


# --- the frame ------------------------------------------------------------------------------------------------


def test_azimuth_90_is_x_bit_for_bit() -> None:
    """ТД01-5 on a model: the series by the azimuth 90° (dip = roll = 0) equals the series by X bit for bit,
    and both are the swaths of the X of the centres; Y is the azimuth 0°, Z — a dip of +90°."""
    grid = tensors((50, 40, 30))
    idx, grade, density, _ = cells(grid, 0.75, seed=11)
    answers = []
    for f in (sf.direction_frame("x"), sf.frame(90.0, 0.0, 0.0), sf.direction_frame("angles", 450.0), sf.frame(-270.0)):
        ext = sb.block_bounds(f, grid, idx)
        c = build(grid, idx, f, width=20.0, anchor=3.0)
        answers.append((ext, c, sb.block_series(grid, idx, grade, f, c, weight="tonnage", density=density)))
    ext0, c0, r0 = answers[0]
    for ext, c, r in answers[1:]:
        assert bits(ext, ext0) and (c.start, c.width, c.count) == (c0.start, c0.width, c0.count)
        assert bits(c.limits, c0.limits) and same(r, r0)
    centres, sizes = geometry(grid, idx)
    edges = [np.asarray(schema.edges(grid, a)) for a in range(3)]
    assert ext0[0].tolist() == [edges[0][0], edges[0][-1]] and ext0[2].tolist() == [edges[2][0], edges[2][-1]]
    w = sizes[:, 0] * sizes[:, 1] * sizes[:, 2] * density
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(grade) & np.isfinite(w) & (w > 0)
    k = sf.swath_index(centres[:, 0], c0.start, c0.width, c0.count)
    assert (k >= 0).all() and (np.bincount(k[ok], minlength=c0.count) == r0.count).all()
    for f, col in ((sf.frame(0.0), 1), (sf.frame(360.0), 1), (sf.frame(33.0, 90.0), 2), (sf.direction_frame("z"), 2)):
        c = build(grid, idx, f, width=15.0)
        r = sb.block_series(grid, idx, grade, f, c, weight="none")
        k = sf.swath_index(centres[:, col], c.start, c.width, c.count)
        assert c.start == edges[col][0] and (np.bincount(k[np.isfinite(grade)], minlength=c.count) == r.count).all()


def rotation(azimuth: float, dip: float, roll: float) -> np.ndarray:
    """The axes u, v, w (rows) built without the formulas of the core (as in test_swath.py): north and east
    turned about Z by the azimuth, tilted by the dip, rolled about the axis of the swaths."""

    def turn(axis: np.ndarray, deg: float, x: np.ndarray) -> np.ndarray:  # Rodrigues, the right-hand rule
        a, c, s = axis / np.linalg.norm(axis), np.cos(np.radians(deg)), np.sin(np.radians(deg))
        return x * c + np.cross(a, x) * s + a * (a @ x) * (1.0 - c)

    up = np.array([0.0, 0.0, 1.0])
    u0, h = turn(up, -azimuth, np.array([0.0, 1.0, 0.0])), turn(up, -azimuth, np.array([1.0, 0.0, 0.0]))
    u, n = turn(h, dip, u0), turn(h, dip, up)
    return np.array([u, turn(u, -roll, h), turn(u, -roll, n)])


WIDTH = 17.3  # no centre of a cell on a boundary of the swaths: the two roads differ in the last bits
TURNED = [(120.0, -35.0, 20.0), (300.0, 60.0, -110.0), (37.5, 0.0, 0.0), (10.0, -89.999, 45.0), (200.0, 90.0, 30.0),
          (0.0, 15.0, 180.0)]  # fmt: skip


@pytest.mark.parametrize("angles", TURNED)
@pytest.mark.parametrize("variable", [False, True])
def test_a_turned_frame_equals_centres_turned_beforehand(angles: tuple[float, float, float], variable: bool) -> None:
    """ТД01-6 on a model: a swath with a dip and a roll = the swath along the first coordinate of the centres
    of the cells turned into the frame of the cuboid beforehand (dense centres, three plain turns), 1e−9."""
    grid = tensors((40, 36, 24), seed=4) if variable else regular((40, 36, 24), (10.0, 5.0, 2.5))
    idx, grade, density, _ = cells(grid, 0.65, seed=13)
    f = sf.frame(*angles)
    ext = sb.block_bounds(f, grid, idx)
    c = sw.cuboid(f, models=[ext], width=WIDTH)
    r = sb.block_series(grid, idx, grade, f, c, weight="tonnage", density=density)
    centres, sizes = geometry(grid, idx)
    rot = rotation(*angles)
    local = centres @ rot.T  # (p·u, p·v, p·w) by another road
    half = 0.5 * (sizes @ np.abs(rot).T)
    ext_x = np.column_stack([(local - half).min(axis=0), (local + half).max(axis=0)])
    scale = max(1.0, np.abs(ext_x).max())
    assert np.allclose(ext, ext_x, rtol=0, atol=1e-9 * scale)
    c_x = sw.cuboid(f, models=[ext_x], width=WIDTH)
    assert c_x.count == c.count and c_x.start == pytest.approx(c.start, abs=1e-9 * scale)
    w = sizes[:, 0] * sizes[:, 1] * sizes[:, 2] * density
    r_x = sw.swath_means(local[:, 0], grade, w, c_x.start, c_x.width, c_x.count)
    assert (r_x.count == r.count).all() and r.counts["used"] == r_x.counts["used"] > 15_000
    assert r.counts["outside_box"] == r_x.counts["outside_box"] == 0
    assert np.allclose(r_x.mean, r.mean, rtol=1e-9, atol=0, equal_nan=True)
    assert np.allclose(r_x.weight, r.weight, rtol=1e-9, atol=0)
    assert r_x.total["mean"] == pytest.approx(r.total["mean"], rel=1e-9)
    # The same in a cuboid given by hand: what is outside it is outside in both.
    span = c.limits[:, 1] - c.limits[:, 0]
    lim = c.limits + span[:, None] * [[0.21, -0.32], [0.13, -0.17], [0.07, -0.11]]
    a = sb.block_series(grid, idx, grade, f, sw.cuboid(f, limits=lim, width=WIDTH), weight="tonnage", density=density)
    tol = 1e-9 * np.maximum(1.0, np.abs(lim).max(axis=1))
    inside = ((local >= lim[:, 0] - tol) & (local <= lim[:, 1] + tol)).all(axis=1)
    cl = sw.cuboid(f, limits=lim, width=WIDTH)
    b = sw.swath_means(local[inside, 0], grade[inside], w[inside], cl.start, cl.width, cl.count)
    assert (a.count == b.count).all() and 0 < a.counts["outside_box"] < a.counts["selected"]
    assert np.allclose(a.mean, b.mean, rtol=1e-9, atol=0, equal_nan=True)


# --- threads, groups of rows, portions of a file ----------------------------------------------------------------


@pytest.mark.windows
def test_threads_and_portions_give_the_same_bits(monkeypatch: pytest.MonkeyPatch) -> None:
    """1, 2, 4 and 8 threads — the same bits (ADR 0019 §5): a group of rows is summed by one thread in the
    order of its rows, the groups are added pairwise in their order. The columns handed over in portions (the
    row groups of the Parquet file, however they are cut) give the same bits as the whole columns."""
    grid = tensors((120, 100, 60), seed=6)
    idx, grade, density, codes = cells(grid, 0.9, seed=17)
    grade *= 10.0 ** np.random.default_rng(1).integers(-8, 9, idx.size)  # sums that feel their order
    keep = sb.keep_of(LABELS, LABELS[:3])
    f = sf.frame(70.0, -25.0, 40.0)
    assert idx.size > 2 * sb.GROUP  # three groups of rows at least
    ext = sb.block_bounds(f, grid, idx, codes=codes, keep=keep)
    c = sw.cuboid(f, models=[ext], width=30.0)
    args = {"weight": "tonnage", "density": density, "codes": codes, "keep": keep}
    base = sb.block_series(grid, idx, grade, f, c, **args)
    assert base.counts["used"] > 300_000
    for threads in (2, 4, 8):
        assert bits(sb.block_bounds(f, grid, idx, codes=codes, keep=keep, threads=threads), ext), threads
        assert same(sb.block_series(grid, idx, grade, f, c, threads=threads, **args), base), threads
    # Small groups: dozens of them on the threads, the last one short.
    monkeypatch.setattr(sb, "GROUP", 10_007)
    small = sb.block_series(grid, idx, grade, f, c, **args)
    assert not bits(small.weight, base.weight)  # other groups — other bits, the same numbers
    assert np.allclose(small.weight, base.weight, rtol=1e-12) and np.allclose(small.mean, base.mean, rtol=1e-12)
    assert (small.count == base.count).all() and small.counts == base.counts
    for threads in (2, 4, 8):
        assert same(sb.block_series(grid, idx, grade, f, c, threads=threads, **args), small), threads
    # Portions: row groups of a file, uneven, down to a single row and an empty one.
    for threads, cuts in ((1, [0, 1, 1, 9_000, 10_007, 10_008, 250_000, 250_001, 600_000]), (4, [333_333, 444_444])):
        acc = sb.BlockSums(grid, f, c, weight="tonnage", keep=keep, threads=threads)
        ends = sorted({*cuts, idx.size})
        for a, b in zip([0, *ends[:-1]], ends, strict=True):
            acc.add(idx[a:b], grade[a:b], density=density[a:b], codes=codes[a:b])
        assert same(acc.result(), small), threads
        box = sb.BlockExtent(f, grid, keep=keep, threads=threads)
        for a, b in zip([0, *ends[:-1]], ends, strict=True):
            box.add(idx[a:b], codes[a:b])
        assert bits(box.result(), ext)
    # The integer type of `idx` (uint64 of the file, uint32 of the cache of columns) does not matter.
    for dtype in (np.uint32, np.int64):
        assert same(sb.block_series(grid, idx.astype(dtype), grade, f, c, threads=4, **args), small), dtype
        assert bits(sb.block_bounds(f, grid, idx.astype(dtype), codes=codes, keep=keep), ext)


def test_two_million_cells_and_no_dense_centres() -> None:
    """Т7.30-6 (the share of pytest: 2 million cells; 20 million — bench `swath_blocks`): the swath of a model
    is built from its columns; the centres and sizes of the cells are never made — the peak of memory above
    the columns is far below the 48 MB of dense centres."""
    grid = regular((200, 100, 100))
    total = schema.cell_count(grid)
    assert total == 2_000_000
    rng = np.random.default_rng(23)
    idx = np.arange(total, dtype=np.uint32)  # the cache of columns: uint32
    grade = rng.lognormal(-1.0, 1.0, total)
    grade[rng.integers(0, total, 20_000)] = np.nan
    density = rng.normal(2.7, 0.05, total)
    codes = rng.integers(0, 3, total).astype(np.int32)
    keep = np.array([True, False, True])
    for column in (idx, grade, density, codes):
        column.setflags(write=False)  # as the cache of columns gives them: the warm-up compiled for these
    f = sf.frame(40.0, -30.0, 15.0)
    sb.warm()
    tracemalloc.start()
    t0 = time.perf_counter()
    ext = sb.block_bounds(f, grid, idx, threads=2)
    c = sw.cuboid(f, models=[ext], block=sf.block_side(f.u, sf.grid_sides(grid)))
    ticks: list[float] = []
    by_volume = sb.block_series(grid, idx, grade, f, c, threads=2, tick=ticks.append)
    by_tonnage = sb.block_series(
        grid, idx, grade, f, c, weight="tonnage", density=density, codes=codes, keep=keep, threads=2
    )
    seconds = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 12e6, f"{peak / 1e6:.1f} MB above the columns"
    assert seconds < 5.0, f"{seconds:.2f} s"  # ~0.05 s; the budget is that of the bench
    assert ticks == sorted(ticks) and ticks[-1] == 1.0 and len(ticks) >= 4
    assert c.count <= sf.DEFAULT_SWATHS and c.width % 10.0 == 0.0
    # Against numpy on the dense centres (made here, after the measurement).
    t = sf.project(schema.centers(grid, idx), f.u)
    k = np.clip(np.floor((t - c.start) / c.width), 0, c.count - 1).astype(np.int64)
    ok = np.isfinite(grade)
    assert (np.bincount(k[ok], minlength=c.count) == by_volume.count).all() and by_volume.counts["used"] == ok.sum()
    mean = np.bincount(k[ok], weights=grade[ok], minlength=c.count) / np.bincount(k[ok], minlength=c.count)
    assert np.allclose(by_volume.mean, mean, rtol=1e-12, atol=0) and by_volume.counts["outside_box"] == 0
    assert np.allclose(by_volume.weight, by_volume.count * 500.0, rtol=1e-12)
    taken = ok & (codes != 1)
    w = 500.0 * density[taken]
    want = np.bincount(k[taken], weights=w * grade[taken], minlength=c.count) / np.bincount(
        k[taken], weights=w, minlength=c.count
    )
    assert np.allclose(by_tonnage.mean, want, rtol=1e-12, atol=0)
    assert by_tonnage.counts["filtered_out"] == int((codes == 1).sum()) and by_tonnage.counts["used"] == taken.sum()
    dev = sw.deviation(by_tonnage.mean, by_volume.mean)
    assert np.isfinite(dev).all() and np.median(np.abs(dev)) < 2.0  # the same grades, another weight, a selection


# --- refusals, progress -------------------------------------------------------------------------------------------


def test_refusals_progress_and_warm() -> None:
    grid = regular((30, 20, 10))
    idx, grade, density, codes = cells(grid, 0.8, seed=19)
    f = sf.frame(15.0, 5.0, 0.0)
    c = build(grid, idx, f)
    keep = sb.keep_of(LABELS, LABELS)
    for kwargs, param in (
        ({"weight": "mass"}, "weight"),
        ({"weight": "tonnage"}, "density"),  # the tonnage needs the field of the density
    ):
        with pytest.raises(StatsError) as e:
            sb.block_series(grid, idx, grade, f, c, **kwargs)  # type: ignore[arg-type]
        assert e.value.code == "bad_param" and e.value.param == param and e.value.message and e.value.hint
    for bad in (idx[:-1], idx.astype(np.float64), idx.reshape(-1, 1)):  # the columns of one model are of one length
        with pytest.raises(ValueError):
            sb.block_series(grid, bad, grade, f, c)
    with pytest.raises(ValueError):
        sb.block_series(grid, idx, grade, f, c, weight="tonnage", density=density[:-1])
    with pytest.raises(ValueError):
        sb.block_bounds(f, grid, idx, codes=codes[:-1], keep=keep)
    for half in ({"codes": codes}, {"keep": keep}):  # a selection is the column and the labels chosen together
        with pytest.raises(ValueError):
            sb.block_series(grid, idx, grade, f, c, **half)
    # Numbers of cells that are not of this grid (a model written against another grid): a refusal, not a crash.
    beyond = idx.copy()
    beyond[-1] = schema.cell_count(grid)
    for broken in (beyond, np.array([-5, 3], dtype=np.int64), np.array([2**63 + 5], dtype=np.uint64)):
        n = broken.size
        with pytest.raises(StatsError) as e:
            sb.block_series(grid, broken, grade[:n], f, c)
        assert e.value.code == "bad_param" and "сетк" in e.value.message and e.value.hint
        with pytest.raises(StatsError) as e:
            sb.block_bounds(f, grid, broken)
        assert e.value.code == "bad_param"
    # Rows out of order are still the cells they name: the lookup of (i, j, k) does not lean on the order.
    order = np.random.default_rng(2).permutation(idx.size)
    shuffled = sb.block_series(grid, idx[order], grade[order], f, c)
    straight = sb.block_series(grid, idx, grade, f, c)
    assert (shuffled.count == straight.count).all() and np.allclose(
        shuffled.mean, straight.mean, rtol=1e-12, atol=0, equal_nan=True
    )
    assert bits(sb.block_bounds(f, grid, idx[order]), sb.block_bounds(f, grid, idx))
    # Progress rises to 1 on any number of threads; `tick` may raise to cancel.
    for threads in (1, 3):
        seen: list[float] = []
        sb.block_series(grid, idx, grade, f, c, threads=threads, tick=seen.append)
        assert seen == sorted(seen) and seen[-1] == 1.0

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    for threads in (1, 4):
        with pytest.raises(Stop):
            sb.block_series(grid, idx, grade, f, c, threads=threads, tick=stop)
    # A model without cells: nothing to draw, nothing is 0 but the counts.
    none = sb.block_series(grid, idx[:0], grade[:0], f, c)
    assert none.as_dict()["mean"] == [None] * c.count and none.counts["rows"] == 0 and none.total["mean"] is None
    sb.warm()
