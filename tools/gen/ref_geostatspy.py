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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GSPY = os.environ.get("GEOSTATSPY", "/tmp/GeostatsPy")
sys.path.insert(0, GSPY)
import geostatspy.geostats as g  # noqa: E402
import geostatspy.GSLIB as G  # noqa: E402

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


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/data/ref/geostatspy"))
