"""Helpers of the ore-interval tests (ТЗ 7.35 Д-26): the examples of the sources (`data/ore/examples.json`),
sample lines for the kernel and a plain-Python reference of the rules — lists and direct sums over the samples
of every fragment, nothing shared with the numba kernels."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.composites.ore_intervals import OreIntervals, OreLines, OreParams

EXAMPLES: dict[str, Any] = json.loads((Path(__file__).parent / "data" / "ore" / "examples.json").read_text("utf-8"))[
    "examples"
]
REL = 1e-12  # criterion of the examples (ТЗ: «1e−12 отн.»)
TOL = 1e-9  # tolerance of the comparisons of the rules, towards equality


def close(a: float, b: float, rel: float = REL) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=rel)


def example_lines(name: str) -> OreLines:
    """The line of an example: one hole, samples one after another from `from`."""
    ex = EXAMPLES[name]
    grades = ex["grades"]
    lengths = ex["lengths"] if isinstance(ex["lengths"], list) else [ex["lengths"]] * len(grades)
    edges = [ex["from"]]
    for length in lengths:
        edges.append(edges[-1] + length)
    return OreLines.of([0, len(grades)], edges[:-1], edges[1:], grades)


def example_params(name: str, mode: str, **over: Any) -> OreParams:
    return OreParams(**{**EXAMPLES[name]["params"], "mode": mode, **over})


def make_lines(holes: list[list[tuple[float, ...]]], *, thick: bool = False, domain: bool = False) -> OreLines:
    """Holes as lists of samples (from, to, grade[, thickness][, domain])."""
    starts = [0]
    cols: list[list[float]] = [[], [], [], [], []]
    for hole in holes:
        for sample in hole:
            cols[0].append(sample[0])
            cols[1].append(sample[1])
            cols[2].append(sample[2])
            cols[3].append(sample[3] if thick else math.nan)
            cols[4].append(sample[-1] if domain else 0)
        starts.append(len(cols[0]))
    return OreLines.of(
        starts, cols[0], cols[1], cols[2], cols[3] if thick else None, np.array(cols[4], np.int32) if domain else None
    )


def unit_line(grades: list[float], length: float = 1.0) -> OreLines:
    """One hole of samples of the same length from 0."""
    return make_lines([[(k * length, (k + 1) * length, g) for k, g in enumerate(grades)]])


def brief(res: OreIntervals, cls: str | None = None) -> list[tuple[str, float, float]]:
    """(class, from, to) of the rows, optionally of one class."""
    return [(r["cls"], r["from"], r["to"]) for r in res.rows() if cls is None or r["cls"] == cls]


def check_rows(res: OreIntervals, expected: list[dict[str, Any]], first_row: int = 0) -> None:
    """Every expected interval of an example is among the rows: class, samples, depths, M, MC, C, basis."""
    rows = res.rows()
    for want in expected:
        a, b = want["samples"]
        span = (first_row + a - 1, first_row + b - 1)
        hit = [r for r in rows if r["cls"] == want["cls"] and (r["row0"], r["row1"]) == span]
        assert len(hit) == 1, (want, [(r["cls"], r["row0"], r["row1"]) for r in rows])
        got = hit[0]
        for key in ("from", "to", "m", "mc", "c"):
            if key in want:
                assert close(got[key], want[key]), (key, got[key], want)
        if "basis" in want:
            assert got["basis"] == want["basis"], (got, want)


def events_of(res: OreIntervals, **want: Any) -> list[dict[str, Any]]:
    """Protocol events with the given field values (`samples=(a, b)` — numbers of the samples in the hole)."""
    assert res.events is not None
    samples = want.pop("samples", None)
    out = []
    for e in res.events.records():
        if samples is not None and (e["row0"], e["row1"]) != (samples[0] - 1, samples[1] - 1):
            continue
        if all(e[key] == value for key, value in want.items()):
            out.append(e)
    return out


def check_protocol(res: OreIntervals, expected: list[dict[str, Any]], decisions: tuple[str, ...]) -> int:
    """The protocol records of an example with one of `decisions` are among the events; returns their number."""
    done = 0
    for want in expected:
        if want.get("decision") not in decisions:
            continue
        keys = {k: want[k] for k in ("step", "stage", "side", "decision", "compared") if k in want}
        hit = events_of(res, samples=tuple(want["samples"]), **keys)
        assert len(hit) == 1, (want, hit)
        for key in ("from", "to", "m", "mc", "c"):
            if key in want:
                assert close(hit[0][key], want[key]), (key, hit[0][key], want)
        done += 1
    return done


# --- reference: plain Python --------------------------------------------------------------------------------


def ge(a: float, b: float) -> bool:
    return a >= b - TOL * max(abs(a), abs(b))


def le(a: float, b: float) -> bool:
    return a <= b + TOL * max(abs(a), abs(b))


def sums(ls: list[float], gs: list[float], a: int, b: int) -> tuple[float, float]:
    """M and MC of the samples [a, b), summed directly."""
    m = 0.0
    mc = 0.0
    for k in range(a, b):
        m += ls[k]
        mc += ls[k] * gs[k]
    return m, mc


def ref_k(ls: list[float], gs: list[float], a: int, b: int, p: OreParams) -> str:
    """Условие К of the samples [a, b): "thickness", "mc" or "" — not met."""
    m, mc = sums(ls, gs, a, b)
    if not ge(mc / m, p.c_min):  # type: ignore[arg-type]
        return ""
    if ge(m, p.m_min):
        return "thickness"
    return "mc" if ge(mc, p.mc_min) else ""  # type: ignore[arg-type]


def ref_units(gs: list[float], cb: float) -> list[list[Any]]:
    """Elementary units [ore, a, b) of a line."""
    units: list[list[Any]] = []
    for k, g in enumerate(gs):
        ore = ge(g, cb)
        if units and units[-1][0] == ore:
            units[-1][2] = k + 1
        else:
            units.append([ore, k, k + 1])
    return units


def ref_simple(ls: list[float], gs: list[float], p: OreParams) -> list[tuple[int, int]]:
    """«Упрощённый»: grown intervals [a, b) of a line."""
    units = ref_units(gs, p.cutoff)
    out = []
    i = 0
    while i < len(units):
        if not units[i][0]:
            i += 1
            continue
        a = units[i][1]
        j = i
        while j + 2 < len(units):
            m, mc = sums(ls, gs, a, units[j + 2][2])
            if not ge(mc / m, p.cutoff):
                break
            j += 2
        out.append((a, units[j][2]))
        i = j + 1
    return out


def ref_rows(
    ls: list[float], gs: list[float], found: list[tuple[int, int]], p: OreParams, layers: Sequence[tuple[int, int]] = ()
) -> list[tuple[Any, ...]]:
    """Rows (class, a, b, basis) of a line from the intervals a mode found and the fragments it excluded
    (step 5): an interval that meets К is `ore`, the rest falls back to the samples."""
    cls = ["waste" if not ge(g, p.cutoff) else "subgrade" for g in gs]
    owner = [-1] * len(gs)
    basis = {}
    for n, (a, b) in enumerate(found):
        k = ref_k(ls, gs, a, b, p)
        if k:
            basis[n] = k
            for i in range(a, b):
                owner[i] = n
    for n, (a, b) in enumerate(layers):
        for i in range(a, b):
            assert owner[i] < 0
            owner[i] = -2 - n
    rows: list[list[Any]] = []
    for i in range(len(gs)):
        key = ("ore", owner[i]) if owner[i] >= 0 else ("interlayer", owner[i]) if owner[i] < -1 else (cls[i], -1)
        if rows and rows[-1][4] == key:
            rows[-1][2] = i + 1
        else:
            rows.append([key[0], i, i + 1, basis.get(owner[i], ""), key])
    return [tuple(r[:4]) for r in rows]


def ref_lines(samples: list[tuple[float, float, float]], eps: float = 1e-6) -> list[list[int]]:
    """Step 0, «разрыв»: indices of the samples of every sampling line of a hole."""
    lines: list[list[int]] = []
    cur: list[int] = []
    prev_to = None
    for i, (f, t, g) in enumerate(samples):
        if not (math.isfinite(f) and math.isfinite(t) and t > f):
            continue
        if prev_to is not None and f < prev_to - eps:
            continue
        gap = prev_to is not None and f > prev_to + eps
        prev_to = t
        if gap or not math.isfinite(g):
            if cur:
                lines.append(cur)
            cur = []
        if math.isfinite(g):
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def ref_soft(ls: list[float], gs: list[float], p: OreParams) -> tuple[list[tuple[int, int]], list[tuple[Any, ...]]]:
    """Step 2 — прирезка по этапам: the ore units [a, b) the stages end with and the log of the decisions
    (stage, what, a, b[, passed]). Every sum is taken directly over the samples of the fragment."""
    units = [[ore, a, b, True] for ore, a, b in ref_units(gs, p.cutoff)]  # ore, a, b, changed at the last stage
    log: list[tuple[Any, ...]] = []
    stage = 0
    while True:
        stage += 1
        changed = False
        accepted = set()
        for i in range(len(units) - 2):
            up, mid, low = units[i], units[i + 1], units[i + 2]
            if not up[0] or not (up[3] or mid[3] or low[3]):
                continue
            if not le(sums(ls, gs, mid[1], mid[2])[0], p.m_waste_max):
                log.append((stage, "gap", mid[1], mid[2]))
                continue
            m_up, mc_up = sums(ls, gs, up[1], mid[2])
            m_low, mc_low = sums(ls, gs, mid[1], low[2])
            ok_up, ok_low = ge(mc_up / m_up, p.cutoff), ge(mc_low / m_low, p.cutoff)
            log.append((stage, "upper", up[1], mid[2], ok_up))
            log.append((stage, "lower", mid[1], low[2], ok_low))
            if ok_up and ok_low:
                accepted.add(i)
        joined: list[list[Any]] = []  # ore, a, b, joined at this stage
        i = 0
        while i < len(units):
            j = i
            while j in accepted:
                j += 2
            if j > i:
                log.append((stage, "joined", units[i][1], units[j][2]))
                changed = True
            joined.append([units[i][0], units[i][1], units[j][2], j > i])
            i = j + 1
        units = []
        waits = False
        k = 0
        while k < len(joined):
            ore, a, b, new = joined[k]
            if ore and not new and not waits and not ref_k(ls, gs, a, b, p):
                log.append((stage, "demoted", a, b))
                if units and not units[-1][0]:
                    a = units.pop()[1]
                if k + 1 < len(joined) and not joined[k + 1][0]:
                    k += 1
                    b = joined[k][2]
                units.append([False, a, b, True])
                changed = waits = True
            else:
                units.append([ore, a, b, new])
                if ore:
                    waits = False
            k += 1
        if not changed:
            return [(a, b) for ore, a, b, _ in units if ore], log


def ref_layer_from(ls: list[float], gs: list[float], units: list[list[Any]], i: int, p: OreParams) -> int | None:
    """The shortest fragment «порода … порода» that begins with the waste unit i (C < Сб, M > Мпп): the index
    of its last unit."""
    for j in range(i, len(units)):
        if units[j][0]:
            continue
        m, mc = sums(ls, gs, units[i][1], units[j][2])
        if not le(m, p.m_waste_max) and not ge(mc / m, p.cutoff):
            return j
    return None


def ref_strict(
    ls: list[float], gs: list[float], a: int, b: int, p: OreParams, excl: bool
) -> tuple[list[tuple[str, int, int]], list[tuple[Any, ...]]]:
    """Step 3 for the interval [a, b) of step 2: its parts (kind, a, b) from the top down — "ore" (К is checked
    at step 5), "interlayer" (the excluded fragment) or "waste" (the torn-off waste unit) — and the log
    (what, a, b). The fragment: the first one from the top above which a кондиционный interval stays; when
    there is none — the first one."""
    parts: list[tuple[str, int, int]] = []
    log: list[tuple[Any, ...]] = []
    todo = [("scan", a, b)]
    while todo:
        kind, a, b = todo.pop()
        if kind != "scan":
            parts.append((kind, a, b))
            continue
        units = [[ore, a + ua, a + ub] for ore, ua, ub in ref_units(gs[a:b], p.cutoff)]
        first = chosen = None
        for i, (ore, s, _) in enumerate(units):
            if ore:
                continue
            t = ref_layer_from(ls, gs, units, i, p)
            if t is None:
                continue
            if first is None:
                first = (i, t)
            if ref_k(ls, gs, a, s, p):
                chosen = (i, t)
                break
            log.append(("skipped", s, units[t][2]))
        chosen = chosen or first
        if chosen is None:
            parts.append(("ore", a, b))
            continue
        s, e, x = units[chosen[0]][1], units[chosen[1]][2], units[chosen[1]][1]
        log.append(("interlayer" if excl else "torn", s, e))
        if excl:
            todo += [("scan", e, b), ("interlayer", s, e), ("scan", a, s)]
        else:
            todo += [("scan", e, b), ("waste", x, e), ("scan", a, x)]
    return parts, log


def brute_layers(ls: list[float], gs: list[float], a: int, b: int, p: OreParams) -> list[tuple[int, int]]:
    """Every fragment [i, j) of the samples [a, b) that begins and ends with a waste sample, C < Сб, M > Мпп
    (the sums — over the samples of the fragment, in their order)."""
    out = []
    for i in range(a, b):
        if ge(gs[i], p.cutoff):
            continue
        m = 0.0
        mc = 0.0
        for j in range(i, b):
            m += ls[j]
            mc += ls[j] * gs[j]
            if not ge(gs[j], p.cutoff) and not le(m, p.m_waste_max) and not ge(mc / m, p.cutoff):
                out.append((i, j + 1))
    return out


def ref_near(
    ls: list[float], gs: list[float], found: list[tuple[int, int]], p: OreParams, strict: bool
) -> tuple[list[tuple[int, int]], list[tuple[Any, ...]]]:
    """Step 4 — сближенные интервалы in the stretches outside the intervals of step 2 (`found`): a chain grows
    from an ore unit down by pairs «Б + Р» while M(Б) ≤ Мпп, C of the whole chain ≥ Сб and (strict modes) no
    fragment «порода … порода» appears in it; it is picked when it meets К and lies farther than Мпп from the
    intervals. Returns the chains [a, b) and the log (what, a, b)."""
    out = []
    log: list[tuple[Any, ...]] = []
    edges = [0, *[x for ab in found for x in ab], len(gs)]
    for n in range(0, len(edges), 2):
        wa, wb = edges[n], edges[n + 1]
        units = [[ore, wa + ua, wa + ub] for ore, ua, ub in ref_units(gs[wa:wb], p.cutoff)]
        i = 0
        while i < len(units):
            if not units[i][0]:
                i += 1
                continue
            a = units[i][1]
            j = i
            while j + 2 < len(units):
                gap = units[j + 1]
                if not le(sums(ls, gs, gap[1], gap[2])[0], p.m_waste_max):
                    break
                end = units[j + 2][2]
                m, mc = sums(ls, gs, a, end)
                if not ge(mc / m, p.cutoff):
                    log.append(("rejected_c", a, end))
                    break
                if strict and brute_layers(ls, gs, a, end, p):
                    log.append(("layer", a, end))
                    break
                j += 2
            b = units[j][2]
            if j > i:
                far_up = wa == 0 or not le(sums(ls, gs, wa, a)[0], p.m_waste_max)
                far_down = wb == len(gs) or not le(sums(ls, gs, b, wb)[0], p.m_waste_max)
                if not ref_k(ls, gs, a, b, p):
                    log.append(("not_k", a, b))
                elif not (far_up and far_down):
                    log.append(("close", a, b))
                else:
                    log.append(("near_joined", a, b))
                    out.append((a, b))
            i = j + 1
    return out, log


def ref_mode(
    ls: list[float], gs: list[float], p: OreParams
) -> tuple[list[tuple[Any, ...]], list[tuple[int, int]], list[tuple[Any, ...]]]:
    """A line in the mode of `p` (not «по контактам»): rows (class, a, b, basis), the candidates of ore
    intervals [a, b) and the log of steps 3–4."""
    if p.mode == "simple":
        found = ref_simple(ls, gs, p)
        return ref_rows(ls, gs, found, p), found, []
    units, _ = ref_soft(ls, gs, p)
    strict = p.mode in ("strict", "strict_excl")
    found = []
    layers = []
    log: list[tuple[Any, ...]] = []
    for a, b in units:
        if not strict:
            found.append((a, b))
            continue
        parts, part_log = ref_strict(ls, gs, a, b, p, p.mode == "strict_excl")
        log += part_log
        found += [(x, y) for kind, x, y in parts if kind == "ore"]
        layers += [(x, y) for kind, x, y in parts if kind == "interlayer"]
    chains, near_log = ref_near(ls, gs, units, p, strict)
    found = sorted(found + chains)
    return ref_rows(ls, gs, found, p, layers), found, log + near_log


def check_reference(
    res: OreIntervals, holes: list[list[tuple[float, float, float]]], p: OreParams
) -> tuple[dict[str, int], dict[str, int]]:
    """The rows of `res` with NESTED = 0 are those of the plain-Python reference (`ref_mode`), line by line.
    Returns the number of rows of every class and the number of log records of every kind of the reference."""
    got = [r for r in res.rows() if not r["nested"]]
    classes: dict[str, int] = {}
    logged: dict[str, int] = {}
    first = 0
    n = 0
    for h, hole in enumerate(holes):
        for line in ref_lines(hole):
            ls = [hole[i][1] - hole[i][0] for i in line]
            gs = [hole[i][2] for i in line]
            rows, _, log = ref_mode(ls, gs, p)
            for record in log:
                logged[record[0]] = logged.get(record[0], 0) + 1
            for cls, a, b, basis in rows:
                assert n < len(got), (h, cls, a, b)
                row = got[n]
                n += 1
                m, mc = sums(ls, gs, a, b)
                assert (row["hole"], row["cls"], row["basis"]) == (h, cls, basis), (row, cls, a, b, basis, gs)
                assert (row["row0"], row["row1"]) == (first + line[a], first + line[b - 1]), (row, cls, a, b, gs)
                assert close(row["m"], m) and close(row["mc"], mc)
                classes[cls] = classes.get(cls, 0) + 1
        first += len(hole)
    assert n == len(got)
    return classes, logged
