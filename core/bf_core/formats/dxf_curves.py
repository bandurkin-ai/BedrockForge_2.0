"""Curves of a DXF file as polylines: ARC, CIRCLE, ELLIPSE, SPLINE and the bulges of polylines (ТЗ 7.18, 9.5).

`tol` (ADR 0011 §2: 0.1 m) is the largest distance between a curve and the chord that replaces a piece of it
(BF: `ezdxf` `flattening(0.1)`). Arcs are cut into equal steps: the sagitta r·(1 − cos(step / 2)) ≤ `tol`, and a
step is at most π / 8 (16 chords in a full turn, as BF: four per quarter), so a small circle stays round. An
ellipse is cut in its parameter with the step √(8·tol / a): the chord error is bounded by step²/8 · max|C″| and
|C″| ≤ a, the major half-axis. A spline is evaluated by `ezdxf.math.BSpline` (ADR 0011: the mathematics of curves
is ezdxf's) and refined where one of three probes of a chord is farther than 0.9·`tol` from it.

Everything works on arrays of many entities at once and counts the vertices before it makes them: `limit` is the
number of vertices the caller can still take (`FormatError("too_large")` beyond it — a circle of an absurd radius
is a "bomb" of a few bytes). Vertices of a polyline with bulges stay as they are, bit for bit; only the points of
the arcs are added between them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from itertools import chain
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError

TOL = 0.1  # metres
MIN_STEP = math.pi / 8
TWO_PI = 2.0 * math.pi
SPLINE_ROUNDS = 16  # every round cuts a chord that is too far into four
PROBE = 0.9  # three probes can miss the farthest point of a chord by a few per cent (a cubic: 5 %): the margin
HARD_LIMIT = 1 << 31  # vertices of one call when the caller sets no limit
PORTION = 50_000  # points of a spline evaluated between two signs of life


def too_many(limit: int) -> FormatError:
    return FormatError(
        "too_large",
        f"В файле больше {limit:,} вершин (с учётом спрямления кривых): он слишком велик.".replace(",", " "),
        "Разделите чертёж на части или упростите линии в исходной программе.",
    )


def _check(total: float, limit: int | None) -> None:
    limit = HARD_LIMIT if limit is None else limit
    if not total <= limit:  # also NaN and infinity
        raise too_many(limit)


def _ranges(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For items repeated `counts` times: (the item of every element, its number within the item)."""
    owner = np.repeat(np.arange(len(counts), dtype=np.int64), counts)
    within = np.arange(len(owner), dtype=np.int64) - np.repeat(np.cumsum(counts) - counts, counts)
    return owner, within


@np.errstate(all="ignore")
def arc_steps(radius: Any, sweep: Any, tol: float = TOL) -> np.ndarray:
    """Chords (float, ≥ 1) for arcs of `radius` over `sweep` radians: sagitta ≤ tol, step ≤ MIN_STEP."""
    r = np.abs(np.asarray(radius, dtype=np.float64))
    # 2·acos(1 − tol / r) without the loss of digits at a large radius
    step = np.minimum(4.0 * np.arcsin(np.sqrt(np.minimum(tol / (2.0 * r), 1.0))), MIN_STEP)
    n = np.ceil(np.abs(np.asarray(sweep, dtype=np.float64)) / step * (1.0 - 1e-12))
    return np.maximum(n, 1.0)


@np.errstate(all="ignore")
def span(start: Any, end: Any, turn: float) -> np.ndarray:
    """Counter-clockwise angle from `start` to `end` in [0, turn]: equal angles — 0, a whole turn apart — a turn."""
    a = np.asarray(start, dtype=np.float64)
    b = np.asarray(end, dtype=np.float64)
    out = np.mod(b - a, turn)
    whole = np.isclose(out, 0.0, atol=1e-12) | np.isclose(out, turn, atol=1e-12)
    return np.where(whole, np.where(np.isclose(a, b, atol=1e-12), 0.0, turn), out)


