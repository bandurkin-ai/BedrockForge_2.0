"""Geometry of a series of sections (ТЗ 8.1.2; ADR 0016 §2), float64; no numba at import.

Axes (A — the azimuth of the line, clockwise from north; φ — the dip of the plane, 90° vertical, 0° a plan):

    ŝ = (sin A, cos A, 0)                      along the line, left to right on the screen
    v̂ = (−cos A·sin φ, sin A·sin φ, −cos φ)    the direction of view; φ = 90°: the viewer to the right of the line
    ŵ = (−cos A·cos φ, sin A·cos φ, sin φ)     = ŝ × v̂, up on the screen (φ = 90°: +Z; a plan at A = 90°: north)

sin and cos of degrees are exact on the multiples of 90° (`sin_cos_deg`, as `sinCosDeg` of the window); a −0.0
component is written 0.0. A plan is φ = 0 (v̂ = (0, 0, −1)): P1.z is its top level, A = 90° unless given. A
vertical series takes P1.z = 0, so that w is the level Z (8.1.2). A from P1 → P2: `azimuth_deg`.

Sections: K = floor((s1 − s0)/Δ + 1e−9) + 1 (`count`), s_k = s0 + k·Δ (a product, no accumulation),
O_k = P1 + s_k·v̂ component by component, k = 0…K−1 (the window and the attributes show k + 1).

Coordinates of a point p: d = p − P1 first (UTM without loss), then u = d·ŝ, w = d·ŵ, the depth τ = d·v̂ and
t = τ − s_k. A dot product is the sum of its terms in the order x, y, z from 0.0, a term with a zero component
of the axis skipped: a NaN in a coordinate the axis does not depend on (an empty Z of a line on a vertical
series) does not matter; in one it does, the result is NaN and the point is in no window.

Windows: section k holds the depths lo_k ≤ τ < hi_k, lo_k = s_k − w_к(k), hi_k = s_k + w_от(k) (the window of
the series or the own window of the section). Neighbours abut when |w_от(k) + w_к(k+1) − Δ| ≤ 1e−9·Δ; their
common face is then b = min(s_k + w_от(k), s_{k+1} − w_к(k+1)) = hi_k = lo_{k+1}: the windows share the space
bit for bit, and a point with t = w_от is in the next section (Т8.1-2). A series whose neighbours all abut is a
partition: the section of a point is found from the candidate floor((τ − lo_0)/Δ) moved by the same
inequalities. Otherwise (gaps, overlaps) it is the nearest plane |t| among the windows that hold the point,
the smaller k on a tie; −1 — none.

A contour (a line) is on section k when all its vertices are in the window k (ADR 0016 §7): the window holds
[τ_min, τ_max] of its vertices; among several such windows — the plane nearest to (τ_min + τ_max)/2.

    count(s0, s1, step) → K                    series_axes(az, phi) → (ŝ, v̂, ŵ)
    series_geometry(meta) → SeriesGeometry, or PolylineSection for a polyline (`polyline.py`)
    SeriesGeometry: origins(), plane(k), depth(xyz), local(xyz, k), in_window(xyz, k),
                    section_of(xyz), section_of_depth(tau), line_sections(xyz, offsets)

The bulk functions take a `tick(fraction, message)` that may raise to cancel; their numba kernels
(`kernels.py`) are imported on the first call. The same answers are read by the 3D window:
`core/tests/data/sections/series_cases.json`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .polyline import PolylineSection

# progress(fraction 0…1 or None, message or None); it may raise (JobCancelled) to cancel the calculation.
Tick = Callable[[float | None, str | None], None]

from .base import (  # noqa: E402, F401
    DEFAULT_WINDOW,
    MAX_K,
    MAX_LEN,
    MIN_LEN,
    MIN_SPAN,
    TYPES,
    SeriesError,
    as_float,
    section_key,
)

K_EPS = 1e-9  # in the formula of K
ABUT_EPS = 1e-9  # relative: windows of neighbours abut
BATCH = 1 << 20  # points between two ticks (a few ms)
_MESSAGE = "Разрезы серии"


def _ru(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _number(v: Any, what: str, name: str) -> float:
    f = as_float(v)  # None for an integer beyond float64 too (10**400)
    if f is None:
        raise SeriesError(f"{what}: ожидается число.", "Введите число.", field=name)
    return f + 0.0  # −0.0 → 0.0


def _length(v: Any, what: str, name: str) -> float:
    f = _number(v, what, name)
    if not MIN_LEN <= f <= MAX_LEN:
        raise SeriesError(
            f"{what}: от 0,01 до 10 000 м (задано {_ru(f)}).", "Введите расстояние в этих пределах.", field=name
        )
    return f


def _get(meta: Mapping[str, Any], key: str, default: Any) -> Any:
    v = meta.get(key)
    return default if v is None else v


# --- axes ------------------------------------------------------------------------------------------------------


def sin_cos_deg(deg: float) -> tuple[float, float]:
    """sin and cos of degrees, exact on the multiples of 90° (as `sinCosDeg` of the 3D window and
    `geo.section._sin_cos`); otherwise of `math.radians(d)` = d·(π/180), d reduced to [0°; 360°)."""
    d = math.fmod(math.fmod(deg, 360.0) + 360.0, 360.0)
    exact = {0.0: (0.0, 1.0), 90.0: (1.0, 0.0), 180.0: (0.0, -1.0), 270.0: (-1.0, 0.0)}
    return exact.get(d) or (math.sin(math.radians(d)), math.cos(math.radians(d)))


def azimuth_deg(dx: float, dy: float) -> float:
    """The azimuth of (dx, dy) in [0°; 360°): exact 0°, 90°, 180°, 270° when dx or dy is zero, otherwise
    `math.degrees(atan2(dx, dy))` = atan2·(180/π), plus 360° below zero."""
    if dx == 0.0 and dy == 0.0:
        raise SeriesError("Направление не задано: точки совпадают.", "Укажите вторую точку в стороне от первой.")
    if dx == 0.0:
        return 0.0 if dy > 0.0 else 180.0
    if dy == 0.0:
        return 90.0 if dx > 0.0 else 270.0
    a = math.degrees(math.atan2(dx, dy))
    if a < 0.0:
        a += 360.0
    return 0.0 if a >= 360.0 else a


def series_axes(az: float, phi: float = 90.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ŝ, v̂, ŵ) of a series with the azimuth `az` and the dip `phi` (degrees), float64, no −0.0."""
    sa, ca = sin_cos_deg(az)
    sp, cp = sin_cos_deg(phi)
    s = np.array([sa, ca, 0.0]) + 0.0
    v = np.array([-ca * sp, sa * sp, -cp]) + 0.0
    w = np.array([-ca * cp, sa * cp, sp]) + 0.0
    return s, v, w


