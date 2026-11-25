"""The references of the estimation (ТЗ 8.8, Т8.8-4…7; ADR 0024 «Точность»; Д-38; Э18, 18.8): the kernel (`compile_row`,
`estimate`) against GeostatsPy 0.0.79 and against an independent NumPy implementation.

* Т8.8-4 — `kb2d` 0.0.79, point OK and SK: 300 samples (seed 42), 50×50 nodes of 20 m, two anisotropic structures
  (spherical azimuth 30, exponential azimuth 60), ndmax 16, radius 400 m: EST and KV to 1e−6 relative.
* Т8.8-5 — the same grid by blocks of 4×4 points: the reference is the copy of `kb2d` with two fixes
  (`tests/reference/kb2d_fixed.py`; the block mode of 0.0.79 is unusable): 1e−6 relative.
* Т8.8-6 — `kb3d` 0.0.79, point OK and SK, ≤ 2 structures, ang3 = 0 (nst = 2 with the one-line fix of the generator):
  the search of BF2 is r1 = r2 above the extent, r3/r1 = radius_vert/radius_hori, n_max = ndmax: 1e−6 relative.
* Т8.8-7 — a 3D block, ang3 ≠ 0, three structures, octants, the limit of a hole, three passes: an independent
  implementation (`tests/reference/krige3d_numpy.py`, brute-force search, `numpy.linalg.solve`): 1e−9 relative.
* ТД38-1, ТД38-2 (Д-38) — a model in normal scores goes into OK through its `back` only (bit for bit the same as the
  model in Z saved apart); without `back` — 422 `variogram_scale`; a simulation takes it.

GeostatsPy numbers carry the conventions of the generator (`convention` of the JSON): x east, y north, z up; azimuth
clockwise from north; a positive dip RAISES the major axis (the same as `ang2` of BF2); GeostatsPy 0.0.79 has π =
3.14159265 in `setup_rotmat` (the 2D answers differ from an exact-π calculation by ≈ 6e−10 absolute, far below 1e−6)."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate.cov import cov_model
from bf_core.geostat.estimate.samples import RowSamples, prepare
from bf_core.geostat.estimate.spec import parse_estimation
from bf_core.geostat.estimate.spec_parts import parse_vario
from bf_core.geostat.variogram.model import parse_model
from tests.estutil import BACK, W, get, make, mk_refused, refused, vmodel, world
from tests.reference.krige3d_numpy import krige_blocks
from tests.test_estimate_kernel import spec_of

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy"
GSLIB_TYPE = {1: "spherical", 2: "exponential", 3: "gaussian"}
REL_GEOSTATSPY = 1e-6  # Т8.8-4…6
REL_NUMPY = 1e-9  # Т8.8-7


def load(name: str) -> dict[str, Any]:
    return json.loads((REF / name).read_text(encoding="utf-8"))


def max_rel(got: np.ndarray, ref: np.ndarray) -> float:
    """max |got − ref| / |ref|; the reference values are far from 0 (checked), a NaN in `got` fails."""
    got, ref = np.asarray(got, dtype=np.float64), np.asarray(ref, dtype=np.float64)
    assert got.shape == ref.shape and np.isfinite(got).all()
    assert np.abs(ref).min() > 1e-3
    return float(np.max(np.abs(got - ref) / np.abs(ref)))


def raw_samples(xyz: Any, z: Any, hole: Any = None) -> RowSamples:
    """The samples through `prepare` (one hole per sample unless `hole` is given), the origin at the centre."""
    xyz = np.asarray(xyz, dtype=np.float64)
    n = xyz.shape[0]
    hole = np.arange(n, dtype=np.int32) if hole is None else np.asarray(hole, dtype=np.int32)
    names = [f"h{i}" for i in range(int(hole.max()) + 1)]
    return prepare(
        xyz,
        np.asarray(z, dtype=np.float64),
        None,
        None,
        hole,
        names,
        np.zeros(n, np.int16),
        np.arange(n, dtype=np.int64),
        np.zeros((n, 2)),
    )


# --- Т8.8-4, Т8.8-5: kb2d 0.0.79 -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kb2d() -> dict[str, Any]:
    return load("kb2d_079.json")


def model_2d(v: dict[str, Any]) -> dict[str, Any]:
    """The variogram dict of `kb2d` → a model of BF2: azimuth = ang1, hmaj = a1, hmin = a2 (z = 0, a3 is idle)."""
    st = [
        {
            "type": GSLIB_TYPE[v[f"it{k}"]],
            "c": v[f"cc{k}"],
            "a1": v[f"hmaj{k}"],
            "a2": v[f"hmin{k}"],
            "a3": v[f"hmaj{k}"],
            "ang1": v[f"azi{k}"],
            "ang2": 0,
            "ang3": 0,
        }
        for k in range(1, v["nst"] + 1)
    ]
    return {"c0": v["nug"], "common_axes": False, "structures": st}


def run_kb2d(ref: dict[str, Any], name: str) -> tuple[np.ndarray, np.ndarray]:
    d, c = ref["data"], ref["cases"][name]
    p, g = c["params"], ref["grid"]
    xyz = np.column_stack([d["X"], d["Y"], np.zeros(len(d["X"]))])
    s = raw_samples(xyz, d["V"])
    kind = "ok" if p["ktype"] == 1 else "sk"
    spec = spec_of(
        kind,
        variogram=model_2d(ref["variogram"]),
        disc=(p["nxdis"], p["nydis"], 1),
        r1=p["radius"],
        mean=p["skmean"] if kind == "sk" else None,
        passes=[{"factor": 1, "n_min": p["ndmin"], "n_max": p["ndmax"]}],
    )
    rk = kn.compile_row(spec, s, block_size=(g["xsiz"], g["ysiz"], 1.0))
    grid = np.array(
        [[g["xmn"] + ix * g["xsiz"], g["ymn"] + iy * g["ysiz"], 0.0] for iy in range(g["ny"]) for ix in range(g["nx"])]
    )
    res = kn.estimate(rk, grid)
    assert (res.status == kn.ST_OK).all()
    return res.values["EST"].reshape(g["ny"], g["nx"]), res.values["KV"].reshape(g["ny"], g["nx"])


@pytest.mark.parametrize("case", ["ok_point", "sk_point"])
def test_t8_8_4_kb2d_079_point(kb2d, case):
    """The estimate and the kriging variance of 2500 nodes of `kb2d` 0.0.79 (OK, SK), 1e−6 relative."""
    assert kb2d["cases"][case]["source"] == "geostatspy 0.0.79 kb2d"
    est, var = run_kb2d(kb2d, case)
    ref = kb2d["cases"][case]
    assert max_rel(est, np.array(ref["est"])) <= REL_GEOSTATSPY
    assert max_rel(var, np.array(ref["var"])) <= REL_GEOSTATSPY


@pytest.mark.parametrize("case", ["ok_block4", "sk_block4"])
def test_t8_8_5_kb2d_fixed_block(kb2d, case):
    """Blocks of 4×4 points against the copy of `kb2d` with the two fixes (the nugget is dropped between the
    identical points of C̄_VV), 1e−6 relative."""
    assert "kb2d_fixed.py" in kb2d["cases"][case]["source"]
    est, var = run_kb2d(kb2d, case)
    ref = kb2d["cases"][case]
    assert max_rel(est, np.array(ref["est"])) <= REL_GEOSTATSPY
    assert max_rel(var, np.array(ref["var"])) <= REL_GEOSTATSPY


def test_t8_8_5_the_block_is_not_the_point(kb2d):
    """A guard against a vacuous test: the block answers of the reference differ from the point ones, and the kernel
    follows the block ones (a variance lower by the smoothing: KV_block < KV_point at every node)."""
    ok_p, ok_b = kb2d["cases"]["ok_point"], kb2d["cases"]["ok_block4"]
    assert np.abs(np.array(ok_p["est"]) - np.array(ok_b["est"])).max() > 1e-3
    assert (np.array(ok_b["var"]) < np.array(ok_p["var"])).all()


# --- Т8.8-6: kb3d 0.0.79 ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kb3d() -> dict[str, Any]:
    return load("kb3d_079.json")


def model_3d(v: dict[str, Any]) -> dict[str, Any]:
    """`kb3d` variogram → a model of BF2: ang1 = azimuth, ang2 = dip (a positive dip raises it in both), a1 = hmaj,
    a2 = hmin, a3 = hvert, ang3 = 0."""
    st = [
        {
            "type": GSLIB_TYPE[v["it"][k]],
            "c": v["cc"][k],
            "a1": v["hmaj"][k],
            "a2": v["hmin"][k],
            "a3": v["hvert"][k],
            "ang1": v["azi"][k],
            "ang2": v["dip"][k],
            "ang3": 0,
        }
        for k in range(v["nst"])
    ]
    return {"c0": v["nug"], "common_axes": False, "structures": st}


@pytest.fixture(scope="module")
def kb3d_samples(kb3d) -> RowSamples:
    d = kb3d["data"]
    return raw_samples(np.column_stack([d["X"], d["Y"], d["Z"]]), d["V"])


@pytest.mark.parametrize(
    "case",
    ["ok_point_nst1", "sk_point_nst1", "ok_point_nst2", "sk_point_nst2", "ok_point_nst2_dip", "sk_point_nst2_dip"],
)
def test_t8_8_6_kb3d_079_point(kb3d, kb3d_samples, case):
    """`kb3d` 0.0.79 on 16×16×8 nodes (nst 1 and 2, the dip of 15° and 10°). The search of BF2: r1 = r2 ten times the
    extent of the box with r3 / r1 = radius_vert / radius_hori and n_max = ndmax is `kb3d`'s «ndmax nearest in the space
    with z / (rv / rh)»: 1e−6 relative."""
    c, g, sr = kb3d["cases"][case], kb3d["grid"], kb3d["search"]
    p = c["params"]
    kind = "ok" if p["ktype"] == 1 else "sk"
    scale = 10.0
    spec = spec_of(
        kind,
        variogram=model_3d(c["variogram"]),
        disc=(1, 1, 1),
        mean=p["skmean"] if kind == "sk" else None,
        radii=(scale * sr["radius_hori"], scale * sr["radius_hori"], scale * sr["radius_vert"]),
        angles=(0.0, 0.0, 0.0),
        passes=[{"factor": 1, "n_min": p["ndmin"], "n_max": p["ndmax"]}],
    )
    rk = kn.compile_row(spec, kb3d_samples)
    grid = np.array(
        [
            [g["xmn"] + ix * g["xsiz"], g["ymn"] + iy * g["ysiz"], g["zmn"] + iz * g["zsiz"]]
            for iz in range(g["nz"])
            for iy in range(g["ny"])
            for ix in range(g["nx"])
        ]
    )
    res = kn.estimate(rk, grid)
    assert (res.status == kn.ST_OK).all() and (res.values["NS"] == p["ndmax"]).all()
    shape = (g["nz"], g["ny"], g["nx"])
    assert max_rel(res.values["EST"].reshape(shape), np.array(c["est"])) <= REL_GEOSTATSPY
    assert max_rel(res.values["KV"].reshape(shape), np.array(c["var"])) <= REL_GEOSTATSPY


def test_t8_8_6_the_reference_has_a_dip_and_two_structures(kb3d):
    """A guard: the dip really changes the reference (a wrong sign of `ang2` could not pass the case with a dip)."""
    ok, dip = kb3d["cases"]["ok_point_nst2"], kb3d["cases"]["ok_point_nst2_dip"]
    assert dip["variogram"]["dip"] == [15, 10] and ok["variogram"]["nst"] == 2
    assert np.abs(np.array(ok["est"]) - np.array(dip["est"])).max() > 1e-3


# --- Т8.8-7: an independent NumPy implementation ---------------------------------------------------------------------

# three structures with their own axes, every angle of the three non-zero (ang3 ≠ 0)
MODEL3 = {
    "c0": 0.12,
    "common_axes": False,
    "structures": [
        {"type": "spherical", "c": 0.35, "a1": 90.0, "a2": 55.0, "a3": 22.0, "ang1": 30.0, "ang2": -8.0, "ang3": 15.0},
        {
            "type": "exponential",
            "c": 0.30,
            "a1": 160.0,
            "a2": 95.0,
            "a3": 38.0,
            "ang1": 48.0,
            "ang2": 6.0,
            "ang3": -12.0,
        },
        {"type": "gaussian", "c": 0.23, "a1": 260.0, "a2": 200.0, "a3": 70.0, "ang1": 22.0, "ang2": 0.0, "ang3": 28.0},
    ],
}
ANGLES = (35.0, -10.0, 20.0)  # the ellipsoid of the search
RADII = (110.0, 70.0, 30.0)
PASSES = [
    {"factor": 1, "n_min": 8, "n_max": 20, "n_hole": 3, "n_oct": 4, "k_oct": 3, "n_holes_min": 3},
    {"factor": 2, "n_min": 5, "n_max": 24, "n_hole": 5, "n_oct": 0, "k_oct": 0, "n_holes_min": 2},
    {"factor": 3, "n_min": 4, "n_max": 24, "n_hole": 0, "n_oct": 0, "k_oct": 0, "n_holes_min": 0},
]
SIZE, DISC, PHI = (20.0, 20.0, 10.0), (3, 3, 2), 17.0
E0, N0, Z0 = 500_000.0, 6_000_000.0, 300.0  # coordinates of a projected system: the origin of the kernel matters


def deviated_holes(seed: int, n_holes: int = 70, per: int = 18) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """70 holes of 200 m, leaning by up to 20°, 18 samples each, lognormal values."""
    rng = np.random.default_rng(seed)
    xyz, hole = [], []
    for h in range(n_holes):
        cx, cy = E0 + rng.uniform(0.0, 1000.0), N0 + rng.uniform(0.0, 1000.0)
        az, lean = rng.uniform(0.0, 2.0 * math.pi), rng.uniform(0.0, 0.35)
        for t in np.sort(rng.uniform(5.0, 200.0, per)):
            xyz.append(
                [
                    cx + math.sin(lean) * math.sin(az) * t,
                    cy + math.sin(lean) * math.cos(az) * t,
                    Z0 - math.cos(lean) * t,
                ]
            )
            hole.append(h)
    xyz_a = np.array(xyz)
    return xyz_a, rng.lognormal(0.3, 0.7, len(xyz_a)), np.array(hole)


@pytest.fixture(scope="module")
def field() -> tuple[RowSamples, np.ndarray]:
    xyz, z, hole = deviated_holes(7)
    s = raw_samples(xyz, z, hole)
    rng = np.random.default_rng(8)
    inner = np.column_stack(
        [
            rng.uniform(E0 + 50.0, E0 + 950.0, 300),
            rng.uniform(N0 + 50.0, N0 + 950.0, 300),
            rng.uniform(Z0 - 170.0, Z0 - 10.0, 300),
        ]
    )
    outer = np.column_stack(
        [
            rng.uniform(E0 - 400.0, E0 + 1400.0, 150),
            rng.uniform(N0 - 400.0, N0 + 1400.0, 150),
            rng.uniform(Z0 - 290.0, Z0 + 90.0, 150),
        ]
    )
    centres = np.vstack([inner, outer])
    return s, centres


def row_3d(kind: str, s: RowSamples, passes: list[dict[str, Any]], mean: float | None):
    outs = ["EST", "KV", "NS", "NH", "PASS", "NOCT"] + (["LAGR"] if kind == "ok" else [])
    spec = spec_of(
        kind, variogram=MODEL3, disc=DISC, radii=RADII, angles=ANGLES, passes=passes, mean=mean, outputs=outs
    )
    return kn.compile_row(spec, s, block_size=SIZE, rotation_deg=PHI)


def numpy_3d(kind: str, s: RowSamples, centres: np.ndarray, passes: list[dict[str, Any]], mean: float | None):
    return krige_blocks(
        s.xyz,
        s.z,
        s.hole,
        centres,
        model=MODEL3,
        kind=kind,
        size=SIZE,
        disc=DISC,
        phi_deg=PHI,
        angles=ANGLES,
        radii=RADII,
        passes=passes,
        mean=mean,
    )


@pytest.mark.parametrize("kind", ["ok", "sk"])
def test_t8_8_7_3d_block_three_structures_octants_holes(field, kind):
    """The kernel against the brute-force NumPy implementation: block 3×3×2 turned by 17°, three structures with their
    own ang1/ang2/ang3, three passes with the limits of a hole and of an octant: the selection (PASS, NS, NH, NOCT) —
    exactly, EST, KV, LAGR — 1e−9 relative."""
    s, centres = field
    mean = 1.4 if kind == "sk" else None
    got = kn.estimate(row_3d(kind, s, PASSES, mean), centres)
    ref = numpy_3d(kind, s, centres, PASSES, mean)
    done = ref["PASS"] > 0
    # the blocks are a real mixture: every pass is used, some blocks have too few samples
    by_pass = [int((ref["PASS"] == k).sum()) for k in (1, 2, 3)]
    assert min(by_pass) >= 20 and int((~done).sum()) >= 10, by_pass
    assert ((got.status == kn.ST_OK) == done).all() and (got.status[~done] == kn.ST_FEW).all()
    for key in ("PASS", "NS", "NH", "NOCT"):
        assert (np.nan_to_num(got.values[key], nan=0.0).astype(np.int64) == ref[key]).all(), key
    assert np.isnan(got.values["EST"][~done]).all()
    keys = ("EST", "KV", "LAGR") if kind == "ok" else ("EST", "KV")
    for key in keys:
        a, b = got.values[key][done], ref[key][done]
        assert np.isfinite(a).all()
        err = float(np.max(np.abs(a - b) / np.maximum(np.abs(b), 1e-3 * np.abs(b).max())))
        assert err <= REL_NUMPY, (key, err)


def test_t8_8_7_the_limits_and_the_passes_really_work(field):
    """A guard against a vacuous Т8.8-7: without the limits of a hole and of an octant (the same passes with the
    limits switched off) the answers of many blocks differ, and the independent selection obeys them."""
    s, centres = field
    free = [{**p, "n_hole": 0, "n_oct": 0, "k_oct": 0, "n_holes_min": 0} for p in PASSES]
    with_limits = numpy_3d("ok", s, centres[:150], PASSES, None)
    without = numpy_3d("ok", s, centres[:150], free, None)
    both = (with_limits["PASS"] > 0) & (without["PASS"] > 0)
    assert int(both.sum()) >= 60
    assert int((np.abs(with_limits["EST"][both] - without["EST"][both]) > 1e-6).sum()) >= 30
    first = with_limits["PASS"] == 1
    assert int(first.sum()) >= 10  # the first pass has n_hole = 3 and k_oct = 3: a hole gives ≤ 3 samples
    assert (with_limits["NH"][first] >= 3).all() and (with_limits["NS"][first] <= 20).all()
    assert (with_limits["NS"][first] <= 3 * with_limits["NH"][first]).all()
    assert (with_limits["NOCT"][first] >= 3).all()


def test_t8_8_7_the_axes_of_the_independent_model_equal_the_kernel_covariance(field):
    """The two covariances (the kernel's numba one and the NumPy one) on the same lags, C and C*: 1e−12 absolute — the
    block test above rests on it."""
    from tests.reference.krige3d_numpy import Covariance

    rng = np.random.default_rng(3)
    d = rng.normal(0.0, 120.0, (500, 3))
    cm = cov_model(parse_model(MODEL3))
    from bf_core.geostat.estimate.cov import cov_c

    got = np.array([cov_c(*cm.args, *row) for row in d])
    assert np.abs(got - Covariance(MODEL3).cov(d)).max() <= 1e-12


# --- Д-38: the scale of a variogram model ----------------------------------------------------------------------------


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def test_td38_1_ok_with_the_y_model_and_back_equals_ok_with_the_same_z_model(w: W) -> None:
    """ТД38-1: a model in normal scores with `back` and the model in Z saved apart (the same `back.model`) give the
    same OK, bit for bit — through the store (the copy of the model), `spec` and the kernel."""
    y = vmodel(w, "Золото Y→Z", transform="nscore", back=BACK)  # the model of Y is MODEL, the back one is BACK["model"]
    z = vmodel(w, "Золото Z отдельно", model=BACK["model"])
    over = {"disc": [2, 2, 2], "search": {"from": "model", "passes": [{"factor": 1, "n_min": 2, "n_max": 24}]}}
    row_y = make(w, name="Из Y", variogram={"model_id": y}, **over)
    row_z = make(w, name="Из Z", variogram={"model_id": z}, **over)
    meta_y, meta_z = get(w, row_y["id"])["meta"], get(w, row_z["id"])["meta"]
    assert (meta_y["variogram"]["scale"], meta_z["variogram"]["scale"]) == ("back", "z")
    spec_y, spec_z = sb.load_spec(meta_y), sb.load_spec(meta_z)
    assert spec_y.variogram is not None and spec_z.variogram is not None
    assert parse_model(spec_y.variogram.snapshot) == parse_model(spec_z.variogram.snapshot)  # the same model
    assert spec_y.search == spec_z.search
    origin = np.asarray(meta_y["origin"], dtype=np.float64)
    s = sm.load(w.project, spec_y, origin=origin)
    assert s.z.size >= 4
    rng = np.random.default_rng(5)
    lo, hi = s.xyz.min(axis=0), s.xyz.max(axis=0)
    centres = lo + rng.uniform(-0.2, 1.2, (80, 3)) * (hi - lo)
    out = {}
    for name, spec in (("y", spec_y), ("z", spec_z)):
        out[name] = kn.estimate(kn.compile_row(spec, s, block_size=(10.0, 10.0, 5.0)), centres)
    assert out["y"].values.keys() == out["z"].values.keys()
    assert int((out["y"].status == kn.ST_OK).sum()) >= 20  # a real answer, not a row of NaN
    for key in out["y"].values:
        a, b = out["y"].values[key], out["z"].values[key]
        assert a.tobytes() == b.tobytes(), key  # bit for bit, NaN in the same places
    assert (out["y"].status == out["z"].status).all()
    # the control: the model of Y itself (not `back`) is another model and gives other numbers
    other = replace(
        spec_y,
        variogram=replace(spec_y.variogram, snapshot=parse_model(spec_y.variogram.snapshot).as_dict() | {"c0": 0.5}),
    )
    res = kn.estimate(kn.compile_row(other, s, block_size=(10.0, 10.0, 5.0)), centres)
    ok = out["y"].status == kn.ST_OK
    assert np.abs(res.values["KV"][ok] - out["y"].values["KV"][ok]).max() > 1e-3


def test_td38_2_a_model_in_y_without_back_is_refused_in_ok_sk_and_ik(w: W) -> None:
    """ТД38-2: a model in normal scores without `back` — 422 `variogram_scale` with the text of Д-38; in SK and IK
    too (the store reads the model of the project, the answer is the same)."""
    y = vmodel(w, "Золото Y", transform="nscore")
    for method in ({"kind": "ok"}, {"kind": "sk", "mean": {"mode": "value", "value": 1.0}}):
        err = mk_refused(w, 422, "variogram_scale", "variogram.model_id", variogram={"model_id": y}, method=method)
        assert err.message == "Модель построена в нормальных оценках"
        assert err.hint == "Выполните обратное преобразование модели или выберите модель в исходных единицах"
    per = {"kind": "ik", "ik": {"thresholds": [0.5, 1.5], "models": "per"}}
    mk_refused(
        w,
        422,
        "variogram_scale",
        "variograms.0.model_id",
        method=per,
        variogram=None,
        variograms=[{"model_id": y}, {"model_id": y}],
    )
    median = {"kind": "ik", "ik": {"thresholds": [0.5, 1.5], "models": "median"}}
    mk_refused(w, 422, "variogram_scale", "variogram.model_id", method=median, variogram={"model_id": y})
    with dialect.read_tx(w.project.engine) as conn:  # the refusals left nothing
        assert st.list_rows(conn) == []


def test_td38_2_the_scale_y_in_the_parameters_and_a_simulation() -> None:
    """ТД38-2 at the level of `spec`: `scale: "y"` of a model in the body of OK is refused; a simulation (SGS, Э18б)
    takes it — `parse_vario(..., simulation=True)`."""
    ref = {
        "model_id": 1,
        "snapshot": {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 30, "a2": 30, "a3": 30}]},
        "scale": "y",
    }
    refused(lambda: parse_estimation(_body_with(ref)), 422, "variogram_scale")
    err = refused(lambda: parse_vario(ref, "variogram"), 422, "variogram_scale", "variogram.model_id")
    assert err.message == "Модель построена в нормальных оценках"
    got = parse_vario(ref, "variogram", simulation=True)
    assert got.scale == "y" and got.model_id == 1
    for scale in ("z", "back", None):  # the others pass in an estimation
        assert parse_vario({**ref, "scale": scale}, "variogram").scale == scale


def _body_with(vario: dict[str, Any]) -> dict[str, Any]:
    out = {
        "model_id": 1,
        "samples": {"set_id": 1, "table": "t", "field": "z"},
        "method": {"kind": "ok"},
        "search": {"from": "manual", "r1": 100.0, "passes": [{"factor": 1, "n_min": 1, "n_max": 24}]},
        "variogram": vario,
    }
    return out
