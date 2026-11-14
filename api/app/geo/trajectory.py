"""Drillhole trajectories by the minimum curvature method.

Conventions (Micromine): azimuth is measured clockwise from north in degrees,
dip is negative downwards (-90 = vertical down). Coordinates are X east,
Y north, Z elevation (up), so a hole going down loses Z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _direction(azimuth_deg: np.ndarray, dip_deg: np.ndarray) -> np.ndarray:
    az, dip = np.radians(azimuth_deg), np.radians(dip_deg)
    return np.column_stack([np.sin(az) * np.cos(dip), np.cos(az) * np.cos(dip), np.sin(dip)])


def _ratio_factor(beta: np.ndarray) -> np.ndarray:
    """2/beta * tan(beta/2), with the limit 1 for a straight segment."""
    out = np.ones_like(beta)
    bent = beta > 1e-9
    out[bent] = 2.0 / beta[bent] * np.tan(beta[bent] / 2.0)
    return out


def _angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.arccos(np.clip((a * b).sum(axis=1), -1.0, 1.0))


@dataclass
class Trajectory:
    hole_id: str
    md: np.ndarray  # measured depth of the stations, ascending, md[0] == 0
    xyz: np.ndarray  # station positions (n, 3)
    dirs: np.ndarray  # unit direction at each station (n, 3)
    source: str  # "survey" | "collar" | "vertical"

    @property
    def length(self) -> float:
        return float(self.md[-1])

    def positions(self, depths) -> np.ndarray:
        """Positions (m, 3) at the given measured depths (clipped to the hole)."""
        d = np.clip(np.asarray(depths, dtype=float), 0.0, self.md[-1])
        if len(self.md) == 1:
            return np.repeat(self.xyz[:1], len(d), axis=0)
        i = np.clip(np.searchsorted(self.md, d, side="right") - 1, 0, len(self.md) - 2)
        t1, t2 = self.dirs[i], self.dirs[i + 1]
        seg = self.md[i + 1] - self.md[i]
        f = (d - self.md[i]) / seg
        beta = _angle(t1, t2)

        # Direction at depth d: spherical interpolation between the two stations.
        sin_beta = np.sin(beta)
        straight = sin_beta < 1e-9
        safe = np.where(straight, 1.0, sin_beta)
        w1 = np.where(straight, 1.0 - f, np.sin((1.0 - f) * beta) / safe)
        w2 = np.where(straight, f, np.sin(f * beta) / safe)
        td = w1[:, None] * t1 + w2[:, None] * t2
        td /= np.linalg.norm(td, axis=1, keepdims=True)

        # Minimum-curvature displacement from station i to depth d.
        length = d - self.md[i]
        rf = _ratio_factor(_angle(t1, td))
        return self.xyz[i] + (length / 2.0 * rf)[:, None] * (t1 + td)


def build_trajectory(
    hole_id: str,
    x: float | None,
    y: float | None,
    z: float | None,
    td: float | None,
    collar_azimuth: float | None,
    collar_dip: float | None,
    stations: list[tuple[float | None, float | None, float | None]],
) -> Trajectory | None:
    """Trajectory from the collar and survey stations (depth, azimuth, dip).

    Unusable stations (empty or out-of-range values, duplicate depths) are
    skipped; the audit reports them. Without stations the collar angles are
    used, and without those the hole is taken as vertical. The path is
    extended to TD with the last orientation. Returns None without X/Y/Z.
    """
    if any(v is None or not math.isfinite(v) for v in (x, y, z)):
        return None

    valid: dict[float, tuple[float, float]] = {}
    for depth, az, dip in stations:
        if None in (depth, az, dip) or not all(math.isfinite(v) for v in (depth, az, dip)):
            continue
        if depth < 0 or not 0 <= az <= 360 or not -90 <= dip <= 90:
            continue
        valid.setdefault(float(depth), (float(az), float(dip)))

    if valid:
        source = "survey"
        rows = sorted((d, *angles) for d, angles in valid.items())
    else:
        has_angles = (
            collar_azimuth is not None and collar_dip is not None
            and math.isfinite(collar_azimuth) and math.isfinite(collar_dip)
            and 0 <= collar_azimuth <= 360 and -90 <= collar_dip <= 90
        )
        source = "collar" if has_angles else "vertical"
        rows = [(0.0, float(collar_azimuth) if has_angles else 0.0, float(collar_dip) if has_angles else -90.0)]

    if rows[0][0] > 0:  # first station below the collar: extend upwards
        rows.insert(0, (0.0, rows[0][1], rows[0][2]))
    end = max(rows[-1][0], float(td) if td is not None and math.isfinite(td) else 0.0)
    if end > rows[-1][0]:
        rows.append((end, rows[-1][1], rows[-1][2]))
    if len(rows) == 1:  # a hole of zero length still needs a segment to be drawn
        rows.append((0.0, rows[0][1], rows[0][2]))

    md = np.array([r[0] for r in rows])
    dirs = _direction(np.array([r[1] for r in rows]), np.array([r[2] for r in rows]))
    xyz = np.empty((len(rows), 3))
    xyz[0] = (x, y, z)
    for k in range(1, len(rows)):
        beta = _angle(dirs[k - 1:k], dirs[k:k + 1])
        rf = _ratio_factor(beta)[0]
        xyz[k] = xyz[k - 1] + (md[k] - md[k - 1]) / 2.0 * rf * (dirs[k - 1] + dirs[k])
    if len(rows) == 2 and md[1] == md[0]:
        return Trajectory(hole_id, md[:1], xyz[:1], dirs[:1], source)
    return Trajectory(hole_id, md, xyz, dirs, source)


def sample_path(traj: Trajectory, step: float) -> np.ndarray:
    """Polyline points (n, 4): x, y, z, measured depth - stations plus a regular grid."""
    if traj.length <= 0:
        return np.column_stack([traj.xyz[:1], traj.md[:1]])
    grid = np.arange(0.0, traj.length, step)
    depths = np.unique(np.concatenate([traj.md, grid, [traj.length]]))
    return np.column_stack([traj.positions(depths), depths])
