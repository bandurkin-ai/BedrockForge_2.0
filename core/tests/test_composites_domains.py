"""Composites by domains (ТЗ 8.2.2, 8.2.4; ADR 0018 §6): Т8.2-6 — no composite crosses a change of the code;
an empty code and a gap of the domain table are stretches without a domain; ready stretches (wireframes).

Tolerance — 1e−12 relative (8.2.4) unless a check says «exact»."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import domains as dm
from bf_core.composites import intervals as iv
from bf_core.composites import length as cl

NAN = math.nan
REL = 1e-12
A, B, NONE = 0, 1, -1


def samples(n: int = 10, hole: int = 1) -> tuple[iv.Lines, np.ndarray]:
    """1 m samples 0…n of one hole with the grade i + 1."""
    frm = np.arange(n, dtype=np.float64)
    lines = iv.build_lines([hole] * n, frm, frm + 1.0)
    return lines, iv.stack_fields(lines, [frm + 1.0])


def spans(c: iv.Lines) -> list[tuple[float, float]]:
    return list(zip(c.frm.tolist(), c.to.tolist(), strict=True))


# --- stretches ---------------------------------------------------------------------------------------------


def test_stretches_of_another_table_an_empty_code_and_a_gap_have_no_domain() -> None:
    lines, _ = samples()
    # lithology: 0–3 A, 3–5 without a code, 5–8 B in two rows, nothing in 8–9, 9–10 B
    litho = iv.build_lines([1] * 5, [0.0, 3.0, 5.0, 6.5, 9.0], [3.0, 5.0, 6.5, 8.0, 10.0])
    d = dm.domain_stretches(lines, litho, [A, NONE, B, B, B])
    assert spans(d.lines) == [(0.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, 9.0), (9.0, 10.0)]  # exact
    assert d.code.tolist() == [A, NONE, B, NONE, B] and d.code.dtype == np.int32
    assert (d.n_empty, d.len_empty) == (2, 3.0)
    # an empty row next to a gap is one stretch without a domain
    litho2 = iv.build_lines([1] * 2, [0.0, 3.0], [3.0, 5.0])
    d2 = dm.domain_stretches(lines, litho2, [A, NONE])
    assert spans(d2.lines) == [(0.0, 3.0), (3.0, 10.0)] and d2.code.tolist() == [A, NONE]


def test_stretches_cover_the_hole_as_composites_by_length_do() -> None:
    """The stretches tile [начало; забой] of every hole of the samples: the collar, the depth of the hole,
    the top of the first sample («от кровли первой пробы») — as `length.hole_stretches`."""
    lines = iv.build_lines([1, 1, 2], [2.0, 3.0, 0.0], [3.0, 4.0, 5.0])
    litho = iv.build_lines([1, 3], [1.0, 0.0], [3.5, 9.0])  # hole 2 has no lithology, hole 3 — no samples
    d = dm.domain_stretches(lines, litho, [A, B], ends=[6.0, NAN])
    assert d.lines.holes.tolist() == [1, 2] and d.lines.starts.tolist() == [0, 3, 4]
    assert spans(d.lines) == [(0.0, 1.0), (1.0, 3.5), (3.5, 6.0), (0.0, 5.0)]
    assert d.code.tolist() == [NONE, A, NONE, NONE]
    first = dm.domain_stretches(lines, litho, [A, B], origin="first")
    assert spans(first.lines) == [(2.0, 3.5), (3.5, 4.0), (0.0, 5.0)] and first.code.tolist() == [A, NONE, NONE]
    with pytest.raises(ValueError):
        dm.domain_stretches(lines, litho, [A])  # the codes are not those of the table


def test_no_table_rows_and_no_samples() -> None:
    lines, _ = samples(4)
    empty = iv.build_lines([], [], [])
    d = dm.domain_stretches(lines, empty, np.zeros(0, dtype=np.int32))
    assert spans(d.lines) == [(0.0, 4.0)] and d.code.tolist() == [NONE]
    none = dm.domain_stretches(empty, lines, np.zeros(4, dtype=np.int32))
    assert none.lines.n == 0 and none.code.shape == (0,) and none.n_empty == 0


# --- composites ----------------------------------------------------------------------------------------------


def test_composites_stop_at_every_change_of_the_code() -> None:
    lines, vals = samples()
    litho = iv.build_lines([1] * 5, [0.0, 3.0, 5.0, 6.5, 9.0], [3.0, 5.0, 6.5, 8.0, 10.0])
    d = dm.domain_stretches(lines, litho, [A, NONE, B, B, B])
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0), d)
    # [0; 3] → 0–2 and the remainder 2–3 (1 m = f_min·L); [5; 8] → 5–7, 7–8; a composite of 2 m crosses the
    # row boundary 6.5 (the same code) and no change of the code
    assert spans(c.lines) == [(0.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 7.0), (7.0, 8.0), (8.0, 9.0), (9.0, 10.0)]
    assert c.domain.tolist() == [A, A, NONE, B, B, NONE, B] and c.domain.dtype == np.int32
    assert c.values.values[:, 0] == pytest.approx([1.5, 3.0, 4.5, 6.5, 8.0, 9.0, 10.0], rel=REL)
    assert c.values.count.tolist() == [2, 1, 2, 2, 1, 1, 1] and not c.values.low.any()
    assert c.report["composites"] == 7 and c.report["stretches"] == 5
    assert (c.report["domains"], c.report["no_domain"], c.report["no_domain_len"]) == (2, 2, 3.0)
    assert any("без домена — 2" in line and "3,00 м" in line for line in c.journal)
    # the columns of the result
    assert dm.domain_text(c.domain, ["ГРН", "КВЦ"]).tolist() == ["ГРН", "ГРН", None, "КВЦ", "КВЦ", None, "КВЦ"]
    num = dm.domain_numbers(c.domain, np.array([10.0, 20.0]))
    assert num[[0, 3]].tolist() == [10.0, 20.0] and math.isnan(num[2])


def test_the_domain_is_a_field_of_the_same_table() -> None:
    lines, vals = samples(6)
    codes = np.array([A, A, A, B, B, NONE], dtype=np.int32)
    d = dm.domain_stretches(lines, lines, codes, ends=[8.0])
    assert spans(d.lines) == [(0.0, 3.0), (3.0, 5.0), (5.0, 8.0)] and d.code.tolist() == [A, B, NONE]
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0, remainder="equal"), d)
    assert spans(c.lines) == [(0.0, 1.5), (1.5, 3.0), (3.0, 5.0), (5.0, 6.5), (6.5, 8.0)]
    assert c.domain.tolist() == [A, A, B, NONE, NONE]
    assert c.values.values[:3, 0] == pytest.approx([(1 + 0.5 * 2) / 1.5, (0.5 * 2 + 3) / 1.5, 4.5], rel=REL)
    assert c.values.values[3, 0] == pytest.approx(6.0, rel=REL) and math.isnan(c.values.values[4, 0])  # unsampled


def test_grid_from_the_collar_inside_the_domains() -> None:
    """«От устья»: the grid 0, 2, 4, … cuts every stretch; the pieces at its ends follow the rule of the
    remainder (12.3) — and still no composite crosses a change of the code."""
    lines, vals = samples()
    litho = iv.build_lines([1] * 2, [0.0, 3.0], [3.0, 10.0])
    d = dm.domain_stretches(lines, litho, [A, B])
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0, origin="collar", f_min=0.5), d)
    assert spans(c.lines) == [(0.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0)]
    assert c.domain.tolist() == [A, A, B, B, B, B]


def test_ready_stretches_of_wireframes() -> None:
    """Stretches given as they are (inside solids, 12.7): boundaries anywhere inside the samples."""
    lines, vals = samples()
    inside = iv.build_lines([1, 1], [2.5, 7.3], [4.5, 9.0])
    d = dm.domain_stretches(lines, inside, [1, 0])
    assert spans(d.lines) == [(0.0, 2.5), (2.5, 4.5), (4.5, 7.3), (7.3, 9.0), (9.0, 10.0)]
    assert d.code.tolist() == [NONE, 1, NONE, 0, NONE]
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0, f_min=0.0), d)
    cut = np.array([2.5, 4.5, 7.3, 9.0])
    assert not ((c.lines.frm[:, None] < cut - 1e-9) & (c.lines.to[:, None] > cut + 1e-9)).any()
    # sampled all along, nothing dropped (f_min = 0): the metal and the length are those of the samples
    ln = c.lines.lengths()
    assert ln.sum() == pytest.approx(10.0, rel=REL)
    assert (ln * c.values.values[:, 0]).sum() == pytest.approx(vals[:, 0].sum(), rel=REL)


def test_number_codes() -> None:
    codes, uniq = dm.number_codes([3.0, NAN, 1.0, 3.0, 2.0])
    assert codes.tolist() == [2, -1, 0, 2, 1] and codes.dtype == np.int32 and uniq.tolist() == [1.0, 2.0, 3.0]
    codes, uniq = dm.number_codes([])
    assert codes.shape == (0,) and uniq.shape == (0,)


# --- Т8.2-6 --------------------------------------------------------------------------------------------------


def _litho(rng: np.random.Generator, holes: int, per_hole: int) -> tuple[np.ndarray, ...]:
    """Lithology of `holes` holes × `per_hole` intervals 0.5…12 m thick: 6 codes, empty codes, gaps."""
    n = holes * per_hole
    hole = np.repeat(np.arange(1, holes + 1, dtype=np.int64), per_hole)
    thick = np.round(rng.uniform(0.5, 12.0, n), 2)
    to = np.cumsum(thick.reshape(holes, per_hole), axis=1).reshape(-1)
    frm = to - thick
    gap = rng.random(n) < 0.03  # a part of the interval is not described
    frm = np.where(gap, frm + 0.25 * thick, frm)
    code = rng.integers(0, 6, n).astype(np.int32)
    code[rng.random(n) < 0.05] = NONE
    return hole, np.round(frm, 4), np.round(to, 4), code


def _assays(rng: np.random.Generator, holes: int, depth: np.ndarray) -> tuple[np.ndarray, ...]:
    """Samples 0.4…1.6 m from the collar to the depth of every hole, a tenth of them missing; 2 fields."""
    per = (depth / 1.0).astype(np.int64) + 1
    hole = np.repeat(np.arange(1, holes + 1, dtype=np.int64), per)
    step = rng.uniform(0.4, 1.6, hole.shape[0])
    starts = np.concatenate(([0], np.cumsum(per)))
    to = np.cumsum(step) - np.repeat(np.cumsum(step)[starts[:-1]] - step[starts[:-1]], per)
    frm = to - step
    keep = rng.random(hole.shape[0]) > 0.1
    au = rng.lognormal(0.0, 1.0, hole.shape[0])
    au[rng.random(hole.shape[0]) < 0.05] = NAN
    return hole[keep], np.round(frm[keep], 3), np.round(to[keep], 3), au[keep], rng.random(int(keep.sum()))


def _code_at(table: iv.Lines, code: np.ndarray, hole: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """Brute force: the code of the table row that covers a depth of a hole, −1 — none."""
    key = table.hole_ids().astype(np.float64) * 1e6
    at = np.searchsorted(key + table.to, hole * 1e6 + depth, side="right")
    at = np.minimum(at, table.n - 1)
    ok = (table.hole_ids()[at] == hole) & (table.frm[at] <= depth) & (depth < table.to[at])
    return np.where(ok, code[at], NONE)


@pytest.mark.parametrize("origin", ["domain", "collar"])
def test_t8_2_6_no_composite_crosses_a_domain_boundary(origin: str) -> None:
    """Т8.2-6. 10⁵ интервалов литологии: ни один композит не пересекает границу домена."""
    rng = np.random.default_rng(826)
    holes, per_hole = 2000, 50
    h, f, t, code = _litho(rng, holes, per_hole)
    assert h.shape[0] == 100_000
    litho = iv.build_lines(h, f, t)
    assert litho.n == 100_000 and litho.rows is None
    depth = t.reshape(holes, per_hole)[:, -1]
    sh, sf, st, au, ag = _assays(rng, holes, depth)
    lines = iv.build_lines(sh, sf, st)
    vals = iv.stack_fields(lines, [au, ag])
    d = dm.domain_stretches(lines, litho, code, ends=depth, threads=2)
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0, origin=origin), d, threads=2)
    n = c.lines.n
    assert n > 150_000 and c.report["no_domain"] > 1000 and c.report["domains"] == 6
    ch, c0, c1 = c.lines.hole_ids(), c.lines.frm, c.lines.to
    assert (c1 > c0).all()

    # the boundaries of the domains: every depth where the code of the lithology changes (a gap and an
    # empty code are «no domain»), found without the engine — by the code just above and just below
    edges = np.unique(np.concatenate((h * 1e6 + f, h * 1e6 + t)))
    eh, ed = np.floor(edges / 1e6).astype(np.int64), edges - np.floor(edges / 1e6) * 1e6
    above, below = _code_at(litho, code, eh, ed - 1e-3), _code_at(litho, code, eh, ed + 1e-3)
    change = np.sort((eh * 1e6 + ed)[above != below])
    assert change.shape[0] > 80_000
    # no boundary lies inside a composite: the first boundary after its start is at its end or further
    key0, key1 = ch * 1e6 + c0, ch * 1e6 + c1
    nxt = np.searchsorted(change, key0 + 1e-6, side="right")
    inside = (nxt < change.shape[0]) & (change[np.minimum(nxt, change.shape[0] - 1)] < key1 - 1e-6)
    assert not inside.any()

    # DOMAIN is the code of the lithology all along the composite
    for share in (0.02, 0.5, 0.98):
        assert np.array_equal(_code_at(litho, code, ch, c0 + share * (c1 - c0)), c.domain)
    # nothing is lost but what the journal counts: composites + short stretches + dropped remainders
    whole = float(np.maximum(depth, lines.to[lines.starts[1:] - 1]).sum())
    assert (c1 - c0).sum() + c.report["short_len"] + c.report["cut_len"] == pytest.approx(whole, rel=1e-9)
    assert c.report["short"] > 0 and any("короче f_min·L" in line for line in c.journal)


# --- order, threads, cancel ----------------------------------------------------------------------------------


def _everything(seed: int, *, threads: int = 1, shuffle: int | None = None) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    holes, per_hole = 9000, 6  # three strips of holes
    h, f, t, code = _litho(rng, holes, per_hole)
    depth = t.reshape(holes, per_hole)[:, -1]
    sh, sf, st, au, ag = _assays(rng, holes, depth)
    if shuffle is not None:
        p, q = (np.random.default_rng(shuffle + k).permutation(m) for k, m in enumerate((h.shape[0], sh.shape[0])))
        h, f, t, code = h[p], f[p], t[p], code[p]
        sh, sf, st, au, ag = sh[q], sf[q], st[q], au[q], ag[q]
    litho, lines = iv.build_lines(h, f, t), iv.build_lines(sh, sf, st)
    vals = iv.stack_fields(lines, [au, ag])
    d = dm.domain_stretches(lines, litho, litho.take(code), ends=depth, threads=threads)
    c = dm.composite_by_domains(lines, vals, cl.LengthParams(length=2.0), d, density=lines.take(ag), threads=threads)
    return {
        "starts": c.lines.starts, "frm": c.lines.frm, "to": c.lines.to, "domain": c.domain,
        "values": c.values.values, "density": c.values.density, "count": c.values.count,
    }  # fmt: skip


def test_threads_and_the_order_of_rows_do_not_change_a_bit() -> None:
    base = _everything(7)
    for other in (_everything(7, threads=2), _everything(7, threads=8), _everything(7, shuffle=3)):
        for k, v in base.items():
            assert v.tobytes() == other[k].tobytes(), k


def test_tick_reports_progress_and_cancels() -> None:
    rng = np.random.default_rng(1)
    h, f, t, code = _litho(rng, 9000, 4)
    litho = iv.build_lines(h, f, t)
    seen: list[float] = []
    d = dm.domain_stretches(litho, litho, code, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 6
    vals = iv.stack_fields(litho, [rng.random(litho.n)])
    seen.clear()
    dm.composite_by_domains(litho, vals, cl.LengthParams(), d, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def stop(_f: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        dm.domain_stretches(litho, litho, code, tick=stop)
    with pytest.raises(Cancelled):
        dm.composite_by_domains(litho, vals, cl.LengthParams(), d, tick=stop)
