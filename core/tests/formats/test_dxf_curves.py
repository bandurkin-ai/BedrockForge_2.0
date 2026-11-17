"""DXF curves → polylines within 0.1 m: arcs, circles, ellipses, bulges, splines (ТЗ 7.18, 9.5; ADR 0011 §2)."""

from __future__ import annotations

import math
import random
import tracemalloc
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
from ezdxf.math import BSpline, ConstructionEllipse, arc_angle_span_deg, bulge_to_arc, fit_points_to_cad_cv

from bf_core.formats import dxf_curves as curves
from bf_core.formats.dxf_lines import read_dxf
from bf_core.formats.errors import FormatError

from .dxf_builder import drawing, tags

TOL = 0.1
X0, Y0 = 466570.586, 4584699.697


def _segments(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from every point to its own segment a–b (rows)."""
    ab = b - a
    length = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.einsum("ij,ij->i", points - a, ab) / np.where(length > 0, length, 1.0), 0.0, 1.0)
    return np.asarray(np.linalg.norm(points - (a + t[:, None] * ab), axis=1))


def _to_polyline(points: np.ndarray, line: np.ndarray, window: int = 12) -> np.ndarray:
    """Distance from every point to the polyline (rows of `line` joined by segments).

    Small inputs (or `window` 0) — every point against every segment. Big ones (both run along the same curve in
    the same direction) — against the segments around the same share of the length: never less than the true one."""
    points, line = np.atleast_2d(points), np.atleast_2d(line)
    if window == 0 or len(points) * len(line) <= 2_000_000:
        a, b = line[:-1], line[1:]
        ab = b - a
        length = np.einsum("ij,ij->i", ab, ab)
        out = np.empty(len(points))
        step = max(1, 2_000_000 // len(line))
        for lo in range(0, len(points), step):
            p = points[lo : lo + step, None, :]
            t = np.clip(np.einsum("mkj,kj->mk", p - a, ab) / np.where(length > 0, length, 1.0), 0.0, 1.0)
            out[lo : lo + step] = np.linalg.norm(p - (a + t[..., None] * ab), axis=2).min(axis=1)
        return out

    def share(v: np.ndarray) -> np.ndarray:
        s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(v, axis=0), axis=1))])
        return np.asarray(s / s[-1])

    at = np.clip(np.searchsorted(share(line), share(points)) - 1, 0, len(line) - 2)
    out = np.full(len(points), np.inf)
    for shift in range(-window, window + 1):
        k = np.clip(at + shift, 0, len(line) - 2)
        out = np.minimum(out, _segments(points, line[k], line[k + 1]))
    return out


def _split(xy: np.ndarray, counts: np.ndarray) -> list[np.ndarray]:
    return np.split(xy, np.cumsum(counts)[:-1])


# --- arcs and circles ---------------------------------------------------------------------------------


def test_the_step_of_an_arc_keeps_the_sagitta_within_the_tolerance() -> None:
    radius = np.array([1e-3, 0.05, 0.1, 0.5, 1.3, 2.0, 10.0, 137.5, 1e3, 5e4, 1e6, 1e9, 1e12])
    for sweep in (2 * math.pi, 1.0, 0.01):
        n = curves.arc_steps(radius, sweep, TOL)
        step = sweep / n
        sagitta = 2.0 * radius * np.sin(step / 4.0) ** 2  # r·(1 − cos(step / 2)) without the loss of digits
        assert (sagitta <= TOL * (1 + 1e-9)).all() and (n >= 1).all()
        assert (step <= math.pi / 8 * (1 + 1e-9)).all() or sweep < math.pi / 8
        # not finer than needed: one chord fewer would break one of the two rules
        fewer = np.maximum(n - 1, 1)
        wider = sweep / fewer
        broken = (2.0 * radius * np.sin(wider / 4.0) ** 2 > TOL) | (wider > math.pi / 8) | (n == 1)
        assert broken.all()
    assert curves.arc_steps(1e12, 2 * math.pi)[()] == pytest.approx(
        2 * math.pi / (2 * math.sqrt(2 * TOL / 1e12)), rel=1e-6
    )
    assert curves.arc_steps(0.01, 2 * math.pi)[()] == 16  # a small circle is still round
    assert curves.arc_steps(5.0, 2 * math.pi, 0.001)[()] > curves.arc_steps(5.0, 2 * math.pi, 0.1)[()]


def test_arcs() -> None:
    rnd = random.Random(1)
    cx = np.array([X0 + rnd.uniform(-1e3, 1e3) for _ in range(200)])
    cy = np.array([Y0 + rnd.uniform(-1e3, 1e3) for _ in range(200)])
    r = np.array([10 ** rnd.uniform(-2, 4) for _ in range(200)])
    a0 = np.array([rnd.uniform(-720, 720) for _ in range(200)])
    a1 = np.array([rnd.uniform(-720, 720) for _ in range(200)])
    a0[:4], a1[:4] = [0.0, 350.0, 90.0, 10.0], [360.0, 10.0, 90.0, 370.0]
    xy, counts = curves.arcs(cx, cy, r, a0, a1, TOL)
    assert counts.sum() == len(xy) and (counts >= 2).all()
    for i, line in enumerate(_split(xy, counts)):
        sweep = math.radians(arc_angle_span_deg(a0[i], a1[i]))  # the etalon of the angle: ezdxf
        d = np.hypot(line[:, 0] - cx[i], line[:, 1] - cy[i])
        assert np.abs(d - r[i]).max() <= 1e-9 * max(1.0, r[i]) + 2e-9  # on the circle (coordinates of 5·10⁶)
        first = (cx[i] + r[i] * math.cos(math.radians(a0[i])), cy[i] + r[i] * math.sin(math.radians(a0[i])))
        last = (cx[i] + r[i] * math.cos(math.radians(a1[i])), cy[i] + r[i] * math.sin(math.radians(a1[i])))
        assert np.abs(line[0] - first).max() <= 2e-9 and np.abs(line[-1] - last).max() <= 1e-8 * max(1.0, r[i])
        step = sweep / (len(line) - 1)
        assert r[i] * (1 - math.cos(step / 2)) <= TOL * (1 + 1e-9)  # the sagitta of every chord
        if sweep > 0:  # counter-clockwise, in equal steps
            angles = np.unwrap(np.arctan2(line[:, 1] - cy[i], line[:, 0] - cx[i]))
            assert np.allclose(np.diff(angles), step, atol=1e-6 + 1e-9 * 5e6 / r[i])
    assert counts[0] == curves.arc_steps(r[0], 2 * math.pi)[()] + 1  # 0 → 360 is a whole turn
    assert counts[2] == 2 and np.allclose(*_split(xy, counts)[2])  # equal angles: no sweep


def test_circles() -> None:
    r = np.array([0.01, 1.0, 50.0, 2500.0])
    xy, counts = curves.circles(np.full(4, X0), np.full(4, Y0), r, TOL)
    assert counts.tolist() == [16, 16, 50, 352]
    for i, line in enumerate(_split(xy, counts)):
        assert np.abs(np.hypot(line[:, 0] - X0, line[:, 1] - Y0) - r[i]).max() <= 2e-9
        assert line[0].tolist() == [X0 + r[i], Y0] and not np.array_equal(
            line[0], line[-1]
        )  # no repeat: closed by flag
        ring = np.vstack([line, line[:1]])
        dense = np.linspace(0, 2 * math.pi, 5000)
        true = np.column_stack([X0 + r[i] * np.cos(dense), Y0 + r[i] * np.sin(dense)])
        assert _to_polyline(true, ring).max() <= min(TOL, r[i] * (1 - math.cos(math.pi / 16))) + 1e-8
    fine, n = curves.circles([0.0], [0.0], [50.0], 0.001)
    assert n[0] > 400 and 50.0 * (1 - math.cos(math.pi / n[0])) <= 0.001


def test_ellipses() -> None:
    rnd = random.Random(2)
    k = 60
    center = np.array(
        [(X0 + rnd.uniform(-1e3, 1e3), Y0 + rnd.uniform(-1e3, 1e3), rnd.uniform(0, 900)) for _ in range(k)]
    )
    major = np.array([(rnd.uniform(-300, 300), rnd.uniform(-300, 300), rnd.uniform(-50, 50)) for _ in range(k)])
    normal = np.cross(major, np.array([(rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(1, 2)) for _ in range(k)]))
    ratio = np.array([rnd.uniform(0.05, 1.0) for _ in range(k)])
    t0 = np.array([rnd.uniform(0, 6.2) for _ in range(k)])
    t1 = np.array([rnd.uniform(0, 6.2) for _ in range(k)])
    t0[:3], t1[:3] = 0.0, 2 * math.pi  # whole ellipses
    xyz, counts, closed = curves.ellipses(center, major, ratio, t0, t1, normal, TOL)
    assert closed.tolist() == [True] * 3 + [False] * (k - 3) and counts.sum() == len(xyz)
    for i, line in enumerate(np.split(xyz, np.cumsum(counts)[:-1])):
        tool = ConstructionEllipse(tuple(center[i]), tuple(major[i]), tuple(normal[i]), ratio[i], t0[i], t1[i])
        sweep = tool.param_span
        params = t0[i] + sweep * np.arange(len(line)) / (len(line) if closed[i] else len(line) - 1)
        want = np.array([tuple(v) for v in tool.vertices(params.tolist())])
        assert np.abs(line - want).max() <= 1e-8  # the etalon: the ellipse of ezdxf at the same parameters
        dense = np.array([tuple(v) for v in tool.vertices(np.linspace(t0[i], t0[i] + sweep, 4000).tolist())])
        ring = np.vstack([line, line[:1]]) if closed[i] else line
        assert _to_polyline(dense, ring).max() <= TOL + 1e-8
    assert (
        len(curves.ellipses(center[:1], major[:1], ratio[:1], [0.0], [2 * math.pi], normal[:1], 0.001)[0])
        > 4 * counts[0]
    )


# --- bulges -------------------------------------------------------------------------------------------


def test_bulges_are_arcs_between_the_vertices() -> None:
    rnd = random.Random(4)
    counts = np.array([5, 1, 4, 0, 30, 2])
    closed = np.array([False, False, True, False, True, False])
    total = int(counts.sum())
    xy = np.array([(X0 + rnd.uniform(-500, 500), Y0 + rnd.uniform(-500, 500)) for _ in range(total)])
    bulge = np.array([rnd.choice([0.0, 0.0, 1.0, -1.0, 0.3, -0.05, 2.5, 1e-7, math.nan]) for _ in range(total)])
    out, new_counts, origin = curves.bulges(xy, bulge, counts, closed, TOL)
    assert new_counts.sum() == len(out) == len(origin) and (new_counts >= counts).all()
    starts, new_starts = np.cumsum(counts) - counts, np.cumsum(new_counts) - new_counts
    for k in range(len(counts)):
        src = xy[starts[k] : starts[k] + counts[k]]
        got = out[new_starts[k] : new_starts[k] + new_counts[k]]
        own = origin[new_starts[k] : new_starts[k] + new_counts[k]] - starts[k]
        at = 0
        for j in range(counts[k]):
            assert got[at].tobytes() == src[j].tobytes() and own[at] == j  # the vertices themselves are untouched
            at += 1
            b = bulge[starts[k] + j]
            last = j == counts[k] - 1
            if not math.isfinite(b) or b == 0.0 or (last and not closed[k]) or counts[k] == 1:
                continue
            nxt = src[0] if last else src[j + 1]
            centre, a_start, a_end, radius = bulge_to_arc(tuple(src[j]), tuple(nxt), b)  # the etalon: ezdxf
            inside = []
            while (
                at < len(got) and own[at] == j and not (j + 1 < counts[k] and got[at].tobytes() == src[j + 1].tobytes())
            ):
                inside.append(got[at])
                at += 1
            arc = np.array([src[j], *inside, nxt])
            d = np.hypot(arc[:, 0] - centre.x, arc[:, 1] - centre.y)
            assert np.abs(d - radius).max() <= 1e-8 * max(1.0, radius)
            angle = 4 * math.atan(b)
            step = abs(angle) / (len(arc) - 1)
            assert radius * (1 - math.cos(step / 2)) <= TOL * (1 + 1e-9) and step <= math.pi / 8 + 1e-9
            turn = np.unwrap(np.arctan2(arc[:, 1] - centre.y, arc[:, 0] - centre.x))
            assert np.allclose(np.diff(turn), angle / (len(arc) - 1), atol=1e-6)  # positive — counter-clockwise
        assert at == len(got)
    same = curves.bulges(xy, np.zeros(total), counts, closed, TOL)
    assert same[0] is xy or same[0].tobytes() == xy.tobytes()
    assert same[2].tolist() == list(range(total))


def test_a_semicircle_and_a_small_bulge() -> None:
    xy = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 5.0]])
    out, counts, origin = curves.bulges(xy, np.array([1.0, 0.0, 0.0]), np.array([3]), np.array([False]), TOL)
    assert counts.tolist() == [10] and origin.tolist() == [0] * 8 + [1, 2]
    arc = out[:9]
    assert np.allclose(np.hypot(arc[:, 0] - 5.0, arc[:, 1]), 5.0, atol=1e-12) and (arc[1:-1, 1] < 0).all()  # below: CCW
    assert out[4].tolist() == pytest.approx([5.0, -5.0])
    clockwise, _, _ = curves.bulges(xy, np.array([-1.0, 0.0, 0.0]), np.array([3]), np.array([False]), TOL)
    assert clockwise[4].tolist() == pytest.approx([5.0, 5.0])
    flat, counts, _ = curves.bulges(xy, np.array([0.01, 0.0, 0.0]), np.array([3]), np.array([False]), TOL)
    assert counts.tolist() == [3] and flat.tobytes() == xy.tobytes()  # sagitta 0.05 m: the chord is close enough
    closing, counts, _ = curves.bulges(xy, np.array([0.0, 0.0, 1.0]), np.array([3]), np.array([True]), TOL)
    assert counts[0] > 3 and closing[:3].tobytes() == xy.tobytes()  # the arc from the last vertex back to the first


# --- splines ------------------------------------------------------------------------------------------


def _dense(tool: BSpline) -> np.ndarray:
    """The etalon of a spline: the adaptive flattening of ezdxf to half a millimetre."""
    return np.array([tuple(v) for v in tool.flattening(0.0005, 16)])


@pytest.mark.parametrize(
    ("count", "scale", "degree", "rational"),
    [(4, 1.0, 3, False), (5, 30.0, 2, True), (9, 300.0, 3, False), (40, 300.0, 3, True), (60, 30.0, 5, False),
     (25, 1000.0, 3, False), (12, 150.0, 4, True)],
)  # fmt: skip
def test_splines_stay_within_the_tolerance(count: int, scale: float, degree: int, rational: bool) -> None:
    rnd = random.Random(count)
    control = np.array(
        [
            (X0 + rnd.uniform(-scale, scale), Y0 + rnd.uniform(-scale, scale), rnd.uniform(0, scale / 10))
            for _ in range(count)
        ]
    )
    weights = [rnd.uniform(0.2, 5.0) for _ in range(count)] if rational else []
    tool = BSpline(control.tolist(), order=degree + 1, weights=weights or None)
    true = _dense(tool)
    for tol in (TOL, 0.01):
        line = curves.spline(control, degree, tool.knots(), weights, tol=tol)
        assert _to_polyline(true, line).max() <= tol  # no point of the curve is farther than `tol` from the polyline
        assert _to_polyline(line, true).max() <= 0.001  # the vertices are on the curve
        assert np.abs(line[0] - true[0]).max() <= 1e-8 and np.abs(line[-1] - true[-1]).max() <= 1e-8
        if tol == TOL:
            coarse = len(line)
            if scale >= 300:
                assert coarse > 4 * count  # sharp turns over hundreds of metres need many chords
        else:
            assert len(line) > coarse


def test_a_rational_spline_is_an_exact_circle() -> None:
    w = math.sqrt(0.5)
    control = [(1, 0, 0), (1, 1, 0), (0, 1, 0), (-1, 1, 0), (-1, 0, 0), (-1, -1, 0), (0, -1, 0), (1, -1, 0), (1, 0, 0)]
    knots = [0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 4]
    r = 500.0
    line = curves.spline(np.array(control) * r + [X0, Y0, 7.0], 2, knots, [1, w, 1, w, 1, w, 1, w, 1], tol=TOL)
    d = np.hypot(line[:, 0] - X0, line[:, 1] - Y0)
    assert np.abs(d - r).max() <= 1e-8 and np.abs(line[:, 2] - 7.0).max() <= 1e-12
    assert np.allclose(line[0], line[-1], atol=1e-9)
    mid = (line[:-1] + line[1:]) / 2
    assert (r - np.hypot(mid[:, 0] - X0, mid[:, 1] - Y0)).max() <= TOL  # the sagitta of every chord
    assert 40 <= len(line) <= 400


def test_a_spline_through_fit_points_and_bad_splines() -> None:
    fit = [(X0, Y0, 0.0), (X0 + 100, Y0 + 250, 5.0), (X0 + 300, Y0 + 100, 9.0), (X0 + 500, Y0 + 400, 2.0)]
    line = curves.spline(np.empty((0, 3)), 3, fit=fit, tol=TOL)
    tool = fit_points_to_cad_cv(fit)
    assert _to_polyline(_dense(tool), line).max() <= TOL
    assert _to_polyline(np.array(fit), line).max() <= 1e-6  # the curve passes through the fit points
    tangents = [(1.0, 0.0, 0.0), (0.0, -1.0, 0.0)]
    bent = curves.spline(np.empty((0, 3)), 3, fit=fit, tangents=tangents, tol=TOL)
    assert _to_polyline(_dense(fit_points_to_cad_cv(fit, tangents=tangents)), bent).max() <= TOL
    control = np.array(fit)
    for bad in (
        lambda: curves.spline(np.empty((0, 3)), 3),  # nothing at all
        lambda: curves.spline(control[:1], 3),  # one control point
        lambda: curves.spline(np.empty((0, 3)), 3, fit=fit[:1]),
        lambda: curves.spline(control, 3, [0.0, 1.0, 2.0]),  # too few knots
        lambda: curves.spline(control, 3, [0.0] * 8),  # no range of the parameter
        lambda: curves.spline(control * np.array([np.nan, 1, 1]), 3),
    ):
        with pytest.raises(ValueError):
            bad()


# --- limits -------------------------------------------------------------------------------------------


def test_curves_count_before_they_allocate() -> None:
    tracemalloc.start()
    for bomb in (
        lambda: curves.circles([0.0], [0.0], [1e30], TOL, 1_000_000),
        lambda: curves.circles(np.zeros(5000), np.zeros(5000), np.full(5000, 1e5), TOL, 1_000_000),
        lambda: curves.arcs([0.0], [0.0], [1e25], [0.0], [270.0], TOL, 1_000_000),
        lambda: curves.arcs([0.0], [0.0], [math.inf], [0.0], [270.0], TOL),  # no limit given: the hard one
        lambda: curves.ellipses(
            [[0.0, 0.0, 0.0]], [[1e25, 0.0, 0.0]], [0.5], [0.0], [6.0], [[0.0, 0.0, 1.0]], TOL, 10**6
        ),
        lambda: curves.bulges(
            np.array([[0.0, 0.0], [1e25, 0.0]]), np.array([1.0, 0.0]), np.array([2]), np.array([False]), TOL, 10**6
        ),
        lambda: curves.spline(
            np.array([[0, 0, 0], [1e20, 1e22, 0], [-1e21, 1e20, 0], [1e22, -1e22, 0.0]]), 3, tol=TOL, limit=10**6
        ),
    ):
        with pytest.raises(FormatError) as info:
            bomb()
        assert info.value.code == "too_large" and "вершин" in info.value.message and info.value.hint
    assert tracemalloc.get_traced_memory()[1] < 50 << 20
    tracemalloc.stop()
    xy, counts = curves.circles([0.0], [0.0], [1e5], TOL, 3000)  # 2 222 vertices fit
    assert counts.tolist() == [len(xy)] and len(xy) <= 3000


# --- curves in a file ---------------------------------------------------------------------------------


def _true_curve(e: Any) -> list[np.ndarray]:
    """The etalon: the exact curve of an entity in the world system, cut a hundred times finer, in pieces."""

    def array(points: Any) -> np.ndarray:
        return np.array([tuple(v) for v in points], dtype=np.float64)

    kind = e.dxftype()
    if kind in ("CIRCLE", "ARC", "ELLIPSE"):
        return [array(e.flattening(0.0005))]
    if kind == "SPLINE":
        return [_dense(e.construction_tool())]
    pieces = []
    for part in e.virtual_entities():  # the lines and arcs a polyline with bulges is made of
        pieces.append(
            array(part.flattening(0.002)) if part.dxftype() == "ARC" else array([part.dxf.start, part.dxf.end])
        )
    return pieces


def test_curve_entities_of_a_drawing_stay_within_the_tolerance(tmp_path: Path) -> None:
    rnd = random.Random(8)
    doc = ezdxf.new("R2004")
    msp = doc.modelspace()

    def normal() -> tuple[float, float, float]:
        return rnd.choice(
            [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(0.2, 1))]
        )

    def xy() -> tuple[float, float]:
        return X0 + rnd.uniform(-2000, 2000), Y0 + rnd.uniform(-2000, 2000)

    for i in range(12):
        attribs = {"layer": f"L{i % 4}"}
        msp.add_circle(
            (*xy(), rnd.uniform(0, 500)), 10 ** rnd.uniform(-1, 3), dxfattribs=attribs | {"extrusion": normal()}
        )
        msp.add_arc((*xy(), rnd.uniform(0, 500)), 10 ** rnd.uniform(-1, 3), rnd.uniform(0, 360), rnd.uniform(0, 360),
                    dxfattribs=attribs | {"extrusion": normal()})  # fmt: skip
        major = (rnd.uniform(-400, 400), rnd.uniform(-400, 400), 0.0)
        msp.add_ellipse(
            (*xy(), 50.0), major, rnd.uniform(0.1, 1.0), rnd.uniform(0, 3), rnd.uniform(3.1, 6.2), dxfattribs=attribs
        )
        points = [(*xy(), 0.0, 0.0, rnd.choice([0.0, 0.4, -1.2, 3.0])) for _ in range(rnd.randrange(2, 9))]
        msp.add_lwpolyline(points, format="xyseb", close=rnd.random() < 0.5,
                           dxfattribs=attribs | {"elevation": rnd.uniform(0, 500), "extrusion": normal()})  # fmt: skip
        heavy = msp.add_polyline2d([xy() for _ in range(5)], close=rnd.random() < 0.5,
                                   dxfattribs=attribs | {"elevation": (0, 0, 120.0),
                                   "extrusion": normal()})  # fmt: skip
        for vertex in heavy.vertices:
            vertex.dxf.bulge = rnd.choice([0.0, 0.7, -0.3])
        msp.add_spline([(*xy(), rnd.uniform(0, 300)) for _ in range(rnd.randrange(3, 9))], dxfattribs=attribs)
        msp.add_open_spline([(*xy(), rnd.uniform(0, 300)) for _ in range(rnd.randrange(5, 12))], dxfattribs=attribs)
    msp.add_ellipse((X0, Y0, 0.0), (300.0, 0.0, 0.0), 0.5, dxfattribs={"layer": "whole"})
    path = tmp_path / "curves.dxf"
    doc.saveas(path)
    read = read_dxf(path, recover=False)
    assert read.warnings == [] and read.records == 12 * 7 + 1
    expected: dict[str, list[list[np.ndarray]]] = {}
    for e in ezdxf.readfile(path).modelspace():
        expected.setdefault(e.dxf.layer, []).append(_true_curve(e))
    worst = 0.0
    for layer in read.layers:
        assert layer.feature_count == len(expected[layer.name])
        for i, pieces in enumerate(expected[layer.name]):
            line = layer.line(i)
            window = 12 if len(pieces) == 1 else 0  # the pieces of a polyline: every segment is tried
            worst = max(worst, max(float(_to_polyline(piece, line, window).max()) for piece in pieces))
            on_curve = np.min([_to_polyline(line, piece, window) for piece in pieces], axis=0)
            assert on_curve.max() <= 0.003, (layer.name, i)  # the vertices lie on the curve (the etalon: 2 mm)
    assert 0.05 < worst <= TOL + 0.001  # the chords are as long as the tolerance lets them be, and no longer
    whole = next(layer for layer in read.layers if layer.name == "whole")
    assert whole.closed.tolist() == [1] and np.array_equal(whole.xyz[0], whole.xyz[-1])
    circles = [i for i in range(read.layers[0].line_count) if i % 7 == 0]
    assert all(read.layers[0].closed[i] == 1 for i in circles[:5])


def test_the_tolerance_can_be_changed(tmp_path: Path) -> None:
    body = tags(0, "CIRCLE", 8, "C", 10, X0, 20, Y0, 30, 5.0, 40, 100.0) + tags(
        0, "LWPOLYLINE", 8, "B", 70, 1, 10, 0.0, 20, 0.0, 42, 1.0, 10, 100.0, 20, 0.0, 42, 1.0
    )
    path = tmp_path / "c.dxf"
    path.write_text(drawing(body), encoding="cp1251", newline="")
    coarse, fine = read_dxf(path, recover=False), read_dxf(path, recover=False, tolerance=0.001)
    assert coarse.layers[0].point_count == 72 and fine.layers[0].point_count == 704
    for read, tol in ((coarse, 0.1), (fine, 0.001)):
        ring = read.layers[0].xyz
        assert np.abs(np.hypot(ring[:, 0] - X0, ring[:, 1] - Y0) - 100.0).max() <= 1e-8 and (ring[:, 2] == 5.0).all()
        mid = (ring[:-1] + ring[1:]) / 2
        assert (100.0 - np.hypot(mid[:, 0] - X0, mid[:, 1] - Y0)).max() <= tol
        two = read.layers[1]  # two semicircles: a circle of radius 50 around (50, 0)
        assert two.closed.tolist() == [1] and np.abs(np.hypot(two.xyz[:, 0] - 50.0, two.xyz[:, 1]) - 50.0).max() <= 1e-9
    assert coarse.layers[1].point_count < fine.layers[1].point_count
