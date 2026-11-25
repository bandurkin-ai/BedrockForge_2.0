"""Plain-numpy closed forms of GSLIB kb2d / kb3d kriging (OK, SK; point and block), independent of GeostatsPy.

Used by tools/gen/ref_geostatspy.py (section 0.0.79) to confirm every committed GeostatsPy answer a second
time: a disagreement above 1e-9 aborts the generation. Conventions are those of GSLIB / GeostatsPy kb2d, kb3d:
azimuth clockwise from north, structure type 1 spherical, 2 exponential, 3 Gaussian (practical range), the
nugget dropped between identical discretisation points of a block, neighbours = ndmax nearest samples.
"""

from __future__ import annotations

import numpy as np


def _shape(it: int, q: np.ndarray) -> np.ndarray:
    """Unit-sill covariance of GSLIB structure `it` at the anisotropic distance q = h / range."""
    if it == 1:
        return np.where(q < 1.0, 1.0 - 1.5 * q + 0.5 * q**3, 0.0)
    if it == 2:
        return np.exp(-3.0 * q)
    if it == 3:
        return np.exp(-3.0 * q * q)
    raise ValueError(f"structure type {it}")


def cov2d(d: np.ndarray, vario: dict) -> np.ndarray:
    """Nested covariance of lag vectors d[..., (dx, dy)]; major axis at azimuth azi (clockwise from north)."""
    out = np.zeros(d.shape[:-1])
    sill = vario["nug"]
    for k in range(1, vario["nst"] + 1):
        a = np.radians(vario[f"azi{k}"])
        major = np.array([np.sin(a), np.cos(a)])
        minor = np.array([np.cos(a), -np.sin(a)])
        q = np.hypot((d @ major) / vario[f"hmaj{k}"], (d @ minor) / vario[f"hmin{k}"])
        out += vario[f"cc{k}"] * _shape(vario[f"it{k}"], q)
        sill += vario[f"cc{k}"]
    return np.where(np.all(d == 0.0, axis=-1), sill, out)


def krige2d_closed(df, grid, vario, nd, search, ktype, skmean):
    """Plain-numpy OK/SK for the kb2d neighbourhood (ndmax nearest within radius), nd x nd discretisation.
    GSLIB convention: the nugget is dropped between identical discretisation points of a block (C_VV)."""
    P = df[["X", "Y"]].to_numpy()
    v = df.V.to_numpy()
    sill = vario["nug"] + sum(vario[f"cc{k}"] for k in range(1, vario["nst"] + 1))
    ox = (np.arange(nd) + 0.5) * grid["xsiz"] / nd - grid["xsiz"] / 2
    oy = (np.arange(nd) + 0.5) * grid["ysiz"] / nd - grid["ysiz"] / 2
    disc = np.array([(a, b) for a in ox for b in oy])
    cvv = cov2d(disc[:, None, :] - disc[None, :, :], vario)
    if nd > 1:
        np.fill_diagonal(cvv, sill - vario["nug"])
    cbb = float(cvv.mean())
    est = np.zeros((grid["ny"], grid["nx"]))
    var = np.zeros_like(est)
    nmin = 10**9
    for iy in range(grid["ny"]):
        for ix in range(grid["nx"]):
            node = np.array([grid["xmn"] + ix * grid["xsiz"], grid["ymn"] + iy * grid["ysiz"]])
            dist = np.hypot(*(P - node).T)
            idx = np.argsort(dist, kind="stable")[: search["ndmax"]]
            idx = idx[dist[idx] < search["radius"]]
            m = len(idx)
            nmin = min(nmin, m)
            K = cov2d(P[idx][:, None, :] - P[idx][None, :, :], vario)
            rhs = cov2d(P[idx][:, None, :] - (node + disc)[None, :, :], vario).mean(axis=1)
            if ktype == 1:
                A = np.zeros((m + 1, m + 1))
                A[:m, :m] = K
                A[:m, m] = A[m, :m] = 1.0
                sol = np.linalg.solve(A, np.r_[rhs, 1.0])
                est[iy, ix] = sol[:m] @ v[idx]
                var[iy, ix] = cbb - sol[:m] @ rhs - sol[m]
            else:
                lam = np.linalg.solve(K, rhs)
                est[iy, ix] = skmean + lam @ (v[idx] - skmean)
                var[iy, ix] = cbb - lam @ rhs
    return est, var, nmin


def cov3d(d: np.ndarray, vario: dict) -> np.ndarray:
    """Nested 3D covariance of lag vectors d[..., (dx, dy, dz)] (x east, y north, z up).
    Major axis: azimuth azi clockwise from north, tilted by dip (positive dip raises it - the GeostatsPy sign);
    the minor horizontal axis is perpendicular to it, the vertical one completes the frame (plunge = 0)."""
    out = np.zeros(d.shape[:-1])
    sill = vario["nug"]
    for s in range(vario["nst"]):
        a, dp = np.radians(vario["azi"][s]), np.radians(vario["dip"][s])
        u1 = np.array([np.sin(a) * np.cos(dp), np.cos(a) * np.cos(dp), np.sin(dp)])
        u2 = np.array([-np.cos(a), np.sin(a), 0.0])
        u3 = np.array([-np.sin(dp) * np.sin(a), -np.sin(dp) * np.cos(a), np.cos(dp)])
        q = np.sqrt(
            (d @ u1 / vario["hmaj"][s]) ** 2 + (d @ u2 / vario["hmin"][s]) ** 2 + (d @ u3 / vario["hvert"][s]) ** 2
        )
        out += vario["cc"][s] * _shape(int(vario["it"][s]), q)
        sill += vario["cc"][s]
    return np.where(np.all(d == 0.0, axis=-1), sill, out)


def krige3d_closed(df, grid, vario, search, ktype, skmean):
    """Plain-numpy point OK/SK for the kb3d neighbourhood: ndmax nearest in the space with z / (rv / rh); kb3d has
    no distance cut-off (the radii only set the vertical stretch)."""
    P = df[["X", "Y", "Z"]].to_numpy()
    v = df.V.to_numpy()
    stretch = np.array([1.0, 1.0, search["radius_vert"] / search["radius_hori"]])
    sill = vario["nug"] + float(np.sum(vario["cc"][: vario["nst"]]))
    shape = (grid["nz"], grid["ny"], grid["nx"])
    est, var = np.zeros(shape), np.zeros(shape)
    for iz in range(grid["nz"]):
        for iy in range(grid["ny"]):
            for ix in range(grid["nx"]):
                node = np.array(
                    [grid["xmn"] + ix * grid["xsiz"], grid["ymn"] + iy * grid["ysiz"], grid["zmn"] + iz * grid["zsiz"]]
                )
                dist = np.linalg.norm((P - node) / stretch, axis=1)
                idx = np.argsort(dist, kind="stable")[: search["ndmax"]]
                m = len(idx)
                K = cov3d(P[idx][:, None, :] - P[idx][None, :, :], vario)
                rhs = cov3d(P[idx] - node, vario)
                if ktype == 1:
                    A = np.zeros((m + 1, m + 1))
                    A[:m, :m] = K
                    A[:m, m] = A[m, :m] = 1.0
                    sol = np.linalg.solve(A, np.r_[rhs, 1.0])
                    est[iz, iy, ix] = sol[:m] @ v[idx]
                    var[iz, iy, ix] = sill - sol[:m] @ rhs - sol[m]
                else:
                    lam = np.linalg.solve(K, rhs)
                    est[iz, iy, ix] = skmean + lam @ (v[idx] - skmean)
                    var[iz, iy, ix] = sill - lam @ rhs
    return est, var
