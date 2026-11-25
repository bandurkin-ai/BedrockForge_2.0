"""The anisotropy of the implicit model (подшаг 19.9; ТЗ 8.9.2, Т8.9-4; ТЗ 7.35 ТД15-7; ADR 0026, решение 11).

Т8.9-4 an elongated body (a field of ‖A·x‖) is reproduced by the RBF with its A better than by the isotropic one, and
the field with A equals the isotropic field of the points turned by A (1e−12); ТД15-7 the RBF of a trend equals the RBF
of the same angles and ratios typed by hand (1e−12); the structural trend of one plane is the global one (1e−12), the
same bytes with 1, 2, 4 and 8 threads, a permutation of the points; the frame of a patch (scale R_j·‖A_j‖₂, a data
point at 7e6 m); the checks and the ellipsoid of a matrix; numba is not loaded by the import."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat import rotation
from bf_core.geostat import trend as tr
from bf_core.geostat import trend_struct as st
from bf_core.model import anisotropy as an
from bf_core.model.evaluate import PatchField
from bf_core.model.octree import build_patches, transform_points
from bf_core.model.solve import solve_patches
from bf_core.model.spec_parts import Anisotropy

F8 = np.ndarray[Any, np.dtype[np.float64]]
TOL = 1e-12
ANGLES = {"ang1": 30.0, "ang2": -20.0, "ang3": 40.0}
RATIOS = [1.0, 1.0 / 3.0, 1.0 / 6.0]


def body_a() -> F8:
    return tr.matrix({**ANGLES, "ratios": RATIOS})


def body_field(x: F8) -> F8:
    """The elongated ellipsoid body: a1 = 60 m along the major axis; f = ‖A·x‖ / 60 − 1 (zero on its surface)."""
    return np.linalg.norm(transform_points(x, body_a()), axis=1) / 60.0 - 1.0


def cloud(n: int, seed: int, half: float = 90.0) -> F8:
    return np.random.default_rng(seed).uniform(-half, half, size=(n, 3))


def rbf(pts: F8, f: F8, *, matrix: F8 | None = None, mats: Any = None, n_patch: int = 150, n_min: int = 80,
        threads: int = 2, kind: str = "linear") -> tuple[Any, PatchField]:  # fmt: skip
    ps = build_patches(pts, n_patch=n_patch, n_min=n_min, matrix=matrix)
    sols = solve_patches(pts, f, ps, kind=kind, matrices=mats, threads=threads)
    return ps, PatchField(ps, sols, matrices=mats, threads=threads)


def plane_source(dip: float, dipdir: float, n: int = 12, seed: int = 3) -> st.Measurements:
    xyz = cloud(n, seed, 60.0)
    return st.Measurements(xyz, np.tile(st.turn_up(st.plane_normals([dip], [dipdir])), (n, 1)))


def meta(radius: float = 200.0, ratios: Any = (1.0, 0.5, 0.2)) -> dict[str, Any]:
    return {"v": 1, "kind": "structural", "radius": radius, "ratios": list(ratios)}


# --- Т8.9-4 -------------------------------------------------------------------------------------------------------


def test_t8_9_4_the_field_with_a_is_the_isotropic_field_of_the_turned_points() -> None:
    """The global A turns the points once: the field equals (1e−12) the isotropic field of the points A·x_i at A·x."""
    a = an.matrix_of(Anisotropy(*(ANGLES.values()), tuple(RATIOS)))  # type: ignore[arg-type]
    assert np.array_equal(a, body_a())
    pts = cloud(500, 1)
    f = body_field(pts)
    q = cloud(3000, 2, 80.0)
    _ps, aniso = rbf(pts, f, matrix=a)
    _pi, iso = rbf(transform_points(pts, a), f)
    d = np.abs(aniso(q) - iso(transform_points(q, a)))
    assert np.nanmax(d) <= TOL * np.max(np.abs(f))
    assert not np.isnan(aniso(q)).any()


def test_t8_9_4_an_elongated_body_is_reproduced_better_with_its_anisotropy() -> None:
    """The same sparse data of an elongated body: the error of the RBF with the right A is a fraction of the isotropic
    one on held-out points, and the surface f = 0 lies where the body does."""
    pts = cloud(400, 4)
    f = body_field(pts)
    q = cloud(4000, 5, 70.0)
    truth = body_field(q)
    _ps, with_a = rbf(pts, f, matrix=body_a())
    _pi, plain = rbf(pts, f)
    err_a = float(np.sqrt(np.mean((with_a(q) - truth) ** 2)))
    err_i = float(np.sqrt(np.mean((plain(q) - truth) ** 2)))
    assert err_a <= 0.35 * err_i, (err_a, err_i)
    near = np.abs(truth) < 0.05  # the skin of the body: the sign of the field is the sign of the body
    assert np.all(np.sign(with_a(q)[near]) == np.sign(truth[near]))
    assert np.max(np.abs(with_a(pts) - f)) <= 1e-8  # the data stay exact


def test_t8_9_4_hard_ratios_are_still_solved() -> None:
    """A sheet 1 : 0,05 : 0,01 with the cubic kernel: no `rbf_singular`, the data are reproduced."""
    a = tr.matrix({"ang1": 10.0, "ang2": 5.0, "ang3": -30.0, "ratios": [1.0, 0.05, 0.01]})
    pts = cloud(400, 6)
    f = np.linalg.norm(transform_points(pts, a), axis=1) / 300.0
    _ps, field = rbf(pts, f, matrix=a, kind="cubic", n_patch=200, n_min=100)
    assert np.max(np.abs(field(pts) - f)) <= 1e-6 * np.max(np.abs(f))


# --- ТД15-7 -------------------------------------------------------------------------------------------------------


def test_td15_7_rbf_with_a_trend_equals_rbf_with_the_same_angles_by_hand() -> None:
    """The A of a stored global trend (its `meta`) and of the same angles and ratios typed in the model are one matrix,
    and the two models are one field (1e−12)."""
    shape = tr.shape(25.0, -15.0, 35.0, [1.0, 0.4, 0.15])
    trend_meta = {"v": 1, "kind": "global", **shape.as_dict(), "size": 100.0, "color": "#aabbcc", "origin": {}}
    typed = Anisotropy(shape.ang1, shape.ang2, shape.ang3, shape.ratios)
    a_trend = an.matrix_of(None, trend_meta)
    a_hand = an.matrix_of(typed)
    assert np.array_equal(a_trend, a_hand) and np.array_equal(a_hand, tr.matrix(shape))
    assert np.array_equal(an.global_matrix(typed), an.global_matrix(None, trend_meta))
    pts = cloud(450, 7)
    f = body_field(pts)
    q = cloud(2500, 8, 75.0)
    _p1, by_trend = rbf(pts, f, matrix=an.global_matrix(None, trend_meta))
    _p2, by_hand = rbf(pts, f, matrix=an.global_matrix(typed))
    assert np.max(np.abs(by_trend(q) - by_hand(q))) <= TOL * np.max(np.abs(f))
    assert np.array_equal(by_trend(q), by_hand(q))


def test_the_ways_of_spelling_one_matrix_and_the_none_case() -> None:
    spec = type("Spec", (), {"anisotropy": Anisotropy(30.0, 0.0, 0.0, (1.0, 0.5, 0.5)), "trend_id": None})()
    bare = {"ang1": 30.0, "ang2": 0.0, "ang3": 0.0, "ratios": [1.0, 0.5, 0.5]}
    assert np.array_equal(an.matrix_of(spec), an.matrix_of(bare))
    assert an.kind_of(spec) == an.GLOBAL and an.kind_of(None) == an.NONE
    nothing = type("Spec", (), {"anisotropy": None, "trend_id": None})()
    assert an.global_matrix(nothing) is None and np.array_equal(an.matrix_of(nothing), np.eye(3))
    assert an.global_matrix({"ang1": 90.0, "ang2": 0.0, "ang3": 0.0, "ratios": [1, 1, 1]}) is None  # A = I exactly
    assert an.kind_of(nothing, meta()) == an.STRUCTURAL
    assert an.global_matrix(nothing, meta()) is None and np.array_equal(an.matrix_of(nothing, meta()), np.eye(3))


# --- the structural trend -----------------------------------------------------------------------------------------


def plane_a(dip: float, dipdir: float, ratios: Any = (1.0, 0.5, 0.2)) -> F8:
    """The global matrix of the same plane: the angles of `angles_of_plane(α, Aп, pitch 0)` with the ratios."""
    ang = rotation.angles_of_plane(dip, dipdir, 0.0)
    return tr.matrix({"ang1": ang[0], "ang2": ang[1], "ang3": ang[2], "ratios": list(ratios)})


def test_a_structural_trend_of_one_plane_is_the_global_one() -> None:
    """The measurements on one plane: A_j equals the global A of its angles at every centre (1e−12), and with one patch
    (the same partition) the two fields are one function (1e−12)."""
    src = plane_source(35.0, 120.0)
    pts = cloud(90, 9, 50.0)
    f = body_field(pts)
    ps = build_patches(pts, n_patch=10_000, n_min=20)
    assert ps.n_patches == 1
    rep: dict[str, Any] = {}
    mats = an.local(meta(), ps.centers, source=src, report=rep)
    a = plane_a(35.0, 120.0)
    assert mats.shape == (1, 3, 3) and np.max(np.abs(mats[0] - a)) <= TOL and rep["nearest"] == 0
    _pg, glob = rbf(pts, f, matrix=a, n_patch=10_000, n_min=20)
    sols = solve_patches(pts, f, ps, kind="linear", matrices=list(mats))
    loc = PatchField(ps, sols, matrices=list(mats))
    q = cloud(2000, 10, 45.0)
    assert np.max(np.abs(loc(q) - glob(q))) <= TOL * np.max(np.abs(f))  # cond₁ of the systems ~1e4: measured 4e-15
    many = build_patches(pts, n_patch=30, n_min=20)
    ml = an.local(meta(), many.centers, source=src)
    assert ml.shape == (many.n_patches, 3, 3) and np.max(np.abs(ml - a)) <= TOL  # every patch of a plane: the same A


def test_a_curved_structure_has_its_own_matrix_at_every_patch_and_the_data_are_kept() -> None:
    """A dome: the dip changes from patch to patch; the field interpolates the data and has no gaps in the extent."""
    xyz = cloud(60, 11, 80.0)
    r = np.hypot(xyz[:, 0], xyz[:, 1]) + 1e-9
    n = np.column_stack([xyz[:, 0] / r * np.sin(np.radians(25.0)), xyz[:, 1] / r * np.sin(np.radians(25.0)),
                         np.full(len(xyz), np.cos(np.radians(25.0)))])  # fmt: skip
    src = st.Measurements(xyz, st.turn_up(n))
    pts = cloud(500, 12)
    f = np.sin(pts[:, 0] / 40.0) + 0.02 * pts[:, 2]
    ps = build_patches(pts, n_patch=120, n_min=70)
    mats = an.local(meta(60.0), ps.centers, source=src)
    assert ps.n_patches > 4 and np.ptp(mats[:, 0, :], axis=0).max() > 0.2  # the axes really differ
    sols = solve_patches(pts, f, ps, kind="linear", matrices=list(mats), threads=2)
    field = PatchField(ps, sols, matrices=list(mats))
    assert np.max(np.abs(field(pts) - f)) <= 1e-8 * np.max(np.abs(f))
    assert not np.isnan(field(cloud(2000, 13, 80.0))).any()


@pytest.mark.parametrize("kind", ["linear", "cubic"])
def test_same_bytes_with_1_2_4_8_threads(kind: str) -> None:
    src = plane_source(40.0, 200.0)
    pts = cloud(700, 14)
    f = body_field(pts)
    q = cloud(5000, 15, 85.0)
    ps = build_patches(pts, n_patch=130, n_min=70)
    base: tuple[bytes, bytes] | None = None
    for threads in (1, 2, 4, 8):
        mats = an.local(meta(), ps.centers, source=src, threads=threads)
        sols = solve_patches(pts, f, ps, kind=kind, matrices=list(mats), threads=threads)
        got = (b"".join(s.lam.tobytes() + s.poly.tobytes() for s in sols),
               PatchField(ps, sols, matrices=list(mats), threads=threads)(q).tobytes())  # fmt: skip
        base = base or got
        assert got == base


def test_a_permutation_of_the_points_changes_no_byte_of_the_field() -> None:
    """The points come in the canonical order (the caller sorts them): a permuted input sorted again is one model."""
    src = plane_source(30.0, 60.0)
    pts = cloud(600, 16)
    f = body_field(pts)
    q = cloud(3000, 17, 80.0)

    def canon(p: F8, v: F8) -> tuple[F8, F8]:
        o = np.lexsort((p[:, 2], p[:, 1], p[:, 0]))
        return p[o], v[o]

    out: list[bytes] = []
    for seed in (0, 1, 2):
        perm = np.random.default_rng(seed).permutation(len(pts))
        p, v = canon(pts[perm], f[perm])
        ps = build_patches(p, n_patch=120, n_min=70)
        mats = an.local(meta(), ps.centers, source=src)
        sols = solve_patches(p, v, ps, kind="linear", matrices=list(mats), threads=3)
        out.append(PatchField(ps, sols, matrices=list(mats), threads=3)(q).tobytes())
    assert out[0] == out[1] == out[2]
    flipped = st.Measurements(src.xyz[::-1].copy(), src.normal[::-1].copy())
    ps = build_patches(*canon(pts, f)[:1], n_patch=120, n_min=70)
    assert (
        np.max(np.abs(an.local(meta(), ps.centers, source=flipped) - an.local(meta(), ps.centers, source=src))) <= TOL
    )


def test_local_frame_at_large_coordinates() -> None:
    """The scale of a patch is R_j·‖A_j‖₂ (the same in the system and in the field); the field of coordinates at 7e6 m
    is that of the same model near the origin (1e−10 of the range; measured 2e−11) and interpolates the data."""
    a = plane_a(20.0, 300.0, (1.0, 0.1, 0.05))
    pts = cloud(150, 18, 60.0)
    f = body_field(pts)
    shift = np.array([7.0e6, 6.1e6, 2.0e3])
    ps0 = build_patches(pts, n_patch=10_000, n_min=20)
    ps1 = build_patches(pts + shift, n_patch=10_000, n_min=20)
    mats = [a]
    s0 = solve_patches(pts, f, ps0, kind="cubic", matrices=mats)
    s1 = solve_patches(pts + shift, f, ps1, kind="cubic", matrices=mats)
    assert s0[0].scale == float(ps0.radii[0]) * an.stretch_of(a) and s0[0].scale > ps0.radii[0] * 5.0
    q = cloud(1500, 19, 50.0)
    f0 = PatchField(ps0, s0, matrices=mats)(q)
    f1 = PatchField(ps1, s1, matrices=mats)(q + shift)
    assert np.max(np.abs(f0 - f1)) <= 1e-10 * np.max(np.abs(f0))
    assert np.max(np.abs(PatchField(ps1, s1, matrices=mats)(pts + shift) - f)) <= 1e-8 * np.max(np.abs(f))
    # a rotation (‖A‖₂ = 1) keeps the patch scale R_j: the system is that of a patch without a matrix up to the rotation
    rot = rotation.rotation_matrix(30.0, 10.0, 20.0)
    assert abs(an.stretch_of(rot) - 1.0) <= 1e-15


def test_the_nearest_measurement_is_journaled() -> None:
    """A centre with no measurement within R_тр takes the nearest one, and the journal counts such centres."""
    src = plane_source(35.0, 120.0, 6)
    far = np.array([[900.0, 900.0, 900.0], [1000.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    rep: dict[str, Any] = {}
    mats = an.local(meta(50.0), far, source=src, report=rep)
    assert rep["nearest"] >= 2 and any("ближайший замер" in line for line in rep["journal"])
    assert np.max(np.abs(mats - plane_a(35.0, 120.0))) <= TOL  # one plane: the nearest measurement is the same plane


def test_patch_hashes_see_the_matrix() -> None:
    ps = build_patches(cloud(300, 20), n_patch=100, n_min=60)
    mats = [None] * ps.n_patches
    assert an.patch_hashes(ps.hashes, mats) == ps.hashes
    a1 = [np.eye(3) * 2.0] * ps.n_patches
    a2 = [np.eye(3) * 2.0] * (ps.n_patches - 1) + [np.eye(3) * 3.0]
    h1, h2 = an.patch_hashes(ps.hashes, a1), an.patch_hashes(ps.hashes, a2)
    assert h1[:-1] == h2[:-1] and h1[-1] != h2[-1] and h1[0] != ps.hashes[0]
    assert h1 == an.patch_hashes(ps.hashes, np.array(a1))
    with pytest.raises(ValueError):
        an.patch_hashes(ps.hashes, a1[:-1])


# --- the checks and the ellipsoid ---------------------------------------------------------------------------------


def codes(fn: Any, *args: Any, **kw: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn(*args, **kw)
    err = e.value
    assert err.status == 422 and err.message and err.hint  # {code, message, hint}, Russian
    assert any("а" <= ch <= "я" for ch in err.message.lower())
    return err


def test_checks_refuse_a_bad_matrix_with_a_russian_message() -> None:
    assert codes(an.check_matrix, np.zeros((3, 3))).code == "bad_anisotropy"
    assert codes(an.check_matrix, np.diag([1.0, 1.0, 1e-7])).code == "bad_anisotropy"  # cond above 1e6
    assert codes(an.check_matrix, np.full((3, 3), np.nan)).extra["param"] == "anisotropy"
    assert codes(an.check_matrix, np.eye(2)).code == "bad_anisotropy"
    assert an.check_matrix(np.diag([1.0, 1e3, 1e-3])).shape == (3, 3)
    bad = {"ang1": 0.0, "ang2": 0.0, "ang3": 0.0, "ratios": [1.0, 0.0, 1.0]}
    e = codes(an.matrix_of, bad)
    assert e.code == "bad_param" and e.extra["param"] == "anisotropy.ratios.1"
    assert codes(an.matrix_of, {**bad, "ratios": [1.0, 0.5, -2.0]}).extra["param"] == "anisotropy.ratios.2"
    assert codes(an.matrix_of, {**bad, "ratios": [1.0, 1e-4, 1e4]}).code == "bad_anisotropy"  # 1e8 apart
    assert codes(an.matrix_of, {**bad, "ratios": [1.0, 1.0, 1.0], "ang2": 120.0}).extra["param"] == "anisotropy.ang2"


def test_the_refusals_of_the_kinds_of_trends() -> None:
    typed = Anisotropy(0.0, 0.0, 0.0, (1.0, 0.5, 0.5))
    spec = type("Spec", (), {"anisotropy": typed, "trend_id": 3})()
    assert (
        codes(an.matrix_of, spec, {"kind": "global", **tr.shape(0, 0, 0, None).as_dict()}).extra["param"]
        == "anisotropy"
    )
    assert codes(an.kind_of, type("Spec", (), {"anisotropy": None, "trend_id": 3})()).extra["param"] == "trend_id"
    assert codes(an.kind_of, None, {"kind": "mystery"}).extra["param"] == "trend_id"
    assert codes(an.matrix_of, meta()).extra["param"] == "trend_id"  # a structural trend as a typed anisotropy
    assert (
        codes(an.local, {"kind": "global"}, np.zeros((1, 3)), source=plane_source(10.0, 10.0)).code == "bad_anisotropy"
    )
    assert (
        codes(an.local, meta(ratios=(1.0, 0.0, 1.0)), np.zeros((1, 3)), source=plane_source(1.0, 1.0)).code
        == "bad_param"
    )
    assert codes(an.local, meta(), np.full((1, 3), np.nan), source=plane_source(10.0, 10.0)).extra["param"] == "centres"
    empty = st.Measurements(np.zeros((0, 3)), np.zeros((0, 3)))
    assert codes(an.local, meta(), np.zeros((2, 3)), source=empty).code == "no_measurements"


def test_ellipsoid_of_a_matrix() -> None:
    shape = tr.shape(130.0, -25.0, 70.0, [1.0, 0.3, 0.1])
    e = an.ellipsoid_of(tr.matrix(shape), 100.0)
    back = (e.ang1, e.ang2, e.ang3, *e.ratios)
    assert np.allclose(back, (shape.ang1, shape.ang2, shape.ang3, *shape.ratios), rtol=0.0, atol=1e-9)
    assert np.allclose(e.semi_axes, (100.0, 30.0, 10.0), rtol=1e-12) and abs(e.stretch - 10.0) <= 1e-12
    assert set(e.as_dict()) == {"semi_axes", "ang1", "ang2", "ang3", "ratios", "stretch"}
    # any other matrix: the semi-axes are 1/σ; the ball of ‖A·x‖ = 1 touches them
    m = np.array([[1.0, 0.3, 0.0], [0.0, 2.0, 0.4], [0.1, 0.0, 0.5]])
    g = an.ellipsoid_of(m)
    sv = np.sort(np.linalg.svd(m, compute_uv=False))
    assert np.allclose(g.semi_axes, 1.0 / sv, rtol=1e-12)
    assert g.semi_axes[0] >= g.semi_axes[1] >= g.semi_axes[2] and g.ratios[0] == 1.0
    rot = rotation.rotation_matrix(g.ang1, g.ang2, g.ang3)
    axis1 = np.linalg.svd(m)[2][-1]
    assert abs(abs(float(rot[0] @ axis1)) - 1.0) <= 1e-9  # the first axis is the longest one
    assert codes(an.ellipsoid_of, np.eye(3), -1.0).extra["param"] == "size"


def test_the_import_does_not_load_numba() -> None:
    code = ("import sys; import bf_core.model.anisotropy as a; import bf_core.model.solve; "
            "assert 'numba' not in sys.modules, 'numba loaded'")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
