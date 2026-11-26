"""«Расширение каркаса» (подшаг 19.6; ТЗ 8.9.3, Т8.9-7; ADR 0026 решение 18): the sphere R = 50 expanded by 5 has the
vertices at 55 ± 0.25·h, shrunk by 5 at 45; a cube against the volume of Steiner; the field is d − Δ within `reach` and
±H beyond, bit for bit under any threads; an open wireframe is refused; a body shrunk to nothing is an empty surface."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from bf_core.geo import checks, meshes, sdf, solids
from bf_core.geo.iso import isosurface
from bf_core.model import expand
from bf_core.model.expand import NEAR, ExpandError, expand_box, expand_field, expand_surface
from tests.meshutil import UTM, cube, icosphere

CORE = Path(__file__).resolve().parents[1]
R = 50.0
H_STEP, LEVELS = 2.0, 2
BIG = H_STEP * 2**LEVELS


def radii(m) -> np.ndarray:  # type: ignore[no-untyped-def]
    return np.linalg.norm(m.vertices - UTM, axis=1)


def assert_closed(m) -> None:  # type: ignore[no-untyped-def]
    assert len(m.triangles) > 0
    assert meshes.edge_summary(m.triangles, len(m.vertices)) == (0, 0)
    assert checks.orientation_conflicts(m.triangles, len(m.vertices)) == 0
    assert solids.signed_volume(m.vertices - m.vertices.mean(axis=0), m.triangles) > 0.0


# --- Т8.9-7: the sphere -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("delta", "expected"), [(5.0, 55.0), (-5.0, 45.0), (0.0, 50.0)])
def test_sphere_expansion_t897(delta: float, expected: float) -> None:
    v, t = icosphere(4, R)  # 5 120 triangles, the facets ~2.4 m: the sagitta is far below 0.25·h
    m = expand_surface(v, t, delta, h=H_STEP, levels=LEVELS)
    assert_closed(m)
    assert m.vertices.dtype == np.float64 and m.triangles.dtype == np.int32
    r = radii(m)
    assert np.abs(r - expected).max() <= 0.25 * H_STEP
    volume = solids.signed_volume(m.vertices - UTM, m.triangles)
    assert volume == pytest.approx(4.0 / 3.0 * math.pi * expected**3, rel=0.01)


def test_cube_expansion_volume_is_steiner() -> None:
    """The offset of a cube of side a (Steiner): a³ + 6·a²·Δ + 3·π·a·Δ² + 4/3·π·Δ³."""
    a, d = 20.0, 4.0
    v, t = cube(4, a)
    m = expand_surface(v, t, d, h=1.0, levels=LEVELS)
    assert_closed(m)
    expected = a**3 + 6 * a**2 * d + 3 * math.pi * a * d**2 + 4.0 / 3.0 * math.pi * d**3
    assert solids.signed_volume(m.vertices - UTM, m.triangles) == pytest.approx(expected, rel=0.01)
    # a cube shrunk by Δ < 0 is a cube of side a + 2Δ; marching cubes cuts its sharp edges (about 0.5·h² along each)
    s = expand_surface(v, t, -3.0, h=1.0, levels=LEVELS)
    assert_closed(s)
    assert solids.signed_volume(s.vertices - UTM, s.triangles) == pytest.approx((a - 6.0) ** 3, rel=0.04)


def test_sphere_shrunk_to_nothing_is_an_empty_surface() -> None:
    v, t = icosphere(3, 5.0)
    m = expand_surface(v, t, -6.0, h=1.0, levels=LEVELS)
    assert len(m.triangles) == 0 and len(m.vertices) == 0


# --- the field ---------------------------------------------------------------------------------------------


def test_field_is_distance_minus_delta_within_reach_and_h_beyond() -> None:
    v, t = icosphere(3, R)
    delta = 5.0
    f = expand_field(v, t, delta, H=BIG)
    rng = np.random.default_rng(3)
    d = rng.uniform(-30.0, 30.0, 400)  # points at about R + d from the centre, in every direction
    u = rng.normal(size=(400, 3))
    p = UTM + u / np.linalg.norm(u, axis=1)[:, None] * (R + d)[:, None]
    exact = sdf.signed_distance(v, t, p)
    reach = abs(delta) + 2 * BIG
    got = f(p)
    near = np.abs(exact) <= reach
    assert near.any() and (~near).any()
    assert (got[~near] == np.sign(exact[~near]) * BIG).all()  # beyond the reach: ±H by the sign
    np.testing.assert_allclose(got[near], exact[near] - delta, atol=1e-9)
    far = np.array([UTM, UTM + [0.0, 0.0, 400.0], UTM + [-300.0, 20.0, 0.0]])
    assert f(far).tolist() == [-BIG, BIG, BIG]  # the centre is 50 m inside: farther than the reach
    assert f(np.array([[np.nan, 0.0, 0.0], [1.0, np.inf, 0.0]])).tolist() == [BIG, BIG]  # not finite → +H


def test_field_depends_on_the_point_alone_and_not_on_threads() -> None:
    v, t = icosphere(3, R)
    p = UTM + np.random.default_rng(5).uniform(-80.0, 80.0, (3000, 3))
    a = expand_field(v, t, 5.0, H=BIG, threads=1)(p)
    b = expand_field(v, t, 5.0, H=BIG, threads=4)(p)
    assert a.tobytes() == b.tobytes()
    assert a[::-1].tobytes() == expand_field(v, t, 5.0, H=BIG)(p[::-1]).tobytes()
    m1 = isosurface(expand_field(v, t, 5.0, H=BIG), *expand_box(v, 5.0, BIG), H_STEP, levels=LEVELS, near=NEAR)
    m4 = expand_surface(v, t, 5.0, h=H_STEP, levels=LEVELS, threads=4)
    assert m1.vertices.tobytes() == m4.vertices.tobytes() and m1.triangles.tobytes() == m4.triangles.tobytes()


def test_open_or_empty_wireframe_is_refused_at_construction() -> None:
    v, t = icosphere(2, R)
    with pytest.raises(sdf.SdfError) as e:
        expand_field(v, t[1:], 5.0, H=BIG)
    assert e.value.code == "wireframe_open" and "не замкнут" in e.value.message and e.value.hint
    with pytest.raises(sdf.SdfError) as e:
        expand_field(v, t[:0], 5.0, H=BIG)
    assert e.value.code == "wireframe_empty"


def test_bad_parameters_are_refused_in_russian() -> None:
    v, t = icosphere(2, R)
    for delta, big in ((math.nan, BIG), (5.0, 0.0), (5.0, math.inf)):
        with pytest.raises(ExpandError) as e:
            expand_field(v, t, delta, H=big)
        assert e.value.code == "bad_expand" and e.value.message and e.value.hint
        assert set(e.value.as_dict()) == {"code", "message", "hint"}


def test_module_does_not_load_numba_nor_the_libraries_on_import() -> None:
    code = (
        "import sys, bf_core.model.expand, bf_core.geo.iso_post;"
        "bad = [m for m in ('numba', 'pyfqmr', 'manifold3d', 'skimage') if m in sys.modules];"
        "raise SystemExit(3 if bad else 0)"
    )
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert run.returncode == 0, run.stderr[-300:]
    assert expand.NEAR == 1.5
