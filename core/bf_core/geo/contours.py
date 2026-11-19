"""Isolines of a surface with the attribute ELEV (ТЗ 7.23 «Изолинии»; ADR 0015 §7).

    contours(v, t, interval=, base=, major_every=, z_min=, z_max=, tick=)   isolines, chained and ordered
    contour_layers(lines, major_every=)                                    layers «Основные» and «Дополнительные»
    surface_mesh(v, t, name)                                               the checks every surface operation shares

Levels z = base + k·interval (k whole). A vertex exactly on a level counts as lying above it: every triangle then
holds at most one segment of a level, so lines neither double nor break at such vertices; a flat area exactly on
a level is outlined along the side where the ground falls below it; a summit that only touches a level gives no
line. The segments are chained by walking from triangle to triangle through the shared edges (half-edge twins):
lines start and end on the border of the surface or close. A closed line does not repeat its first point; for a
surface oriented upward the higher ground is on the left of every line. A point of a line is computed from the
ends of its edge in a fixed order (from the lower end), so neighbouring triangles share it bit for bit; its Z is
the level exactly. At most MAX_LEVELS levels.

The levels are traced in batches of about BATCH_SEGMENTS segments: `tick(fraction, message)` between the batches
reports progress and may raise to cancel; the memory of a batch is about 40 bytes a segment.

Ported from BF `geo/surfaces.py` (`contours`, `_level_ranges`, `_lone`, `_trace`, `_twins`); the kernels are
`nogil`, without `prange`. Errors: `SurfaceError` {code, message, hint} in Russian (a `SolidError`).
numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.geo.solids import FIX_HINT, SolidError, Tick, tick_at
from bf_core.jit import njit

MAX_LEVELS = 20_000  # ADR 0015 §7
MAX_SEGMENTS = 50_000_000  # about 2 GB of points and marks
BATCH_SEGMENTS = 2_000_000
MAJOR, MINOR = "Основные", "Дополнительные"
ELEV = "ELEV"


class SurfaceError(SolidError):
    """A surface cannot be measured or contoured: {code, message, hint} in Russian."""


def _fmt(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


# --- mesh topology ---------------------------------------------------------------------------------------


def twins(tri: np.ndarray, vertex_count: int) -> tuple[np.ndarray, int]:
    """Half-edge 3·t + e runs from tri[t, e] to tri[t, e + 1] → (its twin or −1, edges of 3+ triangles)."""
    t = np.asarray(tri, dtype=np.int64)
    a = t.reshape(-1)
    b = t[:, [1, 2, 0]].reshape(-1)
    key = np.minimum(a, b) * max(int(vertex_count), 1) + np.maximum(a, b)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = ks[1:] == ks[:-1]
    twin = np.full(len(key), -1, np.int64)
    i = np.nonzero(same)[0]
    twin[order[i]] = order[i + 1]
    twin[order[i + 1]] = order[i]
    return twin, int(np.count_nonzero(same[1:] & same[:-1]))


def boundary_edges(t: np.ndarray, vertex_count: int, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray]:
    """(start, end) vertices of the half-edges without a twin: the outline of a surface, walked with the
    surface on the left; SurfaceError for edges of three or more triangles."""
    twin, nonmanifold = twins(t, vertex_count)
    if nonmanifold:
        raise SurfaceError(
            "wireframe_nonmanifold",
            f"Поверхность «{name}»: рёбер, общих для трёх и более треугольников, — {nonmanifold}.",
            FIX_HINT,
        )
    h = np.nonzero(twin < 0)[0]
    return t[h // 3, h % 3], t[h // 3, (h % 3 + 1) % 3]


def used_vertices(t: np.ndarray, vertex_count: int) -> np.ndarray:
    """Sorted numbers of the vertices the triangles use."""
    return np.flatnonzero(np.bincount(t.reshape(-1), minlength=vertex_count))


def surface_mesh(vertices: Any, triangles: Any, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray]:
    """float64 vertices and int64 triangles of a surface, or SurfaceError."""
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3 or (t.size and t.dtype.kind not in "iu"):
        raise SurfaceError("bad_mesh", f"«{name}»: ожидаются вершины (n, 3) и треугольники (m, 3).", "")
    if len(t) == 0:
        raise SurfaceError("empty_mesh", f"Поверхность «{name}» пуста: в ней нет треугольников.", "")
    t = t.astype(np.int64)
    if t.min() < 0 or t.max() >= len(v):
        raise SurfaceError("bad_mesh", f"«{name}»: треугольники ссылаются на несуществующие вершины.", "")
    if not np.isfinite(v[used_vertices(t, len(v))]).all():
        raise SurfaceError(
            "bad_mesh",
            f"«{name}»: в координатах вершин есть пропуски или нечисловые значения.",
            "Отметка не подставляется автоматически: исправьте исходные данные.",
        )
    return v, t


# --- contours (numba) ------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _level_ranges(z, tri, base, interval, k_first, k_last):  # type: ignore[no-untyped-def]
    """For every triangle: first level index and number of levels L with zmin < L <= zmax (in k_first…k_last)."""
    nt = len(tri)
    k0 = np.empty(nt, np.int64)
    cnt = np.empty(nt, np.int64)
    for t in range(nt):
        z0 = z[tri[t, 0]]
        z1 = z[tri[t, 1]]
        z2 = z[tri[t, 2]]
        lo = min(z0, z1, z2)
        hi = max(z0, z1, z2)
        ka = int(math.floor((lo - base) / interval)) + 1
        while base + (ka - 1) * interval > lo:
            ka -= 1
        while base + ka * interval <= lo:
            ka += 1
        kb = int(math.floor((hi - base) / interval))
        while base + kb * interval > hi:
            kb -= 1
        while base + (kb + 1) * interval <= hi:
            kb += 1
        ka = max(ka, k_first)
        kb = min(kb, k_last)
        k0[t] = ka
        cnt[t] = max(0, kb - ka + 1)
    return k0, cnt


@njit(cache=True, nogil=True, inline="always")
def _lone(z0, z1, z2, level):  # type: ignore[no-untyped-def]
    """The vertex alone on its side of the level → (local index, is it above). Above means z >= level."""
    a0 = z0 >= level
    a1 = z1 >= level
    a2 = z2 >= level
    if a0 != a1 and a0 != a2:
        return 0, a0
    if a1 != a0 and a1 != a2:
        return 1, a1
    return 2, a2


@njit(cache=True, nogil=True)
def _trace(v, tri, twin, k0, cnt, off, base, interval, capacity):  # type: ignore[no-untyped-def]
    """Walks every contour from triangle to triangle through the shared edges. Phase 0: open lines from the
    edge where the line enters the mesh; phase 1: the remaining open lines (meshes of mixed orientation),
    walked backward and reversed; phase 2: loops. Consecutive equal points are written once."""
    total = off[len(tri)]
    visited = np.zeros(total, np.uint8)
    pts = np.empty((capacity, 3), np.float64)
    lcap = 1024
    l_start = np.empty(lcap, np.int64)
    l_level = np.empty(lcap, np.int64)
    l_closed = np.empty(lcap, np.uint8)
    nl = 0
    n = 0
    nt = len(tri)
    for phase in range(3):
        for t in range(nt):
            for kk in range(cnt[t]):
                if visited[off[t] + kk]:
                    continue
                k = k0[t] + kk
                level = base + k * interval
                j, above = _lone(v[tri[t, 0], 2], v[tri[t, 1], 2], v[tri[t, 2], 2], level)
                if above:
                    e_in = j
                    e_out = (j + 2) % 3
                else:
                    e_in = (j + 2) % 3
                    e_out = j
                if phase == 0:
                    if twin[3 * t + e_in] >= 0:
                        continue
                elif phase == 1:
                    if twin[3 * t + e_out] >= 0:
                        continue
                    e_in, e_out = e_out, e_in
                begin = n
                ct = t
                closed = False
                first = phase < 2
                if first:
                    edge = e_in
                else:
                    edge = e_out  # a loop: start with the exit point; the walk comes back to this triangle
                    visited[off[t] + kk] = 1
                while True:
                    ia = tri[ct, edge]
                    ib = tri[ct, (edge + 1) % 3]
                    if v[ia, 2] >= level:
                        ia, ib = ib, ia
                    if v[ib, 2] == level:
                        px = v[ib, 0]
                        py = v[ib, 1]
                    else:
                        s = (level - v[ia, 2]) / (v[ib, 2] - v[ia, 2])
                        px = v[ia, 0] + s * (v[ib, 0] - v[ia, 0])
                        py = v[ia, 1] + s * (v[ib, 1] - v[ia, 1])
                    if n == begin or pts[n - 1, 0] != px or pts[n - 1, 1] != py:
                        if n >= len(pts):
                            grown = np.empty((2 * len(pts) + 16, 3), np.float64)
                            grown[:n] = pts[:n]
                            pts = grown
                        pts[n, 0] = px
                        pts[n, 1] = py
                        pts[n, 2] = level
                        n += 1
                    if first:
                        first = False
                        visited[off[ct] + (k - k0[ct])] = 1
                        edge = e_out
                        continue
                    h = twin[3 * ct + edge]
                    if h < 0:
                        break
                    nxt = h // 3
                    kk2 = k - k0[nxt]
                    if kk2 < 0 or kk2 >= cnt[nxt]:
                        break
                    if visited[off[nxt] + kk2]:
                        closed = phase == 2 and nxt == t
                        break
                    j2, _ = _lone(v[tri[nxt, 0], 2], v[tri[nxt, 1], 2], v[tri[nxt, 2], 2], level)
                    enter = h % 3
                    if enter == j2:
                        edge = (j2 + 2) % 3
                    elif enter == (j2 + 2) % 3:
                        edge = j2
                    else:
                        break
                    ct = nxt
                    visited[off[ct] + kk2] = 1
                if closed and n - begin >= 2 and pts[n - 1, 0] == pts[begin, 0] and pts[n - 1, 1] == pts[begin, 1]:
                    n -= 1
                if n - begin < (3 if closed else 2):
                    n = begin
                    continue
                if phase == 1:
                    i = begin
                    q = n - 1
                    while i < q:
                        for d in range(2):
                            tmp = pts[i, d]
                            pts[i, d] = pts[q, d]
                            pts[q, d] = tmp
                        i += 1
                        q -= 1
                if nl >= lcap:
                    lcap *= 2
                    g1 = np.empty(lcap, np.int64)
                    g1[:nl] = l_start[:nl]
                    l_start = g1
                    g2 = np.empty(lcap, np.int64)
                    g2[:nl] = l_level[:nl]
                    l_level = g2
                    g3 = np.empty(lcap, np.uint8)
                    g3[:nl] = l_closed[:nl]
                    l_closed = g3
                l_start[nl] = begin
                l_level[nl] = k
                l_closed[nl] = 1 if closed else 0
                nl += 1
    return pts[:n], l_start[:nl], l_level[:nl], l_closed[:nl]


# --- contours --------------------------------------------------------------------------------------------


def _level_bounds(lo: float, hi: float, base: float, interval: float) -> tuple[int, int]:
    """First and last k with lo <= base + k·interval <= hi (exactly as the levels are computed)."""
    k_first = math.ceil((lo - base) / interval)
    while base + k_first * interval < lo:
        k_first += 1
    while base + (k_first - 1) * interval >= lo:
        k_first -= 1
    k_last = math.floor((hi - base) / interval)
    while base + k_last * interval > hi:
        k_last -= 1
    while base + (k_last + 1) * interval <= hi:
        k_last += 1
    return k_first, k_last


def _batches(k0: np.ndarray, cnt: np.ndarray, k_first: int, k_last: int) -> list[tuple[int, int]]:
    """Ranges of levels of about BATCH_SEGMENTS segments each (segments per level from a difference array)."""
    size = k_last - k_first + 2
    rows = cnt > 0
    diff = np.bincount(k0[rows] - k_first, minlength=size) - np.bincount(k0[rows] + cnt[rows] - k_first, minlength=size)
    per_level = np.cumsum(diff[:-1])
    out, start, acc = [], 0, 0
    for i, c in enumerate(per_level.tolist()):
        if acc and acc + c > BATCH_SEGMENTS:
            out.append((k_first + start, k_first + i - 1))
            start, acc = i, 0
        acc += c
    out.append((k_first + start, k_last))
    return out


def contours(
    vertices: Any,
    triangles: Any,
    *,
    interval: float,
    base: float = 0.0,
    major_every: int = 5,
    z_min: float | None = None,
    z_max: float | None = None,
    tick: Tick = None,
) -> list[dict[str, Any]]:
    """Isolines: [{"z": level, "major": bool, "closed": bool, "points": (n, 3) float64}] sorted by level.

    `major` marks every `major_every`-th level counted from `base` (k divisible by it; 0 or less — no major
    lines). z_min / z_max limit the levels (inclusive)."""
    tick_at(tick, 0.0, "Изолинии")
    v, t = surface_mesh(vertices, triangles)
    interval, base = float(interval), float(base)
    if not (interval > 0 and math.isfinite(interval) and math.isfinite(base)):
        raise SurfaceError(
            "bad_parameter", "Шаг изолиний должен быть положительным числом.", "Задайте шаг больше нуля."
        )
    zs = v[used_vertices(t, len(v)), 2]
    lo = float(zs.min()) if z_min is None else max(float(zs.min()), float(z_min))
    hi = float(zs.max()) if z_max is None else min(float(zs.max()), float(z_max))
    if hi < lo:
        return []
    if (hi - lo) / interval > MAX_LEVELS:
        raise SurfaceError(
            "too_many_levels",
            f"Слишком мелкий шаг изолиний: больше {MAX_LEVELS} уровней на перепаде высот {_fmt(hi - lo)} м.",
            "Увеличьте шаг или сузьте диапазон отметок.",
        )
    k_first, k_last = _level_bounds(lo, hi, base, interval)
    if k_last < k_first:
        return []
    z = np.ascontiguousarray(v[:, 2])
    k0, cnt = _level_ranges(z, t, base, interval, k_first, k_last)
    total = int(cnt.sum())
    if total == 0:
        return []
    if total > MAX_SEGMENTS:
        raise SurfaceError(
            "too_many_levels",
            f"Слишком мелкий шаг изолиний: получится {total} отрезков.",
            "Увеличьте шаг или сузьте диапазон отметок.",
        )
    twin, _ = twins(t, len(v))
    tick_at(tick, 0.1, "Изолинии")
    vv = np.ascontiguousarray(v)
    open_edges = int(np.count_nonzero(twin < 0))
    found: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    done = 0
    for ka, kb in _batches(k0, cnt, k_first, k_last):
        b0, bc = _level_ranges(z, t, base, interval, ka, kb)
        off = np.zeros(len(t) + 1, dtype=np.int64)
        np.cumsum(bc, out=off[1:])
        segments = int(off[-1])
        if segments:
            capacity = segments + min(segments, 2 * open_edges) + 16
            found.append(_trace(vv, t, twin, b0, bc, off, base, interval, capacity))
        done += segments
        tick_at(tick, 0.1 + 0.85 * done / total, "Изолинии")
    major_every = int(major_every)
    out = []
    for pts, l_start, l_level, l_closed in found:
        ends = np.append(l_start[1:], len(pts))
        for i in np.argsort(l_level, kind="stable").tolist():
            k = int(l_level[i])
            out.append(
                {
                    "z": base + k * interval,
                    "major": bool(major_every > 0 and k % major_every == 0),
                    "closed": bool(l_closed[i]),
                    "points": pts[l_start[i] : ends[i]].copy(),
                }
            )
    tick_at(tick, 1.0, "Изолинии")
    return out


def contour_layers(lines: list[dict[str, Any]], *, color: str | None = None) -> list[Any]:
    """The isolines as layers of lines (`formats.lineset.LineSet`): «Основные» and «Дополнительные», each line
    with the attribute ELEV (its level, float64); a closed line repeats its first vertex, as layers keep them.
    An empty group gives no layer."""
    from bf_core.formats.lineset import from_lines

    layers = []
    for name, major in ((MAJOR, True), (MINOR, False)):
        group = [c for c in lines if c["major"] == major]
        if group:
            elev = np.array([c["z"] for c in group], dtype=np.float64)
            closed = [c["closed"] for c in group]
            layer = from_lines(name, [c["points"] for c in group], closed, attrs={ELEV: elev}, color=color)
            layers.append(layer)
    return layers
