"""A polyline section (ТЗ 8.1.2; ADR 0016 §2, решение 10): the polyline P0…Pm in plan and the width of its
corridor — a series of one section (K = 1) with one development; float64, no numba at import.

    segment j: P_j → P_{j+1}, d = P_{j+1} − P_j, L_j = sqrt(dx·dx + dy·dy), ŝ_j = (dx/L_j, dy/L_j, 0),
               v̂_j = (−ŝ_y, ŝ_x, 0) (φ = 90°: the viewer to the right of the segment), ŵ = (0, 0, 1)
    the start of segment j in the development: U_j = L_0 + … + L_{j−1}, summed in order
    the segment of a point: the nearest in plan — the distance to the segment (to its line where the
        projection falls inside it, otherwise to the nearer end); the smaller number on a tie (at a bend and
        in its outer wedge both segments measure the distance to the same vertex); farther than the corridor —
        −1, as a point with x or y not finite
    u = U_j + (p − P_j)·ŝ_j,  w = (p − P_j)·ŵ = Z (P_j.z = 0),  t = (p − P_j)·v̂_j
    in 3D — the current segment: the faces of the corridor t = ±corridor, the bisectors of the bends at its
        ends (n = ŝ_{j−1} + ŝ_j normalized), across a free end — the plane through it along ŝ: four
        half-spaces, a convex region (`half_spaces`)

The development of the whole polyline is U_m = L_0 + … + L_{m−1}: the point P_m unfolds to it (Т8.1-7). The 3D
region of a segment is not the rule of the core in the outer wedge of a bend (the bisector halves it, the core
gives it to the smaller number) nor beyond the free ends (the core keeps the round caps of the corridor): as
with the windows (ADR 0016 решение 12), the screen shows, the core decides. A polyline that turns back on
itself (|ŝ_{j−1} + ŝ_j| < 1e−6) has no bisector and is refused.

    polyline_section(meta) → PolylineSection: segment_of(xyz), local(xyz), section_of(xyz), in_window(xyz),
                             line_sections(xyz, offsets), plane(k, seg), half_spaces(seg)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .geometry import (
    BATCH,
    MAX_LEN,
    MIN_LEN,
    MIN_SPAN,
    SectionPlane,
    SeriesError,
    Tick,
    _kernels,
    _offsets,
    _rows,
    _tick,
    dot,
)

MAX_POINTS = 1_000  # vertices of a polyline (ADR 0016 §1)
DEFAULT_CORRIDOR = 25.0
TURN_EPS = 1e-6  # |ŝ_{j−1} + ŝ_j| below it: the polyline turns back
UP = np.array([0.0, 0.0, 1.0])


@dataclass(frozen=True, eq=False)
class PolylineSection:
    """The polyline section of a series (see the module docstring); one section, `segments` segments."""

    points: np.ndarray  # (m + 1)×2, plan
    corridor: float
    lengths: np.ndarray  # m
    starts: np.ndarray  # m: U_j
    s_axes: np.ndarray  # m×3
    v_axes: np.ndarray  # m×3
    kind: str = "polyline"

    @property
    def count(self) -> int:
        return 1

    @property
    def segments(self) -> int:
        return len(self.lengths)

    @property
    def total(self) -> float:
        """The length of the development U_m."""
        return float(self.starts[-1] + self.lengths[-1])

    @property
    def w(self) -> np.ndarray:
        return UP

    @property
    def offs(self) -> np.ndarray:
        """s_k of the one section: 0."""
        return np.zeros(1)

    @property
    def towards(self) -> np.ndarray:
        return np.array([self.corridor])

    @property
    def away(self) -> np.ndarray:
        return np.array([self.corridor])

    def vertex(self, j: int) -> np.ndarray:
        """P_j in 3D (z = 0)."""
        return np.array([self.points[j, 0], self.points[j, 1], 0.0])

    def origins(self) -> np.ndarray:
        return self.vertex(0).reshape(1, 3)

    def origin(self, k: int = 0) -> np.ndarray:
        self._k(k)
        return self.vertex(0)

    def plane(self, k: int = 0, seg: int | None = 0) -> SectionPlane:
        """The plane of segment `seg` with its corridor and its two ends (`bounds`)."""
        self._k(k)
        j = self._seg(0 if seg is None else seg)
        return SectionPlane(
            self.vertex(j),
            0.0,
            self.s_axes[j],
            self.v_axes[j],
            UP,
            self.corridor,
            self.corridor,
            float(self.starts[j]),
            j,
            self._ends(j),
        )

    def half_spaces(self, seg: int) -> np.ndarray:
        """4×4 [nx, ny, nz, c], n·p + c ≥ 0 inside: the near and the far face of the corridor, the start and the
        end of segment `seg` (bisectors of the bends, planes across the free ends)."""
        return self.plane(0, seg).half_spaces()

    def segment_of(self, xyz: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 the segment of every point, −1 — outside the corridor."""
        kernels = _kernels()
        p = _rows(xyz)
        out = np.empty(len(p), dtype=np.int64)
        sx, sy = np.ascontiguousarray(self.s_axes[:, 0]), np.ascontiguousarray(self.s_axes[:, 1])
        vx, vy = np.ascontiguousarray(self.v_axes[:, 0]), np.ascontiguousarray(self.v_axes[:, 1])
        for a in range(0, len(p), BATCH):
            kernels.polyline_segments(
                p[a : a + BATCH], self.points, sx, sy, vx, vy, self.lengths, self.corridor**2, out[a : a + BATCH]
            )
            _tick(tick, a + BATCH, len(p))
        return out

    def local(self, xyz: Any, seg: int | None = None) -> np.ndarray:
        """n×3 [u, w, t]: by the segment of every point (NaN outside the corridor), or by segment `seg`."""
        p = _rows(xyz)
        j = self.segment_of(p) if seg is None else np.full(len(p), self._seg(seg), dtype=np.int64)
        out = np.full((len(p), 3), np.nan)
        on = j >= 0
        jj = j[on]
        dx = p[on, 0] - self.points[jj, 0]
        dy = p[on, 1] - self.points[jj, 1]
        out[on, 0] = self.starts[jj] + (dx * self.s_axes[jj, 0] + dy * self.s_axes[jj, 1])
        out[on, 1] = 0.0 + p[on, 2]
        out[on, 2] = dx * self.v_axes[jj, 0] + dy * self.v_axes[jj, 1]
        return out

    def section_of(self, xyz: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 0 — in the corridor, −1 — not (the interface of `SeriesGeometry.section_of`)."""
        return np.where(self.segment_of(xyz, tick=tick) >= 0, 0, -1).astype(np.int64)

    def in_window(self, xyz: Any, k: int = 0) -> np.ndarray:
        self._k(k)
        return self.segment_of(xyz) >= 0

    def line_sections(self, xyz: Any, offsets: Any, *, tick: Tick | None = None) -> np.ndarray:
        """int64 for every line: 0 — all its vertices are in the corridor, −1 — not (or the line is empty)."""
        p = _rows(xyz)
        offs = _offsets(offsets, len(p))
        bad = np.concatenate([[0], np.cumsum(self.segment_of(p, tick=tick) < 0)])
        outside = bad[offs[1:]] - bad[offs[:-1]]
        return np.where((np.diff(offs) == 0) | (outside > 0), -1, 0).astype(np.int64)

    def _ends(self, j: int) -> np.ndarray:
        m = self.segments
        start = _bisector(self.s_axes[j - 1], self.s_axes[j]) if j > 0 else self.s_axes[j]
        end = -_bisector(self.s_axes[j], self.s_axes[j + 1]) if j < m - 1 else -self.s_axes[j]
        a, b = self.vertex(j), self.vertex(j + 1)
        c0, c1 = dot(a.reshape(1, 3), start)[0], dot(b.reshape(1, 3), end)[0]
        return np.array([[*start, -c0], [*end, -c1]]) + 0.0

    def _k(self, k: int) -> None:
        if int(k) != 0:
            raise IndexError(f"section {k} of a polyline section")

    def _seg(self, seg: int) -> int:
        if not 0 <= int(seg) < self.segments:
            raise IndexError(f"segment {seg} of {self.segments}")
        return int(seg)


def _bisector(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = a + b
    return n / math.sqrt(n[0] * n[0] + n[1] * n[1])


def polyline_section(meta: Mapping[str, Any]) -> PolylineSection:
    """The polyline section from the parameters of a series (`polyline`, `corridor`; ADR 0016 §1). SeriesError
    (422 `bad_series`) for a polyline it cannot take."""
    pts = meta.get("polyline")
    if not isinstance(pts, list | tuple | np.ndarray) or not 2 <= len(pts) <= MAX_POINTS:
        raise SeriesError(
            "Ломаный разрез: от 2 до 1 000 вершин.", "Укажите вершины ломаной в плане по порядку.", field="polyline"
        )
    xy = []
    for p in pts:
        ok = isinstance(p, list | tuple | np.ndarray) and len(p) in (2, 3)
        if not ok or not all(not isinstance(c, bool) and isinstance(c, int | float) for c in p[:2]):
            raise SeriesError(
                "Вершина ломаной: ожидаются X и Y.", "Укажите координаты вершин числами.", field="polyline"
            )
        xy.append([float(p[0]) + 0.0, float(p[1]) + 0.0])
    points = np.array(xy)
    if not np.isfinite(points).all():
        raise SeriesError("Вершина ломаной: ожидаются числа.", "Укажите координаты вершин числами.", field="polyline")
    corridor = meta.get("corridor")
    corridor = DEFAULT_CORRIDOR if corridor is None else corridor
    if isinstance(corridor, bool) or not isinstance(corridor, int | float) or not MIN_LEN <= corridor <= MAX_LEN:
        raise SeriesError(
            "Ширина коридора: от 0,01 до 10 000 м.", "Введите расстояние в этих пределах.", field="corridor"
        )
    d = np.diff(points, axis=0)
    lengths = np.sqrt(d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1])
    short = np.flatnonzero(~(lengths > MIN_SPAN))
    if len(short):
        i = int(short[0]) + 1
        raise SeriesError(
            f"Ломаный разрез: вершины {i} и {i + 1} совпадают.", "Удалите повторную вершину.", field="polyline"
        )
    sx, sy = d[:, 0] / lengths, d[:, 1] / lengths
    s_axes = np.column_stack([sx, sy, np.zeros(len(d))]) + 0.0
    v_axes = np.column_stack([-sy, sx, np.zeros(len(d))]) + 0.0
    turn = np.hypot(sx[:-1] + sx[1:], sy[:-1] + sy[1:])
    back = np.flatnonzero(turn < TURN_EPS)
    if len(back):
        raise SeriesError(
            f"Ломаный разрез поворачивает назад в вершине {int(back[0]) + 2}.",
            "Сдвиньте или удалите вершину: соседние звенья не должны идти по одной линии в обратные стороны.",
            field="polyline",
        )
    starts = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    return PolylineSection(points, float(corridor), lengths, starts, s_axes, v_axes)
