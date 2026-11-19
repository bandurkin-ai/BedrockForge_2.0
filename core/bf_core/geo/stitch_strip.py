"""The strip between two contours (ADR 0015 §7, «Соответствие вершин», «Тай-линии»).

    strip(pa, pb, la, lb, gap, ties, tick)   triangles between two counter-clockwise contours, or None
    best_start(qa, qb, hh)                   the start of the cheapest strip and its cost (exact)
    start_cost(qa, qb, hh, s)                the cost of the cheapest strip that starts at (0, s)
    match_coords(q)                          a contour brought to its centre and to unit size

The strip is a shortest path in the graph of Fuchs, Kedem and Uselton (1977): node (i, j) is the span
a_i–b_j, a step along either contour adds one triangle and costs the length of the new span. Spans are
measured after both contours are brought to a common centre and size (`match_coords`), so a shifted or
smaller neighbour is matched by shape, not by absolute position; `hh` is the squared gap between the
sections in those units. The best start is found exactly: shortest paths for different starts do not cross,
which bounds each search by two known paths (O(n·m·log m)). A path goes once round both contours, so the
strip has no twist by construction. Tie lines fix nodes of the path: the search is run separately between
consecutive ties, so every tie is an edge of the result. A path that turns fewer than three times walks a
whole contour from one vertex of the other (two cones glued along an edge, not a strip); then the search that
counts the turns (`_ring_path`) is used instead.

Memory and time. Spans are computed where they are needed, not stored; the cheapest costs keep two rows; the
moves take 2·n·m bytes and the bounds of the paths of every start 8·n·m bytes. A pair has at most
`MAX_SPANS` spans (n·m), otherwise StitchError (thin the contours): about 160 MB and a second at the limit.
The search of the best start does at most `BUDGET` cells a call and goes on from where it stopped, so `tick`
(which may raise to cancel) is called between the portions.

Ported from BF `geo/stitch.py` (`_span_matrix`, `_dp_fill`, `_dp_bounds`, `_dp_trace`, `_ring_path`,
`_best_start`, `_match_coords`, `_tie_segments`, `_strip`) with the same paths. Differences: spans computed in
place (BF stored the n × m matrix), two rows of costs, int32 bounds, the resumable search of the best start,
the limit on the size of a pair. The kernels release the GIL; numba is imported with this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from bf_core.jit import njit

MAX_SPANS = 16_000_000  # n · m of one pair of contours
BUDGET = 20_000_000  # cells of the search of the best start between two calls of `tick`

Tick = Callable[[], None] | None


@njit(cache=True, nogil=True, inline="always")
def _span(a, b, hh, i, j):  # type: ignore[no-untyped-def]
    """Length of the span a_i–b_j for contours brought to a common centre and size, √hh apart."""
    dx = a[i, 0] - b[j, 0]
    dy = a[i, 1] - b[j, 1]
    return math.sqrt(dx * dx + dy * dy + hh)


@njit(cache=True, nogil=True)
def _dp_fill(a, b, hh, i0, di, j0, dj, lo, hi, cost, back):  # type: ignore[no-untyped-def]
    """Cheapest monotone path from node (i0, j0) to (i0 + di, j0 + dj); returns its cost and the cells visited.

    Row r is vertex (i0 + r) mod n of the first contour, column j is vertex j mod m of the second
    (columns are absolute, 0..2m). Only columns lo[r]..hi[r] of each row are visited. `back` gets
    1 where the path came along the first contour (from the row above) and 2 along the second.
    `cost` (2, 2m + 1) keeps the rows r − 1 and r.
    """
    n, m = len(a), len(b)
    last = j0 + dj
    cells = 0
    for r in range(di + 1):
        ia = (i0 + r) % n
        cur = r & 1
        prv = 1 - cur
        left = lo[r] if lo[r] > j0 else j0
        right = hi[r] if hi[r] < last else last
        up_lo = j0
        up_hi = j0 - 1
        if r > 0:
            up_lo = lo[r - 1] if lo[r - 1] > j0 else j0
            up_hi = hi[r - 1] if hi[r - 1] < last else last
        jb = left % m if left >= 0 else 0  # j mod m, kept without a division per cell
        ax, ay = a[ia, 0], a[ia, 1]
        run = np.inf  # cost of the cell to the left, kept out of memory (the chain of the row is the slow part)
        for j in range(left, right + 1):
            if r == 0 and j == j0:
                run = 0.0
                back[0, j] = 0
            else:
                # As in BF: along the first contour (from the row above) wins a tie.
                best = run
                move = 2
                if up_lo <= j <= up_hi and cost[prv, j] <= best:
                    best = cost[prv, j]
                    move = 1
                dx = ax - b[jb, 0]
                dy = ay - b[jb, 1]
                run = best + math.sqrt(dx * dx + dy * dy + hh)
                back[r, j] = move
            cost[cur, j] = run
            jb += 1
            if jb == m:
                jb = 0
        if right >= left:
            cells += right - left + 1
    return cost[di & 1, last], cells


@njit(cache=True, nogil=True)
def _dp_bounds(back, di, j0, dj, first, last):  # type: ignore[no-untyped-def]
    """First and last column of the found path in every row."""
    r = di
    j = j0 + dj
    last[r] = j
    while r > 0 or j > j0:
        if back[r, j] == 1:
            first[r] = j
            r -= 1
            last[r] = j
        else:
            j -= 1
    first[0] = j0


@njit(cache=True, nogil=True)
def _dp_trace(back, i0, di, j0, dj, n, m, la, lb, tris, moves, k0):  # type: ignore[no-untyped-def]
    """Triangles of the found path into tris[k0 : k0 + di + dj] (vertex numbers from la, lb);
    moves gets 1 for a step along the first contour and 2 for a step along the second.

    With both contours counter-clockwise seen from the second one's side, the triangles
    (a_i, a_i+1, b_j) and (b_j+1, b_j, a_i) look away from the contours' interior.
    """
    r = di
    j = j0 + dj
    k = k0 + di + dj - 1
    while r > 0 or j > j0:
        moves[k] = back[r, j]
        if back[r, j] == 1:
            tris[k, 0] = la[(i0 + r - 1) % n]
            tris[k, 1] = la[(i0 + r) % n]
            tris[k, 2] = lb[j % m]
            r -= 1
        else:
            tris[k, 0] = lb[j % m]
            tris[k, 1] = lb[(j - 1) % m]
            tris[k, 2] = la[(i0 + r) % n]
            j -= 1
        k -= 1


@njit(cache=True, nogil=True)
def _ring_path(a, b, hh, i0, j0, lo, hi, la, lb, tris):  # type: ignore[no-untyped-def]
    """Cheapest path once round both contours from node (i0, j0) that turns at least three times.

    A path with fewer turns walks one whole contour from a single vertex of the other; it passes
    the same span twice, so the "strip" is two cones glued along an edge. The usual search finds
    such a path only for very dissimilar contours; then this one is used. State: the direction
    of the last step and the number of turns so far (0..3). Row r may use columns lo[r]..hi[r]
    (relative to the start; this is how tie lines are kept). Returns the cost (inf: no path).
    """
    n, m = len(a), len(b)
    cost = np.full((2, m + 1, 8), np.inf)
    back = np.zeros((n + 1, m + 1, 8), dtype=np.int8)  # previous state + 1
    for r in range(n + 1):
        ia = (i0 + r) % n
        cur = r & 1
        prv = 1 - cur
        for j in range(m + 1):
            for state in range(8):
                cost[cur, j, state] = np.inf
            if (r == 0 and j == 0) or j < lo[r] or j > hi[r]:
                continue
            w = _span(a, b, hh, ia, (j0 + j) % m)
            for move in range(2):  # 0: along the first contour (from the row above), 1: along the second
                pr = r - 1 if move == 0 else r
                pj = j if move == 0 else j - 1
                if pr < 0 or pj < 0:
                    continue
                if pr == 0 and pj == 0:
                    cost[cur, j, move * 4] = w
                    back[r, j, move * 4] = 0
                    continue
                row = prv if move == 0 else cur
                for state in range(8):
                    c = cost[row, pj, state]
                    if c == np.inf:
                        continue
                    turns = state % 4
                    if state // 4 != move and turns < 3:
                        turns += 1
                    new = move * 4 + turns
                    if c + w < cost[cur, j, new]:
                        cost[cur, j, new] = c + w
                        back[r, j, new] = state + 1
    end = n & 1
    state = 3 if cost[end, m, 3] <= cost[end, m, 7] else 7
    total = cost[end, m, state]
    if total == np.inf:
        return total
    r, j = n, m
    k = n + m - 1
    while r > 0 or j > 0:
        before = back[r, j, state] - 1
        if state // 4 == 0:
            tris[k, 0] = la[(i0 + r - 1) % n]
            tris[k, 1] = la[(i0 + r) % n]
            tris[k, 2] = lb[(j0 + j) % m]
            r -= 1
        else:
            tris[k, 0] = lb[(j0 + j) % m]
            tris[k, 1] = lb[(j0 + j - 1) % m]
            tris[k, 2] = la[(i0 + r) % n]
            j -= 1
        state = before
        k -= 1
    return total


@njit(cache=True, nogil=True)
def _best_start(a, b, hh, cost, back, first, last, lo, hi, stack, state, best, budget):  # type: ignore[no-untyped-def]
    """The vertex s of the second contour to pair with vertex 0 of the first for the cheapest strip.

    Exact search over all starts (Fuchs, Kedem, Uselton 1977): the paths for starts a < s < b lie
    between the paths for a and b, so each is searched only inside that band. Resumable: state =
    [top of the stack, best s, started], best = [best cost]; returns True when the search is over,
    False after `budget` cells (call again with the same arrays).
    """
    n, m = len(a), len(b)
    done = 0
    if state[2] == 0:
        for r in range(n + 1):
            lo[r] = 0
            hi[r] = m
        c, cells = _dp_fill(a, b, hh, 0, n, 0, m, lo, hi, cost, back)
        done += cells
        best[0] = c
        state[1] = 0
        _dp_bounds(back, n, 0, m, first[0], last[0])
        for r in range(n + 1):
            first[m, r] = first[0, r] + m
            last[m, r] = last[0, r] + m
        stack[0, 0], stack[0, 1] = 0, m
        state[0] = 1
        state[2] = 1
    top = state[0]
    while top > 0:
        if done >= budget:
            state[0] = top
            return False
        top -= 1
        lo_s, hi_s = stack[top, 0], stack[top, 1]
        if hi_s - lo_s < 2:
            continue
        s = (lo_s + hi_s) // 2
        for r in range(n + 1):
            lo[r] = first[lo_s, r]
            hi[r] = last[hi_s, r]
        c, cells = _dp_fill(a, b, hh, 0, n, s, m, lo, hi, cost, back)
        done += cells
        _dp_bounds(back, n, s, m, first[s], last[s])
        if c < best[0]:
            best[0] = c
            state[1] = s
        stack[top, 0], stack[top, 1] = lo_s, s
        stack[top + 1, 0], stack[top + 1, 1] = s, hi_s
        top += 2
    state[0] = 0
    return True


def _buffers(n: int, m: int) -> tuple[np.ndarray, np.ndarray]:
    return np.empty((2, 2 * m + 1), dtype=np.float64), np.empty((n + 1, 2 * m + 1), dtype=np.int8)


def best_start(qa: np.ndarray, qb: np.ndarray, hh: float, tick: Tick = None) -> tuple[int, float]:
    """(s, cost): the start (0, s) of the cheapest strip between contours qa and qb (2D, matched) and its cost."""
    n, m = len(qa), len(qb)
    cost, back = _buffers(n, m)
    first = np.empty((m + 1, n + 1), dtype=np.int32)
    last = np.empty((m + 1, n + 1), dtype=np.int32)
    lo = np.empty(n + 1, dtype=np.int64)
    hi = np.empty(n + 1, dtype=np.int64)
    stack = np.empty((m + 2, 2), dtype=np.int64)
    state = np.zeros(3, dtype=np.int64)
    best = np.zeros(1, dtype=np.float64)
    while not _best_start(qa, qb, float(hh), cost, back, first, last, lo, hi, stack, state, best, BUDGET):
        if tick is not None:
            tick()
    return int(state[1]), float(best[0])


def start_cost(qa: np.ndarray, qb: np.ndarray, hh: float, s: int) -> float:
    """The cost of the cheapest strip from node (0, s) once round both contours (no bounds)."""
    n, m = len(qa), len(qb)
    cost, back = _buffers(n, m)
    lo = np.zeros(n + 1, dtype=np.int64)
    hi = np.full(n + 1, 2 * m, dtype=np.int64)
    c, _ = _dp_fill(qa, qb, float(hh), 0, n, int(s), m, lo, hi, cost, back)
    return float(c)


def match_coords(q: np.ndarray) -> tuple[np.ndarray, float]:
    """The contour moved to its centre and scaled to unit size (both weighted by edge length)."""
    nxt = np.roll(q, -1, axis=0)
    mid = (q + nxt) / 2.0
    w = np.linalg.norm(nxt - q, axis=1)
    if w.sum() <= 0.0:
        w = np.ones(len(q))
    c = (mid * w[:, None]).sum(axis=0) / w.sum()
    size = math.sqrt(float((w * ((mid - c) ** 2).sum(axis=1)).sum() / w.sum()))
    if size <= 0.0:
        size = 1.0
    return np.ascontiguousarray((q - c) / size), size


def _tie_segments(ties: list[tuple[int, int]], n: int, m: int) -> list[tuple[int, int, int, int]] | None:
    """Pieces (i0, di, j0, dj) of the path between consecutive ties; None if the ties cross."""
    i_first, j_first = min(ties)
    order = sorted(set(ties), key=lambda t: (t[0], (t[1] - j_first) % m))
    out = []
    walked = 0
    for k, (i, j) in enumerate(order):
        if k + 1 < len(order):
            ni, nj = order[k + 1]
            di, dj = ni - i, (nj - j) % m
        else:
            di, dj = i_first + n - i, m - walked
        walked += dj
        if dj < 0 or walked > m:
            return None
        out.append((i, di, j, dj))
    return out


def strip(
    pa: np.ndarray,
    pb: np.ndarray,
    la: np.ndarray,
    lb: np.ndarray,
    gap: float,
    ties: list[tuple[int, int]] | None = None,
    tick: Tick = None,
) -> np.ndarray | None:
    """(n + m, 3) int64 triangles between contour a (2D points pa, vertex numbers la) and contour b above it.

    Both contours are counter-clockwise in the projection. `ties` are pairs (index in a, index
    in b) that must be edges of the strip. None is returned when the ties cross each other.
    """
    n, m = len(la), len(lb)
    qa, sa = match_coords(pa)
    qb, sb = match_coords(pb)
    h = abs(gap) / ((sa + sb) / 2.0)
    hh = h * h
    la = np.ascontiguousarray(la, dtype=np.int64)
    lb = np.ascontiguousarray(lb, dtype=np.int64)
    if ties:
        segments = _tie_segments(ties, n, m)
        if segments is None:
            return None
    else:
        segments = [(0, n, best_start(qa, qb, hh, tick)[0], m)]
    cost, back = _buffers(n, m)
    tris = np.empty((n + m, 3), dtype=np.int64)
    moves = np.empty(n + m, dtype=np.int8)
    k = 0
    for i0, di, j0, dj in segments:
        if di == 0 and dj == 0:
            continue
        lo = np.full(di + 1, j0, dtype=np.int64)
        hi = np.full(di + 1, j0 + dj, dtype=np.int64)
        _dp_fill(qa, qb, hh, i0, di, j0, dj, lo, hi, cost, back)
        _dp_trace(back, i0, di, j0, dj, n, m, la, lb, tris, moves, k)
        k += di + dj
    if int(np.count_nonzero(moves[1:] != moves[:-1])) < 3:
        # Two cones instead of a strip (see _ring_path): search again with the turns counted —
        # through the same ties, or, without ties, from a spread of starts.
        i0, j0 = segments[0][0], segments[0][2]
        lo = np.zeros(n + 1, dtype=np.int64)
        hi = np.full(n + 1, m, dtype=np.int64)
        row = col = 0
        for _, di, _, dj in segments[:-1]:
            row, col = row + di, col + dj
            lo[row + 1 :] = np.maximum(lo[row + 1 :], col)
            hi[:row] = np.minimum(hi[:row], col)
        starts = [j0] if ties else sorted({j0, *np.linspace(0, m - 1, min(m, 16)).astype(int).tolist()})
        best = np.inf
        trial = np.empty((n + m, 3), dtype=np.int64)
        for start in starts:
            total = _ring_path(qa, qb, hh, i0, start, lo, hi, la, lb, trial)
            if total < best:
                best = total
                tris[:] = trial
            if tick is not None:
                tick()
    return tris