def count(s0: float, s1: float, step: float) -> int:
    """K = floor((s1 − s0)/Δ + 1e−9) + 1 — may exceed `MAX_K` (the caller says so); SeriesError for a range
    that is not one."""
    if not (math.isfinite(s0) and math.isfinite(s1) and math.isfinite(step)) or step <= 0.0:
        raise SeriesError("Пределы и шаг серии: ожидаются числа, шаг больше нуля.", "Проверьте s0, s1 и шаг.")
    if s1 < s0:
        raise SeriesError("Конец серии раньше начала.", "s1 должен быть не меньше s0 (для плана — верх выше низа).")
    q = (s1 - s0) / step + K_EPS
    if not math.isfinite(q):
        raise SeriesError("Слишком много разрезов.", "Увеличьте шаг или сократите пределы.")
    return math.floor(q) + 1


def dot(d: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """d·axis for the rows of d (n×3): the terms in the order x, y, z from 0.0, a zero component skipped."""
    out = np.zeros(len(d))
    for i in range(3):
        if axis[i] != 0.0:
            out += d[:, i] * axis[i]
    return out


def _rows(xyz: Any) -> np.ndarray:
    a = np.asarray(xyz, dtype=np.float64)
    if a.ndim == 1 and a.size in (0, 3):
        a = a.reshape(-1, 3)
    if a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("points are an n×3 array of x, y, z")
    return np.ascontiguousarray(a)


# --- one section -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class SectionPlane:
    """The plane of a section and its window: t = 0 passes through `origin` = base + offset·v̂. u, w and t of a
    point are measured from `base` (P1 of the series, P_j of a polyline segment): u = u0 + d·ŝ, w = d·ŵ,
    t = d·v̂ − offset. `bounds` — more half-spaces n·p + c ≥ 0 (a polyline segment: its ends)."""

    base: np.ndarray
    offset: float
    s: np.ndarray
    v: np.ndarray
    w: np.ndarray
    towards: float
    away: float
    u0: float = 0.0
    seg: int | None = None
    bounds: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))

    @property
    def origin(self) -> np.ndarray:
        return self.base + self.offset * self.v + 0.0

    def local(self, xyz: Any) -> np.ndarray:
        """n×3 [u, w, t] of the points."""
        d = _rows(xyz) - self.base
        return np.column_stack([self.u0 + dot(d, self.s), dot(d, self.w), dot(d, self.v) - self.offset])

    def half_spaces(self) -> np.ndarray:
        """k×4 [nx, ny, nz, c], n·p + c ≥ 0 inside, project coordinates: the two faces of the window (closed,
        as the clipping of the 3D window — ADR 0016 решение 12), then `bounds`."""
        vb = float(dot(self.base.reshape(1, 3), self.v)[0]) + self.offset  # v̂·O
        faces = np.array([[*self.v, self.towards - vb], [*(-self.v), vb + self.away]]) + 0.0
        return np.vstack([faces, self.bounds])


