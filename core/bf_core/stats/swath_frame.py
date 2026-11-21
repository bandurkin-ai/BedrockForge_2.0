"""Swath plot: the frame of the cuboid and the layout of the swaths (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5).
Plain numpy, no kernels of its own. The 3D window mirrors it in `ui/src/viz/swath/frame.ts`; both read the
shared answers `core/tests/data/swath/frames.json` (`rules` there say how every number is computed).

Angles (degrees): the azimuth A of the axis of the swaths, clockwise from north, [0; 360); the dip D,
[−90; +90], negative — the axis goes down (as the dip of a drillhole); the roll R about the axis, (−180; +180].

    u₀ = (sin A, cos A, 0)    h = (cos A, −sin A, 0)    z = (0, 0, 1)
    u = cos D·u₀ + sin D·z    across the swaths: the axis of the plot, t = p·u
    n = −sin D·u₀ + cos D·z
    v = cos R·h + sin R·n     w = −sin R·h + cos R·n    along the swaths; u = w × v

X is (90; 0; 0), Y — (0; 0; 0), Z — (A; +90; 0): one code for the directions and the angles. sin and cos are
exact on the multiples of 90° (`sections.geometry.sin_cos_deg`, `sinCosDegCore` of the window), so an azimuth of
90° gives u = (1, 0, 0) and the swaths of X bit for bit (ТД01-5); a −0.0 component is written 0.0.

Back (axes → angles): D = asin u_z, taken as atan2(u_z, ρ), ρ = hypot(u_x, u_y) (exact near the vertical too);
|u_z| < 1 − 1e−12: A = atan2(u_x, u_y), R = atan2(v·n, v·h) = atan2(v_z·ρ² − u_z·(v_x·u_x + v_y·u_y),
v_x·u_y − v_y·u_x); otherwise the axis is vertical, the azimuth and the roll are one turn: R = 0,
A = atan2(−v_y, v_x). The angles of a frame give its axes back (1e−9, ТД01-2).

A projection p·axis is the sum of its terms in the order x, y, z from 0.0, a term with a zero component of the
axis skipped (as the sections, ADR 0016): along X it is the coordinate itself, and the centre of a cell of a
model, a[i] + b[j] + c[k] (`swath_blocks`), is projected to the same bits as a point.

Swaths: k = ⌊(t − start) / width⌋, 0 … n − 1; the far boundary of the last swath is in it; the tolerance at the
two ends is 1e−9·max(1, |start|, width). A boundary passes through `anchor`; without it the first swath starts
at the low side of the cuboid. More than `MAX_SWATHS` — `too_many_swaths` (Т7.30-1). The width when none is
given (Т7.30-4): whole parent blocks of the first model, b·⌈span / (50·b)⌉, b — its side along the axis of the
model nearest to u (`block_side`); without a model — a round number (1, 2, 2.5, 5 × 10ⁿ) not below span / 25.

    frame(azimuth, dip=0, roll=0) -> Frame       direction_frame("x" | "y" | "z" | "angles", …) -> Frame
    angles_of(u, v) -> (A, D, R)                 normalize_angles(A, D, R) -> (A, D, R)
    project(points, axis) -> t                   to_frame(f, points), to_world(f, coords) -> n×3
    bounds(f, points, sizes=None) -> 3×2         cell_half(f, size) -> 3     corners(f, limits) -> 8×3
    nice(x)   grid_sides(grid)   block_side(u, sides)   default_width(span, block)   auto_width(lo, hi, …)
    layout(lo, hi, width, anchor=None) -> (start, n)    swath_index(t, start, width, n) -> int64, −1 outside
    centres(start, width, n) -> t of the middles of the swaths

Errors — `StatsError {code, message, hint}` with `param` (API — 422): `too_many_swaths`, `bad_param`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.sections.geometry import sin_cos_deg
from bf_core.stats.weighted import StatsError

MAX_SWATHS = 2000  # ТЗ 7.30
DEFAULT_SWATHS = 50  # the default width: whole blocks, as few as give no more swaths than this
NICE_SHARE = 25.0  # without a model: a round number near 1/25 of the extent
DIRECTIONS = ("x", "y", "z", "angles")
VERTICAL = 1.0 - 1e-12  # |u_z| from it on: the axis of the swaths is vertical
TOL = 1e-9  # relative tolerance of the ends of the swaths and of the cuboid
_TIE = 1e-12  # |u_i| closer than this are one: the axis of the model nearest to u


def _bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


def _number(value: Any, param: str, label: str) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        x = math.nan
    if not math.isfinite(x):
        raise _bad(param, f"{label}: ожидается число.", "Проверьте введённое значение.")
    return x


# --- the frame -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Frame:
    """The axes of the cuboid: `u` across the swaths (the axis of the plot), `v` and `w` along them; the angles
    are normalised (azimuth [0; 360), dip [−90; 90], roll (−180; 180])."""

    azimuth: float
    dip: float
    roll: float
    u: np.ndarray
    v: np.ndarray
    w: np.ndarray

    @property
    def axes(self) -> np.ndarray:
        """3×3: the rows u, v, w."""
        return np.array([self.u, self.v, self.w])

    def as_dict(self) -> dict[str, Any]:
        """For the answer of the API and the shared fixtures."""
        return {
            "azimuth": self.azimuth,
            "dip": self.dip,
            "roll": self.roll,
            "u": [float(x) for x in self.u],
            "v": [float(x) for x in self.v],
            "w": [float(x) for x in self.w],
        }


def normalize_angles(azimuth: Any, dip: Any = 0.0, roll: Any = 0.0) -> tuple[float, float, float]:
    """The angles in their ranges: the azimuth and the roll are turned by whole circles, a dip beyond ±90° is
    refused (it would be another azimuth)."""
    a = _number(azimuth, "azimuth", "Азимут полос")
    d = _number(dip, "dip", "Наклон оси полос")
    r = _number(roll, "roll", "Поворот короба")
    if abs(d) > 90.0:
        raise _bad("dip", "Наклон оси полос должен быть от −90° до +90°.", "Отрицательный наклон — ось идёт вниз.")
    a = math.fmod(math.fmod(a, 360.0) + 360.0, 360.0)
    r = math.fmod(math.fmod(r, 360.0) + 360.0, 360.0)
    if r > 180.0:
        r -= 360.0
    return a + 0.0, d + 0.0, r + 0.0


def frame(azimuth: Any, dip: Any = 0.0, roll: Any = 0.0) -> Frame:
    """The frame of three angles (the formulas of the module docstring)."""
    a, d, r = normalize_angles(azimuth, dip, roll)
    sa, ca = sin_cos_deg(a)
    sd, cd = sin_cos_deg(d)
    sr, cr = sin_cos_deg(r)
    n = (-sd * sa, -sd * ca, cd)
    u = np.array([cd * sa, cd * ca, sd]) + 0.0
    v = np.array([cr * ca + sr * n[0], -cr * sa + sr * n[1], sr * n[2]]) + 0.0
    w = np.array([-sr * ca + cr * n[0], sr * sa + cr * n[1], cr * n[2]]) + 0.0
    return Frame(a, d, r, u, v, w)


def direction_frame(direction: str, azimuth: Any = 0.0, dip: Any = 0.0, roll: Any = 0.0) -> Frame:
    """x, y, z — the coordinate axes as angles: (90; 0; 0), (0; 0; 0) and (azimuth; +90; 0) — for z the
    azimuth turns the swaths in plan and they are counted upwards; angles — the three angles."""
    if direction == "x":
        return frame(90.0)
    if direction == "y":
        return frame(0.0)
    if direction == "z":
        return frame(azimuth, 90.0)
    if direction == "angles":
        return frame(azimuth, dip, roll)
    raise _bad("direction", f"Неизвестное направление полос «{direction}».", "Выберите X, Y, Z или «по углам».")


def _atan2_deg(y: float, x: float) -> float:
    """atan2 in degrees, (−180; 180]; exact 0, ±90, 180 when an argument is zero (0 for both)."""
    if y == 0.0:
        return 0.0 if x >= 0.0 else 180.0
    if x == 0.0:
        return 90.0 if y > 0.0 else -90.0
    return math.degrees(math.atan2(y, x))


def angles_of(u: Any, v: Any) -> tuple[float, float, float]:
    """(azimuth, dip, roll) of the axes u (across the swaths) and v — the rule of the module docstring. u need
    not be of unit length; a vertical axis gives the roll 0 and the whole turn as the azimuth."""
    a = np.asarray(u, dtype=np.float64).reshape(-1)
    b = np.asarray(v, dtype=np.float64).reshape(-1)
    if a.shape != (3,) or b.shape != (3,) or not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise _bad("frame", "Оси короба: ожидаются два вектора из трёх чисел.", "Проверьте оси u и v.")
    length = math.sqrt(float(a[0]) ** 2 + float(a[1]) ** 2 + float(a[2]) ** 2)
    if length == 0.0:
        raise _bad("frame", "Ось полос не задана: вектор нулевой.", "Задайте направление полос.")
    ux, uy, uz = (float(x) / length for x in a)
    vx, vy, vz = (float(x) for x in b)
    rho = math.hypot(ux, uy)
    dip = _atan2_deg(uz, rho)
    if abs(uz) < VERTICAL:
        azimuth = _atan2_deg(ux, uy)
        roll = _atan2_deg(vz * rho * rho - uz * (vx * ux + vy * uy), vx * uy - vy * ux)
    else:
        azimuth, roll = _atan2_deg(-vy, vx), 0.0
    if azimuth < 0.0:
        azimuth += 360.0
    if azimuth >= 360.0:
        azimuth = 0.0
    if roll <= -180.0:
        roll = 180.0
    return azimuth + 0.0, dip + 0.0, roll + 0.0


# --- projections and extents -------------------------------------------------------------------------------------


def _rows(xyz: Any) -> np.ndarray:
    a = np.asarray(xyz, dtype=np.float64)
    if a.ndim == 1 and a.size in (0, 3):
        a = a.reshape(-1, 3)
    if a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("points are an n×3 array of x, y, z")
    return a


def project(points: Any, axis: Any) -> np.ndarray:
    """p·axis of every point: the terms in the order x, y, z from 0.0, a zero component of the axis skipped."""
    p = _rows(points)
    e = np.asarray(axis, dtype=np.float64).reshape(3)
    out = np.zeros(len(p))
    for i in range(3):
        if e[i] != 0.0:
            out += p[:, i] * e[i]
    return out


def to_frame(f: Frame, points: Any) -> np.ndarray:
    """Coordinates of the points along u, v, w: n×3."""
    p = _rows(points)
    return np.column_stack([project(p, f.u), project(p, f.v), project(p, f.w)])


def to_world(f: Frame, coords: Any) -> np.ndarray:
    """Project coordinates of the points given along u, v, w: p = t_u·u + t_v·v + t_w·w (per component, in
    this order, zero components of the axes skipped)."""
    c = _rows(coords)
    out = np.zeros((len(c), 3))
    for j, axis in enumerate((f.u, f.v, f.w)):
        for i in range(3):
            if axis[i] != 0.0:
                out[:, i] += c[:, j] * axis[i]
    return out


def cell_half(f: Frame, size: Any) -> np.ndarray:
    """Half of the extent of a box with the sides `size` (dx, dy, dz; 3 or n×3) along u, v, w:
    Σ |e_i|·size_i / 2 — 3 or n×3."""
    s = np.asarray(size, dtype=np.float64)
    return 0.5 * (s @ np.abs(f.axes).T)


def bounds(f: Frame, points: Any, sizes: Any = None) -> np.ndarray:
    """The extent of points (or of boxes with these sides around them) along u, v, w: rows (min, max). Points
    without a coordinate take no part; with none left the rows are NaN."""
    p = _rows(points)
    ok = np.isfinite(p).all(axis=1)
    half: Any = 0.0
    if sizes is not None:
        s = np.asarray(sizes, dtype=np.float64)
        half = cell_half(f, s)
        if s.ndim == 2:
            ok &= np.isfinite(s).all(axis=1)
            half = half[ok]
    out = np.full((3, 2), np.nan)
    if not ok.any():
        return out
    p = p[ok]
    for i, axis in enumerate((f.u, f.v, f.w)):
        t = project(p, axis)
        h = half if np.ndim(half) == 0 else half[:, i] if np.ndim(half) == 2 else half[i]
        out[i] = (t - h).min(), (t + h).max()
    return out


def corners(f: Frame, limits: Any) -> np.ndarray:
    """The eight corners of the cuboid `limits` (rows (min, max) along u, v, w) in project coordinates, 8×3:
    corner number c takes max along u when c & 4, along v when c & 2, along w when c & 1."""
    lim = np.asarray(limits, dtype=np.float64).reshape(3, 2)
    local = np.array([[lim[0, (c >> 2) & 1], lim[1, (c >> 1) & 1], lim[2, c & 1]] for c in range(8)])
    return to_world(f, local)


# --- the width --------------------------------------------------------------------------------------------------


def nice(value: float) -> float:
    """1, 2, 2.5 or 5 times a power of ten, not below the value (1 for a value that is not positive)."""
    if not math.isfinite(value) or value <= 0.0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(value))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if m * power >= value * (1.0 - TOL):
            return m * power
    return 10.0 * power


def grid_sides(grid: Mapping[str, Any]) -> tuple[float, float, float]:
    """The sides of the parent block of a model (`meta.grid`, blocks/schema.py): its step, or the median of the
    steps of every axis when the step is variable (OMF tensors)."""
    if "tensors" in grid:
        t = grid["tensors"]
        return tuple(float(np.median(np.asarray(t[a], dtype=np.float64))) for a in ("x", "y", "z"))  # type: ignore[return-value]
    sx, sy, sz = (float(s) for s in grid["size"])
    return sx, sy, sz


def block_side(u: Any, sides: Sequence[float]) -> float:
    """The side of the block along the axis of the model nearest to `u` (the largest |u_i|); axes equally near
    (a diagonal) — the larger of their sides."""
    e = np.abs(np.asarray(u, dtype=np.float64).reshape(3))
    near = e >= e.max() - _TIE
    return max(float(s) for s, yes in zip(sides, near, strict=True) if yes)


def default_width(span: float, block: float | None = None) -> float:
    """The width when none is given: whole blocks, the fewest that give no more than 50 swaths; without a
    model — a round number not below 1/25 of the extent."""
    b = float(block) if block is not None and math.isfinite(block) and block > 0.0 else None
    if not math.isfinite(span) or span <= 0.0:
        return b or 1.0
    if b is not None:
        return b * max(1, math.ceil(span / b / DEFAULT_SWATHS - TOL))
    return nice(span / NICE_SHARE)


def auto_width(lo: float, hi: float, block: float | None = None, anchor: float | None = None) -> float:
    """The default width of the extent lo … hi as `layout` counts the swaths (its tolerance, the anchor): with
    a model — the fewest whole blocks that give no more than 50 swaths; without one — `default_width`."""
    width = default_width(hi - lo, block)
    if block is None or not (math.isfinite(block) and block > 0.0 and math.isfinite(lo) and math.isfinite(hi)):
        return width
    hi = max(lo, hi)
    blocks = max(1, round(width / block))
    while blocks > 1 and _count(lo, hi, (blocks - 1) * block, anchor)[1] <= DEFAULT_SWATHS:
        blocks -= 1
    while _count(lo, hi, blocks * block, anchor)[1] > DEFAULT_SWATHS:
        blocks += 1
    return float(blocks * block)


# --- the swaths -------------------------------------------------------------------------------------------------


def _count(lo: float, hi: float, width: float, anchor: float | None) -> tuple[float, float]:
    """The start of the first swath and the number of swaths as a float (it may be huge or inf)."""
    tol = TOL * max(1.0, abs(lo), abs(hi), width)
    start = lo if anchor is None else anchor + math.floor((lo - anchor + tol) / width) * width
    return start, max(1.0, float(np.ceil((hi - start - tol) / width)))


def layout(lo: float, hi: float, width: float, anchor: float | None = None) -> tuple[float, int]:
    """The start of the first swath and the number of swaths that cover lo … hi. A boundary passes through
    `anchor`; without it the first swath starts at `lo`. StatsError: `bad_param` (the width, the anchor, the
    extent), `too_many_swaths` (more than `MAX_SWATHS`)."""
    lo, hi = float(lo), float(hi)
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise _bad("limits", "Габариты полос не определены: нет данных с координатами.", "Проверьте ряды swath.")
    hi = max(lo, hi)
    try:
        width = float(width)
    except (TypeError, ValueError):
        width = math.nan
    if not math.isfinite(width) or width <= 0.0:
        raise _bad(
            "width",
            "Ширина полосы должна быть числом больше нуля.",
            "Задайте ширину в метрах или оставьте поле пустым — ширина подберётся сама.",
        )
    if anchor is not None:
        anchor = _number(anchor, "anchor", "Привязка полос")
    start, n = _count(lo, hi, width, anchor)
    if not n <= MAX_SWATHS:
        many = f"{int(n)}" if n < 1e9 else "больше миллиарда"
        raise StatsError(
            "too_many_swaths",
            f"Слишком много полос: {many} при ширине {width:g} м (не больше {MAX_SWATHS}).",
            "Увеличьте ширину полосы.",
            param="width",
        )
    return start, int(n)


def swath_index(t: Any, start: float, width: float, n: int) -> np.ndarray:
    """The number of the swath of every coordinate along u, −1 outside the swaths (and for NaN). The far
    boundary of the last swath is in it; the two ends have the tolerance 1e−9·max(1, |start|, width)."""
    x = np.asarray(t, dtype=np.float64)
    tol = TOL * max(1.0, abs(start), width)
    with np.errstate(invalid="ignore", over="ignore"):
        q = np.floor((x - start) / width)
        q = np.where((q >= -1.0) & (q <= n), q, -2.0)  # NaN and far values never reach the cast
    idx = q.astype(np.int64)
    idx[(idx == n) & (x <= start + n * width + tol)] = n - 1
    idx[(idx == -1) & (x >= start - tol)] = 0
    idx[(idx < 0) | (idx >= n)] = -1
    return idx


def centres(start: float, width: float, n: int) -> np.ndarray:
    """The coordinates along u of the middles of the swaths."""
    return start + (np.arange(n, dtype=np.float64) + 0.5) * width
