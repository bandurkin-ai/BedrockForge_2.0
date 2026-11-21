"""True thickness: the formulas of ТЗ 8.5.1 and the angles of ТЗ 8.0.1 (ADR 0021 §2). numpy, float64; no numba.

Conventions (8.0.1). X east, Y north, Z up; azimuths clockwise from north, degrees, [0; 360).
  plane   dip α ∈ [0°; 90°], dip direction Aп; the upper normal n = (sin α·sin Aп, sin α·cos Aп, cos α)
  hole    zenith θ ∈ [0°; 180°] (0° — down, 90° — level, above 90° — a rising hole), azimuth Aс;
          the direction down the hole d = (sin θ·sin Aс, sin θ·cos Aс, −cos θ). The inclination of a survey δ
          (−90° — down): θ = 90° + δ

    plane_normal(alpha, dipdir) -> n [..., 3]        angles missing or out of range — NaN, never a zero
    hole_direction(theta, azimuth) -> d [..., 3]
    upper_normal(n) -> unit n [..., 3], n_z ≥ 0      any vector along the normal, of any length and side
    plane_angles(n) -> alpha, dipdir                 the way back; `hole_angles(d) -> theta, azimuth`
    tt_vector(length, n, d)                          m = l·|n·d| — what every calculation uses
    tt_expanded(length, alpha, dipdir, theta, azimuth)     m = l·|cos α·cos θ − sin α·sin θ·cos(Aп − Aс)|
    tt_leontovsky(length, alpha, dipdir, theta, azimuth)   m = l·|sin α·cos β·cos γ − cos α·sin β|,
                                                           β = 90° − θ, γ = Aс − Aп
    is_parallel(tt, length, eps=0.1)                 TT / l < ε_в — the hole runs almost along the plane (bit 1)
    attitude(alpha, dipdir) -> (α, Aп)               numbers of a request; `bad_param` out of range, 360 → 0

The two longer forms are there for the test Т8.5-6 and for the hint of the window; γ of Leontovsky's formula is
counted from the dip direction here (in some sources — from the strike, then it is written with sin γ).

The way back. n_z < 0 → −n; α = arccos n_z, taken as atan2(√(n_x² + n_y²), n_z) — the same angle without the
loss of digits arccos has near 0° (and arcsin near 90°); Aп = atan2(n_x, n_y) mod 360; α < 1e−6° → Aп = 0. A
vertical plane (|n_z| ≤ 1e−12) has two upper normals: the one with Aп ∈ [0°; 180°) is taken, so the result
does not depend on the side of the normal given (the order of the corners of a triangle). For a hole:
θ = atan2(√(d_x² + d_y²), −d_z), Aс = atan2(d_x, d_y) mod 360; a level part below 1e−12 → Aс = 0.

Missing values. NaN in — NaN out: an interval without angles has no orientation, and its thickness is
missing, not zero.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from . import EPS_DEFAULT, EPS_RANGE, STEP_DEFAULT, STEP_RANGE  # noqa: F401 - ε_в, the part of a hole (ТЗ 8.5.4)

FLAG_PARALLEL = 1  # TT_FLAG: the hole runs almost along the body, TT / l < ε_в (ТЗ 8.5.1)
FLAG_NO_ORIENTATION = 2  # no source gave an orientation; TT is missing
FLAG_CORE_LOCAL = 4  # a core measurement was not turned into a world normal; ALPHA and DIPDIR are missing
FLAG_NO_PATH = 8  # the hole has no trajectory; TT is missing

LEVEL_DEG = 1e-6  # degrees: a plane flatter than this has no dip direction (Aп = 0)
VERTICAL = 1e-12  # |n_z| of a vertical plane; the level part of d below which a hole has no azimuth


def _float(x: Any) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)


def _unit_vector(a: np.ndarray, b: np.ndarray, zenith: np.ndarray) -> np.ndarray:
    """(sin a·sin b, sin a·cos b, zenith): a — the angle from the vertical, b — the azimuth, radians."""
    s = np.sin(a)
    return np.stack([s * np.sin(b), s * np.cos(b), zenith], axis=-1) + 0.0  # −0.0 → 0.0


def _angles(first: Any, second: Any, top: float) -> tuple[np.ndarray, np.ndarray]:
    """The two angles in radians, broadcast; NaN where one is missing, the first is outside [0; top] or the
    azimuth is outside [0; 360] (360 is 0)."""
    a, b = np.broadcast_arrays(_float(first), _float(second))
    ok = (a >= 0.0) & (a <= top) & (b >= 0.0) & (b <= 360.0)  # NaN fails
    return np.radians(np.where(ok, a, np.nan)), np.radians(np.where(ok & (b < 360.0), b, np.where(ok, 0.0, np.nan)))


def plane_normal(alpha: Any, dipdir: Any) -> np.ndarray:
    """The upper normal of planes [..., 3] from the dip α ∈ [0; 90] and the dip direction Aп ∈ [0; 360],
    degrees. Missing or out of range — NaN."""
    a, b = _angles(alpha, dipdir, 90.0)
    return _unit_vector(a, b, np.cos(a))


def hole_direction(theta: Any, azimuth: Any) -> np.ndarray:
    """The direction down the hole [..., 3] from the zenith θ ∈ [0; 180] and the azimuth Aс ∈ [0; 360],
    degrees. Missing or out of range — NaN."""
    a, b = _angles(theta, azimuth, 180.0)
    return _unit_vector(a, b, -np.cos(a))


def _normalized(v: Any) -> np.ndarray:
    """Unit vectors [..., 3]; NaN for a zero or non-finite vector."""
    v = _float(v)
    if v.shape[-1:] != (3,):
        raise ValueError(f"vectors must have 3 components, got the shape {v.shape}")
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        scale = np.abs(v).max(axis=-1, keepdims=True)  # first by the largest component: no overflow
        w = v / scale
        norm = np.sqrt((w * w).sum(axis=-1, keepdims=True))
        ok = np.isfinite(scale) & (scale > 0.0)
        return np.where(ok, w / norm, np.nan)


def _azimuth(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """atan2(x, y) in degrees, [0; 360)."""
    az = np.degrees(np.arctan2(x, y)) % 360.0
    return np.where(az >= 360.0, 0.0, az)  # −1e−17 mod 360 rounds to 360


def upper_normal(n: Any) -> np.ndarray:
    """The unit normal of the upper side [..., 3]: n_z ≥ 0; of a vertical plane (|n_z| ≤ 1e−12) — the one
    whose azimuth is in [0°; 180°). The same for n and −n, bit for bit. NaN for a zero or missing vector."""
    u = _normalized(n)
    x, y, z = u[..., 0], u[..., 1], u[..., 2]
    with np.errstate(invalid="ignore"):
        west = (x < 0.0) | ((x == 0.0) & (y < 0.0))  # exact: both sides of a vertical plane agree on it
        south = np.degrees(np.arctan2(np.where(west, -x, x), np.where(west, -y, y))) >= 180.0  # 180 by rounding
        flip = np.where(np.abs(z) <= VERTICAL, west ^ south, z < 0.0)
    return np.where(flip[..., None], -u, u) + 0.0  # −0.0 → 0.0


def plane_angles(n: Any) -> tuple[np.ndarray, np.ndarray]:
    """The dip α ∈ [0; 90] and the dip direction Aп ∈ [0; 360), degrees, of planes by their normals (of any
    length and side). α < 1e−6° → Aп = 0; a vertical plane → Aп ∈ [0; 180). NaN for a zero or missing vector."""
    u = upper_normal(n)
    x, y, z = u[..., 0], u[..., 1], u[..., 2]
    alpha = np.degrees(np.arctan2(np.hypot(x, y), np.abs(z)))
    dipdir = _azimuth(x, y)
    with np.errstate(invalid="ignore"):
        dipdir = np.where((np.abs(z) <= VERTICAL) & (dipdir >= 180.0), 0.0, dipdir)
        dipdir = np.where(alpha < LEVEL_DEG, 0.0, dipdir)
    return alpha, dipdir


def hole_angles(d: Any) -> tuple[np.ndarray, np.ndarray]:
    """The zenith θ ∈ [0; 180] and the azimuth Aс ∈ [0; 360), degrees, of directions down the hole (of any
    length). A level part below 1e−12 → Aс = 0. NaN for a zero or missing vector."""
    u = _normalized(d)
    x, y, z = u[..., 0], u[..., 1], u[..., 2]
    level = np.hypot(x, y)
    theta = np.degrees(np.arctan2(level, -z))
    with np.errstate(invalid="ignore"):
        azimuth = np.where(level < VERTICAL, 0.0, _azimuth(x, y))
    return theta, np.where(np.isnan(theta), np.nan, azimuth)


def tt_vector(length: Any, n: Any, d: Any) -> np.ndarray:
    """m = l·|n·d| — the vector form (n, d — unit vectors [..., 3])."""
    n, d = _float(n), _float(d)
    dot = n[..., 0] * d[..., 0] + n[..., 1] * d[..., 1] + n[..., 2] * d[..., 2]
    return _float(length) * np.abs(dot)


def tt_expanded(length: Any, alpha: Any, dipdir: Any, theta: Any, azimuth: Any) -> np.ndarray:
    """m = l·|cos α·cos θ − sin α·sin θ·cos(Aп − Aс)| — the expanded form; angles in degrees."""
    a, t = np.radians(_float(alpha)), np.radians(_float(theta))
    turn = np.radians(_float(dipdir) - _float(azimuth))
    return _float(length) * np.abs(np.cos(a) * np.cos(t) - np.sin(a) * np.sin(t) * np.cos(turn))


def tt_leontovsky(length: Any, alpha: Any, dipdir: Any, theta: Any, azimuth: Any) -> np.ndarray:
    """m = l·|sin α·cos β·cos γ − cos α·sin β|, β = 90° − θ — the inclination of the hole to the level,
    γ = Aс − Aп — Leontovsky's formula; angles in degrees."""
    a, b = np.radians(_float(alpha)), np.radians(90.0 - _float(theta))
    g = np.radians(_float(azimuth) - _float(dipdir))
    return _float(length) * np.abs(np.sin(a) * np.cos(b) * np.cos(g) - np.cos(a) * np.sin(b))