# --- a series of parallel sections -----------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class SeriesGeometry:
    """Vertical, plan or inclined series: its axes, sections and windows (see the module docstring)."""

    kind: str
    p1: np.ndarray
    az: float
    phi: float
    s: np.ndarray
    v: np.ndarray
    w: np.ndarray
    s0: float
    step: float
    offs: np.ndarray  # s_k, K
    towards: np.ndarray  # w_к of every section, K
    away: np.ndarray  # w_от of every section, K
    lo: np.ndarray  # the faces of the windows in depth τ, K
    hi: np.ndarray
    partition: bool  # all neighbours abut

    @property
    def count(self) -> int:
        return len(self.offs)

    def origins(self) -> np.ndarray:
        """K×3 O_k = P1 + s_k·v̂."""
        return self.p1[None, :] + self.offs[:, None] * self.v[None, :] + 0.0

    def origin(self, k: int) -> np.ndarray:
        return self.p1 + float(self.offs[self._k(k)]) * self.v + 0.0

    def plane(self, k: int, seg: int | None = None) -> SectionPlane:
        k = self._k(k)
        return SectionPlane(
            self.p1, float(self.offs[k]), self.s, self.v, self.w, float(self.towards[k]), float(self.away[k])
        )

    def depth(self, xyz: Any) -> np.ndarray:
        """τ = (p − P1)·v̂ of the points."""
        return dot(_rows(xyz) - self.p1, self.v)

    def local(self, xyz: Any, k: int | None = None) -> np.ndarray:
        """n×3 [u, w, t]: t from section k, or the depth τ when k is None."""
        d = _rows(xyz) - self.p1
        t = dot(d, self.v)
        if k is not None:
            t = t - self.offs[self._k(k)]
        return np.column_stack([dot(d, self.s), dot(d, self.w), t])

    def in_window(self, xyz: Any, k: int) -> np.ndarray:
        """Bool mask: the point is in the window of section k (NaN — not)."""
        k = self._k(k)
        tau = self.depth(xyz)
        return (self.lo[k] <= tau) & (tau < self.hi[k])

    def section_of_depth(self, tau: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 the section of every depth τ, −1 — none."""
        kernels = _kernels()
        t = np.ascontiguousarray(np.asarray(tau, dtype=np.float64).reshape(-1))
        out = np.empty(len(t), dtype=np.int64)
        for a in range(0, len(t), BATCH):
            part = t[a : a + BATCH]
            kernels.find_sections(part, part, *self._args(), out[a : a + BATCH])
            _tick(tick, a + BATCH, len(t))
        return out

    def section_of(self, xyz: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 the section of every point, −1 — none (Т8.1-2)."""
        kernels = _kernels()
        p = _rows(xyz)
        out = np.empty(len(p), dtype=np.int64)
        tau = np.empty(min(len(p), BATCH))
        for a in range(0, len(p), BATCH):
            part = p[a : a + BATCH]
            t = tau[: len(part)]
            kernels.depths(part, *self._frame(), t)
            kernels.find_sections(t, t, *self._args(), out[a : a + BATCH])
            _tick(tick, a + BATCH, len(p))
        return out

    def line_sections(self, xyz: Any, offsets: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 the section of every line (vertices offsets[i]:offsets[i+1] of xyz), −1 — the line is in no
        window as a whole (or is empty, or a vertex has a NaN that matters)."""
        kernels = _kernels()
        p = _rows(xyz)
        offs = _offsets(offsets, len(p))
        n = len(offs) - 1
        mn, mx, out = np.empty(n), np.empty(n), np.empty(n, dtype=np.int64)
        for a, b in _line_batches(offs):
            kernels.depth_ranges(p, offs[a : b + 1], *self._frame(), mn[a:b], mx[a:b])
            _tick(tick, int(offs[b]), int(offs[-1]))
        kernels.find_sections(mn, mx, *self._args(), out)
        return out

    def _k(self, k: int) -> int:
        if not 0 <= int(k) < self.count:
            raise IndexError(f"section {k} of {self.count}")
        return int(k)

    def _frame(self) -> tuple[float, ...]:
        return (*(float(x) for x in self.p1), *(float(x) for x in self.v))

    def _args(self) -> tuple[Any, ...]:
        reach = float(max(self.towards.max(), self.away.max()))
        return (
            self.s0, self.step, self.offs, self.lo, self.hi,
            float(self.lo.min()), float(self.hi.max()), reach, self.partition,
        )  # fmt: skip


def _kernels() -> Any:
    """The numba kernels, imported on the first bulk call (numba is not imported before BF-READY)."""
    from . import kernels

    return kernels


def _tick(tick: Tick | None, done: int, total: int) -> None:
    if tick is not None:
        tick(min(done, total) / total if total else 1.0, _MESSAGE)


def _offsets(offsets: Any, n: int) -> np.ndarray:
    o = np.ascontiguousarray(np.asarray(offsets, dtype=np.int64).reshape(-1))
    if len(o) < 1 or o[0] != 0 or o[-1] != n or np.any(np.diff(o) < 0):
        raise ValueError("offsets of lines: from 0 to the number of vertices, not decreasing")
    return o


def _line_batches(offs: np.ndarray) -> list[tuple[int, int]]:
    """[a, b) ranges of lines of about BATCH vertices each."""
    n = len(offs) - 1
    out, a = [], 0
    while a < n:
        b = int(np.searchsorted(offs, offs[a] + BATCH, side="right")) - 1
        b = min(max(b, a + 1), n)
        out.append((a, b))
        a = b
    return out


def _windows(
    s0: float, step: float, k: int, towards: float, away: float, overrides: Any
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, bool]:
    tw, aw = np.full(k, towards), np.full(k, away)
    if overrides:
        if not isinstance(overrides, Mapping):
            raise SeriesError(
                "Свои окна разрезов: ожидается набор «номер → окно».", "Проверьте окна.", field="overrides"
            )
        for key, win in overrides.items():
            i = section_key(key)  # «²», «١», 5 000 digits — none
            if i is None or not i < k or not isinstance(win, Mapping):
                raise SeriesError(
                    f"Своё окно задано несуществующему разрезу «{str(key)[:20]}».",
                    f"Номера разрезов серии — от 1 до {k}.",
                    field="overrides",
                )
            tw[i] = _length(_get(win, "towards", towards), f"Окно разреза {i + 1} «к себе»", "overrides")
            aw[i] = _length(_get(win, "away", away), f"Окно разреза {i + 1} «от себя»", "overrides")
    offs = s0 + np.arange(k, dtype=np.float64) * step + 0.0
    lo, hi = offs - tw, offs + aw
    abut = np.abs(aw[:-1] + tw[1:] - step) <= ABUT_EPS * step
    face = np.minimum(hi[:-1], lo[1:])
    hi[:-1] = np.where(abut, face, hi[:-1])
    lo[1:] = np.where(abut, face, lo[1:])
    return offs, tw, aw, lo, hi, bool(abut.all())


def series_geometry(meta: Mapping[str, Any]) -> SeriesGeometry | PolylineSection:
    """The geometry of a series from its parameters (`objects.meta`, ADR 0016 §1; the defaults of
    `sections/schema.py`). SeriesError (422 `bad_series`) for parameters the geometry cannot take."""
    kind = _get(meta, "type", "vertical")
    if kind == "polyline":
        from .polyline import polyline_section

        return polyline_section(meta)
    if kind not in TYPES:
        raise SeriesError("Тип серии: вертикальная, план, наклонная или ломаная.", "Выберите тип серии.", field="type")
    p = meta.get("p1")
    if not isinstance(p, list | tuple | np.ndarray) or len(p) not in (2, 3):
        raise SeriesError("Не задана точка P1.", "Укажите X, Y и Z начала серии.", field="p1")
    p1 = np.array([_number(x, "Точка P1", "p1") for x in p] + [0.0] * (3 - len(p)))
    if kind == "vertical":
        p1[2] = 0.0  # w is the level Z
    p2 = meta.get("p2")
    if p2 is not None:
        if not isinstance(p2, list | tuple | np.ndarray) or len(p2) not in (2, 3):
            raise SeriesError("Точка P2: ожидаются X и Y.", "Укажите вторую точку линии.", field="p2")
        dx = _number(p2[0], "Точка P2", "p2") - p1[0]
        dy = _number(p2[1], "Точка P2", "p2") - p1[1]
        if math.hypot(dx, dy) <= MIN_SPAN:
            raise SeriesError("Точки P1 и P2 совпадают в плане.", "Укажите вторую точку в стороне от P1.", field="p2")
        az = azimuth_deg(dx, dy)
    elif meta.get("az") is not None or kind == "plan":
        a = math.fmod(math.fmod(_number(_get(meta, "az", 90.0), "Азимут", "az"), 360.0) + 360.0, 360.0)
        az = a + 0.0 if a < 360.0 else 0.0
    else:
        raise SeriesError("Направление серии: задайте точку P2 или азимут.", "Укажите что-то одно.", field="az")
    if kind == "vertical":
        phi = 90.0
    elif kind == "plan":
        phi = 0.0
    else:
        phi = _number(_get(meta, "phi", 90.0), "Наклон плоскости", "phi")
        if not 0.0 <= phi <= 90.0:
            raise SeriesError("Наклон плоскости: от 0 до 90°.", "90° — вертикальная, 0° — горизонтальная.", field="phi")
    towards = _length(_get(meta, "towards", DEFAULT_WINDOW), "Окно «к себе»", "towards")
    away = _length(_get(meta, "away", DEFAULT_WINDOW), "Окно «от себя»", "away")
    step = _length(_get(meta, "step", towards + away), "Шаг серии", "step")
    s0 = _number(_get(meta, "s0", 0.0), "Начало серии s0", "s0")
    s1 = _number(_get(meta, "s1", s0), "Конец серии s1", "s1")
    k = count(s0, s1, step)
    if k > MAX_K:
        raise SeriesError(
            f"В серии получается {k} разрезов — больше 5 000.",
            "Увеличьте шаг или сократите пределы серии.",
            field="step",
        )
    s, v, w = series_axes(az, phi)
    offs, tw, aw, lo, hi, partition = _windows(s0, step, k, towards, away, meta.get("overrides"))
    return SeriesGeometry(str(kind), p1, az, phi, s, v, w, s0, step, offs, tw, aw, lo, hi, partition)
