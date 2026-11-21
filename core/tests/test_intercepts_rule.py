"""Rule 8.5.3 of the ore intercepts (ТЗ 8.5.3, Т8.5-11, Т8.5-12; ADR 0021 §7, решения 10–11; `intercepts/rule.py`):
the two acceptance examples of the ТЗ, the equalities at the limits (g = c, waste = L_п, mean = c, length =
L_min) on decimals whose float64 sums fall on the wrong side, missing values as waste and as a break, the mean
by density, «в том числе», acceptance by true thickness — and the kernel against the rule written out in plain
Python on random holes, bit for bit, with 1, 2, 4 and 8 threads."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
import pytest

from bf_core.composites.intervals import CompositeError, Lines, build_lines
from bf_core.intercepts import rule

NAN = math.nan
T11 = [0.1, 1.5, 2.0, 0.2, 0.3, 1.8, 0.1, 0.1, 0.1, 3.0]  # Т8.5-11: samples of 1 m from the collar


def hole(grades: Any, depths: Any = None, hole_id: int = 7) -> tuple[Lines, np.ndarray]:
    """Samples of one hole: `depths` — the boundaries (one more than the grades) or the [(from, to)] of every
    sample; by default 1 m samples from the collar."""
    g = np.asarray(grades, dtype=np.float64)
    if depths is None:
        depths = np.arange(len(g) + 1, dtype=np.float64)
    d = np.asarray(depths, dtype=np.float64)
    frm, to = (d[:-1], d[1:]) if d.ndim == 1 else (d[:, 0], d[:, 1])
    lines = build_lines(np.full(len(g), hole_id), frm, to)
    return lines, lines.take(g)


def brief(c: Any) -> list[tuple[float, float]]:
    """[(FROM, TO)] of candidates or of the rows of the table."""
    return list(zip(c.lines.frm.tolist(), c.lines.to.tolist(), strict=True))


def cand(grades: Any, cutoff: float, waste_max: float, depths: Any = None, **kw: Any) -> rule.Candidates:
    lines, g = hole(grades, depths)
    return rule.candidates(lines, g, cutoff, waste_max, **kw)


# --- the rule in plain Python ------------------------------------------------------------------------------


def ge(a: float, b: float) -> bool:
    return a >= b - 1e-9 * max(abs(a), abs(b))


def le(a: float, b: float) -> bool:
    return a <= b + 1e-9 * max(abs(a), abs(b))


def total(values: Any) -> float:
    """A direct sum, left to right (not `sum`: that one compensates the rounding since Python 3.12)."""
    s = 0.0
    for x in values:
        s += x
    return s


def ref_mean(pieces: list[tuple[float, float, float, str, int]], weighted: bool) -> float:
    has = [p for p in pieces if p[2] > 0.0 and math.isfinite(p[2])] if weighted else []
    if not has:
        return total(p[0] * p[1] for p in pieces) / total(p[0] for p in pieces)
    no = [p for p in pieces if not (p[2] > 0.0 and math.isfinite(p[2]))]
    a, c, e = total(p[0] * p[2] * p[1] for p in has), total(p[0] * p[2] for p in has), total(p[0] for p in has)
    rho = c / e
    return (a + rho * total(p[0] * p[1] for p in no)) / (c + rho * total(p[0] for p in no))


def ref_rule(frm: Any, to: Any, grade: Any, cutoff: float, waste_max: float, density: Any = None,
             missing: str = "waste") -> list[dict[str, Any]]:  # fmt: skip
    """Steps 1–2 of ТЗ 8.5.3 for the rows of one hole (or of one accepted intercept), every sum taken afresh
    over its fragment: [{first, last, frm, to, length, grade, lg, waste, miss}]."""
    pieces: list[tuple[float, float, float, str, int]] = []  # length, grade, density, kind, row
    for i in range(len(frm)):
        if i and frm[i] - to[i - 1] > 1e-6:
            pieces.append((frm[i] - to[i - 1], 0.0, NAN, "miss", -1))
        x = float(grade[i])
        kind = ("ore" if ge(x, cutoff) else "waste") if math.isfinite(x) else "miss"
        w = NAN if density is None else float(density[i])
        pieces.append((to[i] - frm[i], x if kind != "miss" else 0.0, w, kind, i))
    series, j = [], 0
    while j < len(pieces):
        if pieces[j][3] != "ore":
            j += 1
            continue
        k = j
        while k < len(pieces) and pieces[k][3] == "ore":
            k += 1
        series.append((j, k))
        j = k
    spans: list[tuple[int, int]] = []
    for a, b in series:
        if spans:
            between = pieces[spans[-1][1] : a]
            whole = pieces[spans[-1][0] : b]
            broken = missing == "break" and any(p[3] == "miss" for p in between)
            if (
                not broken
                and le(total(p[0] for p in between), waste_max)
                and ge(ref_mean(whole, density is not None), cutoff)
            ):
                spans[-1] = (spans[-1][0], b)
                continue
        spans.append((a, b))
    out = []
    for a, b in spans:
        part = pieces[a:b]
        out.append({
            "first": part[0][4], "last": part[-1][4] + 1, "frm": frm[part[0][4]], "to": to[part[-1][4]],
            "length": total(p[0] for p in part), "grade": ref_mean(part, density is not None),
            "lg": total(p[0] * p[1] for p in part), "waste": total(p[0] for p in part if p[3] != "ore"),
            "miss": total(p[0] for p in part if p[3] == "miss"),
        })  # fmt: skip
    return out


def rows(c: rule.Candidates) -> list[dict[str, Any]]:
    cols = {"first": c.first, "last": c.last, "frm": c.lines.frm, "to": c.lines.to, "length": c.length,
            "grade": c.grade, "lg": c.lg, "waste": c.waste, "miss": c.miss}  # fmt: skip
    return [{k: v[i].item() for k, v in cols.items()} for i in range(c.n)]


def random_holes(holes: int, seed: int, per_hole: int = 20) -> tuple[Lines, np.ndarray, np.ndarray]:
    """Holes of 1–2·per_hole samples with gaps, values without an assay and without a density, grades around
    the cut-off 1,0 in steps of 0,1 (equalities are common)."""
    rng = np.random.default_rng(seed)
    n = rng.integers(1, 2 * per_hole, holes)
    ids = np.repeat(np.arange(holes) * 3 + 5, n)
    length = rng.choice([0.5, 1.0, 1.0, 1.0, 1.5, 0.3, 0.7], n.sum())
    gap = np.where(rng.random(n.sum()) < 0.06, rng.choice([0.2, 1.0, 5.0], n.sum()), 0.0)
    to = np.concatenate([np.cumsum(seg) for seg in np.split(length + gap, np.cumsum(n)[:-1])])
    grade = np.round(rng.gamma(1.4, 0.7, n.sum()), 1)
    grade[rng.random(n.sum()) < 0.05] = NAN
    rho = np.round(rng.uniform(2.4, 4.2, n.sum()), 2)
    rho[rng.random(n.sum()) < 0.2] = NAN
    lines = build_lines(ids, to - length, to)
    return lines, lines.take(grade), lines.take(rho)


def digest(*arrays: Any) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def digest_of(out: rule.Outcome) -> str:
    parts: list[Any] = []
    for c, reason, tt in ((out.main, out.main_reason, out.main_tt), (out.sub, out.sub_reason, out.sub_tt)):
        if c is not None:
            parts += [c.lines.holes, c.lines.starts, c.lines.frm, c.lines.to, c.first, c.last, c.length, c.grade,
                      c.lg, c.waste, c.miss, c.parent, reason, tt]  # fmt: skip
    t = out.intercepts()
    parts += [t.lines.holes, t.lines.starts, t.lines.frm, t.lines.to, t.kind, t.id, t.incl, t.first, t.last,
              t.length, t.grade, t.lg, t.waste, t.miss, t.tt]  # fmt: skip
    return digest(*parts)


# --- the examples of the ТЗ --------------------------------------------------------------------------------


def test_t8_5_11() -> None:
    """c = 1, L_п = 2, L_min = 2: one intercept 1–6 м, 5 м @ 1,16, метропроцент 5,8; 9–10 is dropped."""
    lines, g = hole(T11)
    out = rule.run(lines, g, rule.RuleParams(cutoff=1.0, l_min=2.0, waste_max=2.0))
    assert brief(out.main) == [(1.0, 6.0), (9.0, 10.0)]
    assert out.main_reason.tolist() == [rule.REASON_OK, rule.REASON_SHORT] and out.sub is None
    assert rule.REASON_TITLES[rule.REASON_SHORT] == "короче L_min"
    t = out.intercepts()
    assert (t.lines.frm.tolist(), t.lines.to.tolist(), t.lines.holes.tolist()) == ([1.0], [6.0], [7])
    assert t.length.tolist() == [5.0] and abs(t.grade[0] - 1.16) < 1e-12 and abs(t.lg[0] - 5.8) < 1e-12
    assert t.waste.tolist() == [2.0] and t.miss.tolist() == [0.0] and (t.last - t.first).tolist() == [5]
    assert (t.kind.tolist(), t.id.tolist(), t.incl.tolist()) == ([rule.KIND_MAIN], [1], [0]) and np.isnan(t.tt).all()
    assert rule.is_ore(g, 1.0).tolist() == [False, True, True, False, False, True, False, False, False, True]


def test_t8_5_12() -> None:
    """Т8.5-11 with c2 = 1,5: «в том числе 1–3 м: 2 м @ 1,75»; 5–6 is dropped."""
    lines, g = hole(T11)
    out = rule.run(lines, g, rule.RuleParams(cutoff=1.0, l_min=2.0, waste_max=2.0, include_cutoff=1.5))
    assert out.sub is not None and brief(out.sub) == [(1.0, 3.0), (5.0, 6.0)]
    assert out.sub_reason.tolist() == [rule.REASON_OK, rule.REASON_SHORT] and out.sub.parent.tolist() == [0, 0]
    assert out.sub.length.tolist() == [2.0, 1.0] and out.sub.grade.tolist() == [1.75, 1.8]
    t = out.intercepts()
    assert brief(t) == [(1.0, 6.0), (1.0, 3.0)]
    assert (t.kind.tolist(), t.id.tolist(), t.incl.tolist()) == ([rule.KIND_MAIN, rule.KIND_INCL], [1, 2], [0, 1])
    assert t.length.tolist() == [5.0, 2.0] and t.grade[1] == 1.75 and t.lg[1] == 3.5 and t.waste.tolist() == [2.0, 0.0]
    assert rule.KIND_TITLES == ("основное", "в том числе")
    assert t.lines.starts.tolist() == [0, 2]  # both rows are rows of hole 7: targets for `intervals.aggregate`


# --- equalities at the limits ------------------------------------------------------------------------------


def test_limits_hold_where_float_sums_fall_short() -> None:
    """Decimals a geologist types: g = c, waste = L_п, mean = c and length = L_min are «равенство — руда»."""
    assert 0.7 + 0.1 + 0.1 < 0.9  # a grade that is 0,9 on paper, one float below it
    assert brief(cand([0.7 + 0.1 + 0.1, 0.2], 0.9, 0.0)) == [(0.0, 1.0)]
    assert brief(cand([0.9 * (1.0 - 1e-8), 0.2], 0.9, 0.0)) == []  # … and one really below it is waste

    depths = [0.0, 1.0, 1.1, 1.3, 2.3]  # waste 1,0–1,1 and 1,1–1,3: 0,3 m on paper
    assert (1.1 - 1.0) + (1.3 - 1.1) > 0.3
    assert brief(cand([2.0, 0.5, 0.5, 2.0], 1.0, 0.3, depths)) == [(0.0, 2.3)]
    assert brief(cand([2.0, 0.5, 0.5, 2.0], 1.0, 0.3 * (1.0 - 1e-8), depths)) == [(0.0, 1.0), (1.3, 2.3)]

    assert ((0.7 + 0.6) + 0.8) / 3.0 < 0.7  # ore 0,7, waste 0,6, ore 0,8: the mean is 0,7 on paper
    assert brief(cand([0.7, 0.6, 0.8], 0.7, 1.0)) == [(0.0, 3.0)]
    assert brief(cand([0.7, 0.6 - 1e-7, 0.8], 0.7, 1.0)) == [(0.0, 1.0), (2.0, 3.0)]

    lines, g = hole([0.1, 2.0, 2.0, 0.1], [0.0, 1.0, 1.1, 2.3, 3.0])  # ore 1,0–2,3: 1,3 m on paper
    assert (1.1 - 1.0) + (2.3 - 1.1) < 1.3
    c = rule.candidates(lines, g, 1.0, 0.0)
    assert c.length.tolist() == [(1.1 - 1.0) + (2.3 - 1.1)]
    assert rule.accept(c, 1.3).tolist() == [rule.REASON_OK]
    assert rule.accept(c, 1.3 * (1.0 + 1e-8)).tolist() == [rule.REASON_SHORT]
    assert rule.accept(c, 0.0).tolist() == [rule.REASON_OK]


def test_the_sums_are_direct_not_differences_of_running_sums() -> None:
    """ADR 0018, находка 9: 0,8 + 1,1 + 0,8 + 1,3 is 4,0 summed directly; LG and LENGTH of an intercept are
    the sums of its own samples whatever stands above it in the hole."""
    above = [1.3, 0.1, 0.7, 0.1, 0.1, 0.1, 0.1]
    lines, g = hole([*above, 0.8, 1.1, 0.8, 1.3, 0.0, 0.0, 0.0], hole_id=3)
    c = rule.candidates(lines, g, 0.8, 0.0)
    assert brief(c)[-1] == (7.0, 11.0) and c.lg[-1] == 0.8 + 1.1 + 0.8 + 1.3 == 4.0 and c.grade[-1] == 1.0
    running = np.cumsum(g * lines.lengths())
    assert running[10] - running[6] != 4.0  # what a difference of running sums would have given


# --- joining -----------------------------------------------------------------------------------------------


def test_joining_left_to_right() -> None:
    # the waste between two series is longer than L_п: two candidates; L_п = 0: nothing is ever joined
    assert brief(cand([2.0, 0.1, 0.1, 0.1, 2.0], 1.0, 2.0)) == [(0.0, 1.0), (4.0, 5.0)]
    assert brief(cand([2.0, 0.9, 2.0], 1.0, 0.0)) == [(0.0, 1.0), (2.0, 3.0)]
    assert brief(cand([2.0, 2.0, 1.0, 5.0], 1.0, 0.0)) == [(0.0, 4.0)]  # one series: consecutive ore samples
    # every waste stretch is within L_п, the mean of the whole stays above c: one candidate of three series
    c = cand([3.0, 0.2, 0.2, 3.0, 0.5, 2.5], 1.0, 2.0)
    assert brief(c) == [(0.0, 6.0)] and c.waste.tolist() == [3.0] and abs(c.grade[0] - 9.4 / 6.0) < 1e-12
    # the mean of «тек ∪ пустая ∪ R» is what is tested: R2 does not pull the long waste up, it starts anew …
    c = cand([1.2, 0.0, 0.0, 1.2, 0.3, 4.0], 1.0, 2.0)
    assert brief(c) == [(0.0, 1.0), (3.0, 6.0)]  # … and takes R3: (1,2 + 0,3 + 4,0) / 3 ≥ 1
    # the current one is the union: R3 is tested against R1 ∪ R2 as a whole, not against R2 alone
    c = cand([1.0, 0.9, 1.1, 0.0, 1.9], 1.0, 1.0)
    assert brief(c) == [(0.0, 3.0), (4.0, 5.0)]  # (1,0 + 0,9 + 1,1 + 0 + 1,9) / 5 = 0,98 < 1; R2 ∪ R3 alone — 1,0
    # nothing reaches the cut-off; an empty table
    assert cand([0.1, 0.9, 0.99], 1.0, 2.0).n == 0
    empty = rule.candidates(build_lines([], [], []), [], 1.0, 2.0)
    assert empty.n == 0 and empty.lines.n_holes == 0 and empty.lines.starts.tolist() == [0]
    out = rule.run(build_lines([], [], []), [], rule.RuleParams(cutoff=1.0, include_cutoff=2.0))
    assert out.main.n == 0 and out.sub is not None and out.sub.n == 0 and out.intercepts().lines.n == 0


# --- missing values ----------------------------------------------------------------------------------------


def test_missing_as_waste_and_as_break() -> None:
    """ТЗ 8.5.3: g = NaN — waste with g = 0 (default) or a break; a stretch without samples is the same."""
    c = cand([2.0, NAN, 2.0], 1.0, 2.0)
    assert brief(c) == [(0.0, 3.0)] and c.grade.tolist() == [4.0 / 3.0] and c.lg.tolist() == [4.0]
    assert (c.length.tolist(), c.waste.tolist(), c.miss.tolist()) == ([3.0], [1.0], [1.0])
    assert (c.last - c.first).tolist() == [3]
    assert brief(cand([2.0, NAN, 2.0], 1.0, 2.0, missing="break")) == [(0.0, 1.0), (2.0, 3.0)]
    # … it is a zero in the mean, not a value left out: (3 + 0 + 0 + 3) / 4 < 2
    assert brief(cand([3.0, NAN, NAN, 3.0], 2.0, 2.0)) == [(0.0, 1.0), (3.0, 4.0)]
    assert brief(cand([3.0, NAN, 3.0], 2.0, 2.0)) == [(0.0, 3.0)]
    # waste and missing between two series add up against L_п; MISS_LEN is the missing part of WASTE_LEN
    c = cand([3.0, 0.5, NAN, 3.0], 1.0, 2.0)
    assert brief(c) == [(0.0, 4.0)] and (c.waste.tolist(), c.miss.tolist()) == ([2.0], [1.0])
    assert brief(cand([3.0, 0.5, NAN, 0.5, 3.0], 1.0, 2.0)) == [(0.0, 1.0), (4.0, 5.0)]
    # a stretch without samples: 1,0–1,5
    gap = [(0.0, 1.0), (1.5, 2.5)]
    c = cand([2.0, 2.0], 1.0, 2.0, gap)
    assert brief(c) == [(0.0, 2.5)] and c.length.tolist() == [2.5] and c.grade.tolist() == [1.6]
    assert (c.waste.tolist(), c.miss.tolist(), (c.last - c.first).tolist()) == ([0.5], [0.5], [2])
    assert brief(cand([2.0, 2.0], 1.0, 2.0, gap, missing="break")) == [(0.0, 1.0), (1.5, 2.5)]
    assert brief(cand([2.0, 2.0], 1.0, 0.4, gap)) == [(0.0, 1.0), (1.5, 2.5)]  # 0,5 m without samples > L_п
    assert brief(cand([2.0, 2.0], 1.0, 2.0, [(0.0, 1.0), (1.0 + 5e-7, 2.0)], missing="break")) == [(0.0, 2.0)]
    # a missing value is never ore and never a zero-grade intercept; at the ends it is outside
    for missing in ("waste", "break"):
        assert cand([NAN, NAN, NAN], 1.0, 2.0, missing=missing).n == 0
        c = cand([NAN, 2.0, NAN], 1.0, 2.0, missing=missing)
        assert brief(c) == [(1.0, 2.0)] and c.miss.tolist() == [0.0] and c.waste.tolist() == [0.0]
        assert brief(cand([math.inf, 2.0, -math.inf, 2.0], 1.0, 0.0, missing=missing)) == [(1.0, 2.0), (3.0, 4.0)]
    assert not rule.is_ore([NAN, 1.0, 0.99, math.inf], 1.0)[[0, 2, 3]].any()


# --- density -----------------------------------------------------------------------------------------------


def test_mean_by_density() -> None:
    """Σ l·ρ·g / Σ l·ρ; a sample without a density takes the mean density of the fragment."""
    g = [2.0, 0.4, 2.0]
    assert brief(cand(g, 1.4, 1.0)) == [(0.0, 3.0)]  # 4,4 / 3 ≥ 1,4
    c = cand(g, 1.4, 1.0, density=[2.0, 4.0, 2.0])
    assert brief(c) == [(0.0, 1.0), (2.0, 3.0)]  # (4 + 1,6 + 4) / (2 + 4 + 2) = 1,2 < 1,4
    c = cand(g, 1.1, 1.0, density=[2.0, 4.0, 2.0])
    assert brief(c) == [(0.0, 3.0)] and c.grade.tolist() == [9.6 / 8.0] and c.lg.tolist() == [4.4]
    for rho in ([2.5, NAN, 2.5], [2.5, 0.0, 2.5], [2.5, -1.0, 2.5], [NAN, NAN, NAN], [NAN, 3.1, NAN]):
        c = cand(g, 1.4, 1.0, density=rho)  # one density (or none) for all: the mean by length
        assert brief(c) == [(0.0, 3.0)] and abs(c.grade[0] - 4.4 / 3.0) < 1e-14
    c = cand([3.0, 1.0, 0.5, 2.0], 1.0, 1.0, density=[2.0, NAN, 4.0, 3.0], depths=[0.0, 1.0, 3.0, 3.5, 4.5])
    rho = (2.0 + 2.0 + 3.0) / (1.0 + 0.5 + 1.0)  # of the samples that have one, by length
    want = (1.0 * 2.0 * 3.0 + 0.5 * 4.0 * 0.5 + 1.0 * 3.0 * 2.0 + rho * 2.0 * 1.0) / (2.0 + 2.0 + 3.0 + rho * 2.0)
    assert brief(c) == [(0.0, 4.5)] and abs(c.grade[0] - want) < 1e-14 and c.lg.tolist() == [3.0 + 2.0 + 0.25 + 2.0]
    # a missing grade keeps its density and its zero: a heavy barren sample pulls the mean down
    light = cand([2.0, NAN, 2.0], 1.2, 1.0, density=[3.0, 1.0, 3.0])
    heavy = cand([2.0, NAN, 2.0], 1.2, 1.0, density=[3.0, 9.0, 3.0])
    assert brief(light) == [(0.0, 3.0)] and light.grade.tolist() == [12.0 / 7.0]
    assert brief(heavy) == [(0.0, 1.0), (2.0, 3.0)]
    with pytest.raises(ValueError):
        cand(g, 1.0, 1.0, density=[2.0, 2.0])


# --- «в том числе» and the acceptance ----------------------------------------------------------------------


def test_include_inside_the_accepted_only() -> None:
    """The same steps by c2 inside every accepted intercept: nothing of an include is outside its intercept,
    nothing is looked for in a rejected one; ID runs down the hole, INCL names the main row."""
    g = [5.0, 0.1, 0.1, 0.1, 3.0, 1.2, 0.0, 4.0, 6.0, 1.0, 0.1, 0.1, 0.1, 9.0, 9.0, 0.1, 0.1, 0.1, 1.1, 8.0, 1.1]
    lines, col = hole(g, hole_id=4)
    other, col2 = hole([0.0, 7.0, 7.0, 1.0], hole_id=2)
    both = build_lines(
        np.concatenate([lines.hole_ids(), other.hole_ids()]), np.concatenate([lines.frm, other.frm]),
        np.concatenate([lines.to, other.to]),
    )  # fmt: skip
    grade = both.take(np.concatenate([col, col2]))
    out = rule.run(both, grade, rule.RuleParams(cutoff=1.0, l_min=2.0, waste_max=1.0, include_cutoff=3.0))
    assert both.holes.tolist() == [2, 4]
    assert brief(out.main) == [(1.0, 4.0), (0.0, 1.0), (4.0, 10.0), (13.0, 15.0), (18.0, 21.0)]
    assert out.main_reason.tolist() == [0, rule.REASON_SHORT, 0, 0, 0]
    assert out.sub is not None
    # 0–1 (5,0 ≥ c2) was rejected: no include there; 13–15 is its own include whole; in 18–21 one is too short
    assert brief(out.sub) == [(1.0, 3.0), (4.0, 5.0), (7.0, 9.0), (13.0, 15.0), (19.0, 20.0)]
    assert out.sub_reason.tolist() == [0, rule.REASON_SHORT, 0, 0, rule.REASON_SHORT]
    assert out.sub.parent.tolist() == [0, 2, 2, 3, 4]  # rows of `out.main`
    t = out.intercepts()
    assert brief(t) == [(1.0, 4.0), (1.0, 3.0), (4.0, 10.0), (7.0, 9.0), (13.0, 15.0), (13.0, 15.0), (18.0, 21.0)]
    assert t.lines.holes.tolist() == [2, 4] and t.lines.starts.tolist() == [0, 2, 7]
    assert t.kind.tolist() == [0, 1, 0, 1, 0, 1, 0] and t.id.tolist() == [1, 2, 1, 2, 3, 4, 5]
    assert t.incl.tolist() == [0, 1, 0, 1, 0, 3, 0]
    assert np.all(t.first[t.kind == 1] >= t.first[t.kind == 0][[0, 1, 2]])
    # 4–10 at c2 = 3: 3,0 | 1,2 0,0 | 4,0 6,0 — the waste 5–7 is 2 m > L_п = 1; with L_п = 2 the mean decides
    wide = rule.run(both, grade, rule.RuleParams(cutoff=1.0, l_min=2.0, waste_max=2.0, include_cutoff=2.8))
    assert wide.sub is not None and (4.0, 9.0) in brief(wide.sub)  # (3 + 1,2 + 0 + 4 + 6) / 5 = 2,84 ≥ 2,8
    for bad in (1.0, 0.5, NAN, 1.0 + 1e-12):
        with pytest.raises(CompositeError) as err:
            rule.run(both, grade, rule.RuleParams(cutoff=1.0, include_cutoff=bad))
        assert err.value.code == "bad_param" and err.value.param == "include_cutoff"


def test_acceptance_by_true_thickness() -> None:
    """min_by = tt: TT ≥ m_min; a candidate without TT is checked by its length with the same number."""
    lines, g = hole([2.0, 2.0, 2.0, 0.0, 0.0, 0.0, 2.0, 2.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 2.0, 2.0, 2.0])
    seen: list[list[tuple[float, float]]] = []

    def tt(c: Lines) -> np.ndarray:
        seen.append(list(zip(c.frm.tolist(), c.to.tolist(), strict=True)))
        return np.where(c.frm < 10.0, 0.5 * (c.to - c.frm), NAN)  # the body dips 60° to the hole; none below 10 m

    p = rule.RuleParams(cutoff=1.0, l_min=1.5, waste_max=1.0, min_by="tt", include_cutoff=1.5)
    out = rule.run(lines, g, p, tt=tt)
    assert brief(out.main) == [(0.0, 3.0), (6.0, 8.0), (11.0, 12.0), (15.0, 18.0)]
    assert out.main_tt.tolist()[:2] == [1.5, 1.0] and np.isnan(out.main_tt[2:]).all()
    assert out.main_reason.tolist() == [0, rule.REASON_THIN, rule.REASON_SHORT, 0]  # TT = m_min; thin; short; long
    assert rule.REASON_TITLES[rule.REASON_THIN] == "ИМ меньше m_min"
    assert seen == [brief(out.main), [(0.0, 3.0), (15.0, 18.0)]]  # the candidates, then the includes of the accepted
    t = out.intercepts()
    assert t.tt.tolist()[:2] == [1.5, 1.5] and np.isnan(t.tt[2:]).all() and t.kind.tolist() == [0, 1, 0, 1]
    # by length the same number accepts 6–8 too; no source of orientation at all — every candidate goes by length
    by_length = rule.run(lines, g, rule.RuleParams(cutoff=1.0, l_min=1.5, waste_max=1.0))
    assert by_length.main_reason.tolist() == [0, 0, rule.REASON_SHORT, 0] and np.isnan(by_length.main_tt).all()
    blind = rule.run(lines, g, rule.RuleParams(cutoff=1.0, l_min=1.5, waste_max=1.0, min_by="tt"))
    assert blind.main_reason.tolist() == by_length.main_reason.tolist()
    assert rule.accept(out.main, 1.5, min_by="tt", tt=[NAN, 9.0, 9.0, 0.0]).tolist() == [0, 0, 0, rule.REASON_THIN]
    with pytest.raises(ValueError):
        rule.accept(out.main, 1.5, min_by="tt", tt=[1.0])
    with pytest.raises(ValueError):
        rule.run(lines, g, p, tt=lambda c: np.zeros(c.n + 1))


def test_parameters_are_checked() -> None:
    lines, g = hole(T11)
    for name, value in (
        ("cutoff", 0.0), ("cutoff", -1.0), ("cutoff", NAN), ("cutoff", math.inf), ("cutoff", "много"),
        ("l_min", -0.1), ("l_min", NAN), ("waste_max", -1.0), ("waste_max", math.inf), ("min_by", "area"),
        ("missing", "zero"), ("include_cutoff", 0.9),
    ):  # fmt: skip
        with pytest.raises(CompositeError) as err:
            rule.run(lines, g, rule.RuleParams(**{"cutoff": 1.0, name: value}))  # type: ignore[arg-type]
        e = err.value
        assert (e.code, e.param) == ("bad_param", name) and e.message and e.hint
        assert any("а" <= ch <= "я" for ch in e.message.lower()) and e.as_dict()["code"] == "bad_param"
    p = rule.RuleParams(cutoff="1.5", l_min=0, waste_max=0).checked()  # type: ignore[arg-type]
    assert (p.cutoff, p.l_min, p.waste_max) == (1.5, 0.0, 0.0)
    assert (p.min_by, p.missing, p.include_cutoff) == ("length", "waste", None)
    assert rule.RuleParams(cutoff=1.0) == rule.RuleParams(1.0, 2.0, 2.0, "length", None, "waste")  # ТЗ 8.5.4
    with pytest.raises(ValueError):
        rule.candidates(lines, g[:-1], 1.0, 2.0)
    with pytest.raises(ValueError):
        rule.candidates(lines, g, 1.0, 2.0, missing="skip")


# --- the kernel against the rule written out ---------------------------------------------------------------


@pytest.mark.parametrize("missing", ["waste", "break"])
@pytest.mark.parametrize("weighted", [False, True])
def test_against_the_rule_in_plain_python(missing: str, weighted: bool) -> None:
    """600 random holes: every candidate, its sums and its mean — the same float64 numbers; then the includes
    inside the accepted ones."""
    lines, grade, rho = random_holes(600, seed=11)
    density = rho if weighted else None
    got = rule.candidates(lines, grade, 1.0, 1.5, density=density, missing=missing)
    want: list[dict[str, Any]] = []
    for h in range(lines.n_holes):
        a, b = int(lines.starts[h]), int(lines.starts[h + 1])
        part = ref_rule(lines.frm[a:b].tolist(), lines.to[a:b].tolist(), grade[a:b], 1.0, 1.5,
                        None if density is None else density[a:b], missing)  # fmt: skip
        want += [{**r, "first": r["first"] + a, "last": r["last"] + a} for r in part]
    assert got.n == len(want) > 1500 and rows(got) == want
    assert got.lines.hole_ids().tolist() == [int(lines.hole_ids()[r["first"]]) for r in want]
    assert got.parent.tolist() == [-1] * got.n
    joined = sum(1 for r in want if r["waste"] > 0.0)
    assert joined > 150 and sum(1 for r in want if r["miss"] > 0.0) > (20 if missing == "waste" else -1)
    assert (got.miss == 0.0).all() or missing == "waste"

    ok = rule.accept(got, 2.0) == rule.REASON_OK
    acc = got.take(ok)
    assert 300 < acc.n < got.n and rows(acc) == [r for r, keep in zip(want, ok, strict=True) if keep]
    sub = rule.candidates(lines, grade, 1.6, 1.5, density=density, missing=missing, within=acc)
    inner: list[dict[str, Any]] = []
    parent: list[int] = []
    for k, r in enumerate(rows(acc)):
        a, b = r["first"], r["last"]
        part = ref_rule(lines.frm[a:b].tolist(), lines.to[a:b].tolist(), grade[a:b], 1.6, 1.5,
                        None if density is None else density[a:b], missing)  # fmt: skip
        inner += [{**x, "first": x["first"] + a, "last": x["last"] + a} for x in part]
        parent += [k] * len(part)
    assert sub.n == len(inner) > 300 and rows(sub) == inner and sub.parent.tolist() == parent
    assert np.all(sub.first >= acc.first[sub.parent]) and np.all(sub.last <= acc.last[sub.parent])


def test_same_bytes_with_any_threads_and_any_order_of_rows() -> None:
    lines, grade, rho = random_holes(9000, seed=12, per_hole=12)
    assert lines.n_holes > 2 * 4096 and lines.n > 100_000
    p = rule.RuleParams(cutoff=1.0, l_min=2.0, waste_max=1.5, min_by="tt", include_cutoff=1.6)

    def tt(c: Lines) -> np.ndarray:
        return np.where(c.hole_ids() % 7 == 0, NAN, 0.8 * (c.to - c.frm))

    one = digest_of(rule.run(lines, grade, p, density=rho, tt=tt, threads=1))
    for threads in (2, 4, 8):
        assert digest_of(rule.run(lines, grade, p, density=rho, tt=tt, threads=threads)) == one
    order = np.random.default_rng(13).permutation(lines.n)  # the rows of the table in any order
    mixed = build_lines(lines.hole_ids()[order], lines.frm[order], lines.to[order])
    out = rule.run(mixed, mixed.take(grade[order]), p, density=mixed.take(rho[order]), tt=tt, threads=4)
    assert digest_of(out) == one
    t = out.intercepts()
    assert t.lines.n > 5000 and (t.kind == rule.KIND_INCL).sum() > 500
    main = t.kind == rule.KIND_MAIN
    assert np.all(t.incl[main] == 0) and np.all(t.incl[~main] > 0) and np.all(t.incl[~main] < t.id[~main])
    assert np.all(np.diff(t.lines.holes) > 0)
    # balance: what the main intercepts hold is what their samples hold (Σ l·g, missing as zero)
    lg = np.where(np.isfinite(grade), grade, 0.0) * lines.lengths()
    inside = np.zeros(lines.n, dtype=bool)
    for a, b in zip(t.first[main].tolist(), t.last[main].tolist(), strict=True):
        inside[a:b] = True
    assert abs(float(t.lg[main].sum()) - float(lg[inside].sum())) < 1e-9 * float(lg[inside].sum())


def test_tick_reports_and_cancels() -> None:
    lines, grade, _rho = random_holes(9000, seed=14, per_hole=6)
    seen: list[float] = []
    rule.run(lines, grade, rule.RuleParams(cutoff=1.0, include_cutoff=2.0), threads=2, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 6 and 0.0 < seen[0] < 0.5

    class Stop(Exception):
        pass

    def stop(_fraction: float) -> None:
        raise Stop

    for threads in (1, 4):
        with pytest.raises(Stop):
            rule.run(lines, grade, rule.RuleParams(cutoff=1.0), threads=threads, tick=stop)


def test_rows_left_out_of_the_lines_do_not_take_part() -> None:
    """A row with TO ≤ FROM and one that overlaps the row before it are not samples (Э12: counted for the
    log); `first`, `last` are rows of the lines, `lines.rows` leads back to the table."""
    ids = [1, 1, 1, 1, 1, 1]
    frm = [0.0, 1.0, 1.0, 2.0, 3.0, 4.0]
    to = [1.0, 2.0, 1.0, 3.0, 4.0, 5.0]
    g = np.array([2.0, 2.0, 9.0, 0.1, 9.0, 2.0])
    frm[4], to[4] = 2.5, 3.5  # overlaps 2–3
    lines = build_lines(ids, frm, to)
    assert (lines.n, lines.n_bad, lines.n_overlap) == (4, 1, 1) and lines.rows is not None
    c = rule.candidates(lines, lines.take(g), 1.0, 0.5)
    assert brief(c) == [(0.0, 2.0), (4.0, 5.0)] and c.grade.tolist() == [2.0, 2.0]
    assert lines.rows[c.first].tolist() == [0, 5] and (c.last - c.first).tolist() == [2, 1]
    wide = rule.candidates(lines, lines.take(g), 1.0, 2.0)  # 3–4 has no sample left: 2 m of waste, one of them missing
    assert brief(wide) == [(0.0, 5.0)] and (wide.waste.tolist(), wide.miss.tolist()) == ([2.0], [1.0])
