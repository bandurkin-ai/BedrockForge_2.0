"""Helpers of the ore-interval tests (ТЗ 7.35 Д-26): the examples of the sources (`data/ore/examples.json`),
sample lines for the kernel and a plain-Python reference of the rules — lists and direct sums over the samples
of every fragment, nothing shared with the numba kernels."""

from __future__ import annotations

import json
import math
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
        hit = [r for r in rows if r["cls"] == want["cls"] and r["row0"] == first_row + a - 1 and r["row1"] == first_row + b - 1]
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


def ref_rows(ls: list[float], gs: list[float], found: list[tuple[int, int]], p: OreParams) -> list[tuple[Any, ...]]:
    """Rows (class, a, b, basis) of a line from the intervals a mode found (step 5)."""
    cls = ["waste" if not ge(g, p.cutoff) else "subgrade" for g in gs]
    owner = [-1] * len(gs)
    basis = {}
    for n, (a, b) in enumerate(found):
        k = ref_k(ls, gs, a, b, p)
        if k:
            basis[n] = k
            for i in range(a, b):
                owner[i] = n
    rows: list[list[Any]] = []
    for i in range(len(gs)):
        key = ("ore", owner[i]) if owner[i] >= 0 else (cls[i], -1)
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
