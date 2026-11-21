"""Composites by benches (ТЗ 8.2.2, ADR 0018 §6): a hole is cut where its path crosses the levels Z_j.

    p = BenchParams.of({"bench": 10, "bench_top": None, "bench_levels": None, "p_min": 0.5, "unsampled": None})
    c = composite_by_benches(lines, values, p, traj, dense, density=rho, by_density=True, texts=[codes],
                             ends=None, threads=…, tick=…) -> BenchComposites
        the `Composites` of `length.py` (lines, stretch, values, report, journal) and, per composite:
        c.bench, c.crest, c.floor — the bench: its number, the level above and the level below (m);
        c.xyz [n, 3] — X, Y, Z of the middle by length; c.flat — FLAG «пологая»; c.no_trajectory — hole ids
    `traj` — `desurvey.Trajectories` of the set, `dense` — per hole of the lines, its index there (−1 — none).

Levels: Z_j = Z_верх − j·H — a grid through Z_верх that goes on above and below it (j of any sign), so no
part of a hole is thicker than H; Z_верх by default — the highest collar of the holes, rounded up to H
(`default_top`). Or a list (`bench_levels`): then only these levels cut, and a part of a hole above the
highest or below the lowest of them is one composite. Bench j lies between the levels j (its crest) and
j + 1 (its floor): floor < Z ≤ crest — a point exactly on a level belongs to the bench below it (a collar or a
trench on a bench floor samples the rock under the floor).

Boundaries. The path is that of `Trajectories.positions` — the same `desurvey._delta`, so a boundary found
here is on its level in the coordinates every other part of the program computes. A course between two
stations is split where Z may turn (the top or the bottom of an arc of minimum curvature, the middle of a
course of two straight halves); on each part Z is monotone, the levels between its ends are crossed once
each, and the depth of a crossing is found by bracketing (regula falsi, Illinois) to 1e−9 m — the acceptance
is 1e−6 m. Ascending and level parts need nothing special: a level may be crossed any number of times, every
crossing is a boundary; a path that runs along a level or only touches it does not cross it. Crossings
nearer than `DEPTH_EPS` to each other, to the collar or to the end of the hole make no composite of their
own. The hole is taken from the collar to its depth — `ends` (per hole of the lines), by default the length
of the trajectory — or to the bottom of the last sample, whichever is deeper.

Values — `length.composite_values` (ТЗ 8.2.2: p_min, weighting by density, unsampled parts). X, Y, Z — the
point of the path at (FROM + TO) / 2; «пологая» — abs(Z(TO) − Z(FROM)) < 0,2·(TO − FROM). A hole without a
trajectory (no collar coordinates, not in the set) cannot be put on the levels: it gets no composites and is
counted in the journal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np

from bf_core.drillholes.desurvey import _STRAIGHT, BALANCED_TANGENT, METHODS, MINIMUM_CURVATURE, Trajectories, _delta
from bf_core.jit import njit

from . import intervals as iv
from . import length as cl
from . import strips
from .intervals import DEPTH_EPS, CompositeError
from .strips import Tick

FLAG_FLAT = "пологая"
FLAT_SHARE = 0.2  # abs(Δz) below this share of the length — «пологая»
ROOT_TOL = 1e-9  # m: the depth of a crossing is refined until it moves less than this
_NOISE = 8.0 * np.finfo(np.float64).eps  # relative noise of a computed Z

# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _z_at(method, s, depth, md, xyz, dirs):  # type: ignore[no-untyped-def]
    """Z of the course s … s + 1 at a measured depth, as `Trajectories.positions` computes it."""
    _dx, _dy, dz = _delta(method, depth - md[s], md[s + 1] - md[s], dirs[s, 0], dirs[s, 1], dirs[s, 2],
                          dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2])  # fmt: skip
    return xyz[s, 2] + dz


@njit(cache=True, nogil=True)
def _turn(method, seg, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    """Where Z of a course of `seg` metres between the directions a and b may stop being monotone: metres
    from its start, 0 — nowhere inside. Follows the branches of `desurvey._delta`."""
    if method == MINIMUM_CURVATURE:
        cos_b = ax * bx + ay * by + az * bz
        ux = ay * bz - az * by
        uy = az * bx - ax * bz
        uz = ax * by - ay * bx
        sin_b = math.sqrt(ux * ux + uy * uy + uz * uz)
        if sin_b >= _STRAIGHT:
            # Z = Z0 + R·(a_z·sin θ + m_z·(1 − cos θ)), m — the unit normal to a in the plane of the arc,
            # θ from 0 to β: dZ/dθ = 0 where tan θ = −a_z / m_z
            mz = (bz - cos_b * az) / sin_b
            if az == 0.0 and mz == 0.0:
                return 0.0
            theta = math.atan2(-az, mz)
            if theta <= 0.0:
                theta += math.pi
            beta = math.atan2(sin_b, cos_b)
            if theta < beta:
                return seg * theta / beta
            return 0.0
        if cos_b > 0.0:
            return 0.0
        return 0.5 * seg
    if method == BALANCED_TANGENT:
        return 0.5 * seg
    return 0.0


@njit(cache=True, nogil=True)
def _below(z, top, height, levels):  # type: ignore[no-untyped-def]
    """The number of the first level strictly below z (levels are numbered downwards: of a list — from 0, of
    the grid — Z_j = top − j·height, any j). The bench of z is this number − 1."""
    n = levels.shape[0]
    if n > 0:
        lo = 0
        hi = n
        while lo < hi:
            mid = (lo + hi) >> 1
            if levels[mid] < z:
                hi = mid
            else:
                lo = mid + 1
        return lo
    k = int(math.floor((top - z) / height)) + 1
    while top - (k - 1) * height < z:
        k -= 1
    while top - k * height >= z:
        k += 1
    return k


@njit(cache=True, nogil=True)
def _root(method, s, md, xyz, dirs, da, db, za, zb, level):  # type: ignore[no-untyped-def]
    """The depth in [da, db] of the course s where Z is `level`; Z goes from za to zb and the level is between
    them. Regula falsi with the Illinois rule: the bracket always holds the root."""
    flo = za - level
    fhi = zb - level
    if fhi == 0.0:
        return db
    if flo == 0.0:
        return da
    lo = da
    hi = db
    noise = _NOISE * max(1.0, abs(level))
    side = 0
    last = np.inf
    x = lo
    for _ in range(200):
        x = lo - flo * (hi - lo) / (fhi - flo)
        if not (x > lo and x < hi):
            x = 0.5 * (lo + hi)
        if abs(x - last) < ROOT_TOL or hi - lo < ROOT_TOL:
            break
        f = _z_at(method, s, x, md, xyz, dirs) - level
        if abs(f) <= noise:
            break
        if (f > 0.0) == (fhi > 0.0):
            hi = x
            fhi = f
            if side == -1:
                flo *= 0.5
            side = -1
        else:
            lo = x
            flo = f
            if side == 1:
                fhi *= 0.5
            side = 1
        last = x
    return x


@njit(cache=True, nogil=True)
def _grown(a):  # type: ignore[no-untyped-def]
    """The array with twice the room."""
    out = np.empty(2 * a.shape[0], a.dtype)
    out[: a.shape[0]] = a
    return out


@njit(cache=True, nogil=True)
def _bench_strip(h0, h1, method, offsets, md, xyz, dirs, dense, s_frm, s_to, top, height, levels, eps, counts, missing):  # type: ignore[no-untyped-def]
    """Composites of the holes [h0, h1) between the crossings of their paths with the levels: (FROM, TO, bench)
    in the order of the holes; counts[h] — of every hole; missing[h] — the hole has no trajectory."""
    cap = 32 * (h1 - h0) + 64
    o_frm = np.empty(cap, np.float64)
    o_to = np.empty(cap, np.float64)
    o_bench = np.empty(cap, np.int64)
    q = 0
    for h in range(h0, h1):
        counts[h] = 0
        missing[h] = False
        d = dense[h]
        if d < 0 or d >= offsets.shape[0] - 1 or offsets[d] == offsets[d + 1]:
            missing[h] = True
            continue
        lo = offsets[d]
        hi = offsets[d + 1] - 1
        if not (math.isfinite(xyz[lo, 2]) and math.isfinite(xyz[hi, 2])):
            missing[h] = True
            continue
        a = s_frm[h]
        b = s_to[h]
        if not (b > a):
            continue
        first = q
        prev = a
        dc = max(a, 0.0)  # the walk: from dc to wb along the stations
        wb = min(b, md[hi])
        s = lo
        while s < hi - 1 and md[s + 1] <= dc:
            s += 1
        if hi == lo or dc >= md[hi]:
            zc = xyz[hi, 2]
        elif dc == md[s]:
            zc = xyz[s, 2]
        else:
            zc = _z_at(method, s, dc, md, xyz, dirs)
        cur = _below(zc, top, height, levels) - 1  # the bench the path is in
        while s < hi and dc < wb:
            seg = md[s + 1] - md[s]
            if not (seg > 0.0):  # a station with coordinates is listed twice
                s += 1
                continue
            turn = _turn(
                method, seg, dirs[s, 0], dirs[s, 1], dirs[s, 2], dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2]
            )
            for part in range(2):
                e = md[s + 1]
                if part == 0:
                    if not (turn > 0.0 and turn < seg):
                        continue
                    e = md[s] + turn
                if e <= dc:
                    continue
                if e > wb:
                    e = wb
                ze = xyz[s + 1, 2]
                if e != md[s + 1]:
                    ze = _z_at(method, s, e, md, xyz, dirs)
                # the levels between zc and ze, in the order the path meets them: going down — those with
                # zc > Z ≥ ze, going up — with zc ≤ Z < ze
                step = 0
                k = 0
                n = 0
                if zc > ze:
                    step = 1
                    k = _below(zc, top, height, levels)
                    n = _below(ze, top, height, levels) - k
                elif zc < ze:
                    step = -1
                    k = _below(zc, top, height, levels) - 1
                    n = k + 1 - _below(ze, top, height, levels)
                for _i in range(n):
                    level = top - k * height
                    if levels.shape[0] > 0:
                        level = levels[k]
                    x = _root(method, s, md, xyz, dirs, dc, e, zc, ze, level)
                    nxt = k if step == 1 else k - 1  # the bench below the level or above it
                    k += step
                    if x - prev <= eps:  # at the boundary above (or at the collar): the same boundary
                        cur = nxt
                        if q > first and o_bench[q - 1] == nxt:  # back in the bench it left: no boundary
                            q -= 1
                            prev = o_frm[q]
                    elif b - x > eps:  # not at the end of the hole
                        if q == o_frm.shape[0]:
                            o_frm = _grown(o_frm)
                            o_to = _grown(o_to)
                            o_bench = _grown(o_bench)
                        o_frm[q] = prev
                        o_to[q] = x
                        o_bench[q] = cur
                        q += 1
                        prev = x
                        cur = nxt
                dc = e
                zc = ze
                if dc >= wb:
                    break
            s += 1
        if q == o_frm.shape[0]:
            o_frm = _grown(o_frm)
            o_to = _grown(o_to)
            o_bench = _grown(o_bench)
        o_frm[q] = prev
        o_to[q] = b
        o_bench[q] = cur
        q += 1
        counts[h] = q - first
    return o_frm[:q].copy(), o_to[:q].copy(), o_bench[:q].copy()


# --- parameters and levels ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchParams:
    """Parameters of composites by benches; the names are those of the command `composite.run`."""

    bench: float = 10.0  # H, m: 1–50
    bench_top: float | None = None  # Z_верх; None — the highest collar rounded up to H
    bench_levels: tuple[float, ...] | None = None  # a list of levels instead of the grid
    p_min: float = 0.5  # 0–1: the least sampled share of a composite
    unsampled: float | None = None  # the value of unsampled parts; None — no value (NaN)

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> BenchParams:
        """From the parameters of a request: absent or None — the default; checked."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known and v is not None}).checked()

    def checked(self) -> BenchParams:
        """The same parameters as numbers within the ranges of ТЗ 8.2.2, or CompositeError `bad_param`."""
        top = None
        if self.bench_top is not None:
            top = cl.check_range(self.bench_top, -math.inf, math.inf, "Отметка верха уступов Z", "bench_top")
        unsampled = None
        if self.unsampled is not None:
            unsampled = cl.check_range(
                self.unsampled, -math.inf, math.inf, "Значение непробованных участков", "unsampled"
            )
        return BenchParams(
            cl.check_range(self.bench, 1.0, 50.0, "Высота уступа H", "bench", " м"),
            top,
            None if self.bench_levels is None else _level_list(self.bench_levels),
            cl.check_range(self.p_min, 0.0, 1.0, "Минимальная доля опробованной длины p_min", "p_min"),
            unsampled,
        )


