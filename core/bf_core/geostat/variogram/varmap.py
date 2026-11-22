"""The variogram map in a plane, and the job «Веер и карта» over the points of a calculation (ТЗ 8.6.1, 8.6.4;
ADR 0022 §4–5, решение 8; GSLIB varmapv, BF `variogram_map`).

    m = varmap(x, y, z, v, cell, *, frame=None, nu=20, nv=20, thickness=inf, min_pairs=5, rule="round",
               threads=1, tick=None, cells=None)
        frame       3×3, rows: the axis u, the axis v, the normal (`fan.plane_frame(plane)` — a1, a2, a3 of the
                    plane); None — u = X, v = Y, the normal Z (the plan of the tab «Статистика», as BF)
        a row with a missing or infinite number takes no part and is counted (`m.n_missing`); z = None — z = 0
    m.u, m.v            float64[2nu + 1], [2nv + 1]: the centres i·cell, i = −nu … nu (u), j·cell (v)
    m.n                 int64[2nv + 1, 2nu + 1]: ordered pairs of a cell, the self-pairs in the centre
    m.gamma             float64[2nv + 1, 2nu + 1]: Σ (z_i − z_j)² / (2N) over them; N < min_pairs — NaN
    m.to_json()         {nu, nv, cell, thickness, min_pairs, u, v, gamma, n} (docs/progress/e16-api.md)
    plan = prepare(x, y, z, cell, …) / run(plan, v, …)     the same in two steps (the estimate `plan.checks` first)
    r = calculate(p, spec, *, limit=PAIRS_MAX, threads=1, tick=None)     the job `variography.map`: the fan with
                    its rose and the map of `spec` (`spec.MapSpec`, resolved) over `points.Points`; r.to_json()

Layout (ADR 0022 §5; the window draws a1 up, a2 to the right, θ clockwise from a1 — ADR 0022 §15, 16.22):
`gamma[j][i]`, `n[j][i]` — the cell of the shift u[i]·(axis u) + v[j]·(axis v): the row j by v (j = 0 —
v = −nv·cell), the column i by u. With `frame` of a plane u ↔ a1 and v ↔ a2 — the vertical and the horizontal
of the window; with None u ↔ ΔX, v ↔ ΔY, which is BF `gam[iy, ix]`.

A cell (ADR 0022 §5). A pair (i, j) of the rows within the thickness of the layer, |Δ·n| ≤ thickness (∞ — the
projection), falls into the cell (rint(Δu / cell), rint(Δv / cell)) — rounded to the nearest, a half to the
even (BF `np.rint`), Δu = u_j − u_i of the coordinates of the rows in the system of `frame` (`fan.project`,
exact for the axes of the world); the cells with |index| ≤ nu, nv are kept. Every unordered pair counts in its
cell and in the symmetric one (all the ordered pairs, as BF and GSLIB), the self-pairs (N of them, γ = 0) in the
centre: the map is symmetric bit for bit. γ = Σ / (2N) with N ≥ `min_pairs`, otherwise NaN (null): an empty
cell is never 0. `rule="trunc"` — the cell int(Δ / cell) of GeostatsPy `varmapv` (the centre twice as wide),
only for the reference of the tests (Д-21).

Kernel: the points by cells of a grid in the system of the frame — `cells` (MAP_CELLS) per reach of the map
along u and v ((n + ½)·cell for round, (n + 1)·cell for trunc), LAYERS per thickness along the normal (one layer
for ∞); a pair is met once, from the grid cell with the smaller key, over the offsets of the half-space within
reach. Strips (`pairs.strip_bounds`, ≤ 256) of their own compensated sums (Neumaier), added pairwise in their
order: the same bit for bit with 1, 2, 4, 8 threads (`nogil`, the threads of `composites.strips`); `tick` after
every strip in the calling thread, may raise to cancel. `plan.checks` — the pair checks of the grid (ADR 0022 §4).

The job (`calculate`). The estimate is the pair checks of what is asked for — the fan (`fan.prepare`) and the
map — over all the points; above `limit` (2·10⁹) both go over one sample of n′ = ⌊n·√(limit / estimate)⌋ points
(`points.sample_size`, `points.sample`, PCG64(seed)) and the answer has `sampled {n, of, seed}`. tick(fraction of
the pairs, stage) — «Пары: веер», «Пары: карта», the fraction by the checks.

Time (16.4; Linux, 4 cores; 200 000 composites of 2 m on 1000 holes, L = 20 m, 15 lags, the fan of 12 rays,
band_v 2L, the map 41 × 41, the layer 2L): the estimate 6,2·10⁹ (the fan 3,0, the map 3,2) → the sample of
113 335 points; `calculate` 7,6–7,8 s with 4 threads (30 s with 1), the horizontal and a turned plane; 0,25 GB.
All the points without the sample: the fan 24 ns, the map 6,6 ns a check a thread (an accepted pair of the map
— one compensated sum — ~10 ns).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np

from bf_core.composites import strips
from bf_core.jit import njit

from . import experimental as ex
from . import fan as fn
from . import pairs
from .pairs import Tick, bad

if TYPE_CHECKING:
    from .points import Points
    from .spec import MapSpec

RULES = ("round", "trunc")
MAP_CELLS = 4  # grid cells per reach of the map along u and v (by the measurement of 16.4)
LAYERS = 2  # grid cells per thickness along the normal
N_MAX = 100  # cells of the map each way, at most (201 × 201)
STAGE_FAN = "Пары: веер"
STAGE_MAP = "Пары: карта"
_GROW = 1e-6  # the grid cell is this much wider than the reach / m: rounding never puts a pair one cell farther
_AXIS = 2**31


def _len_json(x: float) -> float | str:
    return ex.length_json(x)


@dataclass(frozen=True)
class VarMap:
    nu: int
    nv: int
    cell: float
    thickness: float
    min_pairs: int
    rule: str
    frame: np.ndarray  # 3×3: rows u, v, the normal
    n: np.ndarray  # int64[2nv + 1, 2nu + 1]
    s2: np.ndarray  # float64[2nv + 1, 2nu + 1]: Σ (z_i − z_j)² over the ordered pairs
    n_points: int
    n_missing: int
    checks: int
    strips: int

    @property
    def u(self) -> np.ndarray:
        return np.array([i * self.cell for i in range(-self.nu, self.nu + 1)], dtype=np.float64) + 0.0

    @property
    def v(self) -> np.ndarray:
        return np.array([j * self.cell for j in range(-self.nv, self.nv + 1)], dtype=np.float64) + 0.0

    @property
    def gamma(self) -> np.ndarray:
        out = np.full(self.n.shape, np.nan)
        ok = (self.n >= self.min_pairs) & (self.n > 0)
        np.divide(self.s2, 2 * self.n, out=out, where=ok)
        return out

    def to_json(self) -> dict[str, Any]:
        """`map` of the answer: gamma[j][i], n[j][i] — row j by v, column i by u; N < min_pairs — null."""
        return {"nu": self.nu, "nv": self.nv, "cell": float(self.cell), "thickness": _len_json(self.thickness),
                "min_pairs": int(self.min_pairs), "u": self.u.tolist(), "v": self.v.tolist(),
                "gamma": [ex.nulls(row) for row in self.gamma], "n": self.n.tolist()}  # fmt: skip


class MapPlan(NamedTuple):
    order: np.ndarray  # int64[n]: rows by (grid cell, row)
    us: np.ndarray
    vs: np.ndarray
    ns: np.ndarray
    ckey: np.ndarray
    cstart: np.ndarray
    cijk: np.ndarray
    dims: np.ndarray
    off: np.ndarray  # int64[no, 3]: the offsets of the half-space within reach, the cell itself first
    bounds: np.ndarray  # int64[strips + 1]
    checks: int
    nu: int
    nv: int
    cell: float
    thickness: float
    trunc: bool
    frame: np.ndarray
    n_missing: int

    @property
    def strips(self) -> int:
        return len(self.bounds) - 1

    @property
    def n(self) -> int:
        return len(self.order)


def check_map(nu: int, nv: int, cell: float, thickness: float, min_pairs: int, rule: str) -> tuple[Any, ...]:
    for name, k in (("map.nu", nu), ("map.nv", nv)):
        if isinstance(k, bool) or int(k) != k or not (1 <= int(k) <= N_MAX):
            raise bad(name, "Число ячеек карты в каждую сторону должно быть целым от 1 до 100.",
                      "По умолчанию — 20 (карта 41 × 41).")  # fmt: skip
    cell = float(cell)
    if not (math.isfinite(cell) and cell > 0.0):
        raise bad("map.cell", "Ячейка карты должна быть больше нуля.", "По умолчанию ячейка равна лагу.")
    thickness = float(thickness)
    if not thickness > 0.0:
        raise bad("map.thickness", "Толщина слоя карты должна быть больше нуля.", "∞ — проекция всех пар на плоскость.")
    if isinstance(min_pairs, bool) or int(min_pairs) != min_pairs or not (1 <= int(min_pairs) <= 1000):
        raise bad("map.min_pairs", "Наименьшее число пар в ячейке должно быть целым от 1 до 1000.", "По умолчанию — 5.")
    if rule not in RULES:
        raise bad("rule", "Правило ячейки карты: round или trunc.", "Карта строится с округлением (round).")
    return int(nu), int(nv), cell, thickness, int(min_pairs), rule


def _frame(frame: Any) -> np.ndarray:
    f = np.eye(3) if frame is None else np.asarray(frame, dtype=np.float64)
    if f.shape != (3, 3) or not np.isfinite(f).all():
        raise ValueError("the frame of a map is a finite 3×3 matrix: rows u, v, the normal")
    return np.ascontiguousarray(f) + 0.0


def _too_fine() -> Exception:
    return bad("map.cell", "Ячейка карты слишком мала для охвата данных.",
               "Увеличьте ячейку или толщину слоя карты либо сузьте домен.")  # fmt: skip


def _index(c: np.ndarray, size: float) -> np.ndarray:
    if not math.isfinite(size):
        return np.zeros(len(c), dtype=np.int64)
    span = (float(c.max()) - float(c.min())) / size
    if not span < _AXIS:
        raise _too_fine()
    return np.floor((c - c.min()) / size).astype(np.int64)


def offsets(dims: np.ndarray, m: int, layers: int) -> np.ndarray:
    """The offsets (di, dj, dk) of the half-space «the other cell has a greater key», |di|, |dj| ≤ m, |dk| ≤
    layers (within the grid), ascending by the key offset — the cell itself first."""
    rx, ry, rz = min(m, int(dims[0]) - 1), min(m, int(dims[1]) - 1), min(layers, int(dims[2]) - 1)
    dk, dj, di = np.meshgrid(np.arange(-rz, rz + 1), np.arange(-ry, ry + 1), np.arange(-rx, rx + 1), indexing="ij")
    half = (dk > 0) | ((dk == 0) & (dj > 0)) | ((dk == 0) & (dj == 0) & (di >= 0))
    return np.ascontiguousarray(np.stack([di[half], dj[half], dk[half]], axis=1).astype(np.int64))


def prepare(
    x: Any, y: Any, z: Any, cell: float, *, frame: Any = None, nu: int = 20, nv: int = 20,
    thickness: float = math.inf, rule: str = "round", cells: int | None = None, n_missing: int = 0,
) -> MapPlan:  # fmt: skip
    """The plan of the map over the rows (z = None — z = 0): the grid, the offsets, the strips, the checks."""
    nu, nv, cell, thickness, _mp, rule = check_map(nu, nv, cell, thickness, 1, rule)
    m = MAP_CELLS if cells is None else cells
    if isinstance(m, bool) or int(m) != m or not (1 <= int(m) <= pairs.MAX_CELLS):
        raise bad("cells", "Число ячеек сетки на охват карты должно быть целым от 1 до 16.", "Оставьте по умолчанию.")
    m = int(m)
    f = _frame(frame)
    (xs, ys, zs), dropped = ex.finite_rows(x, y, z)
    if len(xs) < 2:
        raise ex.too_few()
    pu, pv, pn = (fn.project(xs, ys, zs, f[k]) for k in range(3))
    trunc = rule == "trunc"
    reach = cell + 0.0 if trunc else 0.5 * cell
    idx = [_index(pu, (nu * cell + reach) / m * (1.0 + _GROW)), _index(pv, (nv * cell + reach) / m * (1.0 + _GROW)),
           _index(pn, thickness / LAYERS * (1.0 + _GROW))]  # fmt: skip
    dims = np.array([int(i.max()) + 1 for i in idx], dtype=np.int64)
    if int(dims[0]) * int(dims[1]) * int(dims[2]) >= 2**62:
        raise _too_fine()
    key = idx[0] + dims[0] * (idx[1] + dims[1] * idx[2])
    order = np.argsort(key, kind="stable")
    ks = key[order]
    first = np.flatnonzero(np.concatenate([[True], ks[1:] != ks[:-1]]))
    ckey = np.ascontiguousarray(ks[first])
    cstart = np.concatenate([first, [len(ks)]]).astype(np.int64)
    cijk = np.ascontiguousarray(np.stack([ckey % dims[0], (ckey // dims[0]) % dims[1], ckey // (dims[0] * dims[1])],
                                         axis=1))  # fmt: skip
    off = offsets(dims, m, LAYERS)
    cost = pairs._cell_checks(ckey, cstart, cijk, dims, off)
    us, vs, ns = (np.ascontiguousarray(c[order]) for c in (pu, pv, pn))
    return MapPlan(order, us, vs, ns, ckey, cstart, cijk, dims, off, pairs.strip_bounds(cost), int(cost.sum()), nu,
                   nv, cell, thickness, trunc, f, n_missing + dropped)  # fmt: skip


@njit(cache=True, nogil=True)
def _map_strip(us, vs, ns, val, ckey, cstart, cijk, dims, off, cell, nu, nv, thick, trunc, c0, c1, acc, cnt):  # type: ignore[no-untyped-def]  # noqa: E501
    """The pairs of the grid cells c0 ≤ c < c1 (with the cells at the offsets) into acc and cnt [(2nv + 1)·(2nu
    + 1)], flat [j, i]: N and Σ (z_a − z_b)² of the cell of Δ = p_b − p_a (one of the two symmetric ones; the
    other is added in `run`). The order of the additions is that of the pairs (cell, offset, point, point)."""
    nc = ckey.shape[0]
    no = off.shape[0]
    nx, ny, nz = dims[0], dims[1], dims[2]
    width = 2 * nu + 1
    comp = np.zeros(acc.shape[0])
    lim_u = nu + 1.0 if trunc else nu + 0.5  # |Δ / cell| beyond it is off the map
    lim_v = nv + 1.0 if trunc else nv + 0.5
    delta = np.empty(no, dtype=np.int64)
    ptr = np.empty(no, dtype=np.int64)
    for o in range(no):
        delta[o] = off[o, 0] + nx * (off[o, 1] + ny * off[o, 2])
        ptr[o] = np.searchsorted(ckey, ckey[c0] + delta[o]) if c0 < nc else nc
    for c in range(c0, c1):
        a0 = cstart[c]
        a1 = cstart[c + 1]
        for o in range(no):
            jx = cijk[c, 0] + off[o, 0]
            jy = cijk[c, 1] + off[o, 1]
            jz = cijk[c, 2] + off[o, 2]
            if jx < 0 or jx >= nx or jy < 0 or jy >= ny or jz < 0 or jz >= nz:
                continue
            same = delta[o] == 0
            if same:
                b0 = a0
                b1 = a1
            else:
                target = ckey[c] + delta[o]
                p = ptr[o]
                while p < nc and ckey[p] < target:
                    p += 1
                ptr[o] = p
                if p >= nc or ckey[p] != target:
                    continue
                b0 = cstart[p]
                b1 = cstart[p + 1]
            for a in range(a0, a1):
                ua = us[a]
                va = vs[a]
                na = ns[a]
                za = val[a]
                bs = a + 1 if same else b0
                for b in range(bs, b1):
                    if abs(ns[b] - na) > thick:
                        continue
                    qu = (us[b] - ua) / cell
                    qv = (vs[b] - va) / cell
                    if not (abs(qu) <= lim_u and abs(qv) <= lim_v):
                        continue
                    if trunc:
                        if abs(qu) >= lim_u or abs(qv) >= lim_v:
                            continue
                        iu = int(qu)
                        iv = int(qv)
                    else:
                        ru = np.rint(qu)
                        rv = np.rint(qv)
                        if abs(ru) > nu or abs(rv) > nv:
                            continue
                        iu = int(ru)
                        iv = int(rv)
                    k = (iv + nv) * width + iu + nu
                    d = za - val[b]
                    cnt[k] += 1
                    x = d * d
                    s = acc[k]
                    t = s + x
                    bp = t - s
                    comp[k] += (s - (t - bp)) + (x - bp)
                    acc[k] = t
    for k in range(acc.shape[0]):
        acc[k] += comp[k]


def run(plan: MapPlan, v: Any, min_pairs: int = 5, *, threads: int = 1, tick: Tick | None = None) -> VarMap:
    """The map of `plan` for the values `v` (float64, finite, in the order of the rows of the plan)."""
    p = plan
    _nu, _nv, _c, _t, min_pairs, _r = check_map(p.nu, p.nv, p.cell, p.thickness, min_pairs, "round")
    val = np.ascontiguousarray(np.asarray(v, dtype=np.float64)[p.order])
    size = (2 * p.nv + 1) * (2 * p.nu + 1)
    acc = np.zeros((p.strips, size))
    cnt = np.zeros((p.strips, size), dtype=np.int64)

    def one(s: int, _hi: int) -> None:
        _map_strip(p.us, p.vs, p.ns, val, p.ckey, p.cstart, p.cijk, p.dims, p.off, p.cell, p.nu, p.nv, p.thickness,
                   p.trunc, p.bounds[s], p.bounds[s + 1], acc[s], cnt[s])  # fmt: skip

    strips.run(one, p.strips, threads=threads, tick=tick, size=1)
    shape = (2 * p.nv + 1, 2 * p.nu + 1)
    half_n = cnt.sum(axis=0).reshape(shape)
    half_s = pairs.pairwise(acc).reshape(shape)
    n = half_n + half_n[::-1, ::-1]
    n[p.nv, p.nu] += p.n  # the self-pairs
    s2 = half_s + half_s[::-1, ::-1]
    return VarMap(p.nu, p.nv, p.cell, p.thickness, min_pairs, "trunc" if p.trunc else "round", p.frame, n, s2, p.n,
                  p.n_missing, p.checks, p.strips)  # fmt: skip


def varmap(
    x: Any, y: Any, z: Any, v: Any, cell: float, *, frame: Any = None, nu: int = 20, nv: int = 20,
    thickness: float = math.inf, min_pairs: int = 5, rule: str = "round", threads: int = 1, tick: Tick | None = None,
    cells: int | None = None,
) -> VarMap:  # fmt: skip
    """The variogram map of the rows (see the module)."""
    check_map(nu, nv, cell, thickness, min_pairs, rule)
    (xs, ys, zs, vals), n_missing = ex.finite_rows(x, y, z, v)
    plan = prepare(xs, ys, zs, cell, frame=frame, nu=nu, nv=nv, thickness=thickness, rule=rule, cells=cells,
                   n_missing=n_missing)  # fmt: skip
    return run(plan, vals, min_pairs, threads=threads, tick=tick)


# --- the job `variography.map` ------------------------------------------------------------------------------


@dataclass(frozen=True)
class MapResult:
    plane: tuple[float, float, float]
    fan: fn.Fan | None
    rose: fn.Rose | None
    map: VarMap | None
    min_pairs: int  # of the fan («мало пар»)
    sampled: dict[str, int] | None
    estimate: int  # pair checks of all the points: the fan and the map asked for

    @property
    def warnings(self) -> list[str]:
        w = ex.pairwise_warning(self.fan.e.pw_skipped) if self.fan is not None else None
        return [w] if w else []

    def to_json(self) -> dict[str, Any]:
        """{plane, sampled, fan, rose, map, warnings} of the answer (the job adds key, sigma2, origin, counts,
        params)."""
        return {"plane": dict(zip(("ang1", "ang2", "ang3"), self.plane, strict=True)), "sampled": self.sampled,
                "fan": None if self.fan is None else self.fan.to_json(self.min_pairs),
                "rose": None if self.rose is None else self.rose.to_json(),
                "map": None if self.map is None else self.map.to_json(), "warnings": self.warnings}  # fmt: skip


JobTick = Callable[[float | None, str | None], None]


def calculate(
    p: Points, spec: MapSpec, *, limit: float = ex.PAIRS_MAX, threads: int = 1, tick: JobTick | None = None,
    cells: int | None = None, map_cells: int | None = None,
) -> MapResult:  # fmt: skip
    """The fan, the rose and the map of a resolved `spec` over the points of a calculation (see the module)."""
    from . import points

    lag, tol, cell, thickness = spec.lags.lag, spec.lags.tol, spec.cell, spec.thickness
    if lag is None or tol is None or cell is None or thickness is None:
        raise ValueError("resolve the spec first (spec.resolved)")
    nlag = spec.lags.nlag
    if not np.isfinite(p.xyz).all() or not np.isfinite(p.values).all():
        raise ValueError("the points of a calculation are finite (points.load)")
    want_fan, want_map = "fan" in spec.what, "map" in spec.what
    plane = fn.rays(spec.plane)[0]
    frame = fn.plane_frame(plane)
    band_v = math.inf if spec.band_v is None else spec.band_v
    dip_tol = 22.5 if spec.dip_tol is None else spec.dip_tol

    def plans(q: Points) -> tuple[fn.FanPlan | None, MapPlan | None]:
        x, y, z = (np.ascontiguousarray(q.xyz[:, k]) for k in range(3))
        fp = fn.prepare(x, y, z, lag, nlag, tol, plane=plane, n=spec.fan_n, tol=spec.fan_tol,
                        dip_tol=dip_tol, band_v=band_v, cells=cells) if want_fan else None  # fmt: skip
        mp = prepare(x, y, z, cell, frame=frame, nu=spec.nu, nv=spec.nv, thickness=thickness,
                     cells=map_cells) if want_map else None  # fmt: skip
        return fp, mp

    fp, mp = plans(p)
    total = (fp.checks if fp else 0) + (mp.checks if mp else 0)
    q, sampled = points.sample(p, points.sample_size(p.n, total, limit), spec.seed)
    if sampled is not None:
        fp, mp = plans(q)
    work = max(1, (fp.checks if fp else 0) + (mp.checks if mp else 0))
    done = 0.0

    def part(stage: str, share: float) -> Tick:
        start = done
        if tick is not None:
            tick(start, stage)
        return lambda f: tick(start + share * f, stage) if tick is not None else None

    values = np.ascontiguousarray(q.values, dtype=np.float64)
    f = r = None
    if fp is not None:
        share = fp.checks / work
        f = fn.run(fp, values, threads=threads, tick=part(STAGE_FAN, share))
        r = fn.rose(f, p.sigma2, spec.q, spec.min_pairs)
        done += share
    vm = None
    if mp is not None:
        vm = run(mp, values, spec.map_min_pairs, threads=threads, tick=part(STAGE_MAP, mp.checks / work))
    return MapResult(plane, f, r, vm, spec.min_pairs, sampled, int(total))
