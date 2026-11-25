"""The kernel of the SIS (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.33): the steps of one realization — see `sis.py` for the
algorithm. `sis_steps` is `cache=True, nogil=True`; every array is the caller's own writable copy (one signature)."""

from __future__ import annotations

import numpy as np

from bf_core.geostat.estimate.indicator import order_relations
from bf_core.geostat.estimate.krige import O_EST, apply_weights, krige
from bf_core.geostat.simulation import nodes as nd
from bf_core.jit import njit


@njit(cache=True, nogil=True)
def _pick_value(u: float, f: np.ndarray, th: np.ndarray, tail: np.ndarray) -> tuple[int, float]:
    """The class (0 … K) and the value of the draw u from F (non-decreasing, [0; 1]) — the module text.
    tail = zmin, zmax, ltail code, utail code (0 none, 1 linear, 2 power, 3 hyperbolic), 1 / ltpar, 1 / utpar, utpar."""
    kk = th.shape[0]
    k = kk
    for q in range(kk):
        if u <= f[q]:
            k = q
            break
    if k == 0:
        if tail[2] == 0.0:
            return 0, th[0]
        share = u / f[0] if f[0] > 0.0 else 0.0
        share = min(max(share, 0.0), 1.0)
        g = share ** tail[4]
        return 0, tail[0] * (1.0 - g) + th[0] * g
    if k < kk:
        d = f[k] - f[k - 1]
        s = (u - f[k - 1]) / d if d > 0.0 else 0.5
        s = min(max(s, 0.0), 1.0)
        return k, th[k - 1] * (1.0 - s) + th[k] * s
    last = th[kk - 1]
    if tail[3] == 0.0:
        return k, last
    rest = 1.0 - f[kk - 1]
    if tail[3] == 3.0:
        v = (last ** tail[6] * rest / (1.0 - u)) ** (1.0 / tail[6]) if rest > 0.0 else last
        return k, max(v, last)
    share = (u - f[kk - 1]) / rest if rest > 0.0 else 0.0
    share = min(max(share, 0.0), 1.0)
    g = share ** tail[5]
    return k, last * (1.0 - g) + tail[1] * g


@njit(cache=True, nogil=True)
def _pick_category(u: float, f: np.ndarray, prop: np.ndarray) -> int:
    """The category of the draw u from the probabilities f: clipped to [0; 1] and divided by the sum, in place (the
    sum 0 — the proportions); the first whose cumulative sum is above u (the last one with p > 0 on rounding)."""
    s = 0.0
    for q in range(f.shape[0]):
        v = f[q]
        if not v > 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        f[q] = v
        s += v
    if not s > 0.0:
        s = 0.0
        for q in range(f.shape[0]):
            f[q] = prop[q]
            s += prop[q]
    acc = 0.0
    last = 0
    pick = -1
    for q in range(f.shape[0]):
        f[q] /= s
        if f[q] > 0.0:
            last = q
        acc += f[q]
        if pick < 0 and u < acc:
            pick = q
    return pick if pick >= 0 else last


@njit(cache=True, nogil=True)
def _indicator(c: int, q: int, categorical: int) -> float:
    if categorical == 1:
        return 1.0 if c == q else 0.0
    return 1.0 if c <= q else 0.0


