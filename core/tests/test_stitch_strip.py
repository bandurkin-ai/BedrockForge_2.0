"""Polygons and the strip between two contours (ADR 0015 §7; 9.5): caps of non-convex polygons, the exact
best start against the full search, a strip without a twist, tie lines as edges of the result.

From BF `tests/test_stitch.py` (`test_cap_triangles_cover_a_non_convex_polygon`, the strips of
`test_contours_of_different_shape_and_vertex_count`, `test_tie_lines_are_edges_of_the_result`,
`test_very_dissimilar_contours_still_give_a_strip`) and new: strips in threads — the same bits for 1, 2, 4
threads, the limit of memory, the stop on a raising tick. Tolerances: areas 1e-9 relative; the best start
equals the full search to 1e-12 relative (the same path is summed in the same order). No database.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from bf_core.geo import polygon as gp
from bf_core.geo import stitch_strip as ss
from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.meshes import edge_summary, mesh_stats

ORIGIN = np.array([512345.678, 6789012.345, 250.0])  # UTM-size coordinates


def section(xz, position, azimuth=30.0):  # type: ignore[no-untyped-def]
    """A contour on a vertical section: (along strike, elevation) → project coordinates."""
    xz = np.asarray(xz, dtype=np.float64)
    a = np.radians(azimuth)
    strike = np.array([np.cos(a), np.sin(a), 0.0])
    across = np.array([-np.sin(a), np.cos(a), 0.0])
    return ORIGIN + xz[:, :1] * strike + position * across + xz[:, 1:] * np.array([0.0, 0.0, 1.0])


def ellipse(n, a, b, cx=0.0, cy=0.0, phase=0.0):  # type: ignore[no-untyped-def]
    t = np.linspace(0, 2 * np.pi, n, endpoint=False) + phase
    return np.column_stack([cx + a * np.cos(t), cy + b * np.sin(t)])


def rounded_rectangle(a, b, r):  # type: ignore[no-untyped-def]
    pts = [[0.0, -b]]
    for cx, cy, start in ((a - r, -b + r, -90), (a - r, b - r, 0), (-a + r, b - r, 90), (-a + r, -b + r, 180)):
        for d in (0, 30, 60, 90):
            t = np.radians(start + d)
            pts.append([cx + r * np.cos(t), cy + r * np.sin(t)])
    return np.array(pts)


def area(xy):  # type: ignore[no-untyped-def]
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))


def has_edge(t, i, j):  # type: ignore[no-untyped-def]
    return bool(((t == i).any(axis=1) & (t == j).any(axis=1)).any())


def ccw(xy):  # type: ignore[no-untyped-def]
    return xy if gp.signed_area(xy) > 0 else xy[::-1]


def prism_of_strip(lower, upper, gap, ties=None):  # type: ignore[no-untyped-def]
    """Plan contours `lower` at z = 0 and `upper` at z = gap (UTM): the strip, both caps, the solid."""
    lower, upper = ccw(lower), ccw(upper)
    n, m = len(lower), len(upper)
    v = np.vstack([ORIGIN + np.column_stack([lower, np.zeros(n)]), ORIGIN + np.column_stack([upper, np.full(m, gap)])])
    la, lb = np.arange(n), np.arange(n, n + m)
    tris = ss.strip(lower, upper, la, lb, gap, ties)
    assert tris is not None
    caps = [la[gp.triangulate(lower)][:, ::-1], lb[gp.triangulate(upper)]]
    return v, tris, np.vstack([tris, *caps]).astype(np.int32)


L_SHAPE = np.array([[0, 0], [40, 0], [40, 10], [10, 10], [10, 30], [0, 30]], dtype=np.float64)
COMB = np.array(
    [[0, 0], [50, 0], [50, 30], [40, 30], [40, 10], [30, 10], [30, 30], [20, 30], [20, 10], [10, 10], [10, 30],
     [0, 30], [0, 15]], dtype=np.float64,
)  # fmt: skip  — the last vertex is collinear


# --- polygons and caps -------------------------------------------------------------------------------------


def test_cap_triangles_cover_a_non_convex_polygon() -> None:
    for xy in (COMB, COMB[::-1]):
        polygon = section(xy, 3.0, azimuth=75.0)
        tris = gp.cap_triangles(polygon)
        assert tris.shape == (len(xy) - 2, 3) and tris.dtype == np.int32
        cross = np.cross(polygon[tris[:, 1]] - polygon[tris[:, 0]], polygon[tris[:, 2]] - polygon[tris[:, 0]])
        # No triangle outside the polygon: the triangle areas add up to the polygon's area.
        assert 0.5 * np.linalg.norm(cross, axis=1).sum() == pytest.approx(area(COMB), rel=1e-9)
        assert len(np.unique(tris)) == len(xy)  # the collinear vertex is used too
        # All triangles are wound the same way.
        assert (cross @ cross[np.argmax(np.linalg.norm(cross, axis=1))] >= 0).all()


def test_cap_keeps_repeated_and_collinear_vertices() -> None:
    """earcut drops a vertex equal to its neighbour; the fan puts it back, so the cap walks every outline edge."""
    doubled = np.insert(L_SHAPE, 2, L_SHAPE[1], axis=0)  # vertex 1 twice
    runs = np.vstack([[[x, 0.0] for x in np.linspace(0, 40, 9)], L_SHAPE[2:]])  # collinear run on the bottom
    for xy in (doubled, runs, doubled[::-1], runs[::-1]):
        tris = gp.triangulate(xy)
        assert gp.covers_outline(tris if gp.signed_area(xy) > 0 else (len(xy) - 1 - tris)[:, ::-1], len(xy))
        a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
        signed = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
        assert abs(signed.sum()) == pytest.approx(600.0, rel=1e-12)
        assert (np.sign(signed[signed != 0]) == np.sign(gp.signed_area(xy))).all()


def test_ear_clipping_covers_the_outline() -> None:
    """The fallback: n − 2 triangles over every outline edge, also for a damaged outline."""
    for xy in (COMB, ccw(ellipse(30, 5.0, 2.0)), np.array([[0, 0], [4, 0], [4, 0], [4, 4], [2, 2], [0, 4.0]])):
        tris = gp.ear_clip(np.ascontiguousarray(xy - xy.mean(axis=0)))
        assert gp.covers_outline(tris, len(xy))


def test_polygon_errors() -> None:
    with pytest.raises(gp.StitchError, match="нулевая площадь") as error:
        gp.cap_triangles(np.array([[0.0, 0, 0], [1, 1, 1], [2, 2, 2]]))
    assert error.value.code == "degenerate_polygon" and error.value.hint and error.value.between is None
    with pytest.raises(gp.StitchError, match="пропуски"):
        gp.clean_polygon(np.array([[0.0, 0, np.nan], [1, 0, 0], [0, 1, 0]]), "Контур 1")
    with pytest.raises(gp.StitchError, match="Контур 2: нужно не меньше трёх вершин"):
        gp.clean_polygon(np.array([[0.0, 0, 0], [1, 0, 0], [0.0, 0, 0]]), "Контур 2")
    closed = np.column_stack([np.vstack([L_SHAPE, L_SHAPE[:1]]), np.zeros(7)])
    assert gp.clean_polygon(closed, "Контур 1").tobytes() == closed[:6].tobytes()  # the repeated last is dropped


# --- the best start ----------------------------------------------------------------------------------------


def star(rng, n, cx=0.0, cy=0.0):  # type: ignore[no-untyped-def]
    t = np.sort(rng.uniform(0, 2 * np.pi, n))
    r = rng.uniform(0.3, 1.5, n)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def test_best_start_equals_the_full_search() -> None:
    """ADR 0015 9.5: the divide-and-conquer search finds the cost of the best of all m starts."""
    rng = np.random.default_rng(20261119)
    differ = 0
    for _ in range(400):
        n, m = (int(x) for x in rng.integers(3, 14, 2))
        qa, _ = ss.match_coords(star(rng, n))
        qb, _ = ss.match_coords(star(rng, m, *rng.normal(0, 0.3, 2)))
        hh = float(rng.uniform(0.0, 2.0)) ** 2
        s, cost = ss.best_start(qa, qb, hh)
        full = [ss.start_cost(qa, qb, hh, k) for k in range(m)]
        assert cost == pytest.approx(min(full), rel=1e-12, abs=0)
        assert ss.start_cost(qa, qb, hh, s) == pytest.approx(cost, rel=1e-12, abs=0)
        differ += int(np.argmin(full) != 0)
    assert differ > 100  # the best start is mostly not the first vertex: the search is really tested


def rows(t):  # type: ignore[no-untyped-def]
    return sorted(map(tuple, np.sort(t, axis=1).tolist()))


def full_search_strip(pa, pb, gap):  # type: ignore[no-untyped-def]
    """The reference: the cost of every start by an unbounded search, the strip of the cheapest one."""
    n, m = len(pa), len(pb)
    qa, sa = ss.match_coords(pa)
    qb, sb = ss.match_coords(pb)
    hh = (abs(gap) / ((sa + sb) / 2.0)) ** 2
    full = np.array([ss.start_cost(qa, qb, hh, k) for k in range(m)])
    s = int(np.argmin(full))
    cost, back = ss._buffers(n, m)
    lo, hi = np.full(n + 1, s, dtype=np.int64), np.full(n + 1, s + m, dtype=np.int64)
    ss._dp_fill(qa, qb, hh, 0, n, s, m, lo, hi, cost, back)
    tris, moves = np.empty((n + m, 3), dtype=np.int64), np.empty(n + m, dtype=np.int8)
    ss._dp_trace(back, 0, n, s, m, n, m, np.arange(n), np.arange(n, n + m), tris, moves, 0)
    return qa, qb, hh, full, tris, moves


def test_bounded_search_gives_the_start_and_the_strip_of_the_full_search() -> None:
    """The search gives up starts and nodes by a lower bound of the cost (9.37): on similar contours (most is
    given up) and on dissimilar ones the best start, its cost and the strip are those of the full search."""
    rng = np.random.default_rng(937)
    unique = same = 0
    for k in range(160):
        n, m = (int(x) for x in rng.integers(12, 90, 2))
        if k % 2:
            pa = ccw(ellipse(n, 3.0, 1.0 + rng.uniform(0, 1), phase=rng.uniform(0, 6)) + rng.normal(0, 0.02, (n, 2)))
            pb = ccw(ellipse(m, 2.5, 1.2, cx=rng.normal(0, 0.3), phase=rng.uniform(0, 6)) + rng.normal(0, 0.02, (m, 2)))
        else:
            pa, pb = ccw(star(rng, n)), ccw(star(rng, m, *rng.normal(0, 0.3, 2)))
        gap = float(rng.uniform(0.05, 3.0))
        qa, qb, hh, full, ref, moves = full_search_strip(pa, pb, gap)
        s, cost = ss.best_start(qa, qb, hh)
        assert cost == pytest.approx(full.min(), rel=1e-12, abs=0)
        assert ss.start_cost(qa, qb, hh, s) == pytest.approx(full.min(), rel=1e-12, abs=0)
        if int(np.count_nonzero(moves[1:] != moves[:-1])) < 3:
            continue  # the search that counts turns builds this strip
        got = ss.strip(pa, pb, np.arange(n), np.arange(n, n + m), gap)
        assert got is not None
        if (full > full.min() * (1 + 1e-9)).sum() == m - 1:  # one cheapest start
            unique += 1
            assert s == int(np.argmin(full)) and got.tobytes() == ref.tobytes()
        else:  # starts on one strip cost the same: the same triangles, begun at another node of the strip
            assert rows(got) == rows(ref)
            same += 1
    assert unique > 30 and same > 60


def test_best_start_in_portions_with_a_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(5)
    qa, _ = ss.match_coords(ccw(ellipse(300, 3.0, 1.0, phase=0.3)) + rng.normal(0, 0.01, (300, 2)))
    qb, _ = ss.match_coords(ccw(ellipse(250, 2.0, 1.5, cx=0.4)))
    whole = ss.best_start(qa, qb, 0.25)
    calls = []
    monkeypatch.setattr(ss, "BUDGET", 5_000)
    assert ss.best_start(qa, qb, 0.25, lambda: calls.append(1)) == whole
    assert len(calls) > 10

    def cancel() -> None:
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        ss.best_start(qa, qb, 0.25, cancel)


# --- strips ------------------------------------------------------------------------------------------------


def test_strip_has_no_twist() -> None:
    """Contours of different shape, vertex count, winding and start: every outline edge is walked once, the
    strip with two caps is a closed outward solid without self-intersections."""
    big = ellipse(40, 30.0, 14.0, phase=2.0)
    small = rounded_rectangle(18.0, 9.0, 4.0)[::-1] + [6.0, 3.0]
    v, tris, solid = prism_of_strip(big, small, 20.0)
    assert tris.shape == (57, 3)
    lower, upper = np.arange(40), np.arange(40, 57)
    for loop in (lower, upper):
        nxt = np.roll(loop, -1)
        directed = {(int(a), int(b)) for a, b, c in tris for a, b in ((a, b), (b, c), (c, a))}
        walked = [(int(a), int(b)) in directed or (int(b), int(a)) in directed for a, b in zip(loop, nxt, strict=True)]
        assert all(walked)
    assert edge_summary(solid, len(v)) == (0, 0)
    assert orientation_conflicts(solid, len(v)) == 0
    assert self_intersections(v, solid).pairs == 0
    stats = mesh_stats(v, solid)
    assert stats["orientation"] == "outward"
    low, high = sorted([area(big), area(small)])
    assert low * 20.0 < stats["volume"] < high * 20.0
    # Same shapes: the strip is a prism, volume exact.
    v, _, solid = prism_of_strip(L_SHAPE, L_SHAPE, 25.0)
    assert mesh_stats(v, solid)["volume"] == pytest.approx(600.0 * 25.0, rel=1e-9)


def test_tie_lines_are_edges_of_the_result() -> None:
    lower, upper = ellipse(40, 30.0, 14.0), ellipse(28, 22.0, 16.0, cx=5.0)
    pairs = ((0, 3), (10, 9), (22, 17), (31, 24))  # not the nearest vertices: the body turns between the sections
    v, tris, solid = prism_of_strip(lower, upper, 20.0, list(pairs))
    for i, j in pairs:
        assert has_edge(tris, i, 40 + j)
    assert edge_summary(solid, len(v)) == (0, 0) and orientation_conflicts(solid, len(v)) == 0
    _, free, _ = prism_of_strip(lower, upper, 20.0)
    assert not has_edge(free, 0, 40 + 3)  # without the tie this edge is not chosen
    # Ties that cross: the order along one contour differs from the order along the other.
    assert ss.strip(lower, upper, np.arange(40), np.arange(40, 68), 20.0, [(0, 0), (10, 14), (20, 7)]) is None


def test_very_dissimilar_contours_still_give_a_strip() -> None:
    """A sliver far to the side of a sprawling contour: the cheapest path walks one whole contour from one
    vertex of the other; the search that counts turns gives a strip instead."""
    wide = np.array([[4.83, 4.08], [-2.22, 2.75], [-14.44, 0.44], [-14.12, -3.18], [-12.79, -7.15],
                     [-12.52, -7.96], [-16.24, -10.97], [-18.23, -10.54], [-2.59, -36.54], [5.39, -38.51],
                     [1.03, -22.21], [0.93, -14.39], [0.83, -6.56], [0.73, 1.27]])  # fmt: skip
    sliver = np.array([[-56.51, 27.84], [-37.3, 38.77], [-39.46, 38.39], [-41.78, 41.61], [-43.03, 41.47],
                       [-43.18, 42.65], [-43.85, 44.86]])  # fmt: skip
    v, tris, solid = prism_of_strip(wide, sliver, 10.85)
    turns = 0
    for k in range(1, len(tris)):
        turns += int((tris[k, 2] < 14) != (tris[k - 1, 2] < 14))
    assert turns >= 3
    assert edge_summary(solid, len(v)) == (0, 0) and orientation_conflicts(solid, len(v)) == 0
    # Two ties from one vertex allow such a path only through the counted search.
    a, b = ellipse(12, 30.0, 14.0), ellipse(9, 22.0, 16.0)
    v, tris, solid = prism_of_strip(a, b, 20.0, [(0, 0), (0, 5)])
    assert has_edge(tris, 0, 12) and has_edge(tris, 0, 12 + 5)
    assert edge_summary(solid, len(v)) == (0, 0)


# --- strips in threads -------------------------------------------------------------------------------------


def series_items(rng, count=9):  # type: ignore[no-untyped-def]
    """Strips of a series: shapes, vertex counts and starts differ; one strip with tie lines, one dissimilar pair."""
    out = []
    for k in range(count):
        n, m = int(rng.integers(40, 160)), int(rng.integers(40, 160))
        lower = ccw(star(rng, n) * [30.0, 14.0])
        upper = ccw(star(rng, m, *rng.normal(0, 0.3, 2)) * [26.0, 16.0])
        ties = [(0, 3), (n // 2, m // 2)] if k == 4 else []
        out.append((lower, upper, np.arange(n), np.arange(n, n + m), 20.0 + k, ties))
    wide = np.array([[4.83, 4.08], [-2.22, 2.75], [-14.44, 0.44], [-14.12, -3.18], [-12.79, -7.15], [-12.52, -7.96],
                     [-16.24, -10.97], [-18.23, -10.54], [-2.59, -36.54], [5.39, -38.51], [1.03, -22.21]])  # fmt: skip
    sliver = np.array([[-56.51, 27.84], [-37.3, 38.77], [-39.46, 38.39], [-41.78, 41.61], [-43.85, 44.86]])
    out.append((ccw(wide), ccw(sliver), np.arange(11), np.arange(11, 16), 10.85, []))
    return out


@pytest.mark.parametrize("threads", [1, 2, 4])
def test_strips_in_threads_are_the_same_bit_for_bit(threads: int) -> None:
    """ADR 0015 «Замеры», ADR 0014: the strips of a series in a pool of threads give what one thread gives."""
    items = series_items(np.random.default_rng(37))
    alone = [ss.strip(*item) for item in items]
    seen: list[int] = []
    got = ss.strips(items, threads=threads, done=seen.append)
    assert len(got) == len(items)
    for a, b in zip(alone, got, strict=True):
        assert a is not None and b is not None and a.dtype == b.dtype and a.tobytes() == b.tobytes()
    assert seen == list(range(len(items)))  # k strips finished, in order, before the next is waited for


def test_strips_keep_to_the_memory_limit_and_report_crossing_ties(monkeypatch: pytest.MonkeyPatch) -> None:
    items = series_items(np.random.default_rng(8), 5)
    whole = ss.strips(items, threads=4)
    monkeypatch.setattr(ss, "MEMORY_SPANS", 1)  # one strip at a time: always at least one runs
    assert [t.tobytes() for t in ss.strips(items, threads=4)] == [t.tobytes() for t in whole]  # type: ignore[union-attr]
    lower, upper = ellipse(40, 30.0, 14.0), ellipse(28, 22.0, 16.0, cx=5.0)
    crossing = (lower, upper, np.arange(40), np.arange(40, 68), 20.0, [(0, 0), (10, 14), (20, 7)])
    got = ss.strips([items[0], crossing, items[1]], threads=3)
    assert got[1] is None and got[0] is not None and got[2] is not None


def test_strips_in_threads_stop_when_the_tick_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(3)
    big = [(ccw(ellipse(400, 3.0, 1.0, phase=0.1 * k)) + rng.normal(0, 0.01, (400, 2)), ccw(ellipse(380, 2.0, 1.5)),
            np.arange(400), np.arange(400, 780), 1.0, []) for k in range(6)]  # fmt: skip
    monkeypatch.setattr(ss, "BUDGET", 20_000)  # many portions: the threads look at the stop often
    monkeypatch.setattr(ss, "TICK_S", 0.001)
    calls = []

    def tick() -> None:
        calls.append(1)
        raise KeyboardInterrupt  # what JobContext.tick raises goes through untouched

    start = time.perf_counter()
    with pytest.raises(KeyboardInterrupt):
        ss.strips(big, threads=3, tick=tick)
    assert len(calls) == 1
    assert time.perf_counter() - start < 5.0
    assert not [t for t in threading.enumerate() if t.name.startswith("bf-stitch")]  # the pool is gone


def test_kernels_are_cached_and_release_the_gil() -> None:
    kernels = [gp.ear_clip, ss._span, ss._dp_fill, ss._dp_bounds, ss._dp_trace, ss._ring_path, ss._best_start]
    for kernel in kernels:
        assert kernel.targetoptions.get("nogil") is True, kernel
        assert type(kernel._cache).__name__ != "NullCache", kernel
