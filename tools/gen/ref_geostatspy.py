"""Reference vectors for the geostatistics kernels, computed with GeostatsPy (MIT).

GeostatsPy is NOT a dependency of BF (ADR 0012): this script runs in a throw-away
environment and only its output - JSON files under tests/data/ref/geostatspy - is
committed. BF tests read the JSON; they never import GeostatsPy.

    uv venv refenv --python 3.12
    uv pip install --python refenv/bin/python numpy scipy pandas numba statsmodels tqdm
    git clone https://github.com/bandurkin-ai/GeostatsPy /tmp/GeostatsPy     # fork of GeostatsGuy/GeostatsPy
    GEOSTATSPY=/tmp/GeostatsPy refenv/bin/python tools/gen/ref_geostatspy.py tests/data/ref/geostatspy

Every GeostatsPy answer that has a closed form (kriging) is re-derived here with plain
numpy; a disagreement above 1e-9 aborts the run, so the committed vectors are confirmed
twice. Stochastic programs (sgsim, sisim) are not frozen: GSLIB and numpy random streams
differ, so their acceptance is statistical (ТЗ 8.8, 08-08).

Section 0.0.79 (Э18, ТЗ 8.8 Т8.8-4...6, ADR 0024): ``--079`` writes kb2d_079.json, kb3d_079.json and
manifest_079.json next to the files above (the 0.0.46 files are not touched). GeostatsPy 0.0.79 comes from PyPI
into a throw-away environment (not into core/pyproject.toml and not into core/uv.lock):

    export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
    uv venv /tmp/ref079 --python 3.12
    uv pip install --python /tmp/ref079/bin/python geostatspy==0.0.79 numpy scipy pandas numba statsmodels \\
        tqdm matplotlib
    pip download geostatspy==0.0.79 --no-deps -d /tmp/ref079-wheel       # only for the wheel sha256
    GEOSTATSPY_WHEEL=/tmp/ref079-wheel/geostatspy-0.0.79-py3-none-any.whl \\
        /tmp/ref079/bin/python tools/gen/ref_geostatspy.py --079 tests/data/ref/geostatspy

Block kriging does not work in 0.0.79 (``kb2d``: NameError, wrong C_VV loop; ``kb3d``: the same plus a
discretisation without nzdis), so block references come from core/tests/reference/kb2d_fixed.py (two fixes);
every kriging answer is re-derived with plain numpy and a disagreement above 1e-9 aborts the run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

GSPY = os.environ.get("GEOSTATSPY", "/tmp/GeostatsPy")
g: Any = None  # geostatspy.geostats, bound by _import_gspy() (0.0.46 fork or 0.0.79 from PyPI)
G: Any = None  # geostatspy.GSLIB


def _import_gspy(path: str | None) -> None:
    """Bind GeostatsPy lazily: the 0.0.46 fork from a checkout (path), or the installed 0.0.79 (path=None)."""
    global g, G
    if path:
        sys.path.insert(0, path)
    import geostatspy.geostats as _g
    import geostatspy.GSLIB as _G

    g, G = _g, _G


sys.path.insert(0, str(Path(__file__).resolve().parent))
from ref_krige_closed import krige2d_closed, krige3d_closed  # noqa: E402

BIG = 1.0e21
SEED = 20261118


def dataset() -> pd.DataFrame:
    """80 points: 40 uniform on 1000x1000 m and 40 clustered in 200x200 m (for declustering)."""
    rng = np.random.default_rng(SEED)
    x = np.r_[rng.uniform(0, 1000, 40), rng.uniform(100, 300, 40)]
    y = np.r_[rng.uniform(0, 1000, 40), rng.uniform(100, 300, 40)]
    z = np.sin(x / 180) + np.cos(y / 230) + 0.3 * rng.standard_normal(80)
    return pd.DataFrame({"X": x, "Y": y, "V": np.exp(0.6 * z)})


def sph(h: np.ndarray, a: float) -> np.ndarray:
    r = np.minimum(h / a, 1.0)
    return 1.0 - 1.5 * r + 0.5 * r**3  # covariance part, unit sill


def cov(h: np.ndarray, c0: float, cc: float, a: float) -> np.ndarray:
    return np.where(h == 0, c0 + cc, cc * sph(h, a))


def krige_ref(df, grid, vario, nxdis, nydis, ktype, skmean):
    """Plain-numpy OK/SK for an isotropic spherical model, all data, no search limits."""
    c0, cc, a = vario["nug"], vario["cc1"], vario["hmaj1"]
    px, py, pv = df.X.values, df.Y.values, df.V.values
    n = len(px)
    dd = np.hypot(px[:, None] - px[None, :], py[:, None] - py[None, :])
    K = cov(dd, c0, cc, a)
    est = np.zeros((grid["ny"], grid["nx"]))
    var = np.zeros_like(est)
    for iy in range(grid["ny"]):
        for ix in range(grid["nx"]):
            x0 = grid["xmn"] + ix * grid["xsiz"] - grid["xsiz"] / 2
            y0 = grid["ymn"] + iy * grid["ysiz"] - grid["ysiz"] / 2
            sx = x0 + (np.arange(nxdis) + 0.5) * grid["xsiz"] / nxdis
            sy = y0 + (np.arange(nydis) + 0.5) * grid["ysiz"] / nydis
            SX, SY = np.meshgrid(sx, sy)
            SX, SY = SX.ravel(), SY.ravel()
            # mean covariance data -> block and block -> block; the GSLIB kb2d convention drops
            # the nugget inside the block (cbb uses cova at zero lag as c0 + cc only for a point)
            hb = np.hypot(px[:, None] - SX[None, :], py[:, None] - SY[None, :])
            rhs = cov(hb, c0, cc, a).mean(axis=1)
            hv = np.hypot(SX[:, None] - SX[None, :], SY[:, None] - SY[None, :])
            cvv = cov(hv, c0, cc, a)
            np.fill_diagonal(cvv, cc if nxdis * nydis > 1 else c0 + cc)  # nugget removed for blocks
            cbb = cvv.mean()
            if ktype == 1:
                A = np.zeros((n + 1, n + 1))
                A[:n, :n] = K
                A[:n, n] = A[n, :n] = 1.0
                b = np.r_[rhs, 1.0]
                sol = np.linalg.solve(A, b)
                lam, mu = sol[:n], sol[n]
                est[iy, ix] = lam @ pv
                var[iy, ix] = cbb - lam @ rhs - mu
            else:
                lam = np.linalg.solve(K, rhs)
                est[iy, ix] = skmean + lam @ (pv - skmean)
                var[iy, ix] = cbb - lam @ rhs
    return est, var


def main(out_dir: Path) -> None:
    _import_gspy(GSPY)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = dataset()
    commit = subprocess.run(["git", "-C", GSPY, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    manifest = {
        "generator": "tools/gen/ref_geostatspy.py",
        "geostatspy_commit": commit,
        "geostatspy_version": getattr(sys.modules["geostatspy"], "__version__", "?"),
        "numpy": np.__version__,
        "seed": SEED,
        "cross_checks": {},
    }
    manifest["known_defects"] = [
        "geostatspy.kb2d: block discretisation (nxdis*nydis > 1) raises NameError 'real' and its inner covariance loop "
        "sums only the last j; do not use as a reference for block kriging",
        "geostatspy.kb2d returns the grid with row 0 = northernmost (y flipped)",
        "geostatspy.gamv counts the self-pair at lag 0 (GSLIB convention): "
        "npairs[0] = n, or 2n for an omnidirectional variogram",
        "geostatspy.nscore: the score of the minimum sample is taken from the neighbouring rows of the transform "
        "table (-2.20 instead of -2.50 for the reference set); BF2 stats/transforms.py matches it to 1e-6 elsewhere",
    ]
    ref: dict = {"data": {c: df[c].tolist() for c in ("X", "Y", "V")}}

    # --- nscore / backtr -------------------------------------------------------------------
    ns, vr, vrg = g.nscore(df, "V")
    ref["nscore"] = {"ns": ns.tolist(), "vr": vr.tolist(), "vrg": vrg.tolist()}
    tmp = pd.DataFrame({"NS": ns})
    back = g.backtr(
        tmp, "NS", vr, vrg, float(df.V.min()), float(df.V.max()), 1, float(df.V.min()), 1, float(df.V.max())
    )
    err = float(np.max(np.abs(np.asarray(back) - df.V.values)))
    manifest["cross_checks"]["nscore_backtr_roundtrip_max_abs"] = err
    assert err < 1e-9, err
    ref["nscore"]["backtr_roundtrip"] = list(map(float, back))

    # --- experimental variograms (gamv) ---------------------------------------------------
    gamv = {}
    for name, azm, atol, bw in (
        ("az0", 0.0, 22.5, 1e9),
        ("az90", 90.0, 22.5, 1e9),
        ("omni", 0.0, 90.0, 1e9),
        ("az45_bw150", 45.0, 22.5, 150.0),
    ):
        dis, vario, npp = g.gamv(df, "X", "Y", "V", -BIG, BIG, 50.0, 25.0, 10, azm, atol, bw, 0)
        gamv[name] = {
            "params": {"xlag": 50.0, "xltol": 25.0, "nlag": 10, "azm": azm, "atol": atol, "bandwh": bw},
            "lag": dis.tolist(),
            "gamma": vario.tolist(),
            "npairs": npp.tolist(),
        }
    ref["gamv"] = gamv

    # --- variogram model (vmodel) ----------------------------------------------------------
    vm = G.make_variogram(0.1, 2, 1, 0.5, 30, 400, 200, 2, 0.4, 30, 600, 300)
    vres = g.vmodel(10, 50.0, 30.0, vm)
    ref["vmodel"] = {
        "model": vm,
        "azm": 30.0,
        "xlag": 50.0,
        "nlag": 10,
        "result": [np.asarray(a).tolist() for a in vres],
    }

    # --- variogram map ---------------------------------------------------------------------
    vmap, npmap = g.varmapv(df, "X", "Y", "V", -BIG, BIG, 5, 5, 100.0, 100.0, 1, 0)
    ref["varmap"] = {
        "params": {"nxlag": 5, "nylag": 5, "dxlag": 100.0, "dylag": 100.0, "minnp": 1},
        "gamma": np.asarray(vmap).tolist(),
        "npairs": np.asarray(npmap).tolist(),
    }

    # --- declustering (cell, 2D) -----------------------------------------------------------
    wt, cell, dmean = g.declus(df, "X", "Y", "V", 1, 4, 10, 25.0, 300.0)
    ref["declus"] = {
        "params": {"iminmax": 1, "noff": 4, "ncell": 10, "cmin": 25.0, "cmax": 300.0},
        "weights": np.asarray(wt).tolist(),
        "cell_size": np.asarray(cell).tolist(),
        "declustered_mean": np.asarray(dmean).tolist(),
    }
    manifest["cross_checks"]["declus_weights_sum_minus_n"] = float(np.sum(wt) - len(df))

    # --- kb2d: SK / OK, point and block, all data and search-limited ------------------------
    vario = G.make_variogram(0.1, 1, 1, 0.9, 0, 300, 300)
    grid = {"nx": 5, "xmn": 100.0, "xsiz": 200.0, "ny": 5, "ymn": 100.0, "ysiz": 200.0}
    skmean = float(df.V.mean())
    cases = {}
    for name, ktype, nd, ndmax, radius in (
        ("ok_point_all", 1, 1, 80, 5000.0),
        ("sk_point_all", 0, 1, 80, 5000.0),
        ("ok_block4_all", 1, 4, 80, 5000.0),
        ("sk_block4_all", 0, 4, 80, 5000.0),
        ("ok_point_search12", 1, 1, 12, 400.0),
    ):
        if nd == 1:
            est, var = g.kb2d(
                df,
                "X",
                "Y",
                "V",
                -BIG,
                BIG,
                grid["nx"],
                grid["xmn"],
                grid["xsiz"],
                grid["ny"],
                grid["ymn"],
                grid["ysiz"],
                nd,
                nd,
                1,
                ndmax,
                radius,
                ktype,
                skmean,
                vario,
            )
            est, var = np.flipud(est), np.flipud(var)  # GeostatsPy returns row 0 = northernmost; we store iy ascending
            source = "geostatspy.kb2d"
        else:  # GeostatsPy block kriging is broken (see KNOWN_DEFECTS): closed form only
            est, var = krige_ref(df, grid, vario, nd, nd, ktype, skmean)
            source = "numpy closed form (GSLIB kb2d convention: nugget removed inside the block)"
        case = {
            "source": source,
            "params": {
                **grid,
                "nxdis": nd,
                "nydis": nd,
                "ndmin": 1,
                "ndmax": ndmax,
                "radius": radius,
                "ktype": ktype,
                "skmean": skmean,
            },
            "est": np.asarray(est).tolist(),
            "var": np.asarray(var).tolist(),
            "layout": "est[iy][ix], iy and ix ascend with Y and X; cell centres xmn + ix*xsiz, ymn + iy*ysiz",
        }
        if ndmax == 80 and nd == 1:  # no search limits -> closed form must match GeostatsPy
            e2, v2 = krige_ref(df, grid, vario, nd, nd, ktype, skmean)
            de, dv = float(np.max(np.abs(e2 - est))), float(np.max(np.abs(v2 - var)))
            manifest["cross_checks"][f"kb2d_{name}_max_abs_est_var"] = [de, dv]
            if max(de, dv) > 1e-9:
                raise SystemExit(f"kb2d {name}: GeostatsPy and numpy disagree: est {de:.3e}, var {dv:.3e}")
        cases[name] = case
    ref["kb2d"] = {"variogram": vario, "cases": cases}

    (out_dir / "geostatspy_reference.json").write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


# ======================================================================================================
# Section 0.0.79 (Э18, ТЗ 8.8 Т8.8-4...6): kb2d point + block, kb3d point
# ======================================================================================================

SEED_079 = 42
ROOT = Path(__file__).resolve().parents[2]
FIXED_KB2D = ROOT / "core" / "tests" / "reference" / "kb2d_fixed.py"
EXT_079 = 1.0e-8  # numpy closed form vs GeostatsPy (setup_rotmat of 0.0.79 uses PI = 3.14159265: ~6e-10 apart)
VARIO2D_079 = {  # two anisotropic structures: spherical (azimuth 30) + exponential (azimuth 60); sill 1.0
    "nug": 0.1,
    "nst": 2,
    "it1": 1,
    "cc1": 0.5,
    "azi1": 30,
    "hmaj1": 300,
    "hmin1": 150,
    "it2": 2,
    "cc2": 0.4,
    "azi2": 60,
    "hmaj2": 500,
    "hmin2": 250,
}
GRID2D_079 = {"nx": 50, "xmn": 10.0, "xsiz": 20.0, "ny": 50, "ymn": 10.0, "ysiz": 20.0}
SEARCH2D_079 = {"ndmin": 1, "ndmax": 16, "radius": 400.0}
GRID3D_079 = {
    "nx": 16,
    "xmn": 15.0,
    "xsiz": 30.0,
    "ny": 16,
    "ymn": 15.0,
    "ysiz": 30.0,
    "nz": 8,
    "zmn": 6.25,
    "zsiz": 12.5,
}
SEARCH3D_079 = {"ndmin": 1, "ndmax": 16, "radius_hori": 400.0, "radius_vert": 80.0}


def dataset_2d_079(n: int = 300) -> pd.DataFrame:
    """300 samples, seed 42, uniform in the 1000 x 1000 m square; smooth trend plus noise, positive values."""
    rng = np.random.default_rng(SEED_079)
    x = rng.uniform(0, 1000, n)
    y = rng.uniform(0, 1000, n)
    z = np.sin(x / 180) + np.cos(y / 230) + 0.3 * rng.standard_normal(n)
    return pd.DataFrame({"X": x, "Y": y, "V": np.exp(0.6 * z)})


def dataset_3d_079(n: int = 250) -> pd.DataFrame:
    """250 samples, seed 42, in the box 480 x 480 x 100 m (the 3D grid); the vertical trend is stronger."""
    rng = np.random.default_rng(SEED_079)
    x = rng.uniform(0, 480, n)
    y = rng.uniform(0, 480, n)
    z = rng.uniform(0, 100, n)
    t = np.sin(x / 150) + np.cos(y / 200) + z / 60 + 0.3 * rng.standard_normal(n)
    return pd.DataFrame({"X": x, "Y": y, "Z": z, "V": np.exp(0.5 * t)})


def _load_fixed_kb2d() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("kb2d_fixed", FIXED_KB2D)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patched_setrot3d(fix: bool):
    """kb3d of 0.0.79 with nst = 2 loses the rotation matrix of structure 0: setrot3D_array allocates
    `rotmat = np.zeros((2, 3, 3))` INSIDE the structure loop, so the second pass zeroes the first matrix and
    structure 0 acts as a constant covariance. fix=True moves the allocation before the loop (the only change)."""
    import inspect

    if not hasattr(_patched_setrot3d, "orig"):
        _patched_setrot3d.orig = g.setrot3D_array  # type: ignore[attr-defined]
    if not fix:
        g.setrot3D_array = _patched_setrot3d.orig  # type: ignore[attr-defined]
        return
    src = inspect.getsource(_patched_setrot3d.orig.py_func)  # type: ignore[attr-defined]
    inner = "        rotmat = np.zeros((2,3,3))\n"
    loop = "    for ist in range(0,vario3D_array[1]):\n"
    assert src.count(inner) == 1 and src.count(loop) == 1
    src = src.replace(inner, "").replace(loop, "    rotmat = np.zeros((2,3,3))\n" + loop)
    exec(compile(src, "<setrot3D_array fixed>", "exec"), g.__dict__)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _dump(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")


def _jsonable(vario: dict) -> dict:
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in vario.items()}


def main_079(out_dir: Path) -> None:
    import importlib.metadata as md
    import platform

    _import_gspy(None)
    ver = md.version("geostatspy")
    if ver != "0.0.79":
        raise SystemExit(f"GeostatsPy 0.0.79 is required, found {ver}")
    out_dir.mkdir(parents=True, exist_ok=True)
    fixed = _load_fixed_kb2d()
    checks: dict = {}

    # --- kb2d ------------------------------------------------------------------------------------------
    df = dataset_2d_079()
    skmean = float(df.V.mean())
    vario, grid, sr = VARIO2D_079, GRID2D_079, SEARCH2D_079
    cases2: dict = {}
    for name, ktype, nd in (("ok_point", 1, 1), ("sk_point", 0, 1), ("ok_block4", 1, 4), ("sk_block4", 0, 4)):
        args = (df, "X", "Y", "V", -BIG, BIG, grid["nx"], grid["xmn"], grid["xsiz"], grid["ny"], grid["ymn"])
        args += (grid["ysiz"], nd, nd, sr["ndmin"], sr["ndmax"], sr["radius"], ktype, skmean, vario)
        e_fix, v_fix = (np.flipud(a) for a in fixed.kb2d(*args))  # est[iy][ix], iy ascends with Y
        e_ref, v_ref, nmin = krige2d_closed(df, grid, vario, nd, sr, ktype, skmean)
        assert nmin >= 2, f"{name}: a node has {nmin} samples; the one-sample branch of kb2d is not trusted"
        if nd == 1:
            e_g, v_g = (np.flipud(a) for a in g.kb2d(*args))
            d_copy = max(float(np.max(np.abs(e_g - e_fix))), float(np.max(np.abs(v_g - v_fix))))
            checks[f"kb2d_{name}_fixed_copy_vs_079_max_abs"] = d_copy
            if d_copy > 1e-12:
                raise SystemExit(f"kb2d_fixed point mode differs from 0.0.79: {d_copy:.3e}")
            est, var, source = e_g, v_g, "geostatspy 0.0.79 kb2d"
        else:
            est, var = e_fix, v_fix
            source = "core/tests/reference/kb2d_fixed.py (kb2d 0.0.79 with two fixes)"
        de, dv = float(np.max(np.abs(e_ref - est))), float(np.max(np.abs(v_ref - var)))
        checks[f"kb2d_{name}_vs_numpy_max_abs_est_var"] = [de, dv]
        if max(de, dv) > EXT_079:
            raise SystemExit(f"kb2d {name}: reference and numpy disagree: est {de:.3e}, var {dv:.3e}")
        cases2[name] = {
            "source": source,
            "params": {"nxdis": nd, "nydis": nd, **sr, "ktype": ktype, "skmean": skmean},
            "est": est.tolist(),
            "var": var.tolist(),
        }
    try:
        g.kb2d(df, "X", "Y", "V", -BIG, BIG, 5, 10.0, 20.0, 5, 10.0, 20.0, 4, 4, 1, 16, 400.0, 1, skmean, vario)
        raise SystemExit("kb2d 0.0.79 block mode no longer raises: the defect list is out of date")
    except NameError as e:
        checks["kb2d_block_original_raises"] = f"NameError: {e}"
    _dump(
        out_dir / "kb2d_079.json",
        {
            "data": {c: df[c].tolist() for c in ("X", "Y", "V")},
            "variogram": vario,
            "grid": grid,
            "search": sr,
            "skmean": skmean,
            "layout": "est[iy][ix], iy and ix ascend with Y and X; cell centres xmn + ix*xsiz, ymn + iy*ysiz",
            "cases": cases2,
        },
    )

    # --- kb3d (point) ----------------------------------------------------------------------------------
    d3 = dataset_3d_079()
    skmean3 = float(d3.V.mean())
    gr, sr3 = GRID3D_079, SEARCH3D_079
    varios = {
        "nst1": g.make_variogram3D(0.1, 1, 1, 0.9, 30, 0, 250, 120, 40),
        "nst2": g.make_variogram3D(0.1, 2, 1, 0.5, 30, 0, 250, 120, 40, 2, 0.4, 60, 0, 400, 200, 80),
        "nst2_dip": g.make_variogram3D(0.1, 2, 1, 0.5, 30, 15, 250, 120, 40, 2, 0.4, 60, 10, 400, 200, 80),
    }

    def run3(vz, ktype):
        a = (d3, "X", "Y", "Z", "V", -BIG, BIG, gr["nx"], gr["xmn"], gr["xsiz"], gr["ny"], gr["ymn"], gr["ysiz"])
        a += (gr["nz"], gr["zmn"], gr["zsiz"], 1, 1, 1, sr3["ndmin"], sr3["ndmax"], sr3["radius_hori"])
        a += (sr3["radius_vert"], ktype, skmean3, vz)
        e, v = g.kb3d(*a)
        return e[::-1, ::-1, :], v[::-1, ::-1, :]  # est[iz][iy][ix], all ascending

    cases3: dict = {}
    for vname, vz in varios.items():
        for kname, ktype in (("ok", 1), ("sk", 0)):
            name = f"{kname}_point_{vname}"
            _patched_setrot3d(fix=False)
            e_raw, _ = run3(vz, ktype)
            _patched_setrot3d(fix=vz["nst"] == 2)
            est, var = run3(vz, ktype)
            e_ref, v_ref = krige3d_closed(d3, gr, vz, sr3, ktype, skmean3)
            de, dv = float(np.max(np.abs(e_ref - est))), float(np.max(np.abs(v_ref - var)))
            checks[f"kb3d_{name}_vs_numpy_max_abs_est_var"] = [de, dv]
            if max(de, dv) > EXT_079:
                raise SystemExit(f"kb3d {name}: reference and numpy disagree: est {de:.3e}, var {dv:.3e}")
            if vz["nst"] == 2:
                checks[f"kb3d_{name}_unpatched_vs_patched_max_abs_est"] = float(np.max(np.abs(e_raw - est)))
            cases3[name] = {
                "source": "geostatspy 0.0.79 kb3d" + (" + setrot3D_array fix" if vz["nst"] == 2 else ""),
                "variogram": _jsonable(vz),
                "params": {**sr3, "nxdis": 1, "nydis": 1, "nzdis": 1, "ktype": ktype, "skmean": skmean3},
                "est": est.tolist(),
                "var": var.tolist(),
            }
    _patched_setrot3d(fix=False)
    try:  # block mode of kb3d: discretisation without nzdis (MAXDIS = nxdis*nydis) and `real`
        a = (d3, "X", "Y", "Z", "V", -BIG, BIG, 2, 15.0, 30.0, 2, 15.0, 30.0, 2, 6.25, 12.5)
        g.kb3d(*a, 2, 2, 2, 1, 16, 400.0, 80.0, 1, 1.0, varios["nst1"])
        raise SystemExit("kb3d 0.0.79 block mode no longer fails: the defect list is out of date")
    except (NameError, IndexError) as e:
        checks["kb3d_block_original_raises"] = f"{type(e).__name__}: {e}"
    _dump(
        out_dir / "kb3d_079.json",
        {
            "data": {c: d3[c].tolist() for c in ("X", "Y", "Z", "V")},
            "grid": gr,
            "search": sr3,
            "skmean": skmean3,
            "layout": "est[iz][iy][ix], all ascend with Z, Y, X; cell centres xmn + ix*xsiz, ymn + iy*ysiz, "
            "zmn + iz*zsiz",
            "convention": "x east, y north, z up; azi clockwise from north (major axis); positive dip raises the "
            "major axis (GeostatsPy sign, opposite to the GSLIB 'positive down'); plunge 0; hmaj >= hmin are "
            "horizontal ranges, hvert is the vertical one",
            "cases": cases3,
        },
    )

    files = {n: _sha256(out_dir / n) for n in ("kb2d_079.json", "kb3d_079.json")}
    wheel = os.environ.get("GEOSTATSPY_WHEEL")
    manifest = {
        "generator": "tools/gen/ref_geostatspy.py --079",
        "geostatspy_version": ver,
        "geostatspy_source": "PyPI geostatspy==0.0.79 (MIT), throw-away environment, not a BF dependency",
        "geostatspy_wheel_sha256": _sha256(Path(wheel)) if wheel else None,
        "geostats_py_sha256": _sha256(Path(g.__file__)),
        "kb2d_fixed_sha256": _sha256(FIXED_KB2D),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "seed": SEED_079,
        "files": files,
        "cross_checks": checks,
        "known_defects": [
            "geostatspy.kb2d 0.0.79 block mode (nxdis*nydis > 1) raises NameError 'real'; after that, the inner "
            "covariance loop of C_VV sums only the last j (if i == j and cbb += cov stand outside the j loop); "
            "references for blocks come from core/tests/reference/kb2d_fixed.py",
            "geostatspy.kb3d 0.0.79 block mode: MAXDIS = nxdis*nydis ignores nzdis, 'real' is undefined, the same C_VV "
            "loop error; block kb3d is not frozen",
            "geostatspy.kb3d 0.0.79 with nst = 2: setrot3D_array allocates rotmat inside the structure loop, the "
            "rotation matrix of structure 0 is zeroed and it acts as a constant covariance; the committed nst = 2 "
            "cases are computed with that single allocation moved before the loop (cross-checked with numpy)",
            "geostatspy.kb3d 0.0.79 has no search radius: the two radii only set the vertical stretch rv / rh",
            "geostatspy.kb2d / kb3d return the grids with row 0 = northernmost (and the top level first in 3D); "
            "the committed arrays are re-ordered to ascending Z, Y, X",
            "geostatspy.setup_rotmat 0.0.79 uses PI = 3.14159265 (truncated): kb2d differs from an exact-pi "
            "calculation by up to ~6e-10 absolute (est and var); with the same truncated pi numpy agrees to 4e-15",
            "geostatspy.kb2d 0.0.79 treats 'na' as a count after the kd-tree query, so `na == 0` (one-sample branch) "
            "means 'no sample'; the reference nodes all have >= 2 samples",
        ],
    }
    _dump_pretty(out_dir / "manifest_079.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


def _dump_pretty(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--079" in sys.argv:
        main_079(Path(args[0] if args else "tests/data/ref/geostatspy"))
    else:
        main(Path(args[0] if args else "tests/data/ref/geostatspy"))
