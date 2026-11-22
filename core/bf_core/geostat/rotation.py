"""The rotation of the anisotropy ellipsoid: the angles of GSLIB ↔ the axes (ТЗ 8.6.3 `setrot`; ADR 0022 §6).
Plain numpy and math, float64; no numba. The 3D window mirrors it in `ui/src/viz/ellipsoid/frame.ts`; both read
the shared answers `core/tests/data/variography/frames.json` (`rules` there say how every number is computed).

Angles (degrees). ang1 — the azimuth of the major axis a1, clockwise from north, [0; 360); ang2 — its dip,
[−90; +90], negative — the axis goes down (the «plunge» of the window is p = −ang2); ang3 — the turn about the
major axis, (−180; +180]. X east, Y north, Z up.

    a = 90° − ang1 (0 ≤ ang1 < 270°, else 450° − ang1)    b = −ang2    t = ang3
    R = [[  cos b·cos a,                       cos b·sin a,                      −sin b      ],
         [ −cos t·sin a + sin t·sin b·cos a,   cos t·cos a + sin t·sin b·sin a,   sin t·cos b ],
         [  sin t·sin a + cos t·sin b·cos a,  −sin t·cos a + cos t·sin b·sin a,   cos t·cos b ]]

The rows of R are the axes a1, a2, a3 in the world; u₃ = u₁ × u₂. cos a = sin ang1 and sin a = cos ang1 are
taken from the sin and cos of ang1 itself (no subtraction: the major axis is the direction of a variogram
(cos p·sin Az, cos p·cos Az, −sin p) bit for bit), sin b = −sin ang2. sin and cos are exact on the multiples of
90° (`sections.geometry.sin_cos_deg`, `sinCosDegCore` of the window): zero angles give (0; 1; 0), (−1; 0; 0),
(0; 0; 1) (Т8.6-3); a −0.0 component is written 0.0.

    h_r = ‖diag(1, a1/a2, a1/a3)·R·Δ‖ / a1 = √((u₁·Δ / a1)² + (u₂·Δ / a2)² + (u₃·Δ / a3)²)

`anisotropy_matrix` is A = diag(1, a1/a2, a1/a3)·R — the matrix of a trend (8.8, 8.9); `ellipsoid_matrix` turns
the unit sphere into the ellipsoid (its columns are a_i·u_i): A·E = a1·I.

Back (axes → angles): ang2 = atan2(u₁z, ρ), ρ = hypot(u₁x, u₁y) (asin without its loss of digits near ±90°);
|u₁z| ≤ 1 − 1e−12: ang1 = atan2(u₁x, u₁y), ang3 = atan2(u₂z, u₃z). Otherwise the major axis is vertical, the
azimuth and the turn are one rotation: ang1 stays what the caller holds (the window keeps three numbers, not a
matrix) and ang3 is read from u₂ — ang1 + atan2(−u₂y, −u₂x) looking up, atan2(u₂y, −u₂x) − ang1 looking down.

The second notation — the plane of the axes a1–a2 as «dip / dip direction / pitch» (вопрос № 53). The normal
u₃ by the rules of ADR 0021 §2: the upper one; of a vertical plane (|n_z| ≤ 1e−12) — the one with the dip
direction in [0°; 180°); a dip below 1e−6° has the dip direction 0. The strike s = (−cos Aп, sin Aп, 0) looks
along the azimuth Aп − 90°, the line of dip is d = s × n = (cos α·sin Aп, cos α·cos Aп, −sin α); the pitch is
the angle in the plane from s towards d to the major axis, [0°; 180°) (a rounding error below 180° is 0°). Back,
the major axis is u₁ = cos pitch·s + sin pitch·d — it looks down — and u₃ = n: the same ellipsoid (every axis
is the same line), maybe other angles than it was made of.

    normalize_angles(ang1, ang2=0, ang3=0) -> (ang1, ang2, ang3)
    rotation_matrix(ang1, ang2=0, ang3=0) -> R 3×3, rows u₁, u₂, u₃
    angles_of(axes, ang1=0) -> (ang1, ang2, ang3)          `ang1` — the one to keep when the major axis is vertical
    direction(azimuth, plunge=0) -> u                       the direction of a variogram: the row u₁ of (Az, −p, 0)
    anisotropy_matrix(ang1, ang2, ang3, a1, a2, a3) -> A    ellipsoid_matrix(…) -> E
    plane_of(ang1, ang2=0, ang3=0) -> (dip, dip_direction, pitch)
    angles_of_plane(dip, dip_direction, pitch) -> (ang1, ang2, ang3)

Errors — `StatsError {code, message, hint}` with `param` (API — 422, `stats/routes_charts.run`): `bad_param`.
The class is imported when an error is made: `stats.weighted` brings numba, this module does not.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.sections.geometry import sin_cos_deg

VERTICAL = 1.0 - 1e-12  # |u₁z| above it: the major axis is vertical
PLANE_VERTICAL = 1e-12  # |n_z| of a vertical plane (ADR 0021 §2)
LEVEL_DEG = 1e-6  # degrees: a plane flatter than this has no dip direction (ADR 0021 §2)
PITCH_EPS = 1e-9  # degrees: a pitch this close below 180° is 0°


def geostat_error(code: str, message: str, hint: str, param: str | None = None) -> ValueError:
    """The refusal of a geostatistics kernel: `StatsError {code, message, hint}` with `param`."""
    from bf_core.stats.weighted import StatsError

    return StatsError(code, message, hint, param=param)


def bad_param(param: str, message: str, hint: str) -> ValueError:
    return geostat_error("bad_param", message, hint, param)


def number(value: Any, param: str, label: str) -> float:
    """A finite number of a request, or `bad_param`."""
    try:
        x = math.nan if isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        x = math.nan
    if not math.isfinite(x):
        raise bad_param(param, f"{label}: ожидается число.", "Проверьте введённое значение.")
    return x


def positive(value: Any, param: str, label: str) -> float:
    """A finite number above zero (a range, a ratio)."""
    x = number(value, param, label)
    if x <= 0.0:
        raise bad_param(param, f"{label}: значение должно быть больше нуля.", "Задайте положительное число.")
    return x


def _turn(deg: float, period: float = 360.0) -> float:
    """[0; period): an angle already in range is returned unchanged (fmod, +period only when negative)."""
    r = math.fmod(deg, period)
    if r < 0.0:
        r += period
        if r >= period:  # a tiny negative angle rounds to the period
            r = 0.0
    return r + 0.0


def _half_turn(deg: float) -> float:
    """(−180; 180]; an angle already in range is returned unchanged."""
    if -180.0 < deg <= 180.0:
        return deg + 0.0
    d = _turn(deg)
    return d - 360.0 if d > 180.0 else d


def _atan2_deg(y: float, x: float) -> float:
    """atan2 in degrees, (−180; 180]; exact 0, ±90, 180 when an argument is zero (0 for both)."""
    if y == 0.0:
        return 0.0 if x >= 0.0 else 180.0
    if x == 0.0:
        return 90.0 if y > 0.0 else -90.0
    return math.degrees(math.atan2(y, x))


# --- angles → axes ------------------------------------------------------------------------------------------------


def normalize_angles(ang1: Any, ang2: Any = 0.0, ang3: Any = 0.0) -> tuple[float, float, float]:
    """The angles in their ranges: ang1 and ang3 are turned by whole circles, an ang2 beyond ±90° is refused
    (it would be another azimuth)."""
    a = number(ang1, "ang1", "Азимут большой оси (ang1)")
    b = number(ang2, "ang2", "Наклон большой оси (ang2)")
    c = number(ang3, "ang3", "Поворот вокруг большой оси (ang3)")
    if abs(b) > 90.0:
        raise bad_param(
            "ang2",
            "Наклон большой оси (ang2) должен быть от −90° до +90°.",
            "Погружение вниз — отрицательный ang2 (погружение = −ang2).",
        )
    return _turn(a) + 0.0, b + 0.0, _half_turn(c) + 0.0


def _matrix(a: float, b: float, c: float) -> np.ndarray:
    s1, c1 = sin_cos_deg(a)
    s2, c2 = sin_cos_deg(b)
    s3, c3 = sin_cos_deg(c)
    rows = [
        [c2 * s1, c2 * c1, s2],
        [-c3 * c1 - s3 * s2 * s1, c3 * s1 - s3 * s2 * c1, s3 * c2],
        [s3 * c1 - c3 * s2 * s1, -s3 * s1 - c3 * s2 * c1, c3 * c2],
    ]
    return np.array(rows, dtype=np.float64) + 0.0  # −0.0 → 0.0


def rotation_matrix(ang1: Any, ang2: Any = 0.0, ang3: Any = 0.0) -> np.ndarray:
    """R of ТЗ 8.6.3, 3×3: its rows are the axes a1, a2, a3 in the world."""
    return _matrix(*normalize_angles(ang1, ang2, ang3))


def direction(azimuth: Any, plunge: Any = 0.0) -> np.ndarray:
    """The direction of a variogram u = (cos p·sin Az, cos p·cos Az, −sin p): the azimuth clockwise from north,
    the plunge p ∈ [−90°; 90°] positive down (ADR 0022 §3) — the major axis of the angles (Az, −p, 0), bit for bit."""
    az = number(azimuth, "azimuth", "Азимут направления")
    p = number(plunge, "plunge", "Погружение направления")
    if abs(p) > 90.0:
        raise bad_param("plunge", "Погружение направления должно быть от −90° до +90°.", "Вниз — положительное.")
    s1, c1 = sin_cos_deg(_turn(az) + 0.0)
    s2, c2 = sin_cos_deg(-p + 0.0)
    return np.array([c2 * s1, c2 * c1, s2], dtype=np.float64) + 0.0


def _ranges(a1: Any, a2: Any, a3: Any) -> tuple[float, float, float]:
    return (
        positive(a1, "a1", "Дальность a1"),
        positive(a2, "a2", "Дальность a2"),
        positive(a3, "a3", "Дальность a3"),
    )


def anisotropy_matrix(ang1: Any, ang2: Any, ang3: Any, a1: Any, a2: Any, a3: Any) -> np.ndarray:
    """A = diag(1, a1/a2, a1/a3)·R (ТЗ 8.6.3): h_r = ‖A·Δ‖ / a1. Only the ratios of the ranges matter — the
    matrix of a trend (ADR 0022 §10) is this with a1 = 1, a2 and a3 — its ratios."""
    r1, r2, r3 = _ranges(a1, a2, a3)
    r = rotation_matrix(ang1, ang2, ang3)
    return r * np.array([[1.0], [r1 / r2], [r1 / r3]])


def ellipsoid_matrix(ang1: Any, ang2: Any, ang3: Any, a1: Any, a2: Any, a3: Any) -> np.ndarray:
    """E = Rᵀ·diag(a1, a2, a3): the unit sphere → the ellipsoid, world = centre + E·s; the columns are a_i·u_i."""
    r1, r2, r3 = _ranges(a1, a2, a3)
    r = rotation_matrix(ang1, ang2, ang3)
    return np.ascontiguousarray(r.T * np.array([r1, r2, r3]))


# --- axes → angles ------------------------------------------------------------------------------------------------


def _frame(axes: Any) -> np.ndarray:
    try:
        m = np.asarray(axes, dtype=np.float64)
    except (TypeError, ValueError):
        m = np.empty(0)
    if m.shape != (3, 3) or not np.isfinite(m).all() or not m[0].any():
        raise bad_param("axes", "Оси эллипсоида: ожидаются три вектора из трёх чисел.", "Проверьте оси a1–a3.")
    return m


def angles_of(axes: Any, ang1: Any = 0.0) -> tuple[float, float, float]:
    """(ang1, ang2, ang3) of the axes (3×3, the rows a1, a2, a3) — the rule of the module docstring. The major
    axis need not be of unit length; when it is vertical, `ang1` is kept and the whole turn goes to ang3."""
    m = _frame(axes)
    scale = float(np.abs(m[0]).max())
    x, y, z = (float(v) / scale for v in m[0])
    length = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / length, y / length, z / length
    b = _atan2_deg(z, math.hypot(x, y))
    if abs(z) <= VERTICAL:
        a = _atan2_deg(x, y)
        if a < 0.0:
            a += 360.0
        if a >= 360.0:
            a = 0.0
        c = _atan2_deg(float(m[1][2]), float(m[2][2]))
    else:
        a = _turn(number(ang1, "ang1", "Азимут большой оси (ang1)"))
        ux, uy = float(m[1][0]), float(m[1][1])
        c = _half_turn(a + _atan2_deg(-uy, -ux) if z > 0.0 else _atan2_deg(uy, -ux) - a)
    if c <= -180.0:
        c = 180.0
    return a + 0.0, b + 0.0, c + 0.0


# --- the second notation: dip / dip direction / pitch ---------------------------------------------------------------


def _plane_axes(dip: float, dipdir: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(the strike s, the line of dip d = s × n, the upper normal n) of a plane."""
    sd, cd = sin_cos_deg(dip)
    sa, ca = sin_cos_deg(dipdir)
    return (
        np.array([-ca, sa, 0.0]) + 0.0,
        np.array([cd * sa, cd * ca, -sd]) + 0.0,
        np.array([sd * sa, sd * ca, cd]) + 0.0,
    )


