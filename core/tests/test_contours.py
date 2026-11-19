"""Isolines with ELEV (ТЗ 7.23 «Изолинии»; ADR 0015 §7, 9.14).

Ported from BF `tests/test_surfaces.py` (contours) with the assertions kept; new: a cone, the layers «Основные»
and «Дополнительные» with ELEV, the limit of levels, batches and cancellation. Tolerances next to every check.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.geo.contours import MAJOR, MINOR, SurfaceError, contour_layers, contours
from bf_core.geo.dtm import build_dtm

UTM = np.array([512345.678, 6789012.345, 0.0])


def cloud(n: int, f, seed: int = 0, size: float = 100.0, offset=UTM) -> np.ndarray:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    xy = rng.random((n, 2)) * size
    return np.column_stack([xy, f(xy[:, 0], xy[:, 1])]) + offset


def grid(nx: int, ny: int, f, w: float = 100.0, h: float = 50.0, offset=UTM) -> np.ndarray:  # type: ignore[no-untyped-def]
    x, y = np.meshgrid(np.linspace(0, w, nx), np.linspace(0, h, ny))
    x, y = x.ravel(), y.ravel()
    return np.column_stack([x, y, f(x, y)]) + offset


def test_contours_of_a_plane_are_straight_parallel_lines() -> None:
    v, t, _ = build_dtm(grid(41, 17, lambda x, y: 0.5 * x, w=10, h=4))
    lines = contours(v, t, interval=1.0, major_every=2)
    # z = 0.5 x over x in [0, 10]: levels 1..5 at x = 2, 4, 6, 8, 10. Level 0 only touches the lowest edge.
    assert [c["z"] for c in lines] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [c["major"] for c in lines] == [False, True, False, True, False]
    for c in lines:
        p = c["points"]
        assert not c["closed"] and p.dtype == np.float64 and p.shape[1] == 3
        assert np.abs(p[:, 0] - (UTM[0] + 2 * c["z"])).max() < 1e-9  # position, abs 1e-9 m
        assert (p[:, 2] == c["z"]).all()
        y = p[:, 1] - UTM[1]
        assert min(y[0], y[-1]) == pytest.approx(0, abs=1e-9) and max(y[0], y[-1]) == pytest.approx(4, abs=1e-9)
        assert (np.diff(y) > 0).all() or (np.diff(y) < 0).all()  # ordered, no repeated points
    # Limits and base.
    assert [c["z"] for c in contours(v, t, interval=1.0, z_min=2.0, z_max=3.5)] == [2.0, 3.0]
    shifted = contours(v, t, interval=2.0, base=0.5, major_every=1)
    assert [c["z"] for c in shifted] == [0.5, 2.5, 4.5] and all(c["major"] for c in shifted)
    assert contours(v, t, interval=100.0, base=50.0) == []
    with pytest.raises(SurfaceError) as error:
        contours(v, t, interval=0.0)
    assert error.value.code == "bad_parameter"
    with pytest.raises(SurfaceError) as error:
        contours(v, t, interval=1e-9)
    assert error.value.code == "too_many_levels" and "20000" in error.value.message


def test_contours_of_a_paraboloid_are_closed_circles() -> None:
    # z = (x² + y²) / 100 on a unit grid, radius of level L: 10 √L.
    x, y = np.meshgrid(np.arange(-60.0, 61.0), np.arange(-60.0, 61.0))
    x, y = x.ravel(), y.ravel()
    v, t, _ = build_dtm(np.column_stack([x, y, (x * x + y * y) / 100.0]) + UTM)
    lines = contours(v, t, interval=2.5, major_every=5, z_max=35.0)
    closed = [c for c in lines if c["closed"]]
    assert [c["z"] for c in lines] == [2.5 * k for k in range(1, 15)] and len(closed) == 14
    assert [c["major"] for c in lines] == [k % 5 == 0 for k in range(1, 15)]
    for c in closed:
        p = c["points"]
        r = np.hypot(p[:, 0] - UTM[0], p[:, 1] - UTM[1])
        assert np.abs(r - 10 * np.sqrt(c["z"])).max() < 0.02  # discretisation of the unit grid
        assert len(np.unique(p[:, :2], axis=0)) == len(p)  # no repeated points
        assert not np.array_equal(p[0], p[-1])
        # Higher ground (outside of the bowl) on the left: the circles run clockwise.
        q = p[:, :2] - UTM[:2]
        assert np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]) < 0
        assert np.hypot(*(np.roll(q, -1, axis=0) - q).T).max() < 1.5  # chained without gaps


def test_contours_of_a_cone() -> None:
    """A cone z = 20 − |r| / 2 on a polar grid with a node at the apex: circles of radius 2 (20 − L)."""
    rings, sectors = 40, 72
    r = np.repeat(np.arange(1, rings + 1, dtype=np.float64), sectors)
    phi = np.tile(2 * np.pi * np.arange(sectors) / sectors, rings)
    pts = np.column_stack([r * np.cos(phi), r * np.sin(phi), 20 - r / 2])
    v, t, _ = build_dtm(np.vstack([[[0.0, 0.0, 20.0]], pts]) + UTM)
    lines = contours(v, t, interval=1.0, major_every=5)
    assert [c["z"] for c in lines] == [float(k) for k in range(1, 20)]  # level 20 only touches the apex
    for c in lines:
        assert c["closed"] and c["major"] == (int(c["z"]) % 5 == 0)
        p = c["points"]
        radius = np.hypot(p[:, 0] - UTM[0], p[:, 1] - UTM[1])
        # The levels at whole metres pass through the rings of nodes (r = 2 (20 − L)): exact to 1e-9 m there.
        if int(c["z"]) % 1 == 0 and 2 * (20 - c["z"]) <= rings:
            assert radius.max() == pytest.approx(2 * (20 - c["z"]), abs=1e-9)
        assert (radius <= 2 * (20 - c["z"]) + 1e-9).all()


def test_contours_through_vertices_have_no_duplicates_and_no_gaps() -> None:
    # Pyramid z = 10 − max(|x|, |y|) on the whole grid: every whole level passes exactly through vertices.
    x, y = np.meshgrid(np.arange(-10.0, 11.0), np.arange(-10.0, 11.0))
    x, y = x.ravel(), y.ravel()
    v, t, _ = build_dtm(np.column_stack([x, y, 10.0 - np.maximum(np.abs(x), np.abs(y))]) + UTM)
    lines = contours(v, t, interval=1.0)
    # Level 0 is the lowest (the outline of the surface) and level 10 only touches the apex: no lines.
    assert [c["z"] for c in lines] == [float(k) for k in range(1, 10)]
    for c in lines:
        p = c["points"]
        side = 2 * (10 - c["z"])
        assert c["closed"] and len(np.unique(p[:, :2], axis=0)) == len(p)
        q = np.vstack([p, p[:1]])[:, :2]
        assert np.hypot(*np.diff(q, axis=0).T).sum() == pytest.approx(4 * side, rel=1e-12)
        assert np.maximum(np.abs(p[:, 0] - UTM[0]), np.abs(p[:, 1] - UTM[1])) == pytest.approx(side / 2, abs=1e-9)
        # A vertex on the level is the point of the line bit for bit.
        assert {tuple(a) for a in p[:, :2].tolist()} <= {tuple(a) for a in v[:, :2].tolist()}
    half = contours(v, t, interval=1.0, base=0.5)
    assert len(half) == 10 and all(c["closed"] for c in half)
    # A flat terrace exactly on a level is outlined once, on the side where the ground falls.
    z = np.where(np.maximum(np.abs(x), np.abs(y)) <= 4, 6.0, 10.0 - np.maximum(np.abs(x), np.abs(y)))
    v, t, _ = build_dtm(np.column_stack([x, y, z]))
    at6 = [c for c in contours(v, t, interval=1.0) if c["z"] == 6.0]
    assert len(at6) == 1 and at6[0]["closed"]
    q = np.vstack([at6[0]["points"], at6[0]["points"][:1]])[:, :2]
    assert np.hypot(*np.diff(q, axis=0).T).sum() == pytest.approx(32.0, rel=1e-12)


def test_contours_open_and_closed_together() -> None:
    # A dome cut by the edge of the data: inner levels close, outer levels end on the boundary.
    pts = cloud(20000, lambda x, y: 40 - np.hypot(x - 50, y - 50) * 0.5, seed=9)
    v, t, report = build_dtm(pts, max_edge=4.0)
    assert report["dropped_long"] > 0 and report["area_plan"] > 9900
    by_level: dict[float, list] = {}
    for c in contours(v, t, interval=5.0):
        by_level.setdefault(c["z"], []).append(c)
    assert sorted(by_level) == [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
    for level in (20.0, 25.0, 30.0, 35.0):
        assert len(by_level[level]) == 1 and by_level[level][0]["closed"]
    assert len(by_level[10.0]) == 4 and not any(c["closed"] for c in by_level[10.0])
    for group in by_level.values():
        for c in group:
            r = np.hypot(c["points"][:, 0] - UTM[0] - 50, c["points"][:, 1] - UTM[1] - 50)
            assert np.abs(r - 2 * (40 - c["z"])).max() < 0.3  # 0.7 m spacing inside, 4 m at the hull


def test_layers_with_elev() -> None:
    x, y = np.meshgrid(np.arange(-10.0, 11.0), np.arange(-10.0, 11.0))
    v, t, _ = build_dtm(np.column_stack([x.ravel(), y.ravel(), 10.0 - np.maximum(np.abs(x), np.abs(y)).ravel()]) + UTM)
    lines = contours(v, t, interval=1.0, major_every=5)
    layers = contour_layers(lines)
    assert [layer.name for layer in layers] == [MAJOR, MINOR]
    major, minor = layers
    assert major.line_count == 1 and minor.line_count == 8  # level 5 is major; 1–4, 6–9 are not
    assert major.attrs.column("ELEV").to_pylist() == [5.0]
    assert minor.attrs.column("ELEV").to_pylist() == [1.0, 2.0, 3.0, 4.0, 6.0, 7.0, 8.0, 9.0]
    assert major.closed.tolist() == [1]
    # A closed line of a layer repeats its first vertex; the points are the isoline's bit for bit.
    first = major.line(0)
    assert np.array_equal(first[0], first[-1]) and np.array_equal(first[:-1], lines[4]["points"])
    assert np.isfinite(major.xyz).all() and (major.xyz[:, 2] == 5.0).all()
    assert contour_layers([]) == []
    only_minor = contour_layers([c for c in lines if not c["major"]])
    assert [layer.name for layer in only_minor] == [MINOR]


def test_refused_surfaces() -> None:
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    with pytest.raises(SurfaceError, match="пуста"):
        contours(v, np.zeros((0, 3), dtype=np.int32), interval=1.0)
    with pytest.raises(SurfaceError, match="несуществующие"):
        contours(v, np.array([[0, 1, 3]]), interval=1.0)
    holed = v.copy()
    holed[2, 2] = np.nan
    with pytest.raises(SurfaceError, match="пропуски") as error:
        contours(holed, np.array([[0, 1, 2]]), interval=1.0)
    assert "не подставляется" in error.value.hint


def test_batches_ticks_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.geo import contours as module

    pts = cloud(5000, lambda x, y: 20 * np.sin(x / 15) * np.cos(y / 20), seed=3)
    v, t, _ = build_dtm(pts)
    whole = contours(v, t, interval=0.5)
    monkeypatch.setattr(module, "BATCH_SEGMENTS", 500)  # many small batches: the same lines
    seen: list[float] = []
    batched = contours(v, t, interval=0.5, tick=lambda fraction, message: seen.append(fraction))
    assert len(seen) > 10 and seen == sorted(seen) and seen[-1] == 1.0
    assert [c["z"] for c in batched] == [c["z"] for c in whole]
    assert all(np.array_equal(a["points"], b["points"]) for a, b in zip(batched, whole, strict=True))

    def stop(fraction: float, message: str) -> None:
        if fraction > 0.3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        contours(v, t, interval=0.5, tick=stop)


@pytest.mark.slow
def test_speed_contours_one_million_triangles() -> None:
    """ADR 0015 «Замеры»: isolines of 1 million triangles, 100 levels (ориентир ≤ 3 с)."""
    rng = np.random.default_rng(11)
    xy = rng.random((500_000, 2)) * 10_000
    v, t, _ = build_dtm(np.column_stack([xy, 50 * np.sin(xy[:, 0] / 700) * np.cos(xy[:, 1] / 900)]) + UTM)
    contours(*build_dtm(v[:1000])[:2], interval=1.0)  # the kernels are loaded before the clock starts
    started = time.perf_counter()
    lines = contours(v, t, interval=1.0)
    seconds = time.perf_counter() - started
    levels = len({c["z"] for c in lines})
    print(f"\ncontours: {len(t)} triangles, {levels} levels: {seconds:.2f} s, {len(lines)} lines")
    assert 95 <= levels <= 101 and math.isfinite(seconds) and seconds < 30
