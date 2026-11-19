"""Geometry of a series of sections (ТЗ 8.1.2, 8.1.6; ADR 0016 §2; подшаг 10.1).

Т8.1-1 exactly, Т8.1-2 on 10⁶ points (every point in exactly one window, t = w_от — in the next one), Т8.1-5 to
1e−12, plans z_k = z_top − kΔ, K ≤ 5 000, the shared answers `tests/data/sections/series_cases.json`; the section
of 10⁶ points ≤ 50 ms.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.sections.geometry import (
    MAX_K,
    SeriesError,
    SeriesGeometry,
    azimuth_deg,
    count,
    series_axes,
    series_geometry,
    sin_cos_deg,
)
from tests import section_cases as cases

UTM = np.array([512_345.678, 7_012_345.123, 0.0])


def geom(**params: object) -> SeriesGeometry:
    g = series_geometry({"type": "vertical", "p1": [0.0, 0.0, 0.0], "az": 90.0, "s0": 0.0, "s1": 100.0,
                         "step": 25.0, "towards": 12.5, "away": 12.5, **params})  # fmt: skip
    assert isinstance(g, SeriesGeometry)
    return g


# --- the shared answers ------------------------------------------------------------------------------------------


def test_shared_answers_match_the_core() -> None:
    """The file the 3D window reads is what the core computes (exact cases bit for bit)."""
    answers = [cases.series_answer(c) for c in cases.SERIES]
    if cases.REGEN:
        cases.store("series", answers)
    data = cases.load()
    assert data["rules"] == cases.RULES and data["tolerance"] == cases.TOL
    stored = {c["id"]: c for c in data["series"]}
    assert list(stored) == [c["id"] for c in cases.SERIES]
    for a in answers:
        cases.close(a, stored[a["id"]], a["exact"], a["id"])


def test_shared_answers_by_hand() -> None:
    """The answers in the file, against what is expected without the core."""
    data = {c["id"]: c for c in cases.load()["series"]}
    t1 = data["t8_1_1"]
    assert [p["section"] for p in t1["points"]] == [1, 0, -1, 2, -1, 4]
    assert t1["points"][3]["u"] == 3.0 and t1["points"][3]["w"] is None and t1["points"][3]["t"] == 0.0
    assert [ln["section"] for ln in t1["lines"]] == [2, -1, 2, -1]
    t5 = data["t8_1_5"]["points"][0]
    assert abs(t5["u"] - 3) <= 1e-12 and abs(t5["w"] - 2) <= 1e-12 and abs(t5["t"]) <= 1e-12
    assert [p["section"] for p in data["t8_1_5"]["points"]] == [0, 3, 5, -1, -1]
    assert [ln["section"] for ln in data["t8_1_5"]["lines"]] == [2, 2, -1]
    assert [p["section"] for p in data["plan"]["points"]] == [1, 0, 10, -1, 0, -1]
    assert [p["t"] for p in data["plan"]["points"]][:3] == [-5.0, 0.0, -5.0]
    assert data["plan"]["lines"][0]["section"] == 5
    assert [p["section"] for p in data["vertical_az270"]["points"]] == [1, 1, 0, -1]
    assert [p["section"] for p in data["overrides_abut"]["points"]] == [1, 2, 1, -1]
    assert data["overrides_abut"]["partition"] and not data["overrides_gap_overlap"]["partition"]
    assert [p["section"] for p in data["overrides_gap_overlap"]["points"]] == [1, 0, 1, -1, 1]
    assert [ln["section"] for ln in data["overrides_gap_overlap"]["lines"]] == [0, 1]
    # every face of `abut_hard`: lo → k, below lo → k − 1, s_k + w_от → k + 1, below hi → k, hi → k + 1
    hard = data["abut_hard"]
    got = [p["section"] for p in hard["points"]]
    k_last = hard["count"] - 1
    want = []
    for k in (0, 1, 7, k_last - 1, k_last):
        nxt = k + 1 if k < k_last else -1
        want += [k, k - 1, nxt, k, nxt]
    assert got == want
    assert [p["section"] for p in data["utm_p2"]["points"]] == [0, 0, 0, 2000, 2000, 2000, 4000, 4000, 4000, -1]


# --- axes --------------------------------------------------------------------------------------------------------


def test_sin_cos_exact_on_right_angles() -> None:
    for deg, sc in ((0, (0.0, 1.0)), (90, (1.0, 0.0)), (180, (0.0, -1.0)), (270, (-1.0, 0.0)), (-90, (-1.0, 0.0)),
                    (450, (1.0, 0.0)), (-0.0, (0.0, 1.0)), (720, (0.0, 1.0))):  # fmt: skip
        assert sin_cos_deg(deg) == sc
    assert sin_cos_deg(30.0) == (math.sin(math.radians(30.0)), math.cos(math.radians(30.0)))


def test_azimuth() -> None:
    assert [azimuth_deg(0, 5), azimuth_deg(3, 0), azimuth_deg(0, -1e-3), azimuth_deg(-7, 0)] == [0, 90, 180, 270]
    assert azimuth_deg(1, 1) == pytest.approx(45, abs=1e-12) and azimuth_deg(-1, 1) == pytest.approx(315, abs=1e-12)
    assert 0.0 <= azimuth_deg(-1e-300, 1.0) < 360.0
    with pytest.raises(SeriesError):
        azimuth_deg(0.0, 0.0)


@pytest.mark.parametrize("az", [0.0, 30.0, 90.0, 137.25, 180.0, 270.0, 359.999])
@pytest.mark.parametrize("phi", [0.0, 15.0, 60.0, 89.5, 90.0])
def test_axes_orthonormal(az: float, phi: float) -> None:
    s, v, w = series_axes(az, phi)
    m = np.array([s, v, w])
    assert np.abs(m @ m.T - np.eye(3)).max() <= 4e-16
    assert np.abs(np.cross(s, v) - w).max() <= 4e-16  # ŵ = ŝ × v̂
    assert not np.any(np.signbit(m) & (m == 0.0))  # no −0.0


def test_axes_exact() -> None:
    s, v, w = series_axes(90.0, 90.0)
    assert (s.tolist(), v.tolist(), w.tolist()) == ([1, 0, 0], [0, 1, 0], [0, 0, 1])
    s, v, w = series_axes(0.0, 90.0)  # line to the north, the viewer to the east, looking west
    assert (s.tolist(), v.tolist(), w.tolist()) == ([0, 1, 0], [-1, 0, 0], [0, 0, 1])
    s, v, w = series_axes(90.0, 0.0)  # a plan: looking down, north up
    assert (s.tolist(), v.tolist(), w.tolist()) == ([1, 0, 0], [0, 0, -1], [0, 1, 0])


# --- sections ----------------------------------------------------------------------------------------------------


def test_t8_1_1_exact() -> None:
    g = geom(p1=[1000.0, 5000.0, 0.0])
    assert g.count == 5 and g.offs.tolist() == [0, 25, 50, 75, 100]
    assert g.origins().tolist() == [[1000.0, 5000.0 + 25 * k, 0.0] for k in range(5)]
    assert [g.origin(k).tolist() for k in range(5)] == g.origins().tolist()


def test_count() -> None:
    assert count(0, 100, 25) == 5 and count(0, 0, 7) == 1 and count(-1234.5, -34.5, 0.3) == 4001
    assert count(0, 3 * 0.1, 0.1) == 4  # 0.30000000000000004 / 0.1 — the 1e−9 keeps the last section
    assert count(0, 100 - 1e-7, 25) == 4
    assert count(0, 0.01 * (MAX_K - 1), 0.01) == MAX_K
    with pytest.raises(SeriesError):
        count(10, 0, 1)
    with pytest.raises(SeriesError):
        count(0, 1, 0)
    with pytest.raises(SeriesError):
        count(0, 1e308, 1e-300)
    assert geom(s0=0.0, s1=0.01 * (MAX_K - 1), step=0.01, towards=0.01, away=0.01).count == MAX_K
    with pytest.raises(SeriesError) as e:
        geom(s0=0.0, s1=0.01 * MAX_K, step=0.01)
    assert "5 000" in e.value.message and e.value.code == "bad_series" and e.value.field == "step"


def test_plan_levels() -> None:
    g = series_geometry({"type": "plan", "p1": [1e5, 2e5, 1234.5], "s0": 0.0, "s1": 300.0, "step": 7.5})
    assert isinstance(g, SeriesGeometry) and g.count == 41 and g.az == 90.0 and g.phi == 0.0
    assert g.origins()[:, 2].tolist() == [1234.5 - k * 7.5 for k in range(41)]
    assert (g.origins()[:, :2] == [1e5, 2e5]).all()
    assert g.w.tolist() == [0, 1, 0] and g.v.tolist() == [0, 0, -1]
    xyz = np.array([[1e5 + 3, 2e5 + 4, 1234.5 - 15.0]])
    assert g.local(xyz, 2).tolist() == [[3.0, 4.0, 0.0]] and g.section_of(xyz).tolist() == [2]


def test_vertical_takes_level_z() -> None:
    """A vertical series: P1.z is 0, w — the level; an inclined one at φ = 90° keeps P1.z."""
    g = geom(p1=[10.0, 20.0, 350.0])
    assert g.p1.tolist() == [10, 20, 0] and g.local([[10, 30, 123.25]]).tolist() == [[0, 123.25, 10]]
    i = geom(type="inclined", phi=90.0, p1=[10.0, 20.0, 350.0])
    assert i.local([[10, 30, 123.25]]).tolist() == [[0, 123.25 - 350, 10]]
    flat = geom(type="inclined", phi=90.0, p1=[10.0, 20.0, 0.0])
    for name in ("s", "v", "w", "offs", "lo", "hi"):
        assert getattr(flat, name).tolist() == getattr(geom(p1=[10.0, 20.0, 0.0]), name).tolist()


@pytest.mark.parametrize("az", [0.0, 30.0, 90.0, 200.5, 315.0])
def test_t8_1_5_inclined(az: float) -> None:
    for p1, tol in (([1000.0, 2000.0, 300.0], 1e-12), (UTM.tolist(), 1e-9)):
        g = series_geometry({"type": "inclined", "p1": p1, "az": az, "phi": 60.0, "s0": 0.0, "s1": 0, "step": 5.0})
        assert isinstance(g, SeriesGeometry)
        p = g.p1 + 3 * g.s + 2 * g.w
        u, w, t = g.local(p, 0)[0]
        assert abs(u - 3) <= tol and abs(w - 2) <= tol and abs(t) <= tol, (p1, az)


# --- windows: Т8.1-2 ---------------------------------------------------------------------------------------------

PARTITIONS = [
    {},  # 12.5 + 12.5 = 25
    {"s0": -1234.5, "s1": -34.5, "step": 0.3, "towards": 0.1, "away": 0.2, "p1": UTM.tolist(), "az": 117.3},
    {"s0": 0.0, "s1": 99.98, "step": 0.02, "towards": 0.01, "away": 0.01, "p1": UTM.tolist(), "az": 0.0},
    {"s0": -3e5, "s1": 3e5, "step": 200.0, "towards": 12.345, "away": 187.655, "p1": UTM.tolist(), "az": 271.0},
    {
        "type": "inclined",
        "phi": 37.0,
        "p1": UTM.tolist(),
        "az": 45.0,
        "s0": -500.0,
        "s1": 500.0,
        "step": 50.0,
        "towards": 20.0,
        "away": 30.0,
        "overrides": {"3": {"away": 45.0}, "4": {"towards": 5.0}},
    },
]


@pytest.mark.parametrize("params", PARTITIONS)
def test_t8_1_2_every_point_in_one_window(params: dict[str, object]) -> None:
    g = geom(**params)
    assert g.partition and (g.hi[:-1] == g.lo[1:]).all() and (g.lo < g.hi).all()
    rng = np.random.default_rng(12345)
    n = 1_000_000
    span = float(g.hi[-1] - g.lo[0])
    tau = g.lo[0] - 0.01 * span + rng.random(n) * 1.02 * span
    xyz = g.p1 + tau[:, None] * g.v + (rng.random((n, 1)) - 0.5) * 400 * g.s + (rng.random((n, 1)) - 0.5) * 80 * g.w
    k = g.section_of(xyz)
    depth = g.depth(xyz)
    inside = k >= 0
    assert inside.mean() > 0.97
    kk = k[inside]
    d = depth[inside]
    assert ((g.lo[kk] <= d) & (d < g.hi[kk])).all()  # in its window
    assert (d >= np.where(kk > 0, g.hi[kk - 1], -np.inf)).all()  # not in the previous
    assert (d < np.where(kk < g.count - 1, g.lo[np.minimum(kk + 1, g.count - 1)], np.inf)).all()  # nor the next
    out = depth[~inside]
    assert ((out < g.lo[0]) | (out >= g.hi[-1])).all()  # no gaps inside the series
    # every window, by brute force, on a part of the points
    some = depth[:20_000]
    hits = (g.lo[None, :] <= some[:, None]) & (some[:, None] < g.hi[None, :])
    assert hits.sum(axis=1).max() == 1
    assert (np.where(hits.any(axis=1), hits.argmax(axis=1), -1) == k[:20_000]).all()
    for j in (0, g.count // 2, g.count - 1):
        assert (g.in_window(xyz[:50_000], j) == (k[:50_000] == j)).all()


@pytest.mark.parametrize("params", PARTITIONS)
def test_t8_1_2_point_on_far_face_goes_to_next(params: dict[str, object]) -> None:
    g = geom(**params)
    far = g.offs + g.away  # t = w_от
    near = g.offs - g.towards  # t = −w_к
    k = np.arange(g.count)
    assert (g.section_of_depth(far[:-1]) == k[1:]).all() and g.section_of_depth(far[-1:]).tolist() == [-1]
    assert (g.section_of_depth(near) == k).all()
    below = np.nextafter(g.hi, -np.inf)
    assert (g.section_of_depth(below) == k).all()
    assert (g.section_of_depth(np.nextafter(g.lo[1:], -np.inf)) == k[:-1]).all()


def test_depth_kernel_bit_for_bit() -> None:
    from bf_core.sections import kernels

    rng = np.random.default_rng(7)
    xyz = UTM + (rng.random((100_000, 3)) - 0.5) * [4e3, 4e3, 2e3]
    for az, phi in ((0.0, 90.0), (117.3, 90.0), (45.0, 37.0), (300.0, 0.0)):
        g = geom(type="inclined", phi=phi, az=az, p1=UTM.tolist())
        out = np.empty(len(xyz))
        kernels.depths(xyz, *g._frame(), out)
        assert out.tobytes() == g.depth(xyz).tobytes()
        assert (g.section_of(xyz) == g.section_of_depth(g.depth(xyz))).all()


def test_nan_coordinates() -> None:
    xyz = [[0.0, 50.0, np.nan], [np.nan, 50.0, 0.0], [0.0, np.nan, 0.0]]
    assert geom().section_of(xyz).tolist() == [2, 2, -1]  # vertical, A = 90°: x and z do not matter
    assert geom(type="inclined", phi=60.0).section_of(xyz).tolist() == [-1, 2, -1]
    assert geom().in_window(xyz, 2).tolist() == [True, True, False]


def test_overrides_gap_and_overlap() -> None:
    g = geom(az=0.0, step=50.0, s1=200.0, towards=25.0, away=25.0, overrides={"0": {"away": 60.0}, 3: {"away": 10.0}})
    assert not g.partition and g.towards.tolist() == [25] * 5 and g.away.tolist() == [60, 25, 25, 10, 25]
    tau = [30.0, 25.0, 55.0, 165.0, 174.999, 175.0, -25.0, -25.001, 224.999, 225.0, np.nan]
    assert g.section_of_depth(tau).tolist() == [1, 0, 1, -1, -1, 4, 0, -1, 4, -1, -1]
    xyz = np.zeros((4, 3))
    xyz[:, 0] = [-20.0, -30.0, -26.0, -30.0]  # A = 0°: v̂ = (−1, 0, 0)
    assert g.line_sections(xyz, [0, 2, 4]).tolist() == [0, 1]


def test_line_sections() -> None:
    g = geom()
    xyz = np.array([[0, 40, 0], [5, 60, 1], [0, 62.4, 9], [0, 62.5, 0], [1, 1, 1], [1, 1, np.nan]], dtype=float)
    assert g.line_sections(xyz, [0, 3, 4, 4, 6]).tolist() == [2, 3, -1, 0]  # an empty line — −1
    assert g.line_sections(xyz, [0, 4, 6]).tolist() == [-1, 0]
    assert g.line_sections(np.zeros((0, 3)), [0]).tolist() == []
    with pytest.raises(ValueError):
        g.line_sections(xyz, [0, 7])
    with pytest.raises(ValueError):
        g.line_sections(xyz, [0, 4, 3, 6])


def test_window_of_a_section_line_7_14() -> None:
    """φ = 90°: the faces of the window of section 0 are those of `SectionLine` (7.14, `geo.section`)."""
    from bf_core.geo.section import section_window

    for az in (0.0, 30.0, 90.0, 245.5):
        g = geom(p1=[UTM[0], UTM[1], 0.0], az=az, towards=7.0, away=13.0)
        mine = g.plane(0).half_spaces()
        theirs = section_window(UTM[0], UTM[1], az, towards=7.0, away=13.0).planes()
        assert np.abs(mine[[1, 0]] - theirs).max() <= 1e-9
        p = g.plane(2)
        assert np.abs(p.origin - g.origin(2)).max() == 0 and np.abs(p.local([g.origin(2)])).max() <= 1e-9


def test_errors_are_russian() -> None:
    bad = [
        ({"type": "spiral"}, "type"),
        ({"p1": None}, "p1"),
        ({"p1": [1, 2, "a"]}, "p1"),
        ({"az": None, "p2": [0.0, 1e-7]}, "p2"),
        ({"az": None}, "az"),
        ({"type": "inclined", "phi": 95.0}, "phi"),
        ({"step": 0.001}, "step"),
        ({"towards": 10_001.0}, "towards"),
        ({"s0": math.inf}, "s0"),
        ({"overrides": {"5": {"away": 3.0}}}, "overrides"),
        ({"overrides": {"x": {}}}, "overrides"),
        ({"overrides": {"1": {"away": 0.0}}}, "overrides"),
    ]
    for change, name in bad:
        with pytest.raises(SeriesError) as e:
            geom(**change)
        err = e.value
        assert err.code == "bad_series" and err.field == name and err.hint, change
        assert any("а" <= ch <= "я" for ch in err.message.lower()), err.message


def test_tick_and_cancel() -> None:
    g = geom()
    seen: list[float | None] = []
    xyz = np.zeros((2_500_000, 3))
    g.section_of(xyz, tick=lambda f, m: seen.append(f))
    assert seen == [(1 << 20) / 2_500_000, (2 << 20) / 2_500_000, 1.0]

    class Stop(Exception):
        pass

    def stop(f: float | None, m: str | None) -> None:
        raise Stop

    with pytest.raises(Stop):
        g.section_of(xyz, tick=stop)
    with pytest.raises(Stop):
        g.line_sections(xyz, [0, 1_000_000, 2_500_000], tick=stop)


def test_speed_million_points() -> None:
    g = geom(**PARTITIONS[1])
    rng = np.random.default_rng(3)
    xyz = g.p1 + (rng.random((1_000_000, 3)) - 0.5) * [3000.0, 3000.0, 600.0]
    g.section_of(xyz[:10])  # the kernels: compiled or from the cache
    best = math.inf
    for _ in range(3):
        started = time.perf_counter()
        g.section_of(xyz)
        best = min(best, time.perf_counter() - started)
    print(f"\nsection of 10⁶ points: {best * 1e3:.1f} ms")
    assert best <= 0.05
