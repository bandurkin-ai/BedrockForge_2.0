"""Drillhole trajectories (desurvey) for a whole set of holes at once (ТЗ 7.11, 7.34 п. 98). No database.

Conventions (Micromine): azimuth clockwise from north, degrees; dip negative downwards (-90 = vertical
down); X east, Y north, Z up. Everything is float64.

Holes are addressed by a dense index 0 … n_holes-1 (the caller keeps the names). The stations of all
holes live in flat arrays, `offsets[h]:offsets[h + 1]` being the stations of hole `h`, so 50 000 holes
are a few dozen megabytes and no Python objects. A hole without collar coordinates has no stations and
is listed in `skipped`; its positions are NaN (a missing value is never zero).

Station rules (BF `geo/trajectory.py`): survey rows with a missing or out-of-range value (depth < 0,
azimuth outside 0…360, dip outside -90…90) are skipped; of equal depths the first row wins; without valid
stations the collar azimuth and dip are used, without those the hole is vertical; a first station below
the collar gives its orientation up to the collar; the path is extended to TD with the last orientation.

Methods (displacement of a course of length L between unit directions t1 above and t2 below):
  minimum_curvature  circular arc, L/2 · RF · (t1 + t2), RF = 2/β · tan(β/2) — the default;
  tangent            L · t1: a station holds until the next one, so every station is used;
  balanced_tangent   L/2 · (t1 + t2): the upper half along t1, the lower half along t2;
  average_angle      L · direction(mean azimuth along the shorter arc, mean dip); a vertical station has no
                     azimuth, the azimuth of the other station is taken.

numba is imported with this module (about a second): import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from bf_core.jit import njit

METHODS = ("minimum_curvature", "tangent", "balanced_tangent", "average_angle")
METHOD_TITLES = {
    "minimum_curvature": "Минимальная кривизна",
    "tangent": "Касательный",
    "balanced_tangent": "Сбалансированный касательный",
    "average_angle": "Средний угол",
}
MINIMUM_CURVATURE, TANGENT, BALANCED_TANGENT, AVERAGE_ANGLE = range(4)  # method codes of the kernels

SOURCE_NONE, SOURCE_SURVEY, SOURCE_COLLAR, SOURCE_VERTICAL = range(4)  # where the orientation comes from
SOURCE_NAMES = ("none", "survey", "collar", "vertical")

# progress(fraction 0…1 or None, message or None); it may raise (JobCancelled) to cancel the calculation.
Tick = Callable[[float | None, str | None], None]

_STRAIGHT = 1e-9  # sine of the angle between two directions below which a course is not an arc (BF)
_VERTICAL = 1e-12  # horizontal part of a unit direction below which it has no azimuth
_HOLE_CHUNK = 1 << 13  # holes between two ticks
_ROW_CHUNK = 1 << 18  # rows between two ticks (a few hundredths of a second)


# --- kernels --------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _unit(azimuth: float, dip: float) -> tuple[float, float, float]:
    a = math.radians(azimuth)
    d = math.radians(dip)
    c = math.cos(d)
    return math.sin(a) * c, math.cos(a) * c, math.sin(d)


@njit(cache=True, nogil=True)
def _delta(method, along, seg, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    """Displacement after `along` metres of a course `seg` metres long (seg > 0) between the unit
    directions a (upper station) and b (lower station)."""
    if method == TANGENT:
        return along * ax, along * ay, along * az
    if method == AVERAGE_ANGLE:
        ha = math.hypot(ax, ay)
        hb = math.hypot(bx, by)
        dip = 0.5 * (math.atan2(az, ha) + math.atan2(bz, hb))
        if ha < _VERTICAL:
            azimuth = math.atan2(bx, by) if hb >= _VERTICAL else 0.0
        elif hb < _VERTICAL:
            azimuth = math.atan2(ax, ay)
        else:
            azimuth = math.atan2(ax, ay)
            turn = math.atan2(bx, by) - azimuth
            if turn > math.pi:
                turn -= 2.0 * math.pi
            elif turn < -math.pi:
                turn += 2.0 * math.pi
            azimuth += 0.5 * turn
        c = math.cos(dip)
        return along * math.sin(azimuth) * c, along * math.cos(azimuth) * c, along * math.sin(dip)
    if method == MINIMUM_CURVATURE:
        cos_b = ax * bx + ay * by + az * bz
        ux = ay * bz - az * by
        uy = az * bx - ax * bz
        uz = ax * by - ay * bx
        sin_b = math.sqrt(ux * ux + uy * uy + uz * uz)
        if sin_b >= _STRAIGHT:
            # Exact arc of radius seg/β: chord 2R·sin(θ/2) along cos(θ/2)·a + sin(θ/2)·n, θ = β·along/seg,
            # n — the unit normal to a in the plane of the arc. The same curve as BF's slerp formula.
            beta = math.atan2(sin_b, cos_b)
            half = 0.5 * beta * along / seg
            sh = math.sin(half)
            chord = 2.0 * seg / beta * sh
            p = chord * math.cos(half)
            q = chord * sh / sin_b
            return p * ax + q * (bx - cos_b * ax), p * ay + q * (by - cos_b * ay), p * az + q * (bz - cos_b * az)
        if cos_b > 0.0:  # a straight course: the limit of the arc (BF)
            w = 0.5 * along / seg
            v = 1.0 - w
            return along * (v * ax + w * bx), along * (v * ay + w * by), along * (v * az + w * bz)
        # the direction is reversed: no circle fits, the course is taken as two straight halves
    upper = min(along, 0.5 * seg)
    lower = along - upper
    return upper * ax + lower * bx, upper * ay + lower * by, upper * az + lower * bz


@njit(cache=True, nogil=True)
def _group_surveys(hole, depth, azimuth, dip, n_holes):  # type: ignore[no-untyped-def]
    """Valid stations grouped by hole, ascending by depth, without repeated depths (the first row wins).

    Returns start (n_holes + 1), depth, azimuth, dip, «hole has survey rows», invalid rows, repeated depths.
    """
    m = hole.shape[0]
    start = np.zeros(n_holes + 1, dtype=np.int64)
    seen = np.zeros(n_holes, dtype=np.bool_)
    valid = np.zeros(m, dtype=np.bool_)
    for i in range(m):
        h = hole[i]
        seen[h] = True
        d = depth[i]
        if 0.0 <= d < np.inf and 0.0 <= azimuth[i] <= 360.0 and -90.0 <= dip[i] <= 90.0:  # NaN fails
            valid[i] = True
            start[h + 1] += 1
    for h in range(n_holes):
        start[h + 1] += start[h]
    n = start[n_holes]
    gd = np.empty(n, dtype=np.float64)
    ga = np.empty(n, dtype=np.float64)
    gp = np.empty(n, dtype=np.float64)
    fill = start[:n_holes].copy()
    for i in range(m):  # counting sort by hole: stable, O(rows)
        if valid[i]:
            k = fill[hole[i]]
            gd[k] = depth[i] + 0.0  # -0.0 becomes 0.0
            ga[k] = azimuth[i]
            gp[k] = dip[i]
            fill[hole[i]] = k + 1
    w = 0
    for h in range(n_holes):
        a = start[h]
        b = start[h + 1]
        for k in range(a + 1, b):
            if gd[k] < gd[k - 1]:  # rarely: rows of a hole usually come ordered
                order = np.argsort(gd[a:b], kind="mergesort")  # stable
                gd[a:b] = gd[a:b][order]
                ga[a:b] = ga[a:b][order]
                gp[a:b] = gp[a:b][order]
                break
        start[h] = w
        for k in range(a, b):
            if w > start[h] and gd[k] == gd[w - 1]:
                continue
            gd[w] = gd[k]
            ga[w] = ga[k]
            gp[w] = gp[k]
            w += 1
    start[n_holes] = w
    return start, gd[:w], ga[:w], gp[:w], seen, m - n, n - w


@njit(cache=True, nogil=True)
def _collar_has_angles(azimuth: float, dip: float) -> bool:
    return 0.0 <= azimuth <= 360.0 and -90.0 <= dip <= 90.0


@njit(cache=True, nogil=True)
def _count_stations(collar_row, cx, cy, cz, ctd, caz, cdip, start, gd, counts, source):  # type: ignore[no-untyped-def]
    for h in range(counts.shape[0]):
        r = collar_row[h]
        if r < 0 or not (math.isfinite(cx[r]) and math.isfinite(cy[r]) and math.isfinite(cz[r])):
            continue  # no coordinates: no trajectory
        a = start[h]
        b = start[h + 1]
        if b > a:
            source[h] = SOURCE_SURVEY
            n = b - a + (1 if gd[a] > 0.0 else 0)
            last = gd[b - 1]
        else:
            source[h] = SOURCE_COLLAR if _collar_has_angles(caz[r], cdip[r]) else SOURCE_VERTICAL
            n = 1
            last = 0.0
        if math.isfinite(ctd[r]) and ctd[r] > last:
            n += 1
        counts[h] = n


@njit(cache=True, nogil=True)
def _fill_stations(method, h0, h1, collar_row, cx, cy, cz, ctd, caz, cdip, start, gd, ga, gp, offsets, md, xyz, dirs):  # type: ignore[no-untyped-def]
    for h in range(h0, h1):
        o = offsets[h]
        end = offsets[h + 1]
        if end == o:
            continue
        r = collar_row[h]
        a = start[h]
        b = start[h + 1]
        k = o
        if b > a:
            if gd[a] > 0.0:  # the first station is below the collar: its orientation up to the collar
                md[k] = 0.0
                dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(ga[a], gp[a])
                k += 1
            for s in range(a, b):
                md[k] = gd[s]
                dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(ga[s], gp[s])
                k += 1
        else:
            md[k] = 0.0
            if _collar_has_angles(caz[r], cdip[r]):
                dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(caz[r], cdip[r])
            else:
                dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(0.0, -90.0)
            k += 1
        if k < end:  # extension to TD with the last orientation
            md[k] = ctd[r]
            dirs[k] = dirs[k - 1]
        xyz[o, 0] = cx[r]
        xyz[o, 1] = cy[r]
        xyz[o, 2] = cz[r]
        for k in range(o + 1, end):
            seg = md[k] - md[k - 1]
            dx, dy, dz = _delta(method, seg, seg, dirs[k - 1, 0], dirs[k - 1, 1], dirs[k - 1, 2],
                                dirs[k, 0], dirs[k, 1], dirs[k, 2])  # fmt: skip
            xyz[k, 0] = xyz[k - 1, 0] + dx
            xyz[k, 1] = xyz[k - 1, 1] + dy
            xyz[k, 2] = xyz[k - 1, 2] + dz


@njit(cache=True, nogil=True)
def _positions(method, offsets, md, xyz, dirs, hole, depth, out):  # type: ignore[no-untyped-def]
    n_holes = offsets.shape[0] - 1
    for r in range(hole.shape[0]):
        h = hole[r]
        d = depth[r]
        if h < 0 or h >= n_holes or d != d or offsets[h] == offsets[h + 1]:
            out[r, 0] = out[r, 1] = out[r, 2] = np.nan
            continue
        lo = offsets[h]
        hi = offsets[h + 1] - 1
        if d < 0.0:  # depths are clipped to the hole
            d = 0.0
        if d >= md[hi]:  # the end of the hole; also a hole of zero length
            out[r, 0], out[r, 1], out[r, 2] = xyz[hi, 0], xyz[hi, 1], xyz[hi, 2]
            continue
        while hi - lo > 1:  # the course lo…lo+1 with md[lo] <= d < md[lo + 1]
            mid = (lo + hi) >> 1
            if md[mid] <= d:
                lo = mid
            else:
                hi = mid
        dx, dy, dz = _delta(method, d - md[lo], md[lo + 1] - md[lo], dirs[lo, 0], dirs[lo, 1], dirs[lo, 2],
                            dirs[lo + 1, 0], dirs[lo + 1, 1], dirs[lo + 1, 2])  # fmt: skip
        out[r, 0] = xyz[lo, 0] + dx
        out[r, 1] = xyz[lo, 1] + dy
        out[r, 2] = xyz[lo, 2] + dz


@njit(cache=True, nogil=True)
def _paths(method, offsets, md, xyz, dirs, holes, j0, j1, step, p_off, out, fill):  # type: ignore[no-untyped-def]
    """Points of the paths of holes[j0:j1]: stations (and corners of balanced-tangent courses) merged with
    the grid k·step. `fill` False only counts them into p_off[j + 1]."""
    parts = 2 if method == BALANCED_TANGENT else 1
    for j in range(j0, j1):
        a = offsets[holes[j]]
        b = offsets[holes[j] + 1]
        w = p_off[j] if fill else 0
        first = w
        if b > a:
            k = 1
            grid = step
            for q in range((b - a - 1) * parts + 1):
                i = a + q // parts  # the station at or above this point
                at = md[i] if q % parts == 0 else 0.5 * (md[i] + md[i + 1])
                s = a + (q - 1) // parts  # the course above this point
                while grid < at:
                    if fill:
                        dx, dy, dz = _delta(method, grid - md[s], md[s + 1] - md[s], dirs[s, 0], dirs[s, 1],
                                            dirs[s, 2], dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2])  # fmt: skip
                        out[w, 0], out[w, 1], out[w, 2], out[w, 3] = (
                            xyz[s, 0] + dx,
                            xyz[s, 1] + dy,
                            xyz[s, 2] + dz,
                            grid,
                        )
                    w += 1
                    k += 1
                    grid = k * step
                if grid == at:
                    k += 1
                    grid = k * step
                if fill:
                    dx = dy = dz = 0.0
                    if q % parts:
                        dx, dy, dz = _delta(method, at - md[i], md[i + 1] - md[i], dirs[i, 0], dirs[i, 1],
                                            dirs[i, 2], dirs[i + 1, 0], dirs[i + 1, 1], dirs[i + 1, 2])  # fmt: skip
                    out[w, 0], out[w, 1], out[w, 2], out[w, 3] = xyz[i, 0] + dx, xyz[i, 1] + dy, xyz[i, 2] + dz, at
                w += 1
        if not fill:
            p_off[j + 1] = w - first


# --- input and result -----------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Collars:
    """One row per hole that has a collar. `hole` — dense hole index, unique; NaN = missing value."""

    hole: npt.ArrayLike
    x: npt.ArrayLike
    y: npt.ArrayLike
    z: npt.ArrayLike
    td: npt.ArrayLike | None = None  # total depth; None = unknown for every hole
    azimuth: npt.ArrayLike | None = None  # orientation of a hole without usable survey stations
    dip: npt.ArrayLike | None = None


@dataclass(frozen=True, eq=False)
class Surveys:
    """Survey stations in any order. `hole` — the same hole index as in `Collars`."""

    hole: npt.ArrayLike
    depth: npt.ArrayLike
    azimuth: npt.ArrayLike
    dip: npt.ArrayLike


@dataclass(frozen=True, eq=False)
class Paths:
    """Polylines for display: points of `holes[j]` are `points[offsets[j]:offsets[j + 1]]` (x, y, z, depth)."""

    holes: np.ndarray
    offsets: np.ndarray
    points: np.ndarray


@dataclass(frozen=True, eq=False)
class Trajectories:
    """Stations of all holes: hole `h` owns `offsets[h]:offsets[h + 1]` (empty = no trajectory)."""

    method: str
    offsets: np.ndarray  # int64 (n_holes + 1,)
    md: np.ndarray  # measured depth, ascending within a hole from 0
    xyz: np.ndarray  # (n_stations, 3) station positions
    dirs: np.ndarray  # (n_stations, 3) unit direction at the station
    source: np.ndarray  # uint8 (n_holes,): SOURCE_NONE | SOURCE_SURVEY | SOURCE_COLLAR | SOURCE_VERTICAL
    skipped: np.ndarray  # indices of holes with a collar row or survey rows but without coordinates
    invalid_stations: int  # survey rows skipped for a missing or out-of-range value (all holes)
    duplicate_stations: int  # survey rows skipped for a repeated depth (all holes)

    @property
    def n_holes(self) -> int:
        return len(self.offsets) - 1

    def lengths(self) -> np.ndarray:
        """Length of every hole along the path (NaN = no trajectory)."""
        out = np.full(self.n_holes, np.nan)
        built = self.offsets[1:] > self.offsets[:-1]
        out[built] = self.md[self.offsets[1:][built] - 1]
        return out

    def stations(self, hole: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """md, xyz and dirs of one hole (views)."""
        rows = slice(int(self.offsets[hole]), int(self.offsets[hole + 1]))
        return self.md[rows], self.xyz[rows], self.dirs[rows]

    def positions(self, hole: npt.ArrayLike, depths: npt.ArrayLike, *, tick: Tick | None = None) -> np.ndarray:
        """Coordinates (n, 3) at measured depths of holes; depths are clipped to the hole.

        NaN rows: a missing depth, an unknown hole, a hole without a trajectory.
        """
        h = np.asarray(hole)
        if h.size and h.dtype.kind not in "iu":
            raise TypeError(f"hole must be an integer array of hole indices, got {h.dtype}")
        h, d = (v.reshape(-1) for v in np.broadcast_arrays(h, np.asarray(depths, dtype=np.float64)))
        out = np.empty((len(h), 3), dtype=np.float64)
        code = METHODS.index(self.method)
        for a in range(0, len(h), _ROW_CHUNK):
            if tick is not None:
                tick(a / len(h), f"Координаты интервалов: строк {a} из {len(h)}")
            b = a + _ROW_CHUNK
            # A portion is copied only when it has to be: another integer type, a broadcast or read-only
            # array (Arrow, memory map) — the kernel is compiled for one kind of array.
            _positions(code, self.offsets, self.md, self.xyz, self.dirs, _plain(h[a:b], np.int64),
                       _plain(d[a:b], np.float64), out[a:b])  # fmt: skip
        return out

    def interval_positions(
        self, hole: npt.ArrayLike, from_m: npt.ArrayLike, to_m: npt.ArrayLike, *, tick: Tick | None = None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Coordinates of the start, the end and the middle (by depth) of intervals: three (n, 3) arrays."""
        start = np.asarray(from_m, dtype=np.float64)
        end = np.asarray(to_m, dtype=np.float64)
        parts = []
        for k, depths in enumerate((start, end, 0.5 * (start + end))):
            part = None if tick is None else _sub_tick(tick, k / 3.0, 1.0 / 3.0)
            parts.append(self.positions(hole, depths, tick=part))
        return parts[0], parts[1], parts[2]


