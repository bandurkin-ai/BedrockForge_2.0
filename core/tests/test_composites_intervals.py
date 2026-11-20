"""The interval engine (ADR 0018 §4–5, подшаг 12.1): lines, overlay, runs, targets, aggregate, mode.

Tolerances are written at every check: boundaries and counts — exact; weighted means — 1e−12 relative against
`math.fsum`; the order of summation — bit for bit against the reference `cascade`; threads — bit for bit."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import intervals as iv
from bf_core.composites import strips

NAN = math.nan


def cascade(terms: list[float]) -> float:
    """The reference of the kernels' summation: blocks of 8 terms left to right, the blocks combined as a
    binary counter, what is left folded from the last block to the first."""
    stack: list[float] = []
    block = 0.0
    k = 0
    for x in terms:
        block += x
        k += 1
        if k % 8 == 0:
            m = k // 8
            while m % 2 == 0:
                block = stack.pop() + block
                m //= 2
            stack.append(block)
            block = 0.0
    while stack:
        block = stack.pop() + block
    return block


def one_hole(frm: list[float], to: list[float], hole: int = 1) -> iv.Lines:
    return iv.build_lines([hole] * len(frm), frm, to)


def col(*v: float) -> np.ndarray:
    return np.array(v, dtype=np.float64).reshape(-1, 1)


def _spans(frm: list[float], to: list[float], hole: int = 1) -> iv.Lines:
    """Targets of one hole as they are: in any order, overlapping (`build_lines` would drop the overlaps)."""
    return iv.Lines(np.array([hole]), np.array([0, len(frm)]), np.array(frm), np.array(to))


# --- lines -------------------------------------------------------------------------------------------------


def test_lines_order_bad_rows_and_overlaps_are_counted() -> None:
    #        row: 0     1     2     3     4      5     6     7
    hole = [20, 10, 10, 20, 10, 10, 20, 10]
    frm = [5.0, 2.0, 0.0, 0.0, 1.5, 4.0, NAN, 3.0]
    to = [9.0, 3.0, 2.0, 5.0, 2.5, 4.0, 1.0, 3.5]
    lines = iv.build_lines(hole, frm, to)
    # hole 10: 0–2 (row 2), 1.5–2.5 overlaps → out, 2–3 (row 1), 3–3.5 (row 7), 4–4 → bad; hole 20: rows 3, 0
    assert lines.holes.tolist() == [10, 20]
    assert lines.starts.tolist() == [0, 3, 5]
    assert lines.rows is not None and lines.rows.tolist() == [2, 1, 7, 3, 0]
    assert lines.frm.tolist() == [0.0, 2.0, 3.0, 0.0, 5.0]
    assert lines.to.tolist() == [2.0, 3.0, 3.5, 5.0, 9.0]
    assert (lines.n_source, lines.n_bad, lines.n_overlap) == (8, 2, 1)
    assert lines.take(np.arange(8) * 10.0).tolist() == [20.0, 10.0, 70.0, 30.0, 0.0]
    assert lines.hole_ids().tolist() == [10, 10, 10, 20, 20]
    notes = lines.notes("Пробы")
    assert "— 2," in notes[0] and "— 1," in notes[1] and "Аудит" in notes[2]
    assert one_hole([0.0, 1.0], [1.0, 2.0]).notes("Пробы") == []


def test_lines_sorted_input_is_taken_as_is_and_float_noise_is_not_an_overlap() -> None:
    frm = np.array([0.0, 0.1 + 0.2, 0.6])  # 0.30000000000000004 after TO = 0.3: noise, not an overlap
    to = np.array([0.3, 0.6, 0.9])
    frm2 = np.array([0.0, 0.3, 0.6])
    to2 = np.array([0.1 + 0.2, 0.6, 0.9])  # FROM 0.3 below the previous TO by 5.6e−17
    a = iv.build_lines([1, 1, 1], frm, to)
    b = iv.build_lines([1, 1, 1], frm2, to2)
    assert a.rows is None and b.rows is None and a.n_overlap == 0 and b.n_overlap == 0
    assert b.frm[1] == to2[0] and frm2[1] == 0.3  # moved to the previous TO; the caller's array is intact
    assert np.all(b.frm[1:] >= b.to[:-1])
    real = iv.build_lines([1, 1], [0.0, 0.299], [0.3, 0.6])  # 1 mm is an overlap
    assert real.n_overlap == 1 and real.n == 1


def test_lines_equal_rows_are_settled_by_ids() -> None:
    hole, frm, to = [1, 1, 1], [0.0, 0.0, 2.0], [2.0, 2.0, 4.0]
    a = iv.build_lines(hole, frm, to, ids=[7, 5, 9])
    assert a.rows is not None and a.rows.tolist() == [1, 2] and a.n_overlap == 1  # id 5 is kept, id 7 overlaps
    b = iv.build_lines(hole, frm, to, ids=[5, 7, 9])
    assert b.rows is not None and b.rows.tolist() == [0, 2]
    empty = iv.build_lines([], [], [])
    assert empty.n == 0 and empty.n_holes == 0 and empty.starts.tolist() == [0]


def test_empty_tables_go_through_every_kernel() -> None:
    empty, some = iv.build_lines([], [], []), one_hole([0.0, 1.0], [1.0, 2.0])
    assert iv.overlay(empty, empty).lines.n == 0 and iv.overlay(some, empty).ia.tolist() == [0, 1]
    assert iv.overlay(empty, some, "left").lines.n == 0 and iv.overlay_many([some])[1][0].tolist() == [0, 1]
    assert iv.runs(empty, []).lines.n == 0 and iv.targets(empty, 2.0).lines.n == 0
    ag = iv.aggregate(some, col(1.0, 2.0), empty)
    assert ag.values.shape == (0, 1) and ag.count.shape == (0,)
    assert np.isnan(iv.aggregate(empty, np.empty((0, 2)), some).values).all()
    assert iv.mode_by_length(empty, [], some).tolist() == [-1, -1] and iv.stack_fields(empty, []).shape == (0, 0)
    with pytest.raises(ValueError):
        iv.aggregate(some, col(1.0), some)  # values are not in line order


# --- overlay -----------------------------------------------------------------------------------------------


def test_overlay_of_two_sets() -> None:
    """Т8.2-8 in the kernel: 0–2, 2–5 and 1–4 → 0–1, 1–2, 2–4, 4–5 with the covering rows."""
    a, b = one_hole([0.0, 2.0], [2.0, 5.0]), one_hole([1.0], [4.0])
    ov = iv.overlay(a, b)
    assert ov.lines.frm.tolist() == [0.0, 1.0, 2.0, 4.0] and ov.lines.to.tolist() == [1.0, 2.0, 4.0, 5.0]
    assert ov.ia.tolist() == [0, 0, 1, 1] and ov.ib.tolist() == [-1, 0, 0, -1]
    left = iv.overlay(b, a, "left")
    assert left.lines.frm.tolist() == [1.0, 2.0] and left.ia.tolist() == [0, 0] and left.ib.tolist() == [0, 1]
    inner = iv.overlay(a, b, "inner")
    assert inner.lines.frm.tolist() == [1.0, 2.0] and inner.lines.to.tolist() == [2.0, 4.0]


def test_overlay_gaps_holes_of_one_set_and_slivers() -> None:
    # hole 1: a 0–1, 3–4; b 0.5–3.5; hole 2 only in a; hole 3 only in b
    a = iv.build_lines([1, 1, 2], [0.0, 3.0, 0.0], [1.0, 4.0, 2.0])
    b = iv.build_lines([1, 3], [0.5, 1.0], [3.5, 2.0])
    ov = iv.overlay(a, b)
    assert ov.lines.holes.tolist() == [1, 2, 3] and ov.lines.starts.tolist() == [0, 5, 6, 7]
    assert ov.lines.frm.tolist() == [0.0, 0.5, 1.0, 3.0, 3.5, 0.0, 1.0]
    assert ov.lines.to.tolist() == [0.5, 1.0, 3.0, 3.5, 4.0, 2.0, 2.0]
    assert ov.ia.tolist() == [0, 0, -1, 1, 1, 2, -1] and ov.ib.tolist() == [-1, 0, 0, 0, -1, -1, 1]
    left = iv.overlay(a, b, "left")  # the gap 1–3 of a is not a sub-interval; hole 3 is gone
    assert left.lines.holes.tolist() == [1, 2] and left.lines.frm.tolist() == [0.0, 0.5, 3.0, 3.5, 0.0]
    inner = iv.overlay(a, b, "inner")
    assert inner.lines.holes.tolist() == [1] and inner.lines.frm.tolist() == [0.5, 3.0]
    # boundaries 5.6e−17 apart are one boundary: no sliver
    s = iv.overlay(one_hole([0.0, 0.3], [0.3, 1.0]), one_hole([0.0, 0.1 + 0.2], [0.1 + 0.2, 1.0]))
    assert s.lines.n == 2 and s.ia.tolist() == [0, 1] and s.ib.tolist() == [0, 1]


def test_overlay_many_folds_three_tables() -> None:
    a, b, c = one_hole([0.0, 2.0], [2.0, 5.0]), one_hole([1.0], [4.0]), one_hole([0.0, 3.0], [3.0, 6.0])
    lines, index = iv.overlay_many([a, b, c])
    assert lines.frm.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert lines.to.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert [ix.tolist() for ix in index] == [[0, 0, 1, 1, 1, -1], [-1, 0, 0, 0, -1, -1], [0, 0, 0, 1, 1, 1]]


def test_overlay_against_brute_force() -> None:
    rng = np.random.default_rng(12345)
    for _ in range(40):
        sets = []
        for _k in range(2):
            cuts = np.unique(rng.integers(0, 60, size=rng.integers(2, 20))).astype(np.float64) / 2.0
            keep = rng.random(len(cuts) - 1) < 0.7
            sets.append((cuts[:-1][keep], cuts[1:][keep]))
        (af, at), (bf, bt) = sets
        ov = iv.overlay(one_hole(list(af), list(at)), one_hole(list(bf), list(bt)))
        edges = np.unique(np.concatenate([af, at, bf, bt]))
        want = []
        for s, e in zip(edges[:-1], edges[1:], strict=True):
            ia = next((i for i in range(len(af)) if af[i] <= s and at[i] >= e), -1)
            ib = next((i for i in range(len(bf)) if bf[i] <= s and bt[i] >= e), -1)
            if ia >= 0 or ib >= 0:
                want.append((s, e, ia, ib))
        got = list(zip(ov.lines.frm.tolist(), ov.lines.to.tolist(), ov.ia.tolist(), ov.ib.tolist(), strict=True))
        assert got == want


# --- runs --------------------------------------------------------------------------------------------------


def test_runs_by_code_and_gap() -> None:
    #                0    1    2    3    4    5    6     hole 2: 7    8
    lines = iv.build_lines([1] * 7 + [2] * 2, [0, 1, 2, 3, 5, 6, 7, 0, 1], [1, 2, 3, 4, 6, 7, 8, 1, 2])
    codes = [3, 3, 1, 1, 1, -1, 3, 1, 1]
    r = iv.runs(lines, codes)
    assert r.lines.starts.tolist() == [0, 5, 6]
    assert r.lines.frm.tolist() == [0.0, 2.0, 5.0, 6.0, 7.0, 0.0]
    assert r.lines.to.tolist() == [2.0, 4.0, 6.0, 7.0, 8.0, 2.0]
    assert r.code.tolist() == [3, 1, 1, -1, 3, 1]  # the gap 4–5 breaks the run of code 1
    assert r.first.tolist() == [0, 2, 4, 5, 6, 7] and r.last.tolist() == [2, 4, 5, 6, 7, 9]
    bridged = iv.runs(lines, codes, gap=math.inf)
    assert bridged.lines.frm.tolist() == [0.0, 2.0, 6.0, 7.0, 0.0]
    assert bridged.lines.to.tolist() == [2.0, 6.0, 7.0, 8.0, 2.0]
    grade = np.array([0.2, 1.5, 1.0, NAN, 0.4, 2.0, 3.0, 0.1, 0.1])  # by a threshold: g ≥ 1 → 1, NaN → −1
    by_cutoff = np.where(np.isnan(grade), -1, grade >= 1.0).astype(np.int32)
    assert iv.runs(lines, by_cutoff).code.tolist() == [0, 1, -1, 0, 1, 0]


# --- targets -----------------------------------------------------------------------------------------------


def cuts(a: float, b: float, length: float, **kw: object) -> tuple[list[float], list[float], iv.Targets]:
    t = iv.targets(one_hole([a], [b]), length, **kw)  # type: ignore[arg-type]
    return t.lines.frm.tolist(), t.lines.to.tolist(), t


def test_targets_remainder_and_equal() -> None:
    """Т8.2-3 in the kernel: 7 m, L = 2 — the remainder of 1 m is a composite; 6.8 m — 4–6.8; equal — 4 × 1.75."""
    assert cuts(0.0, 7.0, 2.0)[:2] == ([0.0, 2.0, 4.0, 6.0], [2.0, 4.0, 6.0, 7.0])
    assert cuts(0.0, 6.8, 2.0)[:2] == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.8])
    assert cuts(0.0, 7.0, 2.0, rule="equal")[:2] == ([0.0, 1.75, 3.5, 5.25], [1.75, 3.5, 5.25, 7.0])
    f, t, tg = cuts(0.0, 6.8, 2.0, attach=False)
    assert (f, t) == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])
    assert tg.n_cut == 1 and tg.len_cut == pytest.approx(0.8, abs=1e-12)
    assert cuts(0.0, 6.0, 2.0)[:2] == ([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])
    assert cuts(10.0, 10.0 + 0.1 * 3, 0.1)[1][-1] == 10.0 + 0.1 * 3  # 0.30000000000000004 / 0.1: n0 = 3, no sliver
    assert len(cuts(0.0, 0.3, 0.1)[0]) == 3  # 0.3 / 0.1 = 2.9999999999999996: n0 = 3 by the 1e−9
    assert cuts(0.0, 0.9, 2.0, rule="equal")[:2] == ([0.0], [0.9])  # n = max(1, floor(0.45 + 0.5))


def test_targets_short_stretch_goes_to_the_log() -> None:
    f, t, tg = cuts(3.0, 3.9, 2.0)  # n0 = 0, r = 0.9 < 0.5·2
    assert f == [] and tg.lines.n_holes == 0 and tg.n_short == 1 and tg.len_short == pytest.approx(0.9, abs=1e-12)
    f, t, tg = cuts(3.0, 4.0, 2.0)  # r = f_min·L exactly: a composite of its own
    assert (f, t) == ([3.0], [4.0]) and tg.n_short == 0
    assert cuts(3.0, 3.9, 2.0, f_min=0.0)[:2] == ([3.0], [3.9])


def test_targets_grid_from_an_origin() -> None:
    # stretch 3–12, grid from the collar (0), L = 2: boundaries 4, 6, 8, 10, 12; the lead 3–4 = f_min·L
    assert cuts(3.0, 12.0, 2.0, origin=[0.0])[:2] == ([3.0, 4.0, 6.0, 8.0, 10.0], [4.0, 6.0, 8.0, 10.0, 12.0])
    # lead 3.4–4 (0.6 < 1) joins the next composite; the tail 12–12.5 joins the last one
    assert cuts(3.4, 12.5, 2.0, origin=[0.0])[:2] == ([3.4, 6.0, 8.0, 10.0], [6.0, 8.0, 10.0, 12.5])
    f, t, tg = cuts(3.4, 12.5, 2.0, origin=[0.0], attach=False)
    assert (f, t) == ([4.0, 6.0, 8.0, 10.0], [6.0, 8.0, 10.0, 12.0]) and tg.n_cut == 2
    assert tg.len_cut == pytest.approx(1.1, abs=1e-12)
    # origin below the start (the top of the first sample at 5): the grid goes up too — 1, 3, 5, …
    assert cuts(0.5, 6.0, 2.0, origin=[5.0])[:2] == ([0.5, 3.0, 5.0], [3.0, 5.0, 6.0])
    # no boundary inside: one composite when long enough, else the log
    assert cuts(4.2, 5.6, 2.0, origin=[0.0])[:2] == ([4.2], [5.6])
    assert cuts(4.2, 4.9, 2.0, origin=[0.0])[2].n_short == 1
    # one boundary, two short pieces: 3.3–4 and 4–4.6 make one composite of 1.3 m; dropped — nothing
    assert cuts(3.3, 4.6, 2.0, origin=[0.0])[:2] == ([3.3], [4.6])
    assert cuts(3.3, 4.6, 2.0, origin=[0.0], attach=False)[2].n_short == 1
    assert cuts(3.0, 5.2, 2.0, origin=[0.0])[:2] == ([3.0, 4.0], [4.0, 5.2])  # both pieces ≥ 1 m
    # the origin on the start, NaN and "equal" — the plain rule
    assert cuts(4.0, 9.0, 2.0, origin=[0.0])[:2] == cuts(4.0, 9.0, 2.0)[:2] == cuts(4.0, 9.0, 2.0, origin=[NAN])[:2]
    assert cuts(3.0, 10.0, 2.0, origin=[0.0], rule="equal")[:2] == cuts(3.0, 10.0, 2.0, rule="equal")[:2]


def test_targets_keep_the_stretch_and_tile_it() -> None:
    rng = np.random.default_rng(12345)
    hole = np.repeat(np.arange(300), 3)
    a = np.tile([0.0, 40.0, 90.0], 300) + rng.random(900) * 5.0
    b = a + rng.random(900) * 30.0 + 0.05
    st = iv.build_lines(hole, a, b)
    for kw in ({}, {"rule": "equal"}, {"origin": np.zeros(900)}):
        tg = iv.targets(st, 2.0, **kw)  # type: ignore[arg-type]
        f, t, s = tg.lines.frm, tg.lines.to, tg.stretch
        assert np.all(t > f) and np.all(np.diff(s) >= 0)
        assert np.array_equal(tg.lines.hole_ids(), st.hole_ids()[s])
        same = s[1:] == s[:-1]
        assert np.all(f[1:][same] == t[:-1][same])  # no gaps and no overlaps inside a stretch
        first = np.concatenate(([True], ~same))
        last = np.concatenate((~same, [True]))
        assert np.all(f[first] == st.frm[s[first]]) and np.all(t[last] == st.to[s[last]])  # attached: all of it
        assert tg.n_cut == 0 and tg.n_short == 900 - len(np.unique(s))


# --- aggregate ---------------------------------------------------------------------------------------------


def test_aggregate_gaps_and_missing_values() -> None:
    """Т8.2-1 / Т8.2-2 numbers in the kernel; a missing value takes no part and does not become 0."""
    lines = one_hole([0.0, 1.0, 3.0, 4.0], [1.0, 3.0, 4.0, 6.0])
    vals = np.array([[1.0, 10.0], [2.0, NAN], [NAN, NAN], [4.0, NAN]])
    rho = np.array([2.5, 3.0, NAN, NAN])
    tg = one_hole([0.0, 2.0, 4.0], [2.0, 4.0, 6.0])
    ag = iv.aggregate(lines, vals, tg, density=rho, p_min=0.5)
    assert ag.values[:, 0].tolist() == [1.5, 2.0, 4.0]
    assert ag.values[0, 1] == 10.0 and np.isnan(ag.values[1:, 1]).all()  # 1 m of 2 with a value: p_min passes
    assert ag.sampled.tolist() == [2.0, 1.0, 2.0] and ag.count.tolist() == [2, 1, 1]
    assert ag.low.tolist() == [False, True, True]  # field 2 has nothing in 2–4 and 4–6
    assert ag.density[0] == 2.75 and ag.density[1] == 3.0 and math.isnan(ag.density[2])
    w = iv.aggregate(lines, vals, tg, weight=rho, density=rho, p_min=0.5)
    assert w.values[0, 0] == pytest.approx(8.5 / 5.5, rel=1e-12)
    assert math.isnan(w.values[2, 0])  # the sample 4–6 has no density: it cannot be weighted, not «ρ = 0»
    strict = iv.aggregate(lines, vals[:, :1], tg, p_min=0.75)
    assert math.isnan(strict.values[1, 0]) and strict.low.tolist() == [False, True, False]
    zero = iv.aggregate(lines, vals[:, :1], tg, p_min=0.75, fill=0.0)  # unsampled 3–4 counts as 0
    assert zero.values[:, 0].tolist() == [1.5, 1.0, 4.0] and zero.low.tolist() == [False, True, False]
    assert iv.aggregate(lines, vals[:, :1], tg, fill=0.5).values[1, 0] == 1.25
    other = iv.aggregate(lines, vals, iv.build_lines([1, 9], [6.0, 0.0], [8.0, 2.0]), fill=0.25)
    assert np.isnan(iv.aggregate(lines, vals, iv.build_lines([9], [0.0], [2.0])).values).all()  # no such hole
    assert other.values.tolist() == [[0.25, 0.25], [0.25, 0.25]] and other.count.tolist() == [0, 0]
    none = iv.aggregate(lines, np.empty((4, 0)), tg, p_min=0.9)  # no fields: the cover of the rows
    assert none.values.shape == (3, 0) and none.sampled.tolist() == [2.0, 2.0, 2.0] and not none.low.any()


def test_aggregate_targets_in_any_order_and_overlapping() -> None:
    lines = one_hole([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0])
    vals = col(1.0, 2.0, 3.0, 4.0)
    tg = _spans([2.0, 0.0, 1.5, 0.0], [4.0, 4.0, 2.5, 1.0])
    assert iv.aggregate(lines, vals, tg).values[:, 0].tolist() == [3.5, 2.5, 2.5, 1.0]


def test_aggregate_sums_in_the_order_of_the_samples() -> None:
    """One target over 1000 samples of very different sizes: the kernel sums as `cascade` — bit for bit; and
    within 1e−12 of the exact sum."""
    rng = np.random.default_rng(12345)
    n = 1000
    edges = np.concatenate(([0.0], np.cumsum(rng.random(n) * 2.0 + 0.01)))
    g = 10.0 ** rng.uniform(-3, 4, size=(n, 3))
    g[rng.random((n, 3)) < 0.1] = NAN
    g[500] = NAN
    rho = rng.uniform(2.0, 4.5, size=n)
    lines = one_hole(list(edges[:-1]), list(edges[1:]))
    c0, c1 = edges[3] + 0.001, edges[-4] - 0.001
    ag = iv.aggregate(lines, g, one_hole([c0], [c1]), weight=rho, density=rho)
    ln = np.minimum(edges[1:], c1) - np.maximum(edges[:-1], c0)
    rows = [i for i in range(n) if ln[i] > 0]
    for f in range(3):
        have = [i for i in rows if not math.isnan(g[i, f])]
        num = cascade([(ln[i] * rho[i]) * g[i, f] if i in have else 0.0 for i in rows])
        den = cascade([ln[i] * rho[i] if i in have else 0.0 for i in rows])
        assert ag.values[0, f] == num / den
        exact = math.fsum(ln[i] * rho[i] * g[i, f] for i in have) / math.fsum(ln[i] * rho[i] for i in have)
        assert ag.values[0, f] == pytest.approx(exact, rel=1e-12)
    assert ag.density[0] == cascade([ln[i] * rho[i] for i in rows]) / cascade([ln[i] for i in rows])
    some = [i for i in rows if not np.isnan(g[i]).all()]  # a sample without any value is not «опробовано»
    assert len(some) < len(rows) and ag.count[0] == len(some)
    assert ag.sampled[0] == cascade([ln[i] if i in some else 0.0 for i in rows])


def test_aggregate_against_brute_force() -> None:
    rng = np.random.default_rng(777)
    lines, vals, rho, codes = _samples(rng, holes=60, per_hole=40)
    tg = iv.targets(iv.build_lines(lines.holes, np.zeros(60), lines.to[lines.starts[1:] - 1]), 2.5).lines
    ag = iv.aggregate(lines, vals, tg, weight=rho, density=rho, p_min=0.5)
    mode = iv.mode_by_length(lines, codes, tg)
    th = tg.hole_ids()
    for t in range(tg.n):
        h = int(np.searchsorted(lines.holes, th[t]))
        r = np.arange(lines.starts[h], lines.starts[h + 1])
        ln = np.minimum(lines.to[r], tg.to[t]) - np.maximum(lines.frm[r], tg.frm[t])
        r, ln = r[ln > 0], ln[ln > 0]
        for f in range(vals.shape[1]):
            ok = ~np.isnan(vals[r, f]) & ~np.isnan(rho[r])
            lf = math.fsum(ln[ok])
            if lf < 0.5 * (tg.to[t] - tg.frm[t]) * (1 - 1e-9):
                assert math.isnan(ag.values[t, f])
            else:
                want = math.fsum(ln[ok] * rho[r][ok] * vals[r, f][ok]) / math.fsum(ln[ok] * rho[r][ok])
                assert ag.values[t, f] == pytest.approx(want, rel=1e-12)
        got: dict[int, float] = {}
        for c, x in zip(codes[r].tolist(), ln.tolist(), strict=True):
            if c >= 0:
                got[c] = got.get(c, 0.0) + x
        best = max(got.values(), default=0.0)
        assert mode[t] == next((c for c, x in got.items() if x >= best - 1e-9), -1)


# --- mode --------------------------------------------------------------------------------------------------


def test_mode_by_length_a_tie_goes_to_the_first() -> None:
    lines = one_hole([0.0, 1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    codes = [2, 0, 0, -1, 1, -1]
    tg = _spans([0.0, 0.0, 0.0, 3.0, 4.5, 5.0], [2.0, 3.0, 6.0, 6.0, 6.0, 6.0])
    # 0–2: codes 2 and 0 by 1 m — the first (2); 0–3: 0 has 2 m; 3–6: only code 1; 5–6: empty
    assert iv.mode_by_length(lines, codes, tg).tolist() == [2, 0, 0, 1, 1, -1]
    thirds = one_hole([0.0, 0.1, 0.3], [0.1, 0.3, 0.6])  # 0.1 + 0.2 against 0.3: equal within 1e−9 — the first
    assert iv.mode_by_length(thirds, [5, 5, 4], one_hole([0.0], [0.6])).tolist() == [5]
    assert iv.mode_by_length(thirds, [4, 5, 5], one_hole([0.0], [0.6])).tolist() == [5]
    many = one_hole(list(np.arange(40.0)), list(np.arange(40.0) + 1.0))  # more codes than the first buffer
    assert iv.mode_by_length(many, list(range(39)) + [7], one_hole([0.0], [40.0])).tolist() == [7]


# --- order of the holes, threads, cancellation -------------------------------------------------------------


Table = tuple[iv.Lines, np.ndarray, np.ndarray, np.ndarray]  # lines, values [n, 4], density, codes


def _samples(rng: np.random.Generator, holes: int, per_hole: int) -> Table:
    n = holes * per_hole
    hole = np.repeat(np.arange(holes, dtype=np.int64) * 3 + 11, per_hole)
    length = rng.choice([0.5, 1.0, 1.5], size=n)
    gap = np.where(rng.random(n) < 0.03, 1.0, 0.0)  # an unsampled metre before the sample
    to = np.cumsum((length + gap).reshape(holes, per_hole), axis=1).reshape(-1)
    frm = to - length  # halves of a metre: exact, FROM equals the previous TO where there is no gap
    vals = 10.0 ** rng.uniform(-2, 2, size=(n, 4))
    vals[rng.random((n, 4)) < 0.08] = NAN
    rho = rng.uniform(2.4, 3.2, size=n)
    rho[rng.random(n) < 0.02] = NAN
    codes = rng.integers(-1, 5, size=n).astype(np.int32)
    lines = iv.build_lines(hole, frm, to)
    assert lines.rows is None and lines.n == n
    return lines, vals, rho, codes


def _everything(
    hole: np.ndarray, frm: np.ndarray, to: np.ndarray, vals: np.ndarray, rho: np.ndarray, codes: np.ndarray, th: int
) -> dict[str, np.ndarray]:
    threads = th
    """All the kernels on one table: lines → runs by code → composites of 2 m → values, mode; overlay."""
    lines = iv.build_lines(hole, frm, to)
    v, r, c = iv.stack_fields(lines, list(vals.T)), lines.take(rho), lines.take(codes)
    rn = iv.runs(lines, c, threads=threads)
    tg = iv.targets(rn.lines, 2.0, threads=threads)
    ag = iv.aggregate(lines, v, tg.lines, weight=r, density=r, p_min=0.5, threads=threads)
    mode = iv.mode_by_length(lines, c, tg.lines, threads=threads)
    ov = iv.overlay(lines, tg.lines, threads=threads)
    return {
        "holes": tg.lines.holes, "starts": tg.lines.starts, "frm": tg.lines.frm, "to": tg.lines.to,
        "stretch_code": rn.code[tg.stretch], "values": ag.values, "density": ag.density, "sampled": ag.sampled,
        "count": ag.count, "low": ag.low, "mode": mode, "ov_frm": ov.lines.frm, "ov_to": ov.lines.to,
        "ov_a": lines.frm[ov.ia], "ov_b": np.where(ov.ib >= 0, tg.lines.frm[ov.ib], -1.0),
        "short": np.array([tg.n_short, tg.len_short]),
    }  # fmt: skip


def _same(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> None:
    for key in a:
        assert a[key].dtype == b[key].dtype and a[key].tobytes() == b[key].tobytes(), key


def test_order_of_holes_and_rows_does_not_change_the_result() -> None:
    rng = np.random.default_rng(12345)
    lines, vals, rho, codes = _samples(rng, holes=9000, per_hole=8)  # 3 strips of holes
    hole, frm, to = lines.hole_ids(), lines.frm, lines.to
    base = _everything(hole, frm, to, vals, rho, codes, 1)
    assert len(base["frm"]) > 20000 and np.isnan(base["values"]).any() and base["short"][0] > 0
    by_hole = np.argsort(rng.permutation(9000)[(hole - 11) // 3], kind="stable")  # holes shuffled, rows in order
    _same(base, _everything(hole[by_hole], frm[by_hole], to[by_hole], vals[by_hole], rho[by_hole], codes[by_hole], 1))
    p = rng.permutation(len(hole))  # every row shuffled
    _same(base, _everything(hole[p], frm[p], to[p], vals[p], rho[p], codes[p], 1))


@pytest.mark.windows
def test_threads_give_the_same_bits() -> None:
    """ТЗ 8.0.2: the result is the same bit for bit with 1, 2, 4 and 8 threads (strips of 4096 holes)."""
    rng = np.random.default_rng(12345)
    lines, vals, rho, codes = _samples(rng, holes=20000, per_hole=10)  # 200 thousand samples, 5 strips
    hole = lines.hole_ids()
    base = _everything(hole, lines.frm, lines.to, vals, rho, codes, 1)
    for threads in (2, 4, 8):
        _same(base, _everything(hole, lines.frm, lines.to, vals, rho, codes, threads))


def test_tick_reports_progress_and_cancels() -> None:
    rng = np.random.default_rng(1)
    lines, vals, _rho, _codes = _samples(rng, holes=13000, per_hole=4)
    tg = iv.targets(iv.runs(lines, np.zeros(lines.n, dtype=np.int32)).lines, 2.0).lines
    for threads in (1, 4):
        seen: list[float] = []
        iv.aggregate(lines, vals, tg, threads=threads, tick=seen.append)
        assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) == len(strips.ranges(tg.n_holes))

        class Cancelled(Exception):
            pass

        def stop(fraction: float) -> None:
            raise Cancelled()

        with pytest.raises(Cancelled):
            iv.aggregate(lines, vals, tg, threads=threads, tick=stop)
        with pytest.raises(Cancelled):
            iv.overlay(lines, tg, threads=threads, tick=stop)
    assert strips.ranges(9000) == [(0, 4096), (4096, 8192), (8192, 9000)] and strips.default_threads(64) == 1
    assert strips.offsets(np.array([2, 0, 3])).tolist() == [0, 2, 2, 5]