@np.errstate(all="ignore")
def arcs(
    cx: Any, cy: Any, radius: Any, start_deg: Any, end_deg: Any, tol: float = TOL, limit: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """ARC entities, counter-clockwise from the start to the end angle → (xy of all vertices, vertices of each)."""
    cx, cy, r = (np.asarray(v, dtype=np.float64) for v in (cx, cy, radius))
    a0 = np.radians(np.asarray(start_deg, dtype=np.float64))
    sweep = np.radians(span(start_deg, end_deg, 360.0))
    steps = arc_steps(r, sweep, tol)
    _check(float(steps.sum()) + len(steps), limit)
    n = steps.astype(np.int64)
    owner, k = _ranges(n + 1)
    angle = a0[owner] + sweep[owner] * (k / n[owner])
    return np.column_stack([cx[owner] + r[owner] * np.cos(angle), cy[owner] + r[owner] * np.sin(angle)]), n + 1


@np.errstate(all="ignore")
def circles(cx: Any, cy: Any, radius: Any, tol: float = TOL, limit: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """CIRCLE entities → (xy, vertices of each); the first vertex is not repeated: the line is closed."""
    cx, cy, r = (np.asarray(v, dtype=np.float64) for v in (cx, cy, radius))
    steps = arc_steps(r, TWO_PI, tol)
    _check(float(steps.sum()), limit)
    n = steps.astype(np.int64)
    owner, k = _ranges(n)
    angle = TWO_PI * (k / n[owner])
    return np.column_stack([cx[owner] + r[owner] * np.cos(angle), cy[owner] + r[owner] * np.sin(angle)]), n


@np.errstate(all="ignore")
def ellipses(
    center: Any,
    major: Any,
    ratio: Any,
    start: Any,
    end: Any,
    normal: Any,
    tol: float = TOL,
    limit: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ELLIPSE entities (WCS: centre, end of the major axis from the centre, minor / major, parameters in radians,
    normal) → (xyz, vertices of each, closed). A whole ellipse is closed and does not repeat its first vertex."""
    c = np.asarray(center, dtype=np.float64).reshape(-1, 3)
    m = np.asarray(major, dtype=np.float64).reshape(-1, 3)
    nrm = np.asarray(normal, dtype=np.float64).reshape(-1, 3)
    ratio = np.asarray(ratio, dtype=np.float64)
    t0 = np.asarray(start, dtype=np.float64)
    a = np.linalg.norm(m, axis=1)
    minor = np.cross(nrm / np.linalg.norm(nrm, axis=1)[:, None], m) * ratio[:, None]
    sweep = span(t0, end, TWO_PI)
    half = np.maximum(a, np.linalg.norm(minor, axis=1))
    step = np.minimum(np.sqrt(8.0 * tol / half), MIN_STEP)
    steps = np.maximum(np.ceil(sweep / step * (1.0 - 1e-12)), 1.0)
    closed = np.isclose(sweep, TWO_PI, atol=1e-12)
    _check(float(steps.sum()) + len(steps), limit)
    n = steps.astype(np.int64)
    counts = np.where(closed, n, n + 1)
    owner, k = _ranges(counts)
    t = t0[owner] + sweep[owner] * (k / n[owner])
    xyz = c[owner] + np.cos(t)[:, None] * m[owner] + np.sin(t)[:, None] * minor[owner]
    return xyz, counts, closed


@np.errstate(all="ignore")
def bulges(
    xy: np.ndarray,
    bulge: np.ndarray,
    counts: np.ndarray,
    closed: np.ndarray,
    tol: float = TOL,
    limit: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Polylines in the plane whose segments may be arcs: `bulge` of a vertex is tan(angle / 4) of the arc to the
    next vertex (positive — counter-clockwise; in a closed polyline the last vertex leads to the first).

    Returns (xy with the points of the arcs put in, vertices of each polyline, origin): `origin` — for every
    output vertex the input vertex it is or follows, to carry Z and the like. Input vertices are not changed.
    """
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    total = len(xy)
    counts = np.asarray(counts, dtype=np.int64)
    starts = np.cumsum(counts) - counts
    some = counts > 0
    last = (starts + counts - 1)[some]
    nxt = np.arange(1, total + 1, dtype=np.int64)
    nxt[last] = starts[some]
    b = np.where(np.isfinite(bulge), bulge, 0.0)
    b[last[~np.asarray(closed, dtype=bool)[some]]] = 0.0  # the last vertex of an open polyline starts nothing
    arc = np.flatnonzero(b != 0.0)
    if len(arc) == 0:
        return xy, counts, np.arange(total, dtype=np.int64)
    p1, p2, ba = xy[arc], xy[nxt[arc]], b[arc]
    d = p2 - p1
    chord = np.hypot(d[:, 0], d[:, 1])
    theta = 4.0 * np.arctan(ba)
    radius = chord * (1.0 + ba * ba) / (4.0 * np.abs(ba))
    steps = np.where(chord > 0.0, arc_steps(radius, theta, tol), 1.0)
    _check(float(steps.sum()) - len(steps) + total, limit)
    n = steps.astype(np.int64)
    centre = (p1 + p2) * 0.5 + np.column_stack([-d[:, 1], d[:, 0]]) * ((1.0 - ba * ba) / (4.0 * ba))[:, None]
    a1 = np.arctan2(p1[:, 1] - centre[:, 1], p1[:, 0] - centre[:, 0])
    owner, k = _ranges(n - 1)
    angle = a1[owner] + theta[owner] * ((k + 1) / n[owner])
    points = centre[owner] + radius[owner][:, None] * np.column_stack([np.cos(angle), np.sin(angle)])
    add = np.zeros(total, dtype=np.int64)
    add[arc] = n - 1
    before = np.concatenate([[0], np.cumsum(add)])
    pos = np.arange(total, dtype=np.int64) + before[:-1]
    out = np.empty((total + int(before[-1]), 2), dtype=np.float64)
    origin = np.empty(len(out), dtype=np.int64)
    out[pos] = xy
    origin[pos] = np.arange(total, dtype=np.int64)
    where = pos[arc][owner] + 1 + k
    out[where] = points
    origin[where] = arc[owner]
    return out, counts + before[starts + counts] - before[starts], origin


@np.errstate(all="ignore")
def _distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from points p to the segments a–b (rows)."""
    ab = b - a
    length = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.where(length > 0.0, np.einsum("ij,ij->i", p - a, ab) / length, 0.0), 0.0, 1.0)
    return np.asarray(np.linalg.norm(p - (a + t[:, None] * ab), axis=1))


def spline(
    control: Any,
    degree: int,
    knots: Sequence[float] = (),
    weights: Sequence[float] = (),
    fit: Any = (),
    tangents: Sequence[Any] | None = None,
    tol: float = TOL,
    limit: int | None = None,
    alive: Callable[[], None] | None = None,
) -> np.ndarray:
    """A SPLINE entity → xyz of a polyline within `tol` of it. Control points with knots (and weights), or fit
    points alone (a cubic spline through them, as CAD programs build it). ValueError — the data make no spline.
    `alive` is called between portions of the work (a long spline stays cancellable)."""
    try:
        return _spline(control, degree, knots, weights, fit, tangents, tol, limit, alive)
    except FormatError:
        raise
    except Exception as exc:  # ezdxf has error types of its own: whatever it is, the data make no spline
        raise ValueError(str(exc)) from exc


@np.errstate(all="ignore")
def _spline(
    control: Any,
    degree: int,
    knots: Sequence[float],
    weights: Sequence[float],
    fit: Any,
    tangents: Sequence[Any] | None,
    tol: float,
    limit: int | None,
    alive: Callable[[], None] | None,
) -> np.ndarray:
    from ezdxf.math import BSpline, fit_points_to_cad_cv

    control = np.asarray(control, dtype=np.float64).reshape(-1, 3)
    fit = np.asarray(fit, dtype=np.float64).reshape(-1, 3)
    if len(control):
        curve = BSpline(
            control.tolist(), order=int(degree) + 1, knots=list(knots) or None, weights=list(weights) or None
        )
    elif len(fit) >= 2:
        curve = fit_points_to_cad_cv(fit.tolist(), tangents=tangents)
    else:
        raise ValueError("a spline without control and fit points")
    kn = np.asarray(curve.knots(), dtype=np.float64)
    t0, t1 = float(kn[curve.order - 1]), float(kn[curve.count])
    if not (math.isfinite(t0) and math.isfinite(t1) and t1 > t0):
        raise ValueError("a spline with an empty range of the parameter")

    def points(t: np.ndarray) -> np.ndarray:
        out = np.empty((len(t), 3), dtype=np.float64)
        for lo in range(0, len(t), PORTION):  # ezdxf: a third of a microsecond per point
            part = t[lo : lo + PORTION].tolist()
            flat = np.fromiter(chain.from_iterable(curve.points(part)), dtype=np.float64, count=3 * len(part))
            out[lo : lo + len(part)] = flat.reshape(-1, 3)
            if alive is not None:
                alive()
        if not np.isfinite(out).all():
            raise ValueError("a spline with points that are not numbers")
        return out

    spans = np.unique(kn[(kn >= t0) & (kn <= t1)])
    quarter = np.array([0.0, 0.25, 0.5, 0.75])
    t = np.append((spans[:-1, None] + np.diff(spans)[:, None] * quarter).ravel(), t1)
    _check(len(t), limit)
    p = points(t)
    todo = np.ones(len(t) - 1, dtype=bool)
    probes = quarter[1:]
    for _ in range(SPLINE_ROUNDS):
        seg = np.flatnonzero(todo)
        if len(seg) == 0:
            break
        q = t[seg, None] + (t[seg + 1] - t[seg])[:, None] * probes
        found = points(q.ravel()).reshape(-1, 3, 3)
        a, b = p[seg], p[seg + 1]
        away = np.max([_distance(found[:, j], a, b) for j in range(3)], axis=0)
        far = away > PROBE * tol
        if not far.any():
            break
        cut = seg[far]
        # the distance falls as the step squared: an estimate of the chords still to come stops a "bomb" at once
        _check(len(t) + float(np.sqrt(away[far] / tol).sum()), limit)
        add = np.zeros(len(t), dtype=np.int64)
        add[cut] = 3
        pos = np.arange(len(t), dtype=np.int64) + np.cumsum(add) - add
        t_new = np.empty(len(t) + 3 * len(cut), dtype=np.float64)
        p_new = np.empty((len(t_new), 3), dtype=np.float64)
        todo = np.zeros(len(t_new) - 1, dtype=bool)
        t_new[pos], p_new[pos] = t, p
        todo[pos[cut]] = True
        for j in range(3):
            t_new[pos[cut] + 1 + j] = q[far, j]
            p_new[pos[cut] + 1 + j] = found[far, j]
            todo[pos[cut] + 1 + j] = True
        t, p = t_new, p_new
    return p
