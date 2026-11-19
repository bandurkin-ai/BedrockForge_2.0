"""Volumes between surfaces and to a level, in the common outline and in a polygon (ТЗ 7.23 «Объём»;
ADR 0015 §7, decision 5, 9.14).

Ported from BF `tests/test_surfaces.py` (footprint, solids, volumes) with the assertions kept; new: the 9.14
criterion (a tilted plane above a level: the analytic volume to 1e-9 relative on both paths), the bodies
«Выемка» and «Насыпь», parts of a surface above one another, cancellation.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from bf_core.geo.contours import SurfaceError
from bf_core.geo.dtm import build_dtm
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.volumes import CUT, FILL, surface_footprint, under_solid, volume_between, volume_to_level

UTM = np.array([512345.678, 6789012.345, 0.0])


def cloud(n: int, f, seed: int = 0, size: float = 100.0, offset=UTM) -> np.ndarray:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    xy = rng.random((n, 2)) * size
    return np.column_stack([xy, f(xy[:, 0], xy[:, 1])]) + offset


def grid(nx: int, ny: int, f, w: float = 100.0, h: float = 50.0, offset=UTM) -> np.ndarray:  # type: ignore[no-untyped-def]
    x, y = np.meshgrid(np.linspace(0, w, nx), np.linspace(0, h, ny))
    x, y = x.ravel(), y.ravel()
    return np.column_stack([x, y, f(x, y)]) + offset


def test_tilted_plane_above_a_level_is_analytic() -> None:
    """9.14 criterion: z = 10 + 0.1 x over 100 × 50 above the level 4: V = 50 · ∫₀¹⁰⁰ (6 + 0.1 x) dx = 55 000."""
    plane = build_dtm(grid(11, 6, lambda x, y: 10 + 0.1 * x))[:2]
    prismatic = volume_to_level(plane, 4.0)
    assert prismatic["cut"] == pytest.approx(55000.0, rel=1e-9) and prismatic["fill"] == 0.0
    assert prismatic["area"] == pytest.approx(5000.0, rel=1e-12) and "призмы" in prismatic["method"]
    by_booleans = volume_to_level(plane, 4.0, bodies=True)
    assert by_booleans["cut"] == pytest.approx(55000.0, rel=1e-9)
    assert by_booleans["fill"] == pytest.approx(0.0, abs=1e-6) and by_booleans["area"] == pytest.approx(5000, rel=1e-9)
    body = by_booleans["bodies"][CUT]
    stats = mesh_stats(*body)
    assert stats["closed"] and stats["orientation"] == "outward" and stats["volume"] == pytest.approx(55000, rel=1e-9)
    assert body[0].dtype == np.float64 and body[1].dtype == np.int32 and by_booleans["bodies"][FILL] is None
    # The vertices of the surface reach the body bit for bit.
    kept = {tuple(p) for p in body[0].tolist()}
    assert all(tuple(p) in kept for p in plane[0].tolist())
    # The level in the middle: cut and fill of the two wedges, z = 15 at x = 50.
    middle = volume_to_level(plane, 15.0)
    assert middle["cut"] == pytest.approx(50 * 0.05 * 50**2, rel=1e-9)
    assert middle["fill"] == pytest.approx(50 * 0.05 * 50**2, rel=1e-9)
    assert middle["net"] == pytest.approx(0.0, abs=1e-6)


def test_footprint_with_a_hole_and_under_solid() -> None:
    v, t, _ = build_dtm(grid(11, 6, lambda x, y: 10 + 0.1 * x))
    loops = surface_footprint(v, t)
    assert len(loops) == 1 and loops[0].shape[1] == 2
    q = loops[0] - UTM[:2]
    assert 0.5 * np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]) == pytest.approx(
        5000.0, rel=1e-12
    )
    # Remove the triangles around the centre: the hole comes second and runs clockwise.
    centre = v[t].mean(axis=1)[:, :2] - UTM[:2]
    holed = t[~((np.abs(centre[:, 0] - 50) < 10) & (np.abs(centre[:, 1] - 25) < 5))]
    loops = surface_footprint(v, holed)
    assert len(loops) == 2
    q = loops[1] - UTM[:2]
    assert 0.5 * np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]) == pytest.approx(
        -200.0, rel=1e-12
    )
    # Solid under the plane z = 10 + 0.1 x down to z = 4: volume = 5000 · (15 − 4) = 55 000.
    sv, st = under_solid(v, t, 4.0)
    stats = mesh_stats(sv, st)
    assert stats["closed"] and stats["orientation"] == "outward"
    assert stats["volume"] == pytest.approx(55000.0, rel=1e-12)
    assert st.dtype == np.int32 and np.array_equal(sv[: len(v)], v)
    sv, st = under_solid(v, holed, 4.0)  # still closed (walls around the hole)
    stats = mesh_stats(sv, st)
    assert stats["closed"] and stats["orientation"] == "outward"
    assert stats["volume"] == pytest.approx(55000.0 - 200 * (15 - 4), rel=1e-12)
    sv, st = under_solid(v, t, 30.0)  # the solid above the surface up to z = 30
    stats = mesh_stats(sv, st)
    assert (
        stats["closed"] and stats["orientation"] == "outward" and stats["volume"] == pytest.approx(75000.0, rel=1e-12)
    )
    with pytest.raises(SurfaceError) as error:
        under_solid(v, t, 12.0)  # the level crosses the surface
    assert error.value.code == "level_crosses"


def test_cut_and_fill_of_two_dtms() -> None:
    # top: z = 10 + 0.1 x; bottom: z = 12. Over 100 × 50 they cross at x = 20.
    top = build_dtm(grid(8, 5, lambda x, y: 10 + 0.1 * x))[:2]
    bottom = build_dtm(grid(4, 7, lambda x, y: 12 + 0 * x))[:2]
    r = volume_between(top, bottom, bodies=True)
    # cut = 50 ∫₂₀¹⁰⁰ (0.1 x − 2) dx = 16 000; fill = 50 ∫₀²⁰ (2 − 0.1 x) dx = 1000. rel 1e-9.
    assert r["cut"] == pytest.approx(16000.0, rel=1e-9)
    assert r["fill"] == pytest.approx(1000.0, rel=1e-9)
    assert r["net"] == pytest.approx(15000.0, rel=1e-9) and r["area"] == pytest.approx(5000.0, rel=1e-9)
    assert r["tolerance"] < 1e-6 and "manifold3d" in r["method"]
    # Decision 5: the bodies «Выемка» and «Насыпь» are closed solids of these volumes.
    for name, expected in ((CUT, 16000.0), (FILL, 1000.0)):
        stats = mesh_stats(*r["bodies"][name])
        assert stats["closed"] and stats["orientation"] == "outward"
        assert stats["volume"] == pytest.approx(expected, rel=1e-9)
    assert "bodies" not in volume_between(top, bottom)
    swapped = volume_between(bottom, top)
    assert swapped["cut"] == pytest.approx(1000.0, rel=1e-9) and swapped["fill"] == pytest.approx(16000.0, rel=1e-9)
    # Top everywhere above the bottom: fill is exactly nothing.
    low = build_dtm(grid(3, 3, lambda x, y: 4 + 0.02 * y))[:2]
    r = volume_between(top, low, bodies=True)
    assert r["cut"] == pytest.approx(5000 * (15 - 4.5), rel=1e-9) and r["fill"] == pytest.approx(0.0, abs=1e-6)
    assert r["bodies"][FILL] is None
    # A limiting polygon: the triangle (10, 10) (90, 10) (10, 40), area 1200; ∫(0.1 x − 2) over it = 2000.
    polygon = np.array([[10.0, 10], [90, 10], [10, 40]]) + UTM[:2]
    r = volume_between(top, bottom, polygon=polygon)
    assert r["area"] == pytest.approx(1200.0, rel=1e-9) and r["net"] == pytest.approx(2000.0, rel=1e-9)
    fill = 30 / 80 * ((2 * 90 * 10) - (2 + 9) * (20**2 - 10**2) / 2 + 0.1 * (20**3 - 10**3) / 3)
    assert r["fill"] == pytest.approx(fill, rel=1e-9) and r["cut"] == pytest.approx(2000.0 + fill, rel=1e-9)
    # The common outline is the intersection of the footprints (bottom shifted by 30 in X: overlap 70 × 50).
    moved = (bottom[0] + [30.0, 0, 0], bottom[1])
    r = volume_between(top, moved)
    assert r["area"] == pytest.approx(3500.0, rel=1e-9)
    assert r["net"] == pytest.approx(50 * (0.05 * (100**2 - 30**2) - 2 * 70), rel=1e-9)
    with pytest.raises(SurfaceError) as error:  # no common region
        volume_between(top, (bottom[0] + [500.0, 0, 0], bottom[1]))
    assert error.value.code == "no_overlap"
    bow = np.array([[0, 0], [10, 10], [10, 0], [0, 10]]) + UTM[:2]
    with pytest.raises(SurfaceError, match="самопересекается"):
        volume_between(top, bottom, polygon=bow)


def test_volume_between_surfaces_that_coincide_outside_a_pit() -> None:
    # Ground z = 10; the excavated surface: the same plane with a pit, crest 20 × 20, floor 10 × 10 at z = 5.
    ground = build_dtm(grid(13, 9, lambda x, y: 10 + 0 * x, w=60, h=60))[:2]
    crest = np.array([[20.0, 20, 10], [40, 20, 10], [40, 40, 10], [20, 40, 10], [20, 20, 10]]) + UTM
    floor = np.array([[25.0, 25, 5], [35, 25, 5], [35, 35, 5], [25, 35, 5], [25, 25, 5]]) + UTM
    rim = grid(7, 7, lambda x, y: 10 + 0 * x, w=60, h=60)
    rim = rim[(np.abs(rim[:, 0] - UTM[0] - 30) > 12) | (np.abs(rim[:, 1] - UTM[1] - 30) > 12)]
    pit = build_dtm(rim, [crest, floor])[:2]
    r = volume_between(ground, pit)
    # Frustum of a pyramid: h / 3 · (A1 + A2 + √(A1 A2)) = 5 / 3 · (400 + 100 + 200). rel 1e-9.
    assert r["cut"] == pytest.approx(5 / 3 * 700, rel=1e-9)
    assert r["fill"] == pytest.approx(0.0, abs=1e-6) and r["area"] == pytest.approx(3600.0, rel=1e-9)
    same = volume_between(ground, ground)
    assert same["cut"] == pytest.approx(0.0, abs=1e-6) and same["fill"] == pytest.approx(0.0, abs=1e-6)


def test_volume_to_level_under_a_pyramid() -> None:
    v = np.array([[0.0, 0, 100], [20, 0, 100], [20, 20, 100], [0, 20, 100], [10, 10, 112]]) + UTM
    t = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    total = 400 * 12 / 3
    r = volume_to_level((v, t), 100.0)
    assert (
        r["cut"] == pytest.approx(total, rel=1e-12) and r["fill"] == 0.0 and r["area"] == pytest.approx(400, rel=1e-12)
    )
    r = volume_to_level((v, t), 106.0)  # the cap above is a pyramid 10 × 10 × 6
    assert r["cut"] == pytest.approx(100 * 6 / 3, rel=1e-12)
    assert r["fill"] == pytest.approx(100 * 6 / 3 - total + 6 * 400, rel=1e-12)
    assert r["net"] == pytest.approx(total - 6 * 400, rel=1e-12)
    r = volume_to_level((v, t), 120.0)
    assert r["cut"] == 0.0 and r["fill"] == pytest.approx(20 * 400 - total, rel=1e-12)
    assert volume_to_level((v, t[:, ::-1]), 106.0)["cut"] == pytest.approx(200.0, rel=1e-12)  # normals down
    # In a polygon (the quarter x < 10, y < 10): a quarter of everything. Booleans: rel 1e-9.
    quarter = np.array([[0.0, 0], [10, 0], [10, 10], [0, 10]]) + UTM[:2]
    r = volume_to_level((v, t), 106.0, polygon=quarter)
    assert r["area"] == pytest.approx(100.0, rel=1e-9)
    assert r["cut"] == pytest.approx(50.0, rel=1e-9) and r["fill"] == pytest.approx((200 - total + 2400) / 4, rel=1e-9)


def test_volume_to_level_paraboloid_tin_matches_booleans() -> None:
    # The prismatic formula and the boolean method are both exact for the TIN → equal (rel 1e-9).
    v, t, report = build_dtm(cloud(3000, lambda x, y: 30 - ((x - 50) ** 2 + (y - 50) ** 2) / 100, seed=10))
    exact = volume_to_level((v, t), 12.0)
    by_booleans = volume_to_level((v, t), 12.0, polygon=surface_footprint(v, t)[0])
    for key in ("cut", "fill", "net", "area"):
        assert by_booleans[key] == pytest.approx(exact[key], rel=1e-9)
    assert exact["area"] == pytest.approx(report["area_plan"], rel=1e-12)
    assert exact["cut"] > 1000 and exact["fill"] > 1000
    assert exact["cut"] == pytest.approx(np.pi * 1800 * 18 / 2, rel=0.02)  # the analytic cap, a TIN below it


def test_volume_refuses_surfaces_not_single_valued_in_z() -> None:
    v = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [10, 0, 5], [10, 10, 5]])
    wall = np.array([[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 2]])  # a vertical wall on one side
    with pytest.raises(SurfaceError) as error:
        volume_to_level((v, wall), -1.0)
    assert "вертикальных треугольников: 2" in error.value.message and error.value.code == "not_single_valued"
    v2 = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [4, 0, 5], [4, 10, 5]])
    overhang = np.array([[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 2]])  # folded back over itself
    with pytest.raises(SurfaceError) as error:
        volume_between((v2, overhang), (v, wall[:2]))
    assert "перевёрнутых" in error.value.message and "Верхняя" in error.value.message
    with pytest.raises(SurfaceError):
        under_solid(v2, overhang, -5.0)
    # Two sheets facing up, one above the other: BF did not see it, here it is refused.
    lower = build_dtm(grid(5, 5, lambda x, y: 0 * x, w=10, h=10))[:2]
    upper = build_dtm(grid(3, 3, lambda x, y: 0 * x + 5, w=4, h=4, offset=UTM + [2, 2, 0]))[:2]
    stacked = (np.vstack([lower[0], upper[0]]), np.vstack([lower[1], upper[1] + len(lower[0])]))
    with pytest.raises(SurfaceError, match="одна над другой"):
        volume_to_level(stacked, -1.0)
    with pytest.raises(SurfaceError) as error:
        volume_to_level(lower, float("nan"))
    assert error.value.code == "bad_parameter"


def test_ticks_and_cancellation() -> None:
    top = build_dtm(grid(8, 5, lambda x, y: 10 + 0.1 * x))[:2]
    bottom = build_dtm(grid(4, 7, lambda x, y: 12 + 0 * x))[:2]
    seen: list[float] = []
    volume_between(top, bottom, tick=lambda fraction, message: seen.append(fraction))
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0

    def stop(fraction: float, message: str) -> None:
        if fraction > 0.2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        volume_between(top, bottom, tick=stop)


@pytest.mark.slow
def test_speed_volume_between_two_hundred_thousand_triangles() -> None:
    """ADR 0015 «Замеры»: 2 × 100 000 triangles (ориентир ≤ 3 с)."""
    rng = np.random.default_rng(12)
    xy = rng.random((50_000, 2)) * 2000
    a = build_dtm(np.column_stack([xy, 20 * np.sin(xy[:, 0] / 300) + 100]) + UTM)[:2]
    b = build_dtm(np.column_stack([xy[::-1], 15 * np.cos(xy[::-1, 1] / 250) + 100]) + UTM)[:2]
    started = time.perf_counter()
    r = volume_between(a, b)
    seconds = time.perf_counter() - started
    started = time.perf_counter()
    level = volume_to_level(a, 100.0)
    level_seconds = time.perf_counter() - started
    assert level["area"] == pytest.approx(r["area"], rel=0.05)
    print(f"\nvolume_between: {len(a[1])} × {len(b[1])} triangles: {seconds:.2f} s; to a level: {level_seconds:.2f} s")
    assert r["cut"] > 0 and r["fill"] > 0 and r["area"] > 3.9e6
    assert seconds < 30 and level_seconds < 10