@njit(cache=True, nogil=True)
def sis_steps(kinds: np.ndarray, pars: np.ndarray, head: np.ndarray, categorical: int, keep: int, cont: int,
              index: np.ndarray, nx: int, ny: int, nz: int, template: np.ndarray, cell: np.ndarray, centres: np.ndarray,
              cidx: np.ndarray, cn: np.ndarray, sxyz: np.ndarray, scls: np.ndarray, sz: np.ndarray, path: np.ndarray,
              draws: np.ndarray, s0: int, s1: int, n_nodes: int, tol2: float, ok: int, th: np.ndarray, tail: np.ndarray,
              prop: np.ndarray, skip: np.ndarray, simulated: np.ndarray, cls: np.ndarray, out: np.ndarray,
              pout: np.ndarray, found: np.ndarray, xyz: np.ndarray, cd: np.ndarray, zz: np.ndarray,
              centre: np.ndarray, offs: np.ndarray, a: np.ndarray, rhs: np.ndarray, sol: np.ndarray,
              kout: np.ndarray, f: np.ndarray, count: np.ndarray) -> None:  # fmt: skip
    """The steps s0 … s1−1 of one realization (the module text): `cls[node]` (the class), `out[node]` (the value, when
    `cont` = 1), `pout[node]` (the probabilities, when `keep` = 1), `simulated[node]`; `count` += (degenerate, free,
    on a sample). `kinds`, `pars`, `head` — [M, …], M = 1 (the median model) or K. `ok` = 0 (the SK) is an
    argument so that it is a variable of one type for `krige` (not a literal)."""
    kk = prop.shape[0]
    nm = head.shape[0]
    for s in range(s0, s1):
        node = path[s]
        cx, cy, cz = centres[node, 0], centres[node, 1], centres[node, 2]
        ns = int(cn[node])
        hit = -1
        for q in range(ns):  # a sample at the node is the node
            si = cidx[node, q]
            if scls[si] < 0:
                continue
            dx, dy, dz = sxyz[si, 0] - cx, sxyz[si, 1] - cy, sxyz[si, 2] - cz
            if dx * dx + dy * dy + dz * dz <= tol2:
                hit = si
                break
        if hit >= 0:
            c = scls[hit]
            cls[node] = c
            if cont == 1:
                out[node] = sz[hit]
            if keep == 1:
                for q in range(kk):
                    pout[node, q] = _indicator(c, q, categorical)
            simulated[node] = True
            count[2] += 1
            continue
        m = 0
        for q in range(ns):
            si = cidx[node, q]
            if scls[si] < 0:
                continue
            xyz[m, 0] = sxyz[si, 0]
            xyz[m, 1] = sxyz[si, 1]
            xyz[m, 2] = sxyz[si, 2]
            cd[m] = scls[si]
            m += 1
        m0 = m
        nn = nd.find_nodes(index, nx, ny, nz, simulated, template, cell[node], n_nodes, found)
        for q in range(nn):
            other = found[q]
            ox, oy, oz = centres[other, 0], centres[other, 1], centres[other, 2]
            same = False
            for t in range(m0):  # the node that is a conditioning sample is that sample
                dx, dy, dz = xyz[t, 0] - ox, xyz[t, 1] - oy, xyz[t, 2] - oz
                if dx * dx + dy * dy + dz * dz <= tol2:
                    same = True
                    break
            if same:
                continue
            xyz[m, 0] = ox
            xyz[m, 1] = oy
            xyz[m, 2] = oz
            cd[m] = cls[other]
            m += 1
        if m == 0:
            count[1] += 1
            for q in range(kk):
                f[q] = prop[q]
        else:
            centre[0], centre[1], centre[2] = cx, cy, cz
            failed = False
            if nm == 1:  # the median model: one system, the weights for every threshold
                for i in range(m):
                    zz[i] = _indicator(cd[i], 0, categorical)
                st = krige(kinds[0], pars[0], head[0], xyz, m, zz, centre, offs, head[0, 1], prop[0], ok, a, rhs, sol,
                           kout)  # fmt: skip
                if st == 0:
                    f[0] = kout[O_EST]
                    for q in range(1, kk):
                        for i in range(m):
                            zz[i] = _indicator(cd[i], q, categorical)
                        f[q] = apply_weights(sol, m, zz, prop[q], ok)
                else:
                    failed = True
                    for q in range(kk):
                        f[q] = prop[q]
            else:
                for q in range(kk):
                    f[q] = prop[q]
                    if skip[q] != 0:
                        continue
                    for i in range(m):
                        zz[i] = _indicator(cd[i], q, categorical)
                    st = krige(kinds[q], pars[q], head[q], xyz, m, zz, centre, offs, head[q, 1], prop[q], ok, a, rhs,
                               sol, kout)  # fmt: skip
                    if st == 0:
                        f[q] = kout[O_EST]
                    else:
                        failed = True
            if failed:
                count[0] += 1
            for q in range(kk):  # a constant indicator is not kriged
                if skip[q] != 0:
                    f[q] = prop[q]
        u = draws[s]
        if categorical == 1:
            k = _pick_category(u, f, prop)
        else:
            order_relations(f)
            k, v = _pick_value(u, f, th, tail)
            out[node] = v
        cls[node] = k
        if keep == 1:
            for q in range(kk):
                pout[node, q] = f[q]
        simulated[node] = True
