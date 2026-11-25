"""An independent block kriging in plain NumPy (ТЗ 8.8, Т8.8-7; ADR 0024 «Точность»): OK and SK of a 3D block, the
anisotropy angles ang1/ang2/ang3 of every structure (nested, any number), the discretization of a block turned about Z,
the search of the passes by brute force with the limits of a hole and of an octant.

TEST-ONLY reference code; it imports nothing from `bf_core` (not the covariance, not the rotation, not the search):
every rule is written here from the text of the ТЗ, so that a disagreement with the kernel is a disagreement of two
readings of the same text and not of one function with itself.

The rules (08-08, 8.8.2), as they are read here:

* a structure: the axes u₁, u₂, u₃ in the world (X east, Y north, Z up). u₁ — the major axis: azimuth `ang1` clockwise
  from north, dip `ang2` (positive raises the axis, negative goes down). Without the turn (`ang3` = 0) u₂ is the
  horizontal axis perpendicular to u₁ (to the west of it, looking along the azimuth: (−cos az, sin az, 0)) and
  u₃ = u₁ × u₂. `ang3` turns u₂ and u₃ about u₁: u₂' = cos t·u₂ + sin t·u₃, u₃' = −sin t·u₂ + cos t·u₃.
  h = √((u₁·Δ/a1)² + (u₂·Δ/a2)² + (u₃·Δ/a3)²); the covariance of unit sill ρ(h): spherical 1 − 1.5h + 0.5h³ (0 from
  h = 1), exponential exp(−3h), gaussian exp(−3h²) (practical ranges, GSLIB).
* C(Δ) = Σ c_s·ρ_s(h_s) for Δ ≠ 0 and C(0) = c0 + Σ c_s; C*(Δ) is C(Δ) without the nugget (also at 0:
  C*(0) = Σ c_s) — GSLIB kb2d / kt3d: the nugget does not enter the mean covariances of a block.
* a block of N = nx·ny·nz points: the centres of its sub-cells, offsets from the centre of the block, turned by φ about
  +Z counter-clockwise seen from above; C̄_iV = (1/N)·Σ_k C*(x_i − (x₀ + u_k)), C̄_VV = (1/N²)·Σ_k Σ_l C*(u_k − u_l)
  (N = 1: C̄_iV = C(x_i − x₀), C̄_VV = C(0)).
* the search of a pass: the ellipsoid of angles (ang1, ang2, ang3) and radii (r1, r2, r3): a sample is inside when
  ‖(u₁·Δ, u₂·Δ·r1/r2, u₃·Δ·r1/r3)‖ ≤ r1·factor; the candidates in the order (that distance, number of the sample);
  the walk: it stops at n_max selected; a candidate whose hole already has n_hole selected, then one whose octant
  already has n_oct, is skipped; the octant is read in the axes of the ellipsoid (bit 0 — u₁·Δ ≥ 0, bit 1 — u₂·Δ ≥ 0,
  bit 2 — u₃·Δ ≥ 0, Δ = sample − centre); the pass holds with n ≥ n_min, occupied octants ≥ k_oct, holes ≥ n_holes_min.
  A block takes the first pass that holds; none — it is not estimated (NaN).
* OK: [[C, 1], [1ᵀ, 0]]·[λ; μ] = [C̄_iV; 1]; EST = λ·z, KV = C̄_VV − λ·C̄_iV − μ, LAGR = μ;
  SK: C·λ = C̄_iV; EST = m + λ·(z − m), KV = C̄_VV − λ·C̄_iV. `numpy.linalg.solve`, no other solver.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


def axes(ang1: float, ang2: float = 0.0, ang3: float = 0.0) -> np.ndarray:
    """The rows u₁, u₂, u₃ of the structure (see the module text)."""
    az, dip, t = math.radians(ang1), math.radians(ang2), math.radians(ang3)
    u1 = np.array([math.cos(dip) * math.sin(az), math.cos(dip) * math.cos(az), math.sin(dip)])
    u2 = np.array([-math.cos(az), math.sin(az), 0.0])
    u3 = np.cross(u1, u2)
    return np.array([u1, math.cos(t) * u2 + math.sin(t) * u3, -math.sin(t) * u2 + math.cos(t) * u3])


def _shape(kind: str, h: np.ndarray) -> np.ndarray:
    if kind == "spherical":
        return np.where(h < 1.0, 1.0 - 1.5 * h + 0.5 * h**3, 0.0)
    if kind == "exponential":
        return np.exp(-3.0 * h)
    if kind == "gaussian":
        return np.exp(-3.0 * h * h)
    raise ValueError(kind)


class Covariance:
    """C and C* of a nested model `{c0, structures: [{type, c, a1, a2, a3, ang1, ang2, ang3}]}` (own angles of
    every structure)."""

    def __init__(self, model: Mapping[str, Any]) -> None:
        self.c0 = float(model.get("c0", 0.0))
        self.parts = []
        for s in model["structures"]:
            r = axes(s.get("ang1", 0.0), s.get("ang2", 0.0), s.get("ang3", 0.0))
            a = np.array([s["a1"], s.get("a2", s["a1"]), s.get("a3", s["a1"])], dtype=np.float64)
            self.parts.append((s["type"], float(s["c"]), r, a))
        self.sill = self.c0 + sum(p[1] for p in self.parts)

    def cstar(self, d: np.ndarray) -> np.ndarray:
        """C* of the lag vectors d[..., 3]: no nugget anywhere."""
        out = np.zeros(d.shape[:-1])
        for kind, c, r, a in self.parts:
            q = (d @ r.T) / a
            out += c * _shape(kind, np.sqrt((q * q).sum(axis=-1)))
        return out

    def cov(self, d: np.ndarray) -> np.ndarray:
        """C of the lag vectors d[..., 3]: the sill at Δ = 0."""
        return np.where(np.all(d == 0.0, axis=-1), self.sill, self.cstar(d))


def disc_offsets(size: Sequence[float], disc: Sequence[int], phi_deg: float = 0.0) -> np.ndarray:
    """The centres of the sub-cells as offsets from the centre of the block, turned by φ about +Z."""
    grids = [((np.arange(n) + 0.5) / n - 0.5) * s for n, s in zip(disc, size, strict=True)]
    gx, gy, gz = np.meshgrid(*grids, indexing="ij")
    u = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
    p = math.radians(phi_deg)
    c, s = math.cos(p), math.sin(p)
    return np.stack([c * u[:, 0] - s * u[:, 1], s * u[:, 0] + c * u[:, 1], u[:, 2]], axis=1)


def select(xyz: np.ndarray, hole: np.ndarray, centre: np.ndarray, angles: Sequence[float], radii: Sequence[float],
           passes: Sequence[Mapping[str, Any]]) -> tuple[int, np.ndarray, int, int]:  # fmt: skip
    """The first pass that holds → (its number from 1, the numbers of the selected samples in the order of the walk,
    the occupied octants, the holes); (0, [], 0, 0) when none holds. Brute force over all the samples."""
    r = axes(*angles)
    stretch = np.array([1.0, radii[0] / radii[1], radii[0] / radii[2]])
    d = xyz - centre
    along = d @ r.T  # the axes of the ellipsoid
    h = np.sqrt(((along * stretch) ** 2).sum(axis=1))
    octant = (along >= 0.0).astype(np.int64) @ np.array([1, 2, 4])
    for k, p in enumerate(passes):
        inside = np.flatnonzero(h <= radii[0] * p.get("factor", k + 1.0))
        order = inside[np.lexsort((inside, h[inside]))]  # (h, number)
        n_max, n_hole, n_oct = p.get("n_max", 24), p.get("n_hole", 0), p.get("n_oct", 0)
        chosen: list[int] = []
        per_hole: dict[int, int] = {}
        per_oct = [0] * 8
        for i in order:
            if len(chosen) >= n_max:
                break
            if n_hole > 0 and per_hole.get(int(hole[i]), 0) >= n_hole:
                continue
            if n_oct > 0 and per_oct[octant[i]] >= n_oct:
                continue
            chosen.append(int(i))
            per_hole[int(hole[i])] = per_hole.get(int(hole[i]), 0) + 1
            per_oct[octant[i]] += 1
        occupied = sum(1 for v in per_oct if v)
        if (len(chosen) >= p.get("n_min", 1) and occupied >= p.get("k_oct", 0)
                and len(per_hole) >= p.get("n_holes_min", 0)):  # fmt: skip
            return k + 1, np.array(chosen, dtype=np.int64), occupied, len(per_hole)
    return 0, np.empty(0, dtype=np.int64), 0, 0


def krige_blocks(xyz: np.ndarray, z: np.ndarray, hole: np.ndarray, centres: np.ndarray, *, model: Mapping[str, Any],
                 kind: str, size: Sequence[float], disc: Sequence[int], phi_deg: float = 0.0,
                 angles: Sequence[float], radii: Sequence[float], passes: Sequence[Mapping[str, Any]],
                 mean: float | None = None) -> dict[str, np.ndarray]:  # fmt: skip
    """OK or SK of every block of `centres` [m, 3] (the module text). Samples in the order the caller gives (the
    number of a sample is its position). Returns EST, KV, LAGR (OK), PASS, NS, NH, NOCT; NaN / 0 — not estimated."""
    cov = Covariance(model)
    offs = disc_offsets(size, disc, phi_deg)
    n_disc = offs.shape[0]
    cvv = cov.sill if n_disc == 1 else float(cov.cstar(offs[:, None, :] - offs[None, :, :]).mean())
    m = centres.shape[0]
    out = {k: np.full(m, np.nan) for k in ("EST", "KV", "LAGR")}
    out.update({k: np.zeros(m, dtype=np.int64) for k in ("PASS", "NS", "NH", "NOCT")})
    for b in range(m):
        k, idx, noct, nholes = select(xyz, hole, centres[b], angles, radii, passes)
        if k == 0:
            continue
        p = xyz[idx]
        n = idx.shape[0]
        k_mat = cov.cov(p[:, None, :] - p[None, :, :])
        if n_disc == 1:
            rhs = cov.cov(p - centres[b])
        else:
            rhs = cov.cstar(p[:, None, :] - (centres[b] + offs)[None, :, :]).mean(axis=1)
        if kind == "ok":
            a = np.zeros((n + 1, n + 1))
            a[:n, :n] = k_mat
            a[:n, n] = a[n, :n] = 1.0
            sol = np.linalg.solve(a, np.append(rhs, 1.0))
            lam, mu = sol[:n], float(sol[n])
            out["EST"][b] = lam @ z[idx]
            out["KV"][b] = cvv - lam @ rhs - mu
            out["LAGR"][b] = mu
        else:
            assert mean is not None
            lam = np.linalg.solve(k_mat, rhs)
            out["EST"][b] = mean + lam @ (z[idx] - mean)
            out["KV"][b] = cvv - lam @ rhs
        out["PASS"][b], out["NS"][b], out["NH"][b], out["NOCT"][b] = k, n, nholes, noct
    return out