def _level_list(raw: Any) -> tuple[float, ...]:
    """The distinct levels, from the highest down."""
    label = "Отметки уступов"
    hint = "Задайте список отметок, например 300; 290; 275."
    if isinstance(raw, str | bytes) or not isinstance(raw, Sequence | np.ndarray):
        raise CompositeError("bad_param", f"{label}: нужен список чисел.", hint, param="bench_levels")
    values = [cl.check_range(v, -math.inf, math.inf, label, "bench_levels") for v in raw]
    if not values:
        raise CompositeError("bad_param", f"{label}: список пуст.", hint, param="bench_levels")
    return tuple(sorted(set(values), reverse=True))


def default_top(collar_z: Any, height: float) -> float:
    """Z_верх by default: the highest collar, rounded up to a multiple of H (float noise above a multiple is
    not a bench); no collar at all — 0."""
    z = np.asarray(collar_z, dtype=np.float64)
    z = z[np.isfinite(z)]
    if z.shape[0] == 0:
        return 0.0
    return float(math.ceil(float(z.max()) / height - 1e-9) * height)


@dataclass(frozen=True)
class Levels:
    """The levels that cut the holes: the grid top − j·height (j of any sign) or `values`, from the highest
    down. Bench j lies between the levels j (crest) and j + 1 (floor)."""

    top: float
    height: float
    values: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))

    def _level(self, number: np.ndarray) -> np.ndarray:
        if self.values.shape[0] == 0:
            return self.top - number * self.height  # type: ignore[no-any-return]
        ok = (number >= 0) & (number < self.values.shape[0])
        out = np.full(number.shape[0], np.nan)
        out[ok] = self.values[number[ok]]
        return out

    def crest(self, bench: np.ndarray) -> np.ndarray:
        """The level above every bench; NaN — above the highest level of a list."""
        return self._level(np.asarray(bench, dtype=np.int64))

    def floor(self, bench: np.ndarray) -> np.ndarray:
        """The level below every bench; NaN — below the lowest level of a list."""
        return self._level(np.asarray(bench, dtype=np.int64) + 1)


