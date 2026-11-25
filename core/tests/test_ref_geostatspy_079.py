"""GeostatsPy 0.0.79 references (Д-21, ТЗ 8.8 Т8.8-4...6; ADR 0024): integrity of the committed files and of the
test-only copy of `kb2d` with two fixes (core/tests/reference/kb2d_fixed.py).

GeostatsPy is not a dependency of BF: the vectors in tests/data/ref/geostatspy/{kb2d,kb3d}_079.json were produced by
tools/gen/ref_geostatspy.py --079 in a throw-away environment. BF kernels are compared to them in 18.7 / 18.8;
this file guards the data and proves that the copy of `kb2d` equals 0.0.79 in point mode (1e-12) and reproduces the
stored block results."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.reference import kb2d_fixed

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy"
FIXED = Path(kb2d_fixed.__file__)
BIG = 1.0e21
TOL = 1e-12


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((REF / "manifest_079.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb2d() -> dict:
    return json.loads((REF / "kb2d_079.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb3d() -> dict:
    return json.loads((REF / "kb3d_079.json").read_text(encoding="utf-8"))


def test_manifest_integrity(manifest: dict) -> None:
    assert manifest["geostatspy_version"] == "0.0.79"
    assert manifest["seed"] == 42
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["geostatspy_wheel_sha256"] or "")
    assert set(manifest["files"]) == {"kb2d_079.json", "kb3d_079.json"}
    for name, sha in manifest["files"].items():
        assert _sha256(REF / name) == sha, f"{name} changed: regenerate with tools/gen/ref_geostatspy.py --079"
    assert _sha256(FIXED) == manifest["kb2d_fixed_sha256"], "kb2d_fixed.py changed: regenerate the references"
    assert (FIXED.parent / "LICENSE").read_text(encoding="utf-8").startswith("Copyright (c) 2019 Michael Pyrcz")
    assert len(manifest["known_defects"]) >= 5
    # the original block mode must have failed when the vectors were made (that is why the fixed copy exists)
    assert manifest["cross_checks"]["kb2d_block_original_raises"].startswith("NameError")
    assert manifest["cross_checks"]["kb3d_block_original_raises"]


def test_manifest_cross_checks_pass(manifest: dict) -> None:
    for key, value in manifest["cross_checks"].items():
        if key.endswith("_vs_numpy_max_abs_est_var"):
            assert max(value) < 1e-8, key  # GeostatsPy uses PI = 3.14159265: ~6e-10 from exact-pi numpy
        elif key.endswith("fixed_copy_vs_079_max_abs"):
            assert value <= TOL, key
        elif key.endswith("unpatched_vs_patched_max_abs_est"):
            assert value > 0.1, key  # the nst = 2 defect of kb3d 0.0.79 is not cosmetic


def test_kb2d_vectors_shape_and_invariants(kb2d: dict) -> None:
    d = kb2d["data"]
    assert len(d["X"]) == len(d["Y"]) == len(d["V"]) == 300
    assert min(d["V"]) > 0 and 0 <= min(d["X"]) and max(d["X"]) <= 1000 and max(d["Y"]) <= 1000
    v = kb2d["variogram"]
    assert v["nst"] == 2 and v["hmaj1"] != v["hmin1"] and v["hmaj2"] != v["hmin2"]  # anisotropic, two structures
    assert abs(v["nug"] + v["cc1"] + v["cc2"] - 1.0) < 1e-12
    assert (kb2d["grid"]["nx"], kb2d["grid"]["ny"]) == (50, 50) and kb2d["search"]["ndmax"] == 16
    c = kb2d["cases"]
    assert set(c) == {"ok_point", "sk_point", "ok_block4", "sk_block4"}
    for name, case in c.items():
        assert np.asarray(case["est"]).shape == (50, 50) and np.isfinite(case["est"]).all(), name
        assert np.asarray(case["var"]).min() > -1e-12, name
    ok_var, sk_var = np.asarray(c["ok_point"]["var"]), np.asarray(c["sk_point"]["var"])
    assert np.all(sk_var <= ok_var + 1e-9)  # SK knows the mean
    for kind in ("ok", "sk"):  # a block is smoother than a point: lower kriging variance
        assert np.all(np.asarray(c[f"{kind}_block4"]["var"]) <= np.asarray(c[f"{kind}_point"]["var"]) + 1e-9)


def test_kb3d_vectors_shape_and_invariants(kb3d: dict) -> None:
    d = kb3d["data"]
    assert len(d["X"]) == len(d["Y"]) == len(d["Z"]) == len(d["V"]) == 250
    g = kb3d["grid"]
    shape = (g["nz"], g["ny"], g["nx"])
    c = kb3d["cases"]
    assert set(c) == {f"{k}_point_{v}" for k in ("ok", "sk") for v in ("nst1", "nst2", "nst2_dip")}
    for name, case in c.items():
        assert np.asarray(case["est"]).shape == shape and np.isfinite(case["est"]).all(), name
        assert np.asarray(case["var"]).min() > -1e-12, name
        assert case["params"]["nxdis"] == case["params"]["nydis"] == case["params"]["nzdis"] == 1  # point only
    assert c["ok_point_nst2"]["variogram"]["nst"] == 2 and c["ok_point_nst1"]["variogram"]["nst"] == 1
    assert c["ok_point_nst2_dip"]["variogram"]["dip"][0] == 15
    for v in ("nst1", "nst2", "nst2_dip"):
        assert np.all(np.asarray(c[f"sk_point_{v}"]["var"]) <= np.asarray(c[f"ok_point_{v}"]["var"]) + 1e-9)


def _run_copy(ref: dict, case: dict, nx: int, ny: int) -> tuple[np.ndarray, np.ndarray]:
    df = pd.DataFrame(ref["data"])
    gr, sr, p = ref["grid"], ref["search"], case["params"]
    est, var = kb2d_fixed.kb2d(
        df, "X", "Y", "V", -BIG, BIG, nx, gr["xmn"], gr["xsiz"], ny, gr["ymn"], gr["ysiz"],
        p["nxdis"], p["nydis"], sr["ndmin"], sr["ndmax"], sr["radius"], p["ktype"], p["skmean"], ref["variogram"],
    )  # fmt: skip
    return np.flipud(est), np.flipud(var)  # the copy returns row 0 = northernmost, like GeostatsPy


@pytest.mark.parametrize("name", ["ok_point", "sk_point"])
def test_kb2d_copy_equals_079_in_point_mode(kb2d: dict, name: str) -> None:
    """Т8.8-4 base: the copy is arithmetically the same code as geostatspy 0.0.79 kb2d (1e-12, whole 50x50 grid)."""
    case = kb2d["cases"][name]
    est, var = _run_copy(kb2d, case, 50, 50)
    assert np.max(np.abs(est - np.asarray(case["est"]))) <= TOL
    assert np.max(np.abs(var - np.asarray(case["var"]))) <= TOL


@pytest.mark.parametrize("name", ["ok_block4", "sk_block4"])
def test_kb2d_copy_reproduces_stored_block_results(kb2d: dict, name: str) -> None:
    """Т8.8-5 base: block kriging (4x4 points) by the copy equals the stored vectors (a 6x6 corner of the grid;
    the vectors themselves were confirmed against a numpy closed form to < 1e-8 when generated)."""
    case = kb2d["cases"][name]
    est, var = _run_copy(kb2d, case, 6, 6)
    assert np.max(np.abs(est - np.asarray(case["est"])[:6, :6])) <= TOL
    assert np.max(np.abs(var - np.asarray(case["var"])[:6, :6])) <= TOL
