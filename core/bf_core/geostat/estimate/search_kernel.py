"""numba kernels of the neighbourhood search (ТЗ 8.8.2; ADR 0024 §4; Э18, 18.6): the selection of the samples of
one pass for a portion of the blocks, and the classification of the candidates of one block for the inspector (Д-30).
`search.py` is the interface; this module is the hot loop. float64, no `prange`, no `fastmath`, `cache=True,
nogil=True`; a kernel allocates only its small scratch buffers; the order of the operations is fixed (one input — one
answer to the bit, on any number of threads).

The rules of one pass, in this order (Д-29, № 92) — the same code answers the blocks (`gather_kernel`) and the
inspector (`classify_kernel` + `pick`):

    1. the candidate is inside the ellipsoid of the pass: h_s = ‖S·Δ‖ ≤ rad, rad = r1·factor      (else «outside»)
    2. its domain: D — always; S — only with d_м > 0 and the euclidean distance to the centre ≤ d_м   («other_domain»,
       «soft_far»; any other domain — «other_domain»)
    3. the rule of the high samples (Д-29): a sample above t farther than q·rad is dropped («hy_dropped») or capped
       to t («hy_capped»)
    4. the order (h_s, number of the sample)                                                  (№ 76)
    5. the walk in that order: n_max stops it («over_n_max»); the limit of a hole («hole_limit»), then of an octant
       («octant_limit») skips a candidate; the rest is selected
    6. n_min, the occupied octants ≥ k_oct, the holes ≥ n_holes_min make the pass `ok`

`NHY` (Д-29) — the candidates the rule touched (dropped or capped) not farther than the reach of the selection: the
h_s of the n_max-th selected sample when the walk was stopped by n_max, else the radius of the pass. Nothing farther
could have been selected, so the number does not depend on how many candidates were looked at (k_c).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.jit import njit

# the codes of `search.REASONS` (the index; u1)
R_SELECTED, R_HY_CAPPED, R_OUTSIDE, R_HOLE_LIMIT, R_OCTANT_LIMIT, R_OVER_N_MAX = 0, 1, 2, 3, 4, 5
R_OTHER_DOMAIN, R_SOFT_FAR, R_HY_DROPPED, R_MERGED = 6, 7, 8, 9
ELIGIBLE = 255  # takes part in the walk
CAPPED = 254  # takes part, the value is capped (z = t)

HY_OFF, HY_DROP, HY_CAP = 0, 1, 2
INSERTION_MAX = 96  # up to this many candidates the order is made by insertion (the tree gives them nearly sorted)


@njit(cache=True, nogil=True, inline="always")
def hs(s: np.ndarray, dx: float, dy: float, dz: float) -> float:
    """h_s = ‖S·Δ‖ — the same operations, in the same order, as `search.Ellipsoid.h`."""
    y0 = s[0, 0] * dx + s[0, 1] * dy + s[0, 2] * dz
    y1 = s[1, 0] * dx + s[1, 1] * dy + s[1, 2] * dz
    y2 = s[2, 0] * dx + s[2, 1] * dy + s[2, 2] * dz
    return math.sqrt(y0 * y0 + y1 * y1 + y2 * y2)


@njit(cache=True, nogil=True, inline="always")
def octant(s: np.ndarray, dx: float, dy: float, dz: float) -> int:
    """The octant in the axes of the ellipsoid: bit 0 — x ≥ 0, bit 1 — y ≥ 0, bit 2 — z ≥ 0 (a sample on a plane
    belongs to the positive side). The scaling of S is positive, so the sign of S·Δ is the sign of R·Δ."""
    y0 = s[0, 0] * dx + s[0, 1] * dy + s[0, 2] * dz
    y1 = s[1, 0] * dx + s[1, 1] * dy + s[1, 2] * dz
    y2 = s[2, 0] * dx + s[2, 1] * dy + s[2, 2] * dz
    o = 0
    if y0 >= 0.0:
        o |= 1
    if y1 >= 0.0:
        o |= 2
    if y2 >= 0.0:
        o |= 4
    return o


@njit(cache=True, nogil=True)
def classify(dx: float, dy: float, dz: float, s: np.ndarray, rad: float, soft: float, dom: int, zv: float,
             hy_mode: int, hy_t: float, hy_q: float) -> tuple[int, float, float]:  # fmt: skip
    """(code, h_s, z used) of one candidate: the rules 1–3; `ELIGIBLE` / `CAPPED` take part in the walk."""
    h = hs(s, dx, dy, dz)
    if h > rad:
        return R_OUTSIDE, h, zv
    if dom != 0:
        if dom != 1 or soft <= 0.0:
            return R_OTHER_DOMAIN, h, zv
        if math.sqrt(dx * dx + dy * dy + dz * dz) > soft:
            return R_SOFT_FAR, h, zv
    if hy_mode != HY_OFF and zv > hy_t and h > hy_q * rad:
        if hy_mode == HY_DROP:
            return R_HY_DROPPED, h, zv
        return CAPPED, h, hy_t
    return ELIGIBLE, h, zv


@njit(cache=True, nogil=True)
def order_by_h(h: np.ndarray, ident: np.ndarray, m: int, perm: np.ndarray) -> None:
    """perm[:m] — the positions 0…m−1 sorted by (h, ident): the rule 4. Insertion for a short list (the tree gives
    the candidates nearly in order), a stable merge sort over the numbers for a long one; the same order."""
    if m <= INSERTION_MAX:
        for a in range(m):
            perm[a] = a
        for a in range(1, m):
            p = perm[a]
            hp = h[p]
            ip = ident[p]
            b = a - 1
            while b >= 0:
                q = perm[b]
                if h[q] > hp or (h[q] == hp and ident[q] > ip):
                    perm[b + 1] = q
                    b -= 1
                else:
                    break
            perm[b + 1] = p
        return
    by_id = np.argsort(ident[:m])
    by_h = np.argsort(h[:m][by_id], kind="mergesort")
    for a in range(m):
        perm[a] = by_id[by_h[a]]


@njit(cache=True, nogil=True)
def pick(hole_c: np.ndarray, oct_c: np.ndarray, m: int, n_max: int, n_hole: int, n_oct: int, hole_cnt: np.ndarray,
         oct_cnt: np.ndarray, code: np.ndarray, fill: bool) -> tuple[int, int, int]:  # fmt: skip
    """The walk of the rule 5 over the candidates `0…m−1` in the order of the rule 4. `code[a]` — SELECTED,
    HOLE_LIMIT, OCTANT_LIMIT or OVER_N_MAX (the rest after n_max is filled only with `fill`). `hole_cnt` — the
    counters of the holes (zeros on entry and on exit). Returns (n, holes, mask of the occupied octants)."""
    for o in range(8):
        oct_cnt[o] = 0
    n = 0
    nh = 0
    mask = 0
    last = 0
    for a in range(m):
        if n >= n_max:
            if not fill:
                break
            code[a] = R_OVER_N_MAX
            last = a + 1
            continue
        last = a + 1
        hh = hole_c[a]
        if n_hole > 0 and hole_cnt[hh] >= n_hole:
            code[a] = R_HOLE_LIMIT
            continue
        o = oct_c[a]
        if n_oct > 0 and oct_cnt[o] >= n_oct:
            code[a] = R_OCTANT_LIMIT
            continue
        code[a] = R_SELECTED
        if hole_cnt[hh] == 0:
            nh += 1
        hole_cnt[hh] += 1
        oct_cnt[o] += 1
        mask |= 1 << o
        n += 1
    for a in range(last):
        if code[a] == R_SELECTED:
            hole_cnt[hole_c[a]] = 0
    return n, nh, mask


@njit(cache=True, nogil=True, inline="always")
def popcount8(mask: int) -> int:
    c = 0
    for o in range(8):
        if (mask >> o) & 1:
            c += 1
    return c


@njit(cache=True, nogil=True)
def gather_kernel(cand: np.ndarray, dkc: np.ndarray, sat: np.ndarray, rows: np.ndarray, centres: np.ndarray,
                  local: np.ndarray, zs: np.ndarray, dom: np.ndarray, hole: np.ndarray, s: np.ndarray,
                  fp: np.ndarray, ip: np.ndarray, ex_kind: int, ex_val: np.ndarray, hole_cnt: np.ndarray,
                  o_idx: np.ndarray, o_h: np.ndarray, o_z: np.ndarray, o_n: np.ndarray, o_nh: np.ndarray,
                  o_nhy: np.ndarray, o_noct: np.ndarray, o_ok: np.ndarray, o_ad: np.ndarray, o_dmin: np.ndarray,
                  done: np.ndarray) -> None:  # fmt: skip
    """The selection of one pass for `nb` blocks from the candidates the tree gave.

    cand [nb, kc]   the numbers of the samples (−1 — none), dkc [nb] the tree distance of the last one, sat [nb] the
                    tree gave kc candidates inside the bound (more may exist)
    rows [nb]       the row of the outputs of every block;  centres [nb, 3] local coordinates
    fp = rad, d_м, t, q; ip = hy mode, n_max, n_hole, n_oct, n_min, k_oct, n_holes_min
    ex_kind, ex_val 0 — nothing is left out; 1 — the sample ex_val[b]; 2 — the hole ex_val[b] (cross-validation)
    o_*             the outputs (`search.Neighbours`) written at `rows`; −1 / NaN pad the idx / h / z
    done [nb]       the selection is final (the walk is decided by the candidates at hand, see `search.Searcher`)
    """
    nb, kc = cand.shape
    rad, soft, hy_t, hy_q = fp[0], fp[1], fp[2], fp[3]
    hy_mode, n_max, n_hole, n_oct, n_min, k_oct, n_holes_min = ip[0], ip[1], ip[2], ip[3], ip[4], ip[5], ip[6]
    id_c = np.empty(kc, np.int64)
    h_c = np.empty(kc, np.float64)
    z_c = np.empty(kc, np.float64)
    cap_c = np.empty(kc, np.bool_)
    perm = np.empty(kc, np.int64)
    hole_s = np.empty(kc, np.int32)
    oct_s = np.empty(kc, np.int64)
    code = np.empty(kc, np.uint8)
    touched = np.empty(kc, np.float64)
    oct_cnt = np.zeros(8, np.int64)
    for b in range(nb):
        r = rows[b]
        c0, c1, c2 = centres[b, 0], centres[b, 1], centres[b, 2]
        m = 0
        nt = 0
        for t in range(kc):
            j = cand[b, t]
            if j < 0:
                continue
            if ex_kind == 1 and j == ex_val[b]:
                continue
            if ex_kind == 2 and hole[j] == ex_val[b]:
                continue
            dx = local[j, 0] - c0
            dy = local[j, 1] - c1
            dz = local[j, 2] - c2
            cd, h, zv = classify(dx, dy, dz, s, rad, soft, dom[j], zs[j], hy_mode, hy_t, hy_q)
            if cd == R_HY_DROPPED:
                touched[nt] = h
                nt += 1
                continue
            if cd != ELIGIBLE and cd != CAPPED:
                continue
            if cd == CAPPED:
                touched[nt] = h
                nt += 1
            id_c[m] = j
            h_c[m] = h
            z_c[m] = zv
            cap_c[m] = cd == CAPPED
            m += 1
        order_by_h(h_c, id_c, m, perm)
        for a in range(m):
            j = id_c[perm[a]]
            hole_s[a] = hole[j]
            oct_s[a] = octant(s, local[j, 0] - c0, local[j, 1] - c1, local[j, 2] - c2)
        n, nh, mask = pick(hole_s, oct_s, m, n_max, n_hole, n_oct, hole_cnt, oct_cnt, code, False)
        w = o_idx.shape[1]
        k = 0
        dsum = 0.0
        dmin = math.nan
        for a in range(m if n > 0 else 0):
            if code[a] != R_SELECTED:
                continue
            p = perm[a]
            j = id_c[p]
            o_idx[r, k] = j
            o_h[r, k] = h_c[p]
            o_z[r, k] = z_c[p]
            dx = local[j, 0] - c0
            dy = local[j, 1] - c1
            dz = local[j, 2] - c2
            e = math.sqrt(dx * dx + dy * dy + dz * dz)
            dsum += e
            if k == 0 or e < dmin:
                dmin = e
            k += 1
            if k >= n:
                break
        for a in range(k, w):
            o_idx[r, a] = -1
            o_h[r, a] = math.nan
            o_z[r, a] = math.nan
        reach = rad
        if n >= n_max and n > 0:
            reach = o_h[r, n - 1]
        cnt = 0
        for a in range(nt):
            if touched[a] <= reach:
                cnt += 1
        noct = popcount8(mask)
        o_n[r] = n
        o_nh[r] = nh
        o_nhy[r] = min(cnt, 32767)
        o_noct[r] = noct
        o_ok[r] = n >= n_min and n > 0 and noct >= k_oct and nh >= n_holes_min
        o_ad[r] = dsum / n if n > 0 else math.nan
        o_dmin[r] = dmin
        done[b] = (not sat[b]) or (reach < dkc[b] - (1e-9 * dkc[b] + 1e-9))


@njit(cache=True, nogil=True)
def classify_kernel(c0: float, c1: float, c2: float, local: np.ndarray, zs: np.ndarray, dom: np.ndarray,
                    s: np.ndarray, rad_u: float, rad: float, soft: float, hy_mode: int, hy_t: float,
                    hy_q: float) -> tuple[Any, ...]:  # fmt: skip
    """Every sample of the pool inside `rad_u` (the largest radius of the passes), classified for the pass of radius
    `rad`: (numbers, h_s, z used, code of the rules 1–3, octant, euclidean distance). Unsorted — ascending numbers."""
    n = local.shape[0]
    keep = np.empty(n, np.int64)
    m = 0
    for j in range(n):
        dx = local[j, 0] - c0
        dy = local[j, 1] - c1
        dz = local[j, 2] - c2
        if hs(s, dx, dy, dz) <= rad_u:
            keep[m] = j
            m += 1
    ids = keep[:m].copy()
    h = np.empty(m, np.float64)
    zv = np.empty(m, np.float64)
    cd = np.empty(m, np.uint8)
    oc = np.empty(m, np.int8)
    ds = np.empty(m, np.float64)
    for a in range(m):
        j = ids[a]
        dx = local[j, 0] - c0
        dy = local[j, 1] - c1
        dz = local[j, 2] - c2
        c, hh, zz = classify(dx, dy, dz, s, rad, soft, dom[j], zs[j], hy_mode, hy_t, hy_q)
        cd[a] = c
        h[a] = hh
        zv[a] = zz
        oc[a] = octant(s, dx, dy, dz)
        ds[a] = math.sqrt(dx * dx + dy * dy + dz * dz)
    return ids, h, zv, cd, oc, ds