# --- composites --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchTargets:
    lines: iv.Lines  # the composites [FROM; TO]
    hole: np.ndarray  # int64 [n]: the hole of the stretches every composite lies in
    bench: np.ndarray  # int64 [n]
    missing: np.ndarray  # bool per hole of the stretches: no trajectory, no composites


def bench_targets(
    traj: Trajectories,
    dense: Any,
    stretches: iv.Lines,
    levels: Levels,
    *,
    eps: float = DEPTH_EPS,
    threads: int = 1,
    tick: Tick | None = None,
) -> BenchTargets:
    """The composites of the holes: the stretch [a; b] of every hole (one row per hole of `stretches`) cut at
    the crossings of its path with the levels. `dense` — per hole, its index in `traj`, −1 — none."""
    nh = stretches.n_holes
    idx = np.ascontiguousarray(dense, dtype=np.int64)
    if idx.shape[0] != nh or stretches.n != nh:
        raise ValueError("dense and the stretches must be per hole")
    counts = np.zeros(nh, dtype=np.int64)
    missing = np.zeros(nh, dtype=np.bool_)
    args = (
        METHODS.index(traj.method),
        np.ascontiguousarray(traj.offsets, dtype=np.int64),
        np.ascontiguousarray(traj.md, dtype=np.float64),
        np.ascontiguousarray(traj.xyz, dtype=np.float64),
        np.ascontiguousarray(traj.dirs, dtype=np.float64),
        idx,
        stretches.frm,
        stretches.to,
        float(levels.top),
        float(levels.height) if levels.values.shape[0] == 0 else 1.0,
        np.ascontiguousarray(levels.values, dtype=np.float64),
        float(eps),
        counts,
        missing,
    )
    parts = strips.run(lambda lo, hi: _bench_strip(lo, hi, *args), nh, threads=threads, tick=tick)
    keep = counts > 0
    cat = [np.concatenate([p[k] for p in parts]) if parts else np.zeros(0, dtype=t) for k, t in enumerate("ddq")]
    lines = iv.Lines(stretches.holes[keep], strips.offsets(counts[keep]), cat[0], cat[1])
    return BenchTargets(lines, np.repeat(np.arange(nh, dtype=np.int64), counts), cat[2], missing)