def plane_of(ang1: Any, ang2: Any = 0.0, ang3: Any = 0.0) -> tuple[float, float, float]:
    """(dip α, dip direction Aп, pitch) of the plane of the axes a1–a2: α ∈ [0; 90], Aп ∈ [0; 360) (a vertical
    plane — [0; 180), a level one — 0), the pitch ∈ [0; 180) from the strike Aп − 90° towards the line of dip."""
    r = rotation_matrix(ang1, ang2, ang3)
    x, y, z = (float(v) for v in r[2])
    vertical = abs(z) <= PLANE_VERTICAL
    if (x < 0.0 or (x == 0.0 and y < 0.0)) if vertical else z < 0.0:
        x, y, z = -x, -y, -z
    dip = _atan2_deg(math.hypot(x, y), abs(z))
    dipdir = _atan2_deg(x, y)
    if dipdir < 0.0:
        dipdir += 360.0
    if dipdir >= 360.0 or (vertical and dipdir >= 180.0) or dip < LEVEL_DEG:
        dipdir = 0.0
    strike, down, _ = _plane_axes(dip, dipdir)
    u = r[0]
    pitch = _atan2_deg(
        u[0] * down[0] + u[1] * down[1] + u[2] * down[2], u[0] * strike[0] + u[1] * strike[1] + u[2] * strike[2]
    )
    if pitch < 0.0:
        pitch += 180.0
    if pitch >= 180.0 - PITCH_EPS:
        pitch = 0.0
    return dip + 0.0, dipdir + 0.0, pitch + 0.0


def angles_of_plane(dip: Any, dip_direction: Any, pitch: Any = 90.0) -> tuple[float, float, float]:
    """The angles of GSLIB of the plane «dip / dip direction» with the major axis at `pitch` from the strike:
    the axis looks down (ang2 ≤ 0), the normal a3 — up. A vertical major axis takes ang1 = the dip direction."""
    alpha = number(dip, "dip", "Угол падения")
    if not 0.0 <= alpha <= 90.0:
        raise bad_param("dip", "Угол падения должен быть от 0° до 90°.", "Сторону падения задаёт азимут падения.")
    dipdir = _turn(number(dip_direction, "dip_direction", "Азимут падения")) + 0.0
    p = _turn(number(pitch, "pitch", "Склонение"), 180.0) + 0.0
    strike, down, normal = _plane_axes(alpha + 0.0, dipdir)
    sp, cp = sin_cos_deg(p)
    u1 = (cp * strike + sp * down) + 0.0
    return angles_of(np.array([u1, np.cross(normal, u1) + 0.0, normal]), dipdir)
