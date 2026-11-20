"""Merging of interval tables (ТЗ 8.2.2, 8.2.4; ADR 0018 §6): Т8.2-8 — the sub-intervals between all FROM / TO
with the values of the covering interval of every table; no cover — NaN / empty, never a zero.

Depths and values are copied, not computed: the checks are exact."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import intervals as iv
from bf_core.composites import merge as mg

NAN = math.nan


def one(frm: list[float], to: list[float], hole: int = 1) -> iv.Lines:
    return iv.build_lines([hole] * len(frm), frm, to)


def spans(m: mg.Merged) -> list[tuple[float, float]]:
    return list(zip(m.lines.frm.tolist(), m.lines.to.tolist(), strict=True))


def same(a: np.ndarray, b: list[float]) -> bool:
    """Equal, NaN where NaN."""
    return bool(np.array_equal(a, np.array(b, dtype=np.float64), equal_nan=True))


# --- Т8.2-8 ------------------------------------------------------------------------------------------------


def test_t8_2_8_two_tables() -> None:
    """Т8.2-8. Таблицы 0–2, 2–5 и 1–4: подынтервалы 0–1, 1–2, 2–4, 4–5 со значениями покрывающих
    интервалов."""
    a, b = one([0.0, 2.0], [2.0, 5.0]), one([1.0], [4.0])
    m = mg.merge_tables([a, b], names=["Пробы", "Литология"])
    assert spans(m) == [(0.0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, 5.0)]  # exact
    assert m.lines.holes.tolist() == [1] and m.lines.starts.tolist() == [0, 4]
    assert [ix.tolist() for ix in m.index] == [[0, 0, 1, 1], [-1, 0, 0, -1]]
    au = np.array([1.5, 0.25])
    assert m.numbers(0, au).tolist() == [1.5, 1.5, 0.25, 0.25]
    assert same(m.numbers(1, np.array([7.0])), [NAN, 7.0, 7.0, NAN])  # no cover — NaN, not 0
    rock = np.array([3], dtype=np.int32)
    assert m.codes(1, rock).tolist() == [-1, 3, 3, -1] and m.codes(1, rock).dtype == np.int32
    # several fields of a table at once: [rows, fields] as `intervals.stack_fields` gives them
    both = m.numbers(0, iv.stack_fields(a, [au, np.array([NAN, 9.0])]))
    assert both.shape == (4, 2) and same(both[:, 1], [NAN, NAN, 9.0, 9.0])
    assert m.report["rows"] == 4 and m.report["length"] == 5.0
    assert m.report["tables"] == [
        {"rows": 2, "left_out": 0, "covered": 4, "covered_len": 5.0},
        {"rows": 1, "left_out": 0, "covered": 2, "covered_len": 3.0},
    ]
    assert any("«Литология»" in line and "без покрытия" in line and "2" in line for line in m.journal)
    assert not any("«Пробы»" in line for line in m.journal)  # it covers every sub-interval


def test_three_tables_gaps_and_holes() -> None:
    # hole 1: A 0–4, 6–10; B 2–7; C 3–5, 5–12.    hole 2: only B.    hole 3: A and C side by side, a gap between
    a = iv.build_lines([1, 1, 3], [0.0, 6.0, 0.0], [4.0, 10.0, 2.0])
    b = iv.build_lines([1, 2], [2.0, 1.0], [7.0, 3.0])
    c = iv.build_lines([1, 1, 3], [3.0, 5.0, 5.0], [5.0, 12.0, 6.0])
    m = mg.merge_tables([a, b, c])
    assert m.lines.holes.tolist() == [1, 2, 3] and m.lines.starts.tolist() == [0, 8, 9, 11]
    assert spans(m)[:8] == [(0, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 10), (10, 12)]
    assert spans(m)[8:] == [(1.0, 3.0), (0.0, 2.0), (5.0, 6.0)]  # nothing is made of the gap 2–5 of hole 3
    assert m.index[0].tolist() == [0, 0, 0, -1, -1, 1, 1, -1, -1, 2, -1]
    assert m.index[1].tolist() == [-1, 0, 0, 0, 0, 0, -1, -1, 1, -1, -1]
    assert m.index[2].tolist() == [-1, -1, 0, 0, 1, 1, 1, 1, -1, -1, 2]
    # the values go with the rows
    va, vb, vc = np.array([1.0, 2.0, 3.0]), np.array([10.0, 20.0]), np.array([100.0, NAN, 300.0])
    total = np.nansum(np.c_[m.numbers(0, va), m.numbers(1, vb), m.numbers(2, vc)], axis=1)
    assert total.tolist() == [1.0, 11.0, 111.0, 110.0, 10.0, 12.0, 2.0, 0.0, 20.0, 3.0, 300.0]
    assert math.isnan(m.numbers(2, vc)[4])  # a missing value of a covering row stays missing
    # the order of the tables changes the order of the columns only
    back = mg.merge_tables([c, b, a])
    assert spans(back) == spans(m) and all(np.array_equal(back.index[2 - k], m.index[k]) for k in range(3))
    # one table is itself
    solo = mg.merge_tables([a])
    assert spans(solo) == [(0.0, 4.0), (6.0, 10.0), (0.0, 2.0)] and solo.index[0].tolist() == [0, 1, 2]
    with pytest.raises(ValueError):
        mg.merge_tables([])
    with pytest.raises(ValueError):
        m.numbers(0, np.zeros(5))  # not a column of table 0


def test_rows_left_out_and_columns_in_table_order() -> None:
    """A table given in any order with a bad row and an overlap: the index is in line order — a column of
    the table goes through `lines.take` first."""
    hole = [1, 1, 1, 1, 1]
    frm, to = [4.0, 0.0, 2.0, 1.0, 6.0], [6.0, 2.0, 4.0, 3.0, 6.0]  # 1–3 overlaps 0–2; 6–6 is empty
    a = iv.build_lines(hole, frm, to)
    assert (a.n, a.n_bad, a.n_overlap) == (3, 1, 1)
    b = one([0.0], [6.0])
    m = mg.merge_tables([a, b], names=["А", "Б"])
    value = np.array([40.0, 0.0, 20.0, 10.0, 60.0])  # in the order of the source rows
    assert m.numbers(0, a.take(value)).tolist() == [0.0, 20.0, 40.0]
    assert m.report["tables"][0] == {"rows": 3, "left_out": 2, "covered": 3, "covered_len": 6.0}
    assert sum("«А»" in line for line in m.journal) == 2  # the bad row and the overlap, each in its line


def test_boundaries_within_the_depth_tolerance_are_one() -> None:
    a, b = one([0.0, 2.0], [2.0, 4.0]), one([0.0], [2.0 + 4e-7])
    m = mg.merge_tables([a, b])
    assert len(spans(m)) == 2 and m.index[1].tolist() == [0, -1]  # no sliver of 0.4 µm


# --- names of the columns ------------------------------------------------------------------------------------


def test_names_of_columns_met_in_two_tables_get_the_table() -> None:
    names = mg.merged_names([("Пробы", ["AU", "CU", "Плотность"]), ("Литология", ["ROCK", "au"]), ("Зоны", ["ZONE"])])
    assert names == [["AU (Пробы)", "CU", "Плотность"], ["ROCK", "au (Литология)"], ["ZONE"]]
    # the name with the table is taken by a field of its own: a number makes the new name distinct
    assert mg.merged_names([("T", ["A", "A (T)"]), ("U", ["a"])]) == [["A (T) (2)", "A (T)"], ["a (U)"]]
    # one table, or the same field twice in a table: nothing to tell apart by the table
    assert mg.merged_names([("T", ["A", "B"])]) == [["A", "B"]] and mg.merged_names([]) == []


# --- a set of holes ------------------------------------------------------------------------------------------


def _table(rng: np.random.Generator, holes: int, per_hole: int, share: float) -> tuple[np.ndarray, ...]:
    """Intervals of random thickness with gaps; `share` of the holes have the table."""
    ids = np.flatnonzero(rng.random(holes) < share).astype(np.int64) + 1
    n = ids.shape[0] * per_hole
    thick = np.round(rng.uniform(0.3, 6.0, n), 2)
    to = np.cumsum(thick.reshape(-1, per_hole), axis=1).reshape(-1)
    frm = to - thick * np.where(rng.random(n) < 0.1, 0.5, 1.0)
    return np.repeat(ids, per_hole), np.round(frm, 3), np.round(to, 3), rng.random(n)


def _brute(lines: iv.Lines, hole: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """The row of the lines that covers a depth of a hole, −1 — none."""
    if lines.n == 0:
        return np.full(hole.shape[0], -1, dtype=np.int64)
    ids = lines.hole_ids()
    at = np.searchsorted(ids * 1e5 + lines.to, hole * 1e5 + depth, side="right")
    at = np.minimum(at, lines.n - 1)
    ok = (ids[at] == hole) & (lines.frm[at] <= depth) & (depth < lines.to[at])
    return np.where(ok, at, -1)


def _merged(threads: int = 1, shuffle: int | None = None) -> tuple[mg.Merged, list[iv.Lines], list[np.ndarray]]:
    rng = np.random.default_rng(88)
    raw = [_table(rng, 9000, per, share) for per, share in ((12, 1.0), (5, 0.8), (3, 0.5))]
    if shuffle is not None:
        order = [np.random.default_rng(shuffle + k).permutation(t[0].shape[0]) for k, t in enumerate(raw)]
        raw = [tuple(col[p] for col in t) for t, p in zip(raw, order, strict=True)]
    tables = [iv.build_lines(h, f, t) for h, f, t, _v in raw]
    values = [lines.take(t[3]) for lines, t in zip(tables, raw, strict=True)]
    return mg.merge_tables(tables, threads=threads), tables, values


def test_set_of_holes_against_brute_force() -> None:
    m, tables, values = _merged()
    n = m.lines.n
    assert n > 150_000 and (m.lines.lengths() > 1e-6).all()
    hole, mid = m.lines.hole_ids(), 0.5 * (m.lines.frm + m.lines.to)
    covered = np.zeros(n, dtype=np.bool_)
    for k, lines in enumerate(tables):
        assert np.array_equal(m.index[k], _brute(lines, hole, mid))  # the covering row, by its middle
        got = m.numbers(k, values[k])
        want = np.where(m.index[k] >= 0, values[k][np.maximum(m.index[k], 0)], NAN)
        assert np.array_equal(got, want, equal_nan=True)
        covered |= m.index[k] >= 0
    assert covered.all()  # a sub-interval is covered by a table at least
    # every FROM and TO of every table is a boundary of the result, and nothing else is
    key = np.unique(np.concatenate([np.r_[t.hole_ids() * 1e5 + t.frm, t.hole_ids() * 1e5 + t.to] for t in tables]))
    got_key = np.unique(np.r_[hole * 1e5 + m.lines.frm, hole * 1e5 + m.lines.to])
    assert np.array_equal(key, got_key)
    # the length covered by a table is its own length
    for k, lines in enumerate(tables):
        assert m.report["tables"][k]["covered_len"] == pytest.approx(float(lines.lengths().sum()), rel=1e-12)


def test_threads_and_the_order_of_rows_do_not_change_a_bit() -> None:
    base, _t, vals = _merged()
    for threads, shuffle in ((2, None), (8, None), (1, 5)):
        other, _t2, vals2 = _merged(threads, shuffle)
        for a, b in (
            (base.lines.starts, other.lines.starts),
            (base.lines.frm, other.lines.frm),
            (base.lines.to, other.lines.to),
            *((base.numbers(k, vals[k]), other.numbers(k, vals2[k])) for k in range(3)),
        ):
            assert a.tobytes() == b.tobytes()
    wide = np.c_[vals[0], vals[0] * 2.0]
    assert base.numbers(0, wide, threads=1).tobytes() == base.numbers(0, wide, threads=4).tobytes()


def test_tick_reports_progress_and_cancels() -> None:
    rng = np.random.default_rng(3)
    tables = [iv.build_lines(*_table(rng, 9000, 4, 1.0)[:3]) for _ in range(3)]
    seen: list[float] = []
    mg.merge_tables(tables, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 6

    class Cancelled(Exception):
        pass

    def stop(_f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        mg.merge_tables(tables, tick=stop)


def test_summary_by_columns_is_the_summary_of_the_matrix() -> None:
    """Э12 12.33б: the summary «до/после» of a merge is counted column by column (no matrix of 5 million rows ×
    10 fields); the sums of a field do not depend on the other fields — the numbers are the same bit for bit."""
    from bf_core.composites.compose_tables import _summary_of
    from bf_core.composites.length import summary

    rng = np.random.default_rng(4)
    n = 20_011  # not a multiple of the blocks of the cascade sum
    ln = np.round(rng.random(n) * 3.0, 2)
    cols = [rng.lognormal(0.0, 2.0, n) for _ in range(5)]
    cols[1][rng.random(n) < 0.4] = np.nan
    cols[2][:] = np.nan  # a field without a value at all
    cols[3][::3] = 1e-300
    cols[4][5] = 1e300
    names = ["A", "B", "C", "D", "E"]
    whole = summary(ln, np.column_stack(cols), names)
    assert _summary_of(ln, list(zip(names, cols, strict=True))) == whole
    assert whole["fields"]["C"] == {"mean": None, "metal": None} and whole["count"] == n
    assert _summary_of(ln, []) == summary(ln, np.zeros((n, 0)), [])