@dataclass(frozen=True)
class BenchComposites(cl.Composites):
    bench: np.ndarray  # int64 [n]: the number of the bench
    crest: np.ndarray  # float64 [n]: the level above the composite, m; NaN — none (above a list)
    floor: np.ndarray  # float64 [n]: the level below it; NaN — none (below a list)
    xyz: np.ndarray  # float64 [n, 3]: X, Y, Z — the middle of the composite by length
    flat: np.ndarray  # bool [n]: FLAG «пологая»
    no_trajectory: np.ndarray  # int64: ids of the holes left out — no trajectory


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def composite_by_benches(
    lines: iv.Lines,
    values: Any,
    params: BenchParams,
    traj: Trajectories,
    dense: Any,
    *,
    density: Any = None,
    by_density: bool = True,
    texts: Sequence[Any] = (),
    ends: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> BenchComposites:
    """Composites by benches of a set of holes (module docstring), on strips of holes. `lines`, `values`,
    `density`, `texts` — the samples, as `length.composite_by_length`; `traj` — the trajectories of the set;
    `dense` — per hole of the lines, its index in `traj` (−1 — none); `ends` — depths of the holes of the
    lines (default — the lengths of the trajectories). The result does not depend on `threads`."""
    p = params.checked()
    idx = np.ascontiguousarray(dense, dtype=np.int64)
    if idx.shape[0] != lines.n_holes:
        raise ValueError("dense must be per hole of the lines")
    known = (idx >= 0) & (idx < traj.n_holes)
    at = np.where(known, idx, 0)
    built = known & (traj.offsets[at + 1] > traj.offsets[at]) if traj.n_holes else known
    if ends is None:
        deep = np.full(lines.n_holes, np.nan)
        deep[built] = traj.md[traj.offsets[at[built] + 1] - 1]
    else:
        deep = np.asarray(ends, dtype=np.float64)
    if p.bench_levels is None:
        collars = traj.xyz[traj.offsets[at[built]], 2] if built.any() else np.zeros(0)
        top = default_top(collars, p.bench) if p.bench_top is None else p.bench_top
        levels = Levels(top, p.bench)
    else:
        levels = Levels(0.0, 0.0, np.array(p.bench_levels, dtype=np.float64))
    tg = bench_targets(
        traj, idx, cl.hole_stretches(lines, "collar", deep), levels, threads=threads, tick=_part(tick, 0.0, 0.3)
    )
    v = cl.composite_values(
        lines,
        values,
        tg.lines,
        density=density,
        by_density=by_density,
        texts=texts,
        p_min=p.p_min,
        unsampled=p.unsampled,
        threads=threads,
        tick=_part(tick, 0.3, 0.8),
    )
    rows = idx[tg.hole]
    spread = _part(tick, 0.8, 1.0)
    ends_xyz = traj.interval_positions(
        rows, tg.lines.frm, tg.lines.to, tick=None if spread is None else (lambda f, _m: spread(f or 0.0))
    )
    length = tg.lines.lengths()
    flat = np.abs(ends_xyz[1][:, 2] - ends_xyz[0][:, 2]) < FLAT_SHARE * length
    if tick is not None:
        tick(1.0)
    left = lines.holes[tg.missing]
    empty = int(np.count_nonzero(v.count == 0))
    report: dict[str, Any] = {
        "holes": tg.lines.n_holes,
        "composites": tg.lines.n,
        "no_trajectory": int(left.shape[0]),
        "flat": int(np.count_nonzero(flat)),
        "empty": empty,
        "low": int(np.count_nonzero(v.low)),
        "no_density": v.n_no_density,
        "levels": None if p.bench_levels is None else list(p.bench_levels),
        "threads": int(threads),
        "strip": strips.STRIP_HOLES,
    }
    if p.bench_levels is None:
        report.update(top=levels.top, height=levels.height)
    journal = []
    if left.shape[0]:
        journal.append(
            f"Скважин без траектории — {left.shape[0]}: композиты по уступам не построены. Проверьте координаты устьев."
        )
    if report["flat"]:
        journal.append(f"Композитов с флагом «{FLAG_FLAT}» (перепад высот меньше 0,2 длины) — {report['flat']}.")
    if empty:
        filled = "без значений (не ноль)" if p.unsampled is None else f"значение {p.unsampled:g}".replace(".", ",")
        journal.append(f"Композитов на непробованных участках — {empty}: {filled}.")
    journal.extend(v.notes())
    return BenchComposites(
        tg.lines, tg.hole, v, report, journal,
        tg.bench, levels.crest(tg.bench), levels.floor(tg.bench), ends_xyz[2], flat, left,
    )  # fmt: skip