def is_parallel(tt: Any, length: Any, eps: float = EPS_DEFAULT) -> np.ndarray:
    """TT / l < ε_в: the hole runs almost along the plane (bit `FLAG_PARALLEL`). A missing TT — False."""
    with np.errstate(invalid="ignore"):
        return _float(tt) < float(eps) * _float(length)


def _angle(raw: Any, top: float, label: str, param: str) -> float:
    try:
        value = float("nan") if isinstance(raw, bool) else float(raw)
    except (TypeError, ValueError):
        value = float("nan")
    if not 0.0 <= value <= top:  # NaN fails
        from bf_core.composites.intervals import CompositeError  # numba comes with it: not at import

        span = f"от 0 до {top:g}°"
        raise CompositeError(
            "bad_param", f"{label}: нужно число {span}.", f"Задайте значение в диапазоне {span}.", param=param
        )
    return value


def attitude(
    alpha: Any, dipdir: Any, *, alpha_param: str = "alpha", dipdir_param: str = "dipdir"
) -> tuple[float, float]:
    """α ∈ [0; 90] and Aп ∈ [0; 360) of a request as numbers (360 is 0), or CompositeError `bad_param` with
    the parameter at fault (API — 422)."""
    a = _angle(alpha, 90.0, "Угол падения α", alpha_param)
    b = _angle(dipdir, 360.0, "Азимут падения Aп", dipdir_param)
    return a, 0.0 if b == 360.0 else b
