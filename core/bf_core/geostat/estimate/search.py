"""The neighbourhood search of the estimation (ТЗ 8.8.2; ADR 0024 §4; Д-29; Д-30; Э18, 18.6).

    ell = Ellipsoid.of((ang1, ang2, ang3), (r1, r2, r3))      S = diag(1, r1/r2, r1/r3)·R; h_s = ‖S·Δ‖; inside: h_s ≤ r1
    sr = Searcher(samples, ell, passes, soft_dist)            one cKDTree per row, in the space where the ellipsoid
                                                              is a ball
    nb = sr.gather(centres, k)                                the selection of pass k (from 0) for a portion of blocks
    fd = sr.search(centres)                                   the first pass that holds, for every block (NN, IDW)
    cd = sr.explain(centre, k)                                every candidate of one block with its reason (Д-30)

The rules of a pass — the walk, in this order (the code is `search_kernel.py`; the same code gives the selection of a
block and the reasons of the inspector): the ellipsoid of the pass (radius r1·factor); the domain (D; S only with
d_м > 0 and the euclidean distance to the centre of the block ≤ d_м, № 79); the rule of the high samples (Д-29: above
t and farther than q·r1·factor — dropped or capped to t; off by default); the order (h_s, number of the sample — the
position in the canonical order, № 76); the walk: n_max, the limit of a hole, the limit of an octant (octants — in the
axes of the ellipsoid, bit 0 — x ≥ 0, bit 1 — y ≥ 0, bit 2 — z ≥ 0; a sample on a plane belongs to the positive
side); then n_min, the occupied octants ≥ k_oct, the holes ≥ n_holes_min make the pass `ok`. A block is estimated by
the first pass that holds; kriging that fails (a degenerate system) goes on to the next pass: `gather(…, k, todo=…)`.

The candidates come from the tree in a batch: the k_c = max(4·n_max, 64) nearest inside the radius of the pass. The
walk is decided by a prefix of the list ordered by h_s, so the answer is final when the walk was stopped by n_max at
a distance below the k_c-th one, or when the tree gave fewer than k_c candidates (all that are inside). Otherwise —
fewer than n_max selected and the k_c-th distance is inside, or the last selected is as far as the k_c-th — the query
is repeated with k_c × 4 (8.8.2). So the answer equals the full enumeration whatever k_c is (`k_c=` of the
constructor makes the first k_c small, for the tests); the coordinates are the local ones (`samples.local`, 8.0.1).

NHY (Д-29): the candidates the rule touched (dropped or capped) not farther than the reach of the selection (the h_s
of the n_max-th selected, else the radius of the pass) — it does not depend on k_c.

The arrays of the kernels are the searcher's own writable copies (a read-only column of the samples is copied once),
so every kernel has one compiled signature (lesson 17.39б); `warm_search()` compiles it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from bf_core.api.errors import ApiError

from . import search_kernel as sk
from .samples import D, RowSamples, S
from .spec_parts import Hy, Pass

# u1 codes = the index (e18-api.md §7); the labels are in the ui (`M/`)
REASONS = (
    "selected",
    "hy_capped",
    "outside",
    "hole_limit",
    "octant_limit",
    "over_n_max",
    "other_domain",
    "soft_far",
    "hy_dropped",
    "merged",
)
PASSES_MAX = 5
SUB = 4096  # blocks per tree query: the candidates of 2¹⁶ blocks would be 100 MB
EXPLAIN_MAX = 1000  # candidates of the inspector (e18-api.md §7)
K_MIN = 64


def _own(a: np.ndarray, dtype: Any) -> np.ndarray:
    """The array as the kernels take it: contiguous, `dtype`, writable (a read-only one is copied)."""
    out = np.asarray(a, dtype=dtype, order="C")
    return out if out.flags.writeable else out.copy()


@dataclass(frozen=True)
class Ellipsoid:
    angles: tuple[float, float, float]
    radii: tuple[float, float, float]
    S: np.ndarray  # float64 [3, 3]: diag(1, r1/r2, r1/r3)·R

    @classmethod
    def of(cls, angles: Sequence[float], radii: Sequence[float]) -> Ellipsoid:
        from bf_core.geostat.rotation import anisotropy_matrix

        a = (float(angles[0]), float(angles[1]), float(angles[2]))
        r = (float(radii[0]), float(radii[1]), float(radii[2]))
        return cls(a, r, np.ascontiguousarray(anisotropy_matrix(*a, *r), dtype=np.float64))

    def h(self, d: np.ndarray) -> np.ndarray:
        """h_s of the offsets `d` [n, 3] — the operations of the kernel, one by one (bit for bit)."""
        s = self.S
        d = np.asarray(d, dtype=np.float64).reshape(-1, 3)
        y0 = s[0, 0] * d[:, 0] + s[0, 1] * d[:, 1] + s[0, 2] * d[:, 2]
        y1 = s[1, 0] * d[:, 0] + s[1, 1] * d[:, 1] + s[1, 2] * d[:, 2]
        y2 = s[2, 0] * d[:, 0] + s[2, 1] * d[:, 1] + s[2, 2] * d[:, 2]
        return np.sqrt(y0 * y0 + y1 * y1 + y2 * y2)

    def octant(self, d: np.ndarray) -> np.ndarray:
        """The octant in the axes of the ellipsoid: bit 0 — x ≥ 0, bit 1 — y ≥ 0, bit 2 — z ≥ 0."""
        s = self.S
        d = np.asarray(d, dtype=np.float64).reshape(-1, 3)
        out = np.zeros(d.shape[0], dtype=np.int8)
        for bit in range(3):
            y = s[bit, 0] * d[:, 0] + s[bit, 1] * d[:, 1] + s[bit, 2] * d[:, 2]
            out |= ((y >= 0.0).astype(np.int8)) << bit
        return out

    @property
    def r1(self) -> float:
        return self.radii[0]


@dataclass
class Neighbours:
    """The selection of one pass for `m` blocks; −1 / NaN — none. Rows in the order (h_s, number)."""

    idx: np.ndarray  # int32 [m, n_max]: numbers of the samples (positions in `RowSamples`)
    h: np.ndarray  # float64 [m, n_max]: h_s
    z: np.ndarray  # float64 [m, n_max]: the value used (top cut, averaging, the rule of Д-29)
    n: np.ndarray  # int16 [m]
    nh: np.ndarray  # int16 [m]: holes
    nhy: np.ndarray  # int16 [m]: touched by the rule of Д-29
    noct: np.ndarray  # int8 [m]: occupied octants
    ok: np.ndarray  # bool [m]: n_min, k_oct, n_holes_min hold
    ad: np.ndarray  # float64 [m]: mean euclidean distance to the selected (AD)
    dmin: np.ndarray  # float64 [m]: the least one (DMIN)

    @classmethod
    def empty(cls, m: int, width: int) -> Neighbours:
        return cls(np.full((m, width), -1, np.int32), np.full((m, width), np.nan), np.full((m, width), np.nan),
                   np.zeros(m, np.int16), np.zeros(m, np.int16), np.zeros(m, np.int16), np.zeros(m, np.int8),
                   np.zeros(m, np.bool_), np.full(m, np.nan), np.full(m, np.nan))  # fmt: skip

    def put(self, rows: np.ndarray, src: Neighbours, from_rows: np.ndarray) -> None:
        """self[rows] = src[from_rows] (the width of `src` may be smaller)."""
        w = src.idx.shape[1]
        self.idx[rows, :w] = src.idx[from_rows]
        self.h[rows, :w] = src.h[from_rows]
        self.z[rows, :w] = src.z[from_rows]
        for name in ("n", "nh", "nhy", "noct", "ok", "ad", "dmin"):
            getattr(self, name)[rows] = getattr(src, name)[from_rows]


@dataclass
class Found:
    """`Searcher.search`: for every block the selection of the first pass that holds."""

    nb: Neighbours
    pass_: np.ndarray  # int8 [m]: 1…P; 0 — no pass holds («мало проб»; the row of `nb` is empty)


@dataclass
class Candidates:
    """The candidates of one block for the inspector, by (h_s, number); at most `EXPLAIN_MAX`."""

    idx: np.ndarray  # int64 [k]: numbers of the samples of the pool
    h: np.ndarray  # float64 [k]
    dist: np.ndarray  # float64 [k]: euclidean
    octant: np.ndarray  # int8 [k]
    z: np.ndarray  # float64 [k]: the value used (t for a capped one)
    reason: np.ndarray  # uint8 [k]: index into `REASONS`
    total: int = 0  # the candidates before the cut to `EXPLAIN_MAX`
    n: int = 0  # selected
    nh: int = 0
    noct: int = 0
    nhy: int = 0
    ok: bool = False  # the pass holds
    radius: float = 0.0  # r1·factor of the pass

    def names(self) -> list[str]:
        return [REASONS[int(c)] for c in self.reason]


def _bad(message: str, hint: str = "Сообщите разработчику.") -> ApiError:
    return ApiError(500, "internal", message, hint)


class Searcher:
    """The search of one row of the plan: the tree is built once, the passes share it (the radius × factor)."""

    def __init__(self, s: RowSamples, ell: Ellipsoid, passes: Sequence[Pass], soft_dist: float = 0.0, *,
                 k_c: int | None = None) -> None:  # fmt: skip
        if not 1 <= len(passes) <= PASSES_MAX:
            raise _bad(f"Поиск: число проходов {len(passes)}, допустимо 1–{PASSES_MAX}.")
        if not all(r > 0.0 for r in ell.radii):
            raise _bad("Поиск: радиусы эллипсоида должны быть положительными.")
        self.s = s
        self.ell = ell
        self.passes = tuple(passes)
        self.soft = float(soft_dist)
        self.k_c = k_c
        self.rounds = 0  # repeats of a query with a larger k_c (the tests and the journal)
        self._S = _own(ell.S, np.float64)
        self._local = _own(s.local, np.float64)
        self._z = _own(s.z, np.float64)
        self._dom = _own(s.dom, np.int16)
        self._hole = _own(s.hole, np.int32)
        self._n_holes = max(len(s.holes), 1)
        soft_on = self.soft > 0.0 and bool((self._dom == S).any())
        ids = np.flatnonzero(self._dom <= (S if soft_on else D)).astype(np.int64)
        self._ids = np.append(ids, -1)  # the tree answers `len(ids)` for «no neighbour»
        self._n_tree = int(ids.shape[0])
        self._tree = None
        if ids.size:
            self._tree = cKDTree(self._local[ids] @ self._S.T, balanced_tree=False, compact_nodes=False)
        self._fp = [np.array([ell.radii[0] * p.factor, self.soft, p.hy.t if p.hy else 0.0, p.hy.q if p.hy else 0.0])
                    for p in self.passes]  # fmt: skip
        self._ip = [np.array([_hy_mode(p.hy), p.n_max, p.n_hole, p.n_oct, p.n_min, p.k_oct, p.n_holes_min],
                             dtype=np.int64) for p in self.passes]  # fmt: skip

    @property
    def n_passes(self) -> int:
        return len(self.passes)

    def radius(self, k: int) -> float:
        return self.fp_of(k)[0]

    def fp_of(self, k: int) -> np.ndarray:
        if not 0 <= k < len(self.passes):
            raise _bad(f"Поиск: нет прохода {k + 1}.")
        return self._fp[k]

    # --- a portion of the blocks ----------------------------------------------------------------------------

    def gather(self, centres: np.ndarray, k: int, *, todo: np.ndarray | None = None,
               exclude: tuple[str, np.ndarray] | None = None) -> Neighbours:  # fmt: skip
        """The selection of pass `k` (from 0) for the blocks `centres` [m, 3] (local coordinates); `todo` [m] —
        the blocks to do (the others are empty, `ok` False; a centre with a NaN is not done either). `exclude` —
        ("sample", numbers [m]) | ("hole", dense indices [m]) leaves a sample / a hole out for its block (xval)."""
        c = _centres(centres)
        out = Neighbours.empty(c.shape[0], self.passes[k].n_max)
        sel = np.isfinite(c).all(axis=1)
        if todo is not None:
            sel &= np.asarray(todo, dtype=np.bool_)
        self._run(out, c, k, np.flatnonzero(sel), exclude)
        return out

    def search(self, centres: np.ndarray, *, start: int = 0, todo: np.ndarray | None = None,
               exclude: tuple[str, np.ndarray] | None = None) -> Found:  # fmt: skip
        """For every block the first pass from `start` that holds (`pass_` 1…P), else 0 and an empty row."""
        c = _centres(centres)
        m = c.shape[0]
        width = max(p.n_max for p in self.passes[start:])
        res = Neighbours.empty(m, width)
        pass_ = np.zeros(m, np.int8)
        sel = np.isfinite(c).all(axis=1)
        if todo is not None:
            sel &= np.asarray(todo, dtype=np.bool_)
        pending = np.flatnonzero(sel)
        ex = None if exclude is None else (exclude[0], np.asarray(exclude[1]))
        for k in range(start, len(self.passes)):
            if pending.size == 0:
                break
            sub = Neighbours.empty(pending.size, self.passes[k].n_max)
            sub_ex = None if ex is None else (ex[0], ex[1][pending])
            self._run(sub, c[pending], k, np.arange(pending.size), sub_ex)
            ok = np.flatnonzero(sub.ok)
            if ok.size:
                res.put(pending[ok], sub, ok)
                pass_[pending[ok]] = k + 1
            pending = pending[~sub.ok]
        return Found(res, pass_)

    def _run(self, out: Neighbours, c: np.ndarray, k: int, rows: np.ndarray,
             exclude: tuple[str, np.ndarray] | None) -> None:  # fmt: skip
        """Fills the rows `rows` of `out` for the centres `c` (the same rows)."""
        if self._tree is None or rows.size == 0:
            return
        p = self.passes[k]
        fp, ip = self._fp[k], self._ip[k]
        rad = float(fp[0])
        bound = rad * (1.0 + 1e-9) + 1e-9
        ex_kind, ex_val = 0, np.zeros(1, np.int64)
        if exclude is not None:
            ex_kind = {"sample": 1, "hole": 2}[exclude[0]]
            ex_val = np.asarray(exclude[1], dtype=np.int64)
        kc = min(self.k_c if self.k_c else max(4 * p.n_max, K_MIN), self._n_tree)
        hole_cnt = np.zeros(self._n_holes, np.int32)
        pending = rows
        while pending.size:
            saturable = kc < self._n_tree
            again: list[np.ndarray] = []
            for a in range(0, pending.size, SUB):
                rb = pending[a : a + SUB]
                cb = np.ascontiguousarray(c[rb])
                dist, near = self._tree.query(cb @ self._S.T, k=kc, distance_upper_bound=bound)
                dist = dist.reshape(rb.size, kc)
                cand = np.ascontiguousarray(self._ids[near.reshape(rb.size, kc)])
                dkc = np.ascontiguousarray(dist[:, kc - 1])
                sat = np.isfinite(dkc) & saturable
                done = np.zeros(rb.size, np.bool_)
                exb = ex_val[rb] if ex_kind else ex_val
                sk.gather_kernel(cand, dkc, sat, rb, cb, self._local, self._z, self._dom, self._hole, self._S,
                                 fp, ip, ex_kind, exb, hole_cnt, out.idx, out.h, out.z, out.n, out.nh, out.nhy,
                                 out.noct, out.ok, out.ad, out.dmin, done)  # fmt: skip
                if not done.all():
                    again.append(rb[~done])
            pending = np.concatenate(again) if again else rows[:0]
            if pending.size:
                kc = min(kc * 4, self._n_tree)
                self.rounds += 1

    # --- one block, for the inspector --------------------------------------------------------------------------

    def explain(self, centre: np.ndarray, k: int, *, pool: RowSamples | None = None,
                limit: int = EXPLAIN_MAX) -> Candidates:  # fmt: skip
        """Every candidate of one block (local coordinates) with the reason of the pass `k` (Д-30): the samples of
        the pool (this row's by default; `all_domains` samples of 18.5 add the other domains) inside the largest
        radius of the passes, by (h_s, number), at most `limit`. `reason`: `REASONS`. The selection is that of
        `gather` (the same code), without the exclusion of cross-validation."""
        fp = self.fp_of(k)
        ip = self._ip[k]
        pl = self.s if pool is None else pool
        if pl is self.s:
            local, zs, dom, hole, n_holes = self._local, self._z, self._dom, self._hole, self._n_holes
        else:
            local, zs, dom = _own(pl.local, np.float64), _own(pl.z, np.float64), _own(pl.dom, np.int16)
            hole, n_holes = _own(pl.hole, np.int32), max(len(pl.holes), 1)
        c = np.asarray(centre, dtype=np.float64).reshape(3)
        rad = float(fp[0])
        rad_u = max(float(f[0]) for f in self._fp)
        ids, h, zv, cd, oc, ds = sk.classify_kernel(
            float(c[0]), float(c[1]), float(c[2]), local, zs, dom, self._S, rad_u, rad, self.soft, int(ip[0]),
            float(fp[2]), float(fp[3]),
        )  # fmt: skip
        order = np.lexsort((ids, h))
        ids, h, zv, cd, oc, ds = ids[order], h[order], zv[order], cd[order], oc[order], ds[order]
        elig = np.flatnonzero((cd == sk.ELIGIBLE) | (cd == sk.CAPPED))
        code = np.empty(elig.size, np.uint8)
        n, nh, mask = sk.pick(np.ascontiguousarray(hole[ids[elig]], dtype=np.int32),
                              np.ascontiguousarray(oc[elig], dtype=np.int64), int(elig.size), int(ip[1]),
                              int(ip[2]), int(ip[3]), np.zeros(n_holes, np.int32), np.zeros(8, np.int64), code,
                              True)  # fmt: skip
        reason = cd.copy()
        reason[elig] = code
        reason[elig[(code == sk.R_SELECTED) & (cd[elig] == sk.CAPPED)]] = sk.R_HY_CAPPED
        reach = rad
        if n >= int(ip[1]) and n > 0:
            reach = float(h[elig[np.flatnonzero(code == sk.R_SELECTED)[-1]]])
        nhy = int(np.count_nonzero(((cd == sk.R_HY_DROPPED) | (cd == sk.CAPPED)) & (h <= reach)))
        noct = bin(mask).count("1")
        ok = bool(n >= int(ip[4]) and n > 0 and noct >= int(ip[5]) and nh >= int(ip[6]))
        keep = slice(0, limit)
        return Candidates(
            ids[keep], h[keep], ds[keep], oc[keep].astype(np.int8), zv[keep], reason[keep].astype(np.uint8),
            total=int(ids.size), n=int(n), nh=int(nh), noct=noct, nhy=min(nhy, 32767), ok=ok, radius=rad,
        )  # fmt: skip


def _hy_mode(hy: Hy | None) -> int:
    if hy is None:
        return sk.HY_OFF
    return sk.HY_CAP if hy.mode == "cap" else sk.HY_DROP


def _centres(centres: np.ndarray) -> np.ndarray:
    c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
    return c if c.flags.c_contiguous else np.ascontiguousarray(c)


# --- the warm-up -------------------------------------------------------------------------------------------------


def warm_search() -> None:
    """Compiles the kernels (one signature: the searcher copies a read-only input) and runs a block through the
    tree, the passes and the inspector — also with a read-only column of samples (lesson 17.39б)."""
    from .samples import prepare

    rng = np.random.default_rng(1)
    n = 40
    xyz = rng.uniform(-30.0, 30.0, (n, 3))
    dom = np.zeros(n, np.int16)
    dom[::7] = 1
    hole = rng.integers(0, 4, n).astype(np.int32)
    s = prepare(xyz, rng.uniform(0.0, 5.0, n), None, None, hole, ["a", "b", "c", "d"], dom,
                np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    ell = Ellipsoid.of((20.0, 10.0, 5.0), (40.0, 30.0, 20.0))
    passes = (Pass(1.0, 3, 8, 3, 2, 1, 1, Hy(3.0, 0.5, "cap")), Pass(2.0, 2, 8, 0, 0, 0, 0, Hy(3.0, 0.5, "drop")))
    centres = np.array([[0.0, 0.0, 0.0], [5.0, -5.0, 2.0]])
    for ro in (False, True):
        pool = s
        if ro:
            for a in (s.local, s.z, s.dom, s.hole):
                a.setflags(write=False)
        sr = Searcher(pool, ell, passes, 10.0)
        sr.gather(centres, 0)
        sr.gather(centres, 1, todo=np.array([True, False]), exclude=("sample", np.array([0, 1])))
        sr.gather(centres, 1, exclude=("hole", np.array([0, 1])))
        sr.search(centres)
        sr.explain(centres[0], 0)
