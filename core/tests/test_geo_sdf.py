"""The signed distance of points to a closed wireframe (Д-28, ТД28-3; `geo/sdf.py`; the kernel `Расширение каркаса`
8.9.3 of Э19 takes too): the cube and the sphere against the analytic distance and against a full search (1e-9 m),
the sign by the parity of a vertical ray — through vertices, edges and faces, on the surface, in a body with a
cavity — the same bytes with any number of threads and any order of the points."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geo import sdf
from bf_core.geo.intersect import MeshTarget
from tests.meshutil import UTM, cube, icosphere
from tests.test_geo_nearest import ref_distances

TOL = 1e-9  # metres
NAN = math.nan
ZERO = np.zeros(3)


def box_sdf(p: np.ndarray, low: np.ndarray, side: float) -> np.ndarray:
    """The exact signed distance to an axis-aligned cube (inside < 0): an independent formula."""
    q = np.abs(p - (low + 0.5 * side)) - 0.5 * side
    out = np.linalg.norm(np.maximum(q, 0.0), axis=1)
    return out + np.minimum(q.max(axis=1), 0.0)  # type: ignore[no-any-return]


def winding(p: np.ndarray, v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """|winding number| of the closed surface round each point by the solid angles of its triangles
    (Van Oosterom–Strackee): the independent reference of «inside». [k]"""
    out = np.empty(len(p))
    for k, q in enumerate(p):
        a, b, c = (v[t[:, i]] - q for i in range(3))
        la, lb, lc = (np.linalg.norm(x, axis=1) for x in (a, b, c))
        num = (a * np.cross(b, c)).sum(1)
        den = la * lb * lc + (a * b).sum(1) * lc + (a * c).sum(1) * lb + (b * c).sum(1) * la
        out[k] = abs(2.0 * np.arctan2(num, den).sum() / (4.0 * math.pi))
    return out


def ref_signed(p: np.ndarray, v: np.ndarray, t: np.ndarray) -> np.ndarray:
    d = ref_distances(p, v, t).min(axis=1)
    return np.where(winding(p, v, t) > 0.5, -d, d)  # type: ignore[no-any-return]


# --- the cube: ТД28-3 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 3])
def test_cube_distance_is_analytic(n: int) -> None:
    side = 10.0
    v, t = cube(n, side, UTM)
    rng = np.random.default_rng(1)
    low = UTM
    p = low + rng.uniform(-6.0, 16.0, (3000, 3))
    got = sdf.signed_distance(v, t, p)
    assert np.abs(got - box_sdf(p, low, side)).max() < TOL
    assert ((got < 0) == (box_sdf(p, low, side) < 0)).all()
    assert (got < 0).any() and (got > 0).any()


def test_cube_points_on_lines_through_vertices_edges_and_faces() -> None:
    """A vertical ray through a vertex, along an edge, over a diagonal of a face: every lattice point of the cube
    (the corners of the cells of the faces among them) has the right sign, a point on the surface — 0."""
    side, n = 8.0, 4
    low = np.array([1000.0, 2000.0, 300.0])  # whole numbers: the lattice is exact
    v, t = cube(n, side, low)
    g = np.arange(-2, 11) * (side / n)  # −4 … 20: the lattice of the cells beyond the cube too
    p = low + np.array(np.meshgrid(g, g, g, indexing="ij")).reshape(3, -1).T
    got = sdf.signed_distance(v, t, p)
    want = box_sdf(p, low, side)
    assert np.abs(got - want).max() < TOL
    on = np.abs(want) < 1e-12
    assert on.any() and (got[on] == 0.0).all()
    assert ((got[~on] < 0) == (want[~on] < 0)).all()


def test_cube_with_a_cavity_is_hollow() -> None:
    """An inner cube turned inside out: its inside is outside of the body (parity), the distance is that of the
    nearer wall."""
    low = np.array([0.0, 0.0, 0.0])
    v1, t1 = cube(2, 10.0, low)
    v2, t2 = cube(2, 4.0, np.array([3.0, 3.0, 3.0]))
    v = np.vstack([v1, v2])
    t = np.vstack([t1, t2[:, ::-1] + len(v1)])
    p = np.array([[5.0, 5.0, 5.0], [1.0, 5.0, 5.0], [5.0, 5.0, 2.0], [12.0, 5.0, 5.0], [5.0, 5.0, 3.0]])
    got = sdf.signed_distance(v, t, p, check=False)
    assert got[0] == pytest.approx(2.0, abs=TOL)  # in the cavity: 2 m from its walls
    assert got[1] == pytest.approx(-1.0, abs=TOL)  # in the shell: 1 m from the outer wall
    assert got[2] == pytest.approx(-1.0, abs=TOL)  # 1 m under the cavity
    assert got[3] == pytest.approx(2.0, abs=TOL)
    assert got[4] == 0.0  # on the wall of the cavity


# --- the sphere -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("level", [1, 2])
def test_sphere_against_a_full_search(level: int) -> None:
    v, t = icosphere(level, 10.0, UTM)
    rng = np.random.default_rng(level)
    p = UTM + rng.uniform(-14.0, 14.0, (400, 3))
    got = sdf.signed_distance(v, t, p)
    assert np.abs(got - ref_signed(p, v, t)).max() < TOL
    inside = np.linalg.norm(p - UTM, axis=1) < 9.0
    assert (got[inside] < 0).all() and (got[np.linalg.norm(p - UTM, axis=1) > 11.0] > 0).all()


def test_sphere_rays_through_the_vertices() -> None:
    """Over and under every vertex of the sphere on the same vertical line: the ray goes through a vertex of the
    wireframe (several triangles meet there), and the parity is still right."""
    c = np.array([5.0, 7.0, 3.0])
    v, t = icosphere(2, 10.0, c)
    inner = v * 0.5 + c * 0.5
    p = np.vstack([v - [0.0, 0.0, 2.0], v + [0.0, 0.0, 2.0], inner])  # the vertical line of a point meets a vertex
    got = sdf.signed_distance(v, t, p)
    assert np.abs(got - ref_signed(p, v, t)).max() < TOL
    assert (got[2 * len(v) :] < 0).all()
    assert np.array_equal(sdf.signed_distance(v, t, v), np.zeros(len(v)))  # the vertices: on the surface


# --- the rest ---------------------------------------------------------------------------------------------


def test_threads_and_order_do_not_change_a_byte() -> None:
    v, t = icosphere(3, 20.0, UTM)
    target = MeshTarget(v, t, solid="outward")
    rng = np.random.default_rng(5)
    p = UTM + rng.uniform(-25.0, 25.0, (3 * sdf.BAND + 17, 3))
    one = sdf.signed_distance_to(target, p, threads=1)
    assert np.array_equal(one, sdf.signed_distance_to(target, p, threads=4))
    order = rng.permutation(len(p))
    assert np.array_equal(one[order], sdf.signed_distance_to(target, p[order], threads=2))
    ticks: list[float] = []
    sdf.signed_distance_to(target, p, tick=ticks.append)
    assert ticks and ticks[-1] == pytest.approx(1.0) and ticks == sorted(ticks)


def test_reach_cuts_the_search_not_the_sign() -> None:
    v, t = icosphere(2, 10.0, UTM)
    rng = np.random.default_rng(9)
    p = UTM + rng.uniform(-16.0, 16.0, (500, 3))
    full = sdf.signed_distance(v, t, p)
    cut = sdf.signed_distance(v, t, p, reach=3.0)
    near = np.abs(full) <= 3.0
    assert near.any() and (~near).any()
    assert np.array_equal(cut[near], full[near])  # within reach: the same bits
    assert np.isinf(cut[~near]).all() and np.array_equal(np.sign(cut[~near]), np.sign(full[~near]))
    on = sdf.signed_distance(v, t, v[:5], reach=0.0)
    assert np.array_equal(on, np.zeros(5))
    assert np.isnan(sdf.signed_distance(v, t, np.array([[NAN, 0.0, 0.0]]), reach=1.0)).all()


def test_cancel_through_tick() -> None:
    v, t = cube(1, 4.0, ZERO)
    p = np.zeros((2 * sdf.BAND, 3))

    class Stop(Exception):
        pass

    def stop(_f: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        sdf.signed_distance(v, t, p, tick=stop)


def test_missing_values_stay_missing() -> None:
    v, t = cube(1, 4.0, ZERO)
    p = np.array([[1.0, 1.0, 1.0], [NAN, 1.0, 1.0], [1.0, math.inf, 1.0], [9.0, 9.0, 9.0]])
    got = sdf.signed_distance(v, t, p)
    assert got[0] == pytest.approx(-1.0) and np.isnan(got[1]) and np.isnan(got[2])
    assert got[3] == pytest.approx(math.sqrt(3.0) * 5.0)
    assert sdf.signed_distance(v, t, np.zeros((0, 3))).shape == (0,)


def test_read_only_and_foreign_dtypes() -> None:
    v, t = icosphere(1, 3.0, ZERO)
    v.setflags(write=False)
    t64 = t.astype(np.int64)
    t64.setflags(write=False)
    p = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]], dtype=np.float32)
    got = sdf.signed_distance(v, t64, p)
    assert got.dtype == np.float64 and got[0] < 0 < got[1]
    f_order = np.asfortranarray(np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]]))
    assert np.array_equal(sdf.signed_distance(v, t, f_order), got)


def test_an_open_wireframe_is_refused_in_russian() -> None:
    v, t = cube(2, 4.0, ZERO)
    with pytest.raises(sdf.SdfError) as e:
        sdf.signed_distance(v, t[:-1], np.zeros((1, 3)))
    assert e.value.code == "wireframe_open"
    assert e.value.message and e.value.hint and set(e.value.as_dict()) == {"code", "message", "hint"}
    assert sdf.signed_distance(v, t[:-1], np.array([[2.0, 2.0, 2.0]]), check=False).shape == (1,)
    with pytest.raises(sdf.SdfError) as e:
        sdf.signed_distance(v, t[:0], np.zeros((1, 3)))
    assert e.value.code == "wireframe_empty"


def test_inside_is_the_parity_of_the_ray() -> None:
    v, t = cube(2, 4.0, ZERO)
    target = MeshTarget(v, t)
    p = np.array([[2.0, 2.0, 2.0], [2.0, 2.0, -1.0], [2.0, 2.0, 9.0], [NAN, 0.0, 0.0], [9.0, 2.0, 2.0]])
    assert sdf.inside(target, p).tolist() == [True, False, False, False, False]


def test_warm_compiles_on_read_only_and_writable_arrays() -> None:
    sdf.warm()
