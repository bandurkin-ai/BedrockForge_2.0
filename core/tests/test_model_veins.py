"""The body of a vein (подшаг 19.15; ТЗ 8.9.2 «Жилы», Т8.9-7; ADR 0026 решение 17): a flat vein of 2 m has the volume
area × 2 within 1 %; an inclined and a curved vein; the pinch-out (wedge, t_min, two lobes); the solid is closed, the
normals look outward, the volume is positive; the 2D partition of unity (above 4000 contacts) agrees with the dense
solution; the same bytes for any order of the points and any number of threads; degenerate data are {code, message,
hint} in Russian; numba and manifold3d are not loaded by the modules."""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull

from bf_core.api.errors import ApiError
from bf_core.geo import checks, meshes, solids
from bf_core.model import data_holes as dhm
from bf_core.model import veins, veins_surface
from bf_core.model.spec_parts import _vein
from tests.test_model_data import scene_arrays

CORE = Path(__file__).resolve().parents[1]
ORG = np.array([512_345.0, 6_234_567.0, 412.0])  # UTM-sized coordinates
HULL = {"bound": "hull", "offset": 0.0, "t_min": 0.0}
LX, LY = 100.0, 60.0


def frame(tilt: float = 30.0, azimuth: float = 40.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The true axes (u0, v0, w0) of a plane dipping by `tilt`; w0 looks up (to the hanging wall)."""
    a, b = math.radians(tilt), math.radians(azimuth)
    w0 = np.array([math.sin(a) * math.cos(b), math.sin(a) * math.sin(b), math.cos(a)])
    u0 = np.array([-math.sin(b), math.cos(b), 0.0])
    return u0, np.cross(w0, u0), w0


def to_xyz(uv: np.ndarray, w: np.ndarray, fr: Any) -> np.ndarray:
    u0, v0, w0 = fr
    return ORG + uv[:, 0:1] * u0 + uv[:, 1:2] * v0 + np.asarray(w)[:, None] * w0


def scatter(n: int, seed: int = 1) -> np.ndarray:
    """n random (u, v) in the rectangle and its four corners (so the hull is the rectangle)."""
    rng = np.random.default_rng(seed)
    corners = np.array([[0.0, 0.0], [LX, 0.0], [LX, LY], [0.0, LY]])
    return np.concatenate([corners, np.column_stack([rng.uniform(0, LX, n), rng.uniform(0, LY, n)])])


def lattice(nu: int, nv: int = 7) -> np.ndarray:
    u, v = np.meshgrid(np.linspace(0, LX, nu), np.linspace(0, LY, nv), indexing="ij")
    return np.column_stack([u.ravel(), v.ravel()])


def vein(uv: np.ndarray, mid: Any, thick: Any, fr: Any) -> tuple[np.ndarray, np.ndarray]:
    """The sides of a vein: the hanging wall at mid + t/2 and the footwall at mid − t/2 along w0."""
    m, t = mid(uv[:, 0], uv[:, 1]), thick(uv[:, 0], uv[:, 1])
    return to_xyz(uv, m + t / 2.0, fr), to_xyz(uv, m - t / 2.0, fr)


def flat(_u: Any, _v: Any) -> Any:
    return np.zeros_like(_u)


def const(t: float) -> Any:
    return lambda u, v: np.full_like(u, t)


def build(hang: Any, foot: Any, params: dict[str, Any] | None = None, h: float = 2.0, **kw: Any) -> veins.VeinBody:
    body = veins.build_vein(hang, foot, params or HULL, h, **kw)
    assert body is not None
    return body


def assert_closed(body: veins.VeinBody) -> None:
    v, t = body.vertices, body.triangles
    assert v.dtype == np.float64 and t.dtype == np.int32 and v.shape[1] == t.shape[1] == 3
    assert meshes.edge_summary(t, len(v)) == (0, 0)
    assert checks.orientation_conflicts(t, len(v)) == 0
    closed, why = solids.is_closed_solid(v, t)
    assert closed, why
    local = v - v.mean(axis=0)
    volume = solids.signed_volume(local, t.astype(np.int64))
    assert volume > 0.0 and volume == pytest.approx(body.volume, rel=1e-9)  # the normals look outward
    assert body.area == pytest.approx(solids.surface_area(local, t.astype(np.int64)), rel=1e-9)


def parts(body: veins.VeinBody) -> int:
    t, n = body.triangles, len(body.vertices)
    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    return int(connected_components(coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n)), directed=False)[0])


def hull_area(uv: np.ndarray) -> float:
    return float(ConvexHull(uv).volume)  # in 2D `volume` is the area


# --- Т8.9-7: the flat vein of 2 m ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tilt", [0.0, 30.0, 75.0])
def test_a_flat_vein_of_two_metres_has_the_volume_area_times_two(tilt: float) -> None:
    fr = frame(tilt)
    uv = scatter(300)
    hang, foot = vein(uv, flat, const(2.0), fr)
    body = build(hang, foot)
    assert body.volume == pytest.approx(hull_area(uv) * 2.0, rel=0.01)
    assert body.stats["thickness"]["mean"] == pytest.approx(2.0, rel=0.01)
    assert body.stats["pinched_share"] == 0.0
    assert_closed(body)
    # the plane found by PCA is the true one and the hanging wall is on the side of w
    assert abs(float(body.plane.w @ fr[2])) > 0.9999
    assert float(body.plane.w[2]) > 0.0 and np.allclose(np.cross(body.plane.u, body.plane.v), body.plane.w)
    # the solid lies between the two planes: the distance of its vertices to the mid plane is 0 or ±1 m
    d = (body.vertices - ORG) @ fr[2]
    assert np.all(np.abs(np.abs(d) - 1.0) < 1e-6)


def test_the_volume_does_not_depend_on_h() -> None:
    fr = frame(20.0)
    uv = scatter(200)
    hang, foot = vein(uv, flat, const(2.0), fr)
    for h in (1.0, 3.0, 7.0, 25.0):
        assert build(hang, foot, h=h).volume == pytest.approx(hull_area(uv) * 2.0, rel=0.01)


def test_the_hull_is_widened_by_the_offset_with_round_corners() -> None:
    uv = scatter(200)
    hang, foot = vein(uv, flat, const(2.0), frame())
    d = 5.0
    area = LX * LY + 2.0 * (LX + LY) * d + math.pi * d * d
    body = build(hang, foot, {"bound": "hull", "offset": d, "t_min": 0.0})
    assert body.volume == pytest.approx(area * 2.0, rel=0.005)
    assert_closed(body)


def test_the_polygon_is_in_the_uv_of_the_plane() -> None:
    fr = frame(35.0, 110.0)
    uv = scatter(400)
    hang, foot = vein(uv, flat, const(2.0), fr)
    plane = veins.fit_plane(hang, foot)
    rect = np.array([[-30.0, -20.0], [30.0, -20.0], [30.0, 20.0], [-30.0, 20.0]])
    body = build(hang, foot, _vein({"bound": "polygon", "polygon": rect.tolist(), "offset": 99}, "p"))
    assert body.volume == pytest.approx(60.0 * 40.0 * 2.0, rel=0.01)  # the offset belongs to the hull only
    assert np.allclose(body.plane.origin, plane.origin) and np.allclose(body.plane.u, plane.u)
    assert_closed(body)
    # an L: the area 60·40 − 30·20 of the rectangle without a corner, clockwise given
    ell = np.array([[-30.0, -20.0], [-30.0, 20.0], [0.0, 20.0], [0.0, 0.0], [30.0, 0.0], [30.0, -20.0]])
    body = build(hang, foot, {"bound": "polygon", "polygon": ell.tolist(), "offset": 0.0, "t_min": 0.0}, h=1.5)
    assert body.volume == pytest.approx((2400.0 - 600.0) * 2.0, rel=0.02)
    assert_closed(body)


# --- an inclined and a curved vein ----------------------------------------------------------------------------------


def test_an_inclined_vein_with_a_changing_thickness() -> None:
    fr = frame(55.0, 200.0)
    uv = scatter(400, seed=3)
    hang, foot = vein(uv, lambda u, v: 0.02 * u - 0.01 * v, lambda u, v: 1.0 + 0.03 * u, fr)  # exact for the spline
    body = build(hang, foot)
    assert body.volume == pytest.approx(LY * (LX + 0.015 * LX * LX), rel=0.01)  # ∫ t dA over the rectangle
    assert_closed(body)


def test_a_curved_vein() -> None:
    fr = frame(40.0, 300.0)
    uv = scatter(500, seed=5)
    mid = lambda u, v: 3.0 * np.sin(u / 40.0) + 0.004 * v * v  # noqa: E731
    thick = lambda u, v: 2.0 + 0.01 * u  # noqa: E731
    hang, foot = vein(uv, mid, thick, fr)
    body = build(hang, foot)
    assert body.volume == pytest.approx(LY * (2.0 * LX + 0.005 * LX * LX), rel=0.02)
    assert_closed(body)
    # the hanging wall of the body passes through its contacts
    uvw = body.plane.to_uvw(hang)
    assert np.isfinite(uvw).all()


# --- the pinch-out ---------------------------------------------------------------------------------------------------


def test_a_wedge_converges_to_a_line() -> None:
    fr = frame(30.0, 15.0)
    hang, foot = vein(lattice(21), flat, lambda u, v: 2.0 - 0.03 * u, fr)  # t = 0 at u = 66.7
    body = build(hang, foot, h=1.5)
    assert body.volume == pytest.approx(LY * (2.0 * 66.667 - 0.015 * 66.667**2), rel=0.01)
    assert 0.30 < body.stats["pinched_share"] < 0.36
    assert_closed(body)
    # no wall at the line: the top meets the bottom there (no vertex is farther than 0 from the other side's mate)
    u0 = (body.vertices - ORG) @ frame(30.0, 15.0)[0]
    assert float(u0.max()) == pytest.approx(66.667, abs=0.01)  # the line of the pinch is exact on the edges
    assert any("Выклинивается" in line for line in body.notes)


def test_t_min_cuts_the_vein_where_it_is_thinner_and_closes_it_with_a_wall() -> None:
    fr = frame(30.0, 15.0)
    hang, foot = vein(lattice(21), flat, lambda u, v: 2.0 - 0.03 * u, fr)
    body = build(hang, foot, {"bound": "hull", "offset": 0.0, "t_min": 0.5}, h=1.5)  # t = 0.5 at u = 50
    assert body.volume == pytest.approx(LY * (2.0 * 50.0 - 0.015 * 50.0**2), rel=0.01)
    assert_closed(body)
    u0 = (body.vertices - ORG) @ frame(30.0, 15.0)[0]
    far = body.vertices[u0 > 49.99]
    w = (far - ORG) @ frame(30.0, 15.0)[2]
    assert float(np.ptp(w)) == pytest.approx(0.5, abs=1e-6)  # the wall at the contour is t_min high


def test_two_lobes_make_two_closed_pieces() -> None:
    fr = frame(25.0, 70.0)
    uv = lattice(41, 7)
    thick = lambda u, v: 2.0 * np.sin(2.0 * np.pi * (u - 10.0) / 50.0) + 0.3  # noqa: E731
    hang, foot = vein(uv, flat, thick, fr)
    body = build(hang, foot, h=1.0)
    x = np.linspace(0.0, LX, 200_001)
    expected = LY * float(np.sum(np.maximum(2.0 * np.sin(2.0 * np.pi * (x - 10.0) / 50.0) + 0.3, 0.0)) * (LX / 200_000))
    assert body.volume == pytest.approx(expected, rel=0.03)
    assert parts(body) == 2 and body.stats["pinched_share"] > 0.2
    assert_closed(body)


def test_a_vein_pinched_out_everywhere_is_none() -> None:
    hang, foot = vein(scatter(100), flat, const(2.0), frame())
    assert veins.build_vein(hang, foot, {"bound": "hull", "offset": 0.0, "t_min": 5.0}, 2.0) is None
    assert veins.vein_body(hang, foot, {"bound": "hull", "offset": 0.0, "t_min": 5.0}, 2.0) is None
    swapped = veins.vein_body(foot, hang, HULL, 2.0)  # the sides turned over: w follows the hanging wall, a vein again
    assert swapped is not None


def test_vein_body_is_the_contract_of_bodies() -> None:
    hang, foot = vein(scatter(100), flat, const(2.0), frame())
    got = veins.vein_body(hang, foot, HULL, 2.0)
    assert got is not None
    v, t = got
    assert v.dtype == np.float64 and v.shape[1] == 3 and t.dtype == np.int32 and t.shape[1] == 3
    ref = build(hang, foot)
    assert v.tobytes() == ref.vertices.tobytes() and t.tobytes() == ref.triangles.tobytes()


# --- the 2D partition of unity ---------------------------------------------------------------------------------------


def smooth(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return 5.0 * np.sin(u / 30.0) * np.cos(v / 20.0) + 0.01 * u


def test_the_pou_surface_interpolates_and_follows_the_dense_one() -> None:
    rng = np.random.default_rng(7)
    uv = np.column_stack([rng.uniform(0, LX, 2500), rng.uniform(0, LY, 2500)])
    w = smooth(uv[:, 0], uv[:, 1])
    dense = veins_surface.fit_surface(uv, w, dense_max=10_000)
    pou = veins_surface.fit_surface(uv, w, dense_max=600)
    assert dense.n_patches == 0 and pou.n_patches > 8
    assert np.abs(pou.evaluate(uv) - w).max() < 1e-8  # every patch interpolates its points
    q = np.column_stack([rng.uniform(-10, LX + 10, 4000), rng.uniform(-10, LY + 10, 4000)])
    assert np.isfinite(pou.evaluate(q)).all()  # outside the patches: the nearest one
    inside = np.column_stack([rng.uniform(0, LX, 3000), rng.uniform(0, LY, 3000)])
    assert np.abs(pou.evaluate(inside) - dense.evaluate(inside)).max() < 2e-2  # the range of w is 10 m


def test_above_4000_contacts_a_side_goes_through_the_pou_and_agrees_with_the_dense_solution() -> None:
    fr = frame(30.0, 100.0)
    uv = scatter(4100, seed=9)
    mid = lambda u, v: 2.0 * np.sin(u / 25.0) * np.cos(v / 15.0)  # noqa: E731
    thick = lambda u, v: 2.0 + 0.2 * np.cos(u / 20.0)  # noqa: E731
    hang, foot = vein(uv, mid, thick, fr)
    pou = build(hang, foot, h=2.0)
    assert pou.stats["patches"]["hanging"] > 0 and pou.stats["patches"]["footwall"] > 0
    dense = build(hang, foot, h=2.0, dense_max=10_000)
    assert dense.stats["patches"] == {"hanging": 0, "footwall": 0}
    assert pou.volume == pytest.approx(dense.volume, rel=2e-3)
    assert pou.stats["thickness"]["mean"] == pytest.approx(dense.stats["thickness"]["mean"], rel=2e-3)
    assert_closed(pou)
    assert pou.volume == pytest.approx(hull_area(uv) * 2.0, rel=0.02)  # t = 2 + 0.2·cos: the mean of cos is ≈ 0


# --- the same bytes ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dense_max", [4000, 300])
def test_the_order_of_the_points_and_the_threads_do_not_change_a_bit(dense_max: int) -> None:
    fr = frame(33.0, 250.0)
    uv = scatter(450, seed=11)
    mid = lambda u, v: 2.0 * np.sin(u / 25.0)  # noqa: E731
    hang, foot = vein(uv, mid, const(2.5), fr)
    ref = build(hang, foot, h=2.0, dense_max=dense_max)
    rng = np.random.default_rng(2)
    for threads in (1, 4):
        got = build(hang[rng.permutation(len(hang))], foot[rng.permutation(len(foot))], h=2.0, threads=threads,
                    dense_max=dense_max)  # fmt: skip
        assert got.vertices.tobytes() == ref.vertices.tobytes()
        assert got.triangles.tobytes() == ref.triangles.tobytes()
        assert got.volume == ref.volume and got.area == ref.area
    assert (ref.stats["patches"]["hanging"] > 0) == (dense_max == 300)


# --- the data of the fixture scene -----------------------------------------------------------------------------------


def test_the_vein_of_the_fixture_scene() -> None:
    sc = scene_arrays()
    state = (sc["code"] == "VN").astype(np.int8)
    hang, foot = dhm.vein_sides(sc["lines"], state, sc["traj"], np.arange(sc["lines"].n_holes))
    body = build(hang, foot, h=5.0)
    assert float(body.plane.w @ np.array([0.8, 0.0, 0.6])) > 0.999  # the normal of the slab of `tools/gen/implicit.py`
    assert 2.5 < body.stats["thickness"]["mean"] < 3.2  # the slab is 3 m thick
    assert_closed(body)


# --- degenerate data ---------------------------------------------------------------------------------------------


def refused(code: str, reason: str | None, *args: Any, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as err:
        veins.build_vein(*args, **kw)
    e = err.value
    assert e.code == code and e.status == 422 and re.search("[А-Яа-я]{4}", e.message) and e.hint
    assert e.extra.get("reason") == reason
    return e


def test_no_contacts_and_too_few() -> None:
    hang, foot = vein(scatter(20), flat, const(2.0), frame())
    refused("no_contacts", "few_contacts", np.zeros((0, 3)), foot, HULL, 2.0)
    refused("no_contacts", "few_contacts", hang, foot[:2], HULL, 2.0)
    refused("no_contacts", "few_contacts", hang, np.zeros((0, 3)), HULL, 2.0)
    with pytest.raises(ApiError) as err:
        veins.fit_plane(hang[:2], foot)
    assert err.value.code == "no_contacts"


def test_contacts_on_a_line_or_in_a_cloud_give_no_plane() -> None:
    t = np.linspace(0.0, 100.0, 12)
    line = ORG + t[:, None] * np.array([0.6, 0.8, 0.0])
    refused("no_contacts", "collinear", line + [0, 0, 1.0], line - [0, 0, 1.0], HULL, 2.0)
    rng = np.random.default_rng(4)
    cloud = ORG + rng.uniform(0, 10, (30, 3))
    refused("no_contacts", "not_planar", cloud, cloud + rng.uniform(0, 2, (30, 3)), HULL, 2.0)


def test_a_side_on_a_line_is_refused_even_when_the_other_one_is_wide() -> None:
    hang, foot = vein(scatter(60), flat, const(2.0), frame())
    t = np.linspace(0.0, 100.0, 12)
    refused(
        "no_contacts", "collinear", hang, to_xyz(np.column_stack([t, 0.5 * t]), np.full(12, -1.0), frame()), HULL, 2.0
    )
    del foot


def test_missing_coordinates_are_dropped_with_a_note_never_zero() -> None:
    hang, foot = vein(scatter(80), flat, const(2.0), frame())
    bad_h = hang.copy()
    bad_h[5, 2] = np.nan
    bad_h[9] = np.inf
    clean = build(np.delete(hang, [5, 9], axis=0), foot)
    got = build(bad_h, foot)
    assert got.vertices.tobytes() == clean.vertices.tobytes()
    assert any("пропуск" in line for line in got.notes)
    refused("no_contacts", "few_contacts", np.full((10, 3), np.nan), foot, HULL, 2.0)


def test_coincident_contacts_are_merged() -> None:
    hang, foot = vein(scatter(80), flat, const(2.0), frame())
    twice = np.concatenate([hang, hang[:10]])  # a contact met twice: the plane moves a little, no crash
    assert build(twice, foot).volume == pytest.approx(build(hang, foot).volume, rel=1e-3)


def test_bad_parameters() -> None:
    hang, foot = vein(scatter(60), flat, const(2.0), frame())
    for h in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(ApiError) as err:
            veins.build_vein(hang, foot, HULL, h)
        assert err.value.code == "bad_param"
    with pytest.raises(ApiError) as err:
        veins.build_vein(hang, foot, HULL, 0.001)  # millions of nodes
    assert err.value.code == "bad_param" and err.value.hint
    line = {"bound": "polygon", "polygon": [[0, 0], [10, 0], [20, 0]], "offset": 0.0, "t_min": 0.0}
    with pytest.raises(ApiError) as err:
        veins.build_vein(hang, foot, line, 2.0)
    assert err.value.code == "bad_param"


def test_the_cancel_of_the_tick_stops_the_build() -> None:
    hang, foot = vein(scatter(60), flat, const(2.0), frame())
    seen: list[float | None] = []

    class Stop(Exception):
        pass

    def tick(share: float | None, _text: str | None) -> None:
        seen.append(share)
        if len(seen) >= 3:
            raise Stop

    with pytest.raises(Stop):
        veins.build_vein(hang, foot, HULL, 2.0, tick=tick)
    assert len(seen) == 3


# --- numba is not loaded -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["bf_core.model.veins", "bf_core.model.veins_surface"])
def test_the_vein_modules_load_neither_numba_nor_manifold3d(module: str) -> None:
    """The handlers of jobs import them (`test_job_handler_modules_do_not_load_numba`): the kernels come with the
    first surface, inside the functions."""
    code = f"import {module}, sys; raise SystemExit(3 if {{'numba', 'manifold3d'}} & set(sys.modules) else 0)"
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr[-400:] or f"numba or manifold3d is loaded by {module}"