def _sub_tick(tick: Tick, base: float, share: float) -> Tick:
    return lambda fraction, message: tick(None if fraction is None else base + share * fraction, message)


def _plain(values: Any, dtype: Any) -> np.ndarray:
    """C-contiguous writable array of `dtype`; the input itself when it already is one."""
    return np.require(values, dtype=dtype, requirements=["C", "W"])


def _index(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values)
    if arr.size and arr.dtype.kind not in "iu":
        raise TypeError(f"{name} must be an integer array of hole indices, got {arr.dtype}")
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return _plain(arr, np.int64)


def _column(values: Any, n: int, name: str) -> np.ndarray:
    if values is None:
        return np.full(n, np.nan)
    arr = _plain(values, np.float64)
    if arr.shape != (n,):
        raise ValueError(f"{name} has length {arr.shape}, expected {n}")
    return arr


# --- calculation ----------------------------------------------------------------------------------


def desurvey(
    collars: Collars,
    surveys: Surveys | None = None,
    *,
    method: str = "minimum_curvature",
    n_holes: int | None = None,
    tick: Tick | None = None,
) -> Trajectories:
    """Trajectories of all holes from collars and survey stations (module docstring: rules and methods).

    `n_holes` — number of holes of the set (default: the largest index met + 1); `tick` — progress and
    cancellation, called between portions of holes.
    """
    if method not in METHODS:
        raise ValueError(f"unknown desurvey method {method!r}; known: {', '.join(METHODS)}")
    code = METHODS.index(method)
    c_hole = _index(collars.hole, "collars.hole")
    n = len(c_hole)
    cx, cy, cz = (_column(v, n, f"collars.{k}") for k, v in (("x", collars.x), ("y", collars.y), ("z", collars.z)))
    ctd, caz, cdip = (
        _column(v, n, f"collars.{k}")
        for k, v in (("td", collars.td), ("azimuth", collars.azimuth), ("dip", collars.dip))
    )
    if surveys is None:
        surveys = Surveys(np.empty(0, dtype=np.int64), *(np.empty(0) for _ in range(3)))
    s_hole = _index(surveys.hole, "surveys.hole")
    m = len(s_hole)
    sd, sa, sp = (
        _column(v, m, f"surveys.{k}")
        for k, v in (("depth", surveys.depth), ("azimuth", surveys.azimuth), ("dip", surveys.dip))
    )

    top = max(int(c_hole.max(initial=-1)), int(s_hole.max(initial=-1))) + 1
    low = min(int(c_hole.min(initial=0)), int(s_hole.min(initial=0)))
    if n_holes is None:
        n_holes = top
    if low < 0 or top > n_holes:
        raise ValueError(f"hole index outside 0…{n_holes - 1}: from {low} to {top - 1}")
    collar_row = np.full(n_holes, -1, dtype=np.int64)
    collar_row[c_hole] = np.arange(n)
    if int((collar_row >= 0).sum()) != n:
        raise ValueError("a hole index is met twice in collars")

    if tick is not None:
        tick(0.0, "Траектории: инклинометрия")
    start, gd, ga, gp, seen, invalid, repeated = _group_surveys(s_hole, sd, sa, sp, n_holes)
    counts = np.zeros(n_holes, dtype=np.int64)
    source = np.zeros(n_holes, dtype=np.uint8)
    _count_stations(collar_row, cx, cy, cz, ctd, caz, cdip, start, gd, counts, source)
    offsets = np.zeros(n_holes + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    total = int(offsets[-1])
    md = np.empty(total, dtype=np.float64)
    xyz = np.empty((total, 3), dtype=np.float64)
    dirs = np.empty((total, 3), dtype=np.float64)
    for h0 in range(0, n_holes, _HOLE_CHUNK):
        if tick is not None:
            tick(h0 / n_holes, f"Траектории: скважин {h0} из {n_holes}")
        _fill_stations(code, h0, min(n_holes, h0 + _HOLE_CHUNK), collar_row, cx, cy, cz, ctd, caz, cdip,
                       start, gd, ga, gp, offsets, md, xyz, dirs)  # fmt: skip
    seen[c_hole] = True
    skipped = np.flatnonzero(seen & (counts == 0))
    if tick is not None:
        tick(1.0, f"Траектории: скважин {n_holes} из {n_holes}")
    return Trajectories(method, offsets, md, xyz, dirs, source, skipped, int(invalid), int(repeated))


def sample_paths(
    traj: Trajectories,
    step: float,
    *,
    holes: npt.ArrayLike | None = None,
    max_points: int = 20_000_000,
    tick: Tick | None = None,
) -> Paths:
    """Polylines of the holes for display: the stations plus a regular grid of `step` metres of depth
    (BF `sample_path`), for all holes or for `holes`. More than `max_points` points is an error."""
    step = float(step)
    if not (math.isfinite(step) and step > 0.0):
        raise ValueError(f"step must be a positive number of metres, got {step}")
    if holes is None:
        which = np.arange(traj.n_holes, dtype=np.int64)
    else:
        which = _index(holes, "holes")
        if len(which) and (which.min() < 0 or which.max() >= traj.n_holes):
            raise ValueError(f"hole index outside 0…{traj.n_holes - 1}")
    code = METHODS.index(traj.method)
    stations = (traj.offsets[1:] - traj.offsets[:-1])[which]
    bound = float(stations.sum()) * 2.0 + float(np.floor(np.nan_to_num(traj.lengths()[which]) / step).sum())
    if bound > max_points:  # checked before any loop over the grid
        raise ValueError(f"the paths would have up to {bound:.0f} points, more than {max_points}: take a larger step")
    counts = np.zeros(len(which) + 1, dtype=np.int64)
    _paths(
        code, traj.offsets, traj.md, traj.xyz, traj.dirs, which, 0, len(which), step, counts, np.empty((0, 4)), False
    )
    offsets = np.cumsum(counts)
    points = np.empty((int(offsets[-1]), 4), dtype=np.float64)
    for j0 in range(0, len(which), _HOLE_CHUNK):
        if tick is not None:
            tick(j0 / len(which), f"Траектории: линии скважин {j0} из {len(which)}")
        _paths(code, traj.offsets, traj.md, traj.xyz, traj.dirs, which, j0, min(len(which), j0 + _HOLE_CHUNK), step,
               offsets, points, True)  # fmt: skip
    return Paths(which, offsets, points)
