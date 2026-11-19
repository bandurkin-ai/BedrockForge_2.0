"""A polyline section (ТЗ 8.1.2, Т8.1-7; ADR 0016 §2, решение 10; подшаг 10.2): the nearest segment (the smaller
number at a bend), the corridor, the development u, the four half-spaces of a segment; the shared answers of the
polyline in `tests/data/sections/series_cases.json`.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.sections.geometry import SeriesError, series_geometry
from bf_core.sections.polyline import PolylineSection, polyline_section
from tests import section_cases as cases

UTM = np.array([512_345.678, 7_012_345.123])


def poly(points: object, corridor: float = 10.0) -> PolylineSection:
    return polyline_section({"polyline": points, "corridor": corridor})


def test_shared_answers_match_the_core() -> None:
    answers = [cases.polyline_answer(c) for c in cases.POLYLINES]
    if cases.REGEN:
        cases.store("polylines", answers)
    stored = {c["id"]: c for c in cases.load()["polylines"]}
    assert list(stored) == [c["id"] for c in cases.POLYLINES]
    for a in answers:
        cases.close(a, stored[a["id"]], a["exact"], a["id"])


def test_shared_answers_by_hand() -> None:
    data = {c["id"]: c for c in cases.load()["polylines"]}
    t7 = data["t8_1_7"]
    assert [s["length"] for s in t7["segments"]] == [500.0, 520.0, 340.0] and t7["total"] == 1360.0
    assert [s["start"] for s in t7["segments"]] == [0.0, 500.0, 1020.0]
    pts = t7["points"]
    assert [p["segment"] for p in pts] == [0, 0, 1, 2, 1, 2, -1, 0, 1]
    assert abs(pts[3]["u"] - 1360.0) <= 1e-12 * 1360 and pts[0]["u"] == 0.0  # Т8.1-7
    assert abs(pts[4]["u"] - 760.0) <= 1e-9 and abs(pts[4]["t"] - 10.0) <= 1e-9 and pts[4]["w"] == -50.0
    assert abs(pts[5]["u"] - 1370.0) <= 1e-9  # beyond the free end: the round cap of the corridor
    assert pts[7]["w"] is None
    assert [ln["section"] for ln in t7["lines"]] == [0, -1]
    s = data["straight_axes"]
    assert [p["segment"] for p in s["points"]] == [0, 0, 0, 1, 0, -1, 0, 0, -1, 1, 0]
    assert [p["u"] for p in s["points"]] == [100, 105, 95, 108, 50, None, 50, -10, None, 160, 50]
    assert [p["t"] for p in s["points"]] == [0, -5, 5, 3, 10, None, -10, 0, None, 0, 3]
    assert [ln["section"] for ln in s["lines"]] == [0, -1, -1]
    h = s["segments"][0]["half_spaces"]
    assert h[:3] == [[0, 1, 0, 10], [0, -1, 0, 10], [1, 0, 0, 0]]
    r = 1 / math.sqrt(2)
    assert h[3] == pytest.approx([-r, -r, 0, 100 * r], abs=1e-15)
    assert [p["segment"] for p in data["zigzag_utm"]["points"]] == [j for j in range(5) for _ in range(2)] + [-1]


def test_series_geometry_gives_a_polyline_section() -> None:
    pl = series_geometry({"type": "polyline", "polyline": [[0, 0], [10, 0]], "p1": [5, 5, 5], "towards": 3.0})
    assert isinstance(pl, PolylineSection) and pl.count == 1 and pl.corridor == 25.0 and pl.segments == 1
    assert pl.origins().tolist() == [[0, 0, 0]] and pl.offs.tolist() == [0] and pl.towards.tolist() == [25]
    xyz = [[5, 24, 7], [5, 26, 7], [5, np.nan, 7]]
    assert pl.section_of(xyz).tolist() == [0, -1, -1] and pl.in_window(xyz).tolist() == [True, False, False]
    assert pl.line_sections(xyz, [0, 1, 1, 3]).tolist() == [0, -1, -1]
    with pytest.raises(IndexError):
        pl.plane(1)
    with pytest.raises(IndexError):
        pl.half_spaces(1)


def _random_polyline(rng: np.random.Generator, m: int) -> np.ndarray:
    pts = [UTM + (rng.random(2) - 0.5) * 2000]
    heading = rng.random() * 2 * math.pi
    for _ in range(m):
        heading += (rng.random() - 0.5) * 2.5  # turns up to ±72°
        step = 1.0 + rng.random() * 900
        pts.append(pts[-1] + step * np.array([math.sin(heading), math.cos(heading)]))
    return np.array(pts)


def test_t8_1_7_development_is_the_sum_of_lengths() -> None:
    rng = np.random.default_rng(12345)
    for _ in range(300):
        pts = _random_polyline(rng, 3)
        pl = poly(pts.tolist(), 25.0)
        lengths = [math.hypot(*(b - a)) for a, b in zip(pts[:-1], pts[1:], strict=True)]
        total = math.fsum(lengths)
        assert abs(pl.total - total) <= 1e-12 * total
        ends = np.column_stack([pts, np.zeros(len(pts))])
        u = pl.local(ends)[:, 0]
        assert u[0] == 0.0 and abs(u[-1] - total) <= 1e-12 * total
        # every bend goes to the smaller segment and unfolds to the start of the next one
        assert pl.segment_of(ends).tolist() == [0, 0, 1, 2]
        assert np.abs(u[1:-1] - pl.starts[1:]).max() <= 1e-12 * total


def test_bends_and_wedges() -> None:
    rng = np.random.default_rng(7)
    for _ in range(100):
        pts = _random_polyline(rng, 5)
        pl = poly(pts.tolist(), 20.0)
        for j in range(1, 5):
            cross = pl.s_axes[j - 1, 0] * pl.s_axes[j, 1] - pl.s_axes[j - 1, 1] * pl.s_axes[j, 0]
            n = pl.v_axes[j - 1] + pl.v_axes[j]
            out = -np.sign(cross) * n / np.linalg.norm(n)  # the outer side of the bend
            q = pl.vertex(j) + 5.0 * out
            if pl.segment_of(q).tolist() == [j - 1]:
                continue
            # the wedge may be cut by another segment of a short zigzag: then the point is nearer to it
            d = [_dist(pl, i, q) for i in range(5)]
            assert min(d) < 5.0 - 1e-9 or np.argmin(d) < j - 1


def _dist(pl: PolylineSection, i: int, q: np.ndarray) -> float:
    a, b = pl.points[i], pl.points[i + 1]
    t = np.clip(np.dot(q[:2] - a, b - a) / np.dot(b - a, b - a), 0, 1)
    return float(np.linalg.norm(q[:2] - (a + t * (b - a))))


def test_corridor_is_closed() -> None:
    pl = poly([[0, 0], [100, 0]], 10.0)
    assert pl.segment_of([[50, 10, 0], [50, -10, 0], [50, math.nextafter(10, 11), 0], [110, 0, 0]]).tolist() == [
        0,
        0,
        -1,
        0,
    ]


def test_half_spaces_of_segments() -> None:
    rng = np.random.default_rng(3)
    pts = _random_polyline(rng, 6)
    pl = poly(pts.tolist(), 15.0)
    for j in range(pl.segments):
        h = pl.half_spaces(j)
        assert h.shape == (4, 4) and np.allclose(np.hypot(h[:, 0], h[:, 1]), 1.0, atol=1e-15)
        # points of the segment inside its corridor are inside its four half-spaces
        a = rng.random(200) * pl.lengths[j]
        t = (rng.random(200) - 0.5) * 2 * 14.9
        q = pl.vertex(j) + a[:, None] * pl.s_axes[j] + t[:, None] * pl.v_axes[j] + [0, 0, 1] * rng.random((200, 1))
        inside = (q @ h[:, :3].T + h[:, 3] >= -1e-6).all(axis=1)
        mid = (a > 1.0) & (a < pl.lengths[j] - 1.0)
        assert inside[mid & (np.abs(t) < 1)].all()
        if j + 1 < pl.segments:
            assert (pl.half_spaces(j + 1)[2] == -h[3]).all()  # the same bisector, the other side
    assert (pl.half_spaces(0)[2, :3] == pl.s_axes[0]).all()
    assert (pl.half_spaces(pl.segments - 1)[3, :3] == -pl.s_axes[-1]).all()


def test_plane_of_a_segment() -> None:
    pl = poly([[0, 0], [100, 0], [100, 50]], 10.0)
    p = pl.plane(0, 1)
    assert p.seg == 1 and p.u0 == 100.0 and p.origin.tolist() == [100, 0, 0]
    assert p.s.tolist() == [0, 1, 0] and p.v.tolist() == [-1, 0, 0] and p.w.tolist() == [0, 0, 1]
    assert p.local([[97, 8, 3]]).tolist() == [[108, 3, 3]]
    assert pl.local([[97, 8, 3]], seg=0).tolist() == [[97, 3, 8]]


def test_errors() -> None:
    bad = [
        ({"polyline": [[0, 0]]}, "polyline"),
        ({"polyline": None}, "polyline"),
        ({"polyline": [[0, 0]] * 1001}, "polyline"),
        ({"polyline": [[0, 0], [0, 1e-7]]}, "polyline"),
        ({"polyline": [[0, 0], [1, "a"]]}, "polyline"),
        ({"polyline": [[0, 0], [1, math.nan]]}, "polyline"),
        ({"polyline": [[0, 0], [10, 0], [5, 0]]}, "polyline"),
        ({"polyline": [[0, 0], [10, 0]], "corridor": 0.0}, "corridor"),
        ({"polyline": [[0, 0], [10, 0]], "corridor": True}, "corridor"),
    ]
    for meta, name in bad:
        with pytest.raises(SeriesError) as e:
            polyline_section(meta)
        assert e.value.code == "bad_series" and e.value.field == name and e.value.hint
        assert any("а" <= ch <= "я" for ch in e.value.message.lower())
    assert poly([[0, 0], [10, 0], [5, 0.01]]).segments == 2  # a sharp turn is not a turn back


def test_speed_and_cancel() -> None:
    rng = np.random.default_rng(5)
    pts = _random_polyline(rng, 3)
    pl = poly(pts.tolist(), 25.0)
    xyz = np.column_stack([pts[0] + (rng.random((1_000_000, 2)) - 0.5) * 3000, np.zeros(1_000_000)])
    pl.segment_of(xyz[:10])
    started = time.perf_counter()
    seg = pl.segment_of(xyz)
    seconds = time.perf_counter() - started
    print(f"\nsegment of 10⁶ points, 3 segments: {seconds * 1e3:.1f} ms")
    assert seconds < 0.2 and (seg >= -1).all()

    class Stop(Exception):
        pass

    def stop(f: float | None, m: str | None) -> None:
        raise Stop

    with pytest.raises(Stop):
        pl.segment_of(np.zeros((2_000_000, 3)), tick=stop)
