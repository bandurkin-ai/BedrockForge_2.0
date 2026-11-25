"""The body of a vein (ТЗ 8.9.2–8.9.3 «Жилы»; ADR 0026 решение 17, находка 6; 19.15).

    fit_plane(hanging, footwall) -> Plane                the plane of the contacts: PCA, axes (u, v, w), w up the dip
    build_vein(hanging, footwall, params, h, *, threads=1, tick=None, dense_max=DENSE_MAX) -> VeinBody | None
    vein_body(hanging, footwall, params, h, **same) -> (f8[v, 3], i4[t, 3]) | None     the contract of `bodies`

The hanging wall and the footwall come from `data.load_vein_sides` (the top and the bottom of the intervals of the code,
project coordinates, float64). The contacts of both sides give the plane by their principal components: u, v along the
two long axes, w across; w points to the hanging wall (the mean w of its contacts is the larger one). In (u, v) each
side is a thin-plate surface, w_ВБ(u, v) and w_ЛБ(u, v) (`veins_surface`: φ = r²·ln r with the linear polynomial,
one dense LU up to 4000 contacts of a side, the partition of unity in 2D above). The thickness is t = w_ВБ − w_ЛБ.

The body is built on a planform, never by marching cubes: the boundary — the polygon of `params` in (u, v) of the plane
or the convex hull of all contacts widened by `offset` (rounded corners) — is sampled with the step h, the nodes of the
grid (u, v) = (i·h, j·h) inside it (not closer than 0.45·h to the boundary) are added, and the Delaunay triangulation of
the points, cut to the boundary, is the planform. Where t ≤ `t_min` the vein is pinched out: a triangle with some of its
nodes pinched is cut along the line t = t_min (the crossing points sit on the edges, shared by the neighbours), the part
with t ≤ t_min is dropped. At t_min = 0 the top and the bottom meet in that line (one vertex serves both: the body
converges to a line); at t_min > 0 a wall of the height t_min closes it. The solid is the top (w_ВБ), the bottom (w_ЛБ,
turned over) and the walls above every boundary edge of the planform: closed by construction, the normals outward,
the volume positive. All arithmetic is float64 in the coordinates of the plane (centre taken away); the contacts are
sorted first, so the bytes of the body do not depend on the order of the points nor on the number of threads.

Degenerate data are `{code, message, hint}` in Russian (`spec.refuse`): `no_contacts` with `reason` `few_contacts`
(fewer than three distinct contacts on a side), `collinear` (the contacts lie on a line), `not_planar` (the principal
components of the contacts do not give a plane), `rbf_singular` (the system of a side is singular), `iso_check` with
`reason` `closed` (the solid did not close — a defect of the program); a vein pinched out everywhere is `None`.
numba and manifold3d are not imported here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial import ConvexHull, Delaunay, cKDTree

from . import veins_surface as vs
from .spec_parts import bad, refuse

F8 = np.ndarray
Tick = Callable[[float | None, str | None], None]

DENSE_MAX = vs.DENSE_MAX
NODES_MAX = 3_000_000  # nodes of the grid inside the boundary
EDGE_FRACTION = 0.45  # a grid node nearer than this × h to the boundary is left out
BULGE = 1.0e-6  # the outward bulge of the boundary samples, × h
ARC_STEPS = 32  # the segments of the round corner of the widened hull
COLLAPSE = 1.0e-9  # t_min at or below it: the top and the bottom meet in the line of the pinch
ELONGATION = 1.0e-3  # sqrt(λ₂ / λ₁) of the contacts below it: they lie on a line
FLATNESS = 0.7  # sqrt(λ₃ / λ₂) above it: they do not lie near a plane
MERGE = 1.0e-6  # contacts nearer than this × the span of (u, v) are one point (mean w)
TILE = 4_000_000  # entries of a block of the polygon test


@dataclass(frozen=True)
class Plane:
    """The plane of a vein: the origin (whole numbers, project coordinates) and the unit axes u, v, w, u × v = w."""

    origin: F8
    u: F8
    v: F8
    w: F8

    def to_uvw(self, xyz: Any) -> F8:
        d = np.asarray(xyz, dtype=np.float64).reshape(-1, 3) - self.origin
        cols = [d[:, 0] * a[0] + d[:, 1] * a[1] + d[:, 2] * a[2] for a in (self.u, self.v, self.w)]
        return np.column_stack(cols)

    def to_xyz(self, uvw: Any) -> F8:
        q = np.asarray(uvw, dtype=np.float64).reshape(-1, 3)
        return self.origin + q[:, 0:1] * self.u + q[:, 1:2] * self.v + q[:, 2:3] * self.w


@dataclass(frozen=True)
class VeinBody:
    """The solid of a vein: project coordinates, closed, outward normals; `stats` — the numbers of the journal."""

    vertices: F8
    triangles: np.ndarray
    volume: float
    area: float
    plane: Plane
    stats: dict[str, Any]
    notes: list[str]


# --- the plane -----------------------------------------------------------------------------------------------------


def _sorted(points: Any, side: str, notes: list[str]) -> F8:
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    ok = np.isfinite(p).all(axis=1)
    if not ok.all():
        notes.append(
            f"{side}: отброшено контактов с пропуском координаты — {int((~ok).sum())} (пропуск не равен нулю)."
        )
        p = p[ok]
    p = p + 0.0  # −0.0 is 0.0
    return np.ascontiguousarray(p[np.lexsort((p[:, 2], p[:, 1], p[:, 0]))])


def _few(side: str, n: int) -> Exception:
    return refuse(
        "no_contacts",
        f"На {side} боку жилы меньше трёх различных контактов ({n}): поверхность не определена.",
        "Проверьте коды жилы и таблицу или добавьте скважины, пересекающие жилу.",
        reason="few_contacts",
    )


def _axis_sign(a: F8) -> F8:
    return a if a[int(np.argmax(np.abs(a)))] >= 0.0 else -a


def fit_plane(hanging: Any, footwall: Any) -> Plane:
    """The plane of the contacts of both sides (see the module text). `hanging`, `footwall` [n, 3] float64; the
    contacts are taken in the sorted order. Raises ApiError `no_contacts` (reasons `few_contacts`, `collinear`,
    `not_planar`)."""
    notes: list[str] = []
    hang, foot = _sorted(hanging, "Висячий бок", notes), _sorted(footwall, "Лежачий бок", notes)
    if len(hang) < 3:
        raise _few("висячем", len(hang))
    if len(foot) < 3:
        raise _few("лежачем", len(foot))
    return _plane(hang, foot)


def _plane(hang: F8, foot: F8) -> Plane:
    pts = np.concatenate([hang, foot])
    pts = np.ascontiguousarray(pts[np.lexsort((pts[:, 2], pts[:, 1], pts[:, 0]))])
    origin = np.round(pts.mean(axis=0))
    d = pts - origin
    cov = np.einsum("ni,nj->ij", d, d) / len(pts)  # no BLAS: the same bits for the same points
    val, vec = np.linalg.eigh(cov)
    s3, s2, s1 = (math.sqrt(max(float(x), 0.0)) for x in val)  # ascending: w, v, u
    if s1 <= 0.0 or s2 < ELONGATION * s1:
        raise refuse("no_contacts", "Контакты жилы лежат на одной прямой: плоскость жилы не определена.",
                     "Добавьте скважины, пересекающие жилу в разных местах.", reason="collinear")  # fmt: skip
    if s3 > FLATNESS * s2:
        raise refuse("no_contacts", "Контакты жилы не лежат вблизи одной плоскости: расстояние между скважинами "
                     "сопоставимо с мощностью жилы.", "Добавьте контакты на большем расстоянии друг от друга.",
                     reason="not_planar")  # fmt: skip
    u = _axis_sign(vec[:, 2].copy())
    w = vec[:, 0].copy()
    w = w if w[2] > 1e-12 else (-w if w[2] < -1e-12 else _axis_sign(w))
    v = np.cross(w, u)
    plane = Plane(origin, u, v / np.linalg.norm(v), w)
    up = plane.to_uvw(hang)[:, 2].mean() - plane.to_uvw(foot)[:, 2].mean()
    if up < 0.0:  # w towards the hanging wall; u × v = w kept
        plane = Plane(origin, u, -plane.v, -plane.w)
    return plane


def _merge(uv: F8, w: F8) -> tuple[F8, F8, int]:
    """The contacts nearer than MERGE × span are one point with the mean w; sorted by (u, v)."""
    span = max(float(np.ptp(uv[:, 0])), float(np.ptp(uv[:, 1])), 1e-9)
    key = np.round(uv / (MERGE * span)).astype(np.int64)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, uv, w = key[order], uv[order], w[order]
    first = np.ones(len(key), dtype=bool)
    first[1:] = (key[1:] != key[:-1]).any(axis=1)
    if first.all():
        return uv, w, 0
    grp = np.cumsum(first) - 1
    cnt = np.bincount(grp)
    return (np.add.reduceat(uv, np.flatnonzero(first), axis=0) / cnt[:, None],
            np.add.reduceat(w, np.flatnonzero(first)) / cnt, int(len(key) - len(cnt)))  # fmt: skip


# --- the boundary --------------------------------------------------------------------------------------------------


def _ccw(poly: F8) -> F8:
    x, y = poly[:, 0], poly[:, 1]
    area2 = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return poly if area2 > 0.0 else poly[::-1].copy()


def _hull(uv: F8, offset: float) -> F8:
    pts = uv
    if offset > 0.0:
        ang = np.arange(ARC_STEPS) * (2.0 * math.pi / ARC_STEPS)
        ring = offset * np.column_stack([np.cos(ang), np.sin(ang)])
        h0 = uv[ConvexHull(uv).vertices]
        pts = (h0[:, None, :] + ring[None, :, :]).reshape(-1, 2)
    return _ccw(pts[ConvexHull(pts).vertices])


def boundary_of(params: Mapping[str, Any], uv_all: F8) -> tuple[F8, bool]:
    """The polygon of the boundary [k, 2], counter-clockwise, and whether it is convex (the hull)."""
    if params.get("bound") == "polygon" and params.get("polygon"):
        poly = np.asarray(params["polygon"], dtype=np.float64).reshape(-1, 2)
        keep = np.ones(len(poly), dtype=bool)
        keep[1:] = (poly[1:] != poly[:-1]).any(axis=1)
        poly = poly[keep]
        if len(poly) > 1 and (poly[0] == poly[-1]).all():
            poly = poly[:-1]
        if (
            len(poly) < 3
            or abs(float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]))) <= 0.0
        ):
            raise bad(
                "bad_param",
                "Полигон границы жилы вырожден: площадь равна нулю.",
                "Обведите жилу в плане заново.",
                "params.polygon",
            )
        return _ccw(poly), False
    return _hull(uv_all, float(params.get("offset") or 0.0)), True


def _inside(poly: F8, pts: F8) -> np.ndarray:
    """Even-odd test of the points [m, 2] against the polygon [k, 2]."""
    x1, y1 = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    dy = np.where(y2 != y1, y2 - y1, 1.0)
    out = np.zeros(len(pts), dtype=bool)
    rows = max(1, TILE // len(poly))
    for a in range(0, len(pts), rows):
        x, y = pts[a : a + rows, 0:1], pts[a : a + rows, 1:2]
        cross = ((y1[None, :] > y) != (y2[None, :] > y)) & (
            x < x1[None, :] + (y - y1[None, :]) * ((x2 - x1) / dy)[None, :]
        )
        out[a : a + rows] = (cross.sum(axis=1) & 1).astype(bool)
    return out


def _grid_inside(poly: F8, i0: np.ndarray, i1: np.ndarray, h: float) -> np.ndarray:
    """bool [nu, nv]: the nodes (i·h, j·h), i0 ≤ (i, j) ≤ i1, inside the polygon — one scan line (v = j·h) at a time."""
    nu, nv = (int(a) for a in i1 - i0 + 1)
    x = (np.arange(nu) + i0[0]) * h
    x1, y1 = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    out = np.zeros((nu, nv), dtype=bool)
    for j in range(nv):
        y = (i0[1] + j) * h
        hit = (y1 > y) != (y2 > y)
        if hit.any():
            xc = np.sort(x1[hit] + (y - y1[hit]) * (x2[hit] - x1[hit]) / (y2[hit] - y1[hit]))
            out[:, j] = ((len(xc) - np.searchsorted(xc, x, side="right")) & 1).astype(bool)
    return out


def _planform(poly: F8, convex: bool, h: float) -> tuple[F8, np.ndarray]:
    """The nodes [n, 2] and the counter-clockwise triangles [m, 3] of the planform of the boundary (module text).

    The samples of an edge bulge outward by up to BULGE·h (a hair: the area changes by 1e-6): the samples of a straight
    hull edge are then strictly convex and the triangulation has no flat triangle along it — it would be a hole in the
    solid once the nearly zero area is told from the sign of the orientation."""
    edge = np.roll(poly, -1, axis=0) - poly
    length = np.hypot(edge[:, 0], edge[:, 1])
    parts = np.maximum(1, np.ceil(length / h)).astype(np.int64)
    ids = np.repeat(np.arange(len(poly)), parts)
    k = np.arange(len(ids)) - np.repeat(np.cumsum(parts) - parts, parts)
    s = (k / parts[ids])[:, None]
    out = np.column_stack([edge[:, 1], -edge[:, 0]]) / length[:, None]  # to the right of a counter-clockwise edge
    ring = poly[ids] + edge[ids] * s + out[ids] * (BULGE * h * 4.0 * s * (1.0 - s))
    lo, hi = ring.min(axis=0), ring.max(axis=0)
    i0, i1 = np.ceil(lo / h).astype(np.int64), np.floor(hi / h).astype(np.int64)
    if float(np.prod((i1 - i0 + 1).astype(np.float64))) > NODES_MAX:
        raise bad("bad_param", "Шаг h слишком мал для жилы: узлов сетки больше трёх миллионов.",
                  "Увеличьте шаг h или ограничьте границу жилы.", "h")  # fmt: skip
    gu, gv = np.meshgrid(np.arange(i0[0], i1[0] + 1), np.arange(i0[1], i1[1] + 1), indexing="ij")
    grid = np.column_stack([gu.ravel() * h, gv.ravel() * h])[_grid_inside(ring, i0, i1, h).ravel()]
    if len(grid):
        grid = grid[cKDTree(ring).query(grid)[0] >= EDGE_FRACTION * h]
    nodes, back = np.unique(np.concatenate([ring, grid]), axis=0, return_inverse=True)
    tri = Delaunay(nodes).simplices.astype(np.int64)
    a, b, c = nodes[tri[:, 0]], nodes[tri[:, 1]], nodes[tri[:, 2]]
    flip = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]) < 0.0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    if not convex:  # a triangle outside the polygon has a vertex on the ring
        on_ring = np.zeros(len(nodes), dtype=bool)
        on_ring[back.reshape(-1)[: len(ring)]] = True
        near = np.flatnonzero(on_ring[tri].any(axis=1))
        centre = (a[near] + b[near] + c[near]) / 3.0
        drop = near[~_inside(ring, centre)]
        tri = np.delete(tri, drop, axis=0)
    return nodes, np.ascontiguousarray(tri)


# --- the pinch-out and the solid -----------------------------------------------------------------------------------


def _clip(uv: F8, wh: F8, wf: F8, tri: np.ndarray, t_min: float) -> tuple[F8, F8, F8, np.ndarray, np.ndarray]:
    """Cut the triangles along t = t_min → (uv, w_top, w_bottom, triangles, vertices where top and bottom meet)."""
    n = len(uv)
    t = wh - wf
    alive = t > t_min
    cnt = alive[tri].sum(axis=1)
    whole = tri[cnt == 3]
    two = tri[cnt == 2]
    two = np.take_along_axis(two, (np.argmin(alive[two], axis=1)[:, None] + np.array([1, 2, 0])) % 3, axis=1)
    one = tri[cnt == 1]
    one = np.take_along_axis(one, (np.argmax(alive[one], axis=1)[:, None] + np.array([0, 1, 2])) % 3, axis=1)
    pairs = np.concatenate([two[:, [0, 2]], two[:, [1, 2]], one[:, [0, 1]], one[:, [0, 2]]])  # (alive, dead)
    keys = np.unique(pairs[:, 0] * n + pairs[:, 1])

    def x(a: np.ndarray, d: np.ndarray) -> np.ndarray:
        return n + np.searchsorted(keys, a * n + d)

    ua, ud = keys // n, keys % n
    s = (t[ua] - t_min) / (t[ua] - t[ud])
    uv_x = uv[ua] + s[:, None] * (uv[ud] - uv[ua])
    wh_x, wf_x = wh[ua] + s * (wh[ud] - wh[ua]), wf[ua] + s * (wf[ud] - wf[ua])
    meet = np.zeros(n + len(keys), dtype=bool)
    if t_min <= COLLAPSE:  # the top and the bottom meet in the line
        wh_x = wf_x = 0.5 * (wh_x + wf_x)
        meet[n:] = True
    parts = [whole]
    if len(two):
        a, b, c = two[:, 0], two[:, 1], two[:, 2]
        parts += [np.column_stack([a, b, x(b, c)]), np.column_stack([a, x(b, c), x(a, c)])]
    if len(one):
        a, b, c = one[:, 0], one[:, 1], one[:, 2]
        parts.append(np.column_stack([a, x(a, b), x(a, c)]))
    return (np.concatenate([uv, uv_x]), np.concatenate([wh, wh_x]), np.concatenate([wf, wf_x]),
            np.ascontiguousarray(np.concatenate(parts)), meet)  # fmt: skip


def _solid(uv: F8, wt: F8, wb: F8, tri: np.ndarray, meet: np.ndarray) -> tuple[F8, np.ndarray]:
    """The closed solid (u, v, w) of the planform: top, bottom turned over, walls over the boundary edges."""
    used = np.unique(tri)
    remap = np.full(len(uv), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    tri = remap[tri]
    uv, wt, wb, meet = uv[used], wt[used], wb[used], meet[used]
    n = len(used)
    split = np.flatnonzero(~meet)
    bot = np.arange(n)
    bot[split] = n + np.arange(len(split))
    verts = np.concatenate([np.column_stack([uv, wt]), np.column_stack([uv[split], wb[split]])])
    e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    key = e[:, 0] * n + e[:, 1]
    edge = e[~np.isin(e[:, 1] * n + e[:, 0], key)]  # the edges nobody walks back: the boundary of the planform
    ba, bb, ta, tb = bot[edge[:, 0]], bot[edge[:, 1]], edge[:, 0], edge[:, 1]
    walls = np.concatenate([np.column_stack([ba, bb, tb]), np.column_stack([ba, tb, ta])])
    walls = walls[(walls[:, 0] != walls[:, 1]) & (walls[:, 1] != walls[:, 2]) & (walls[:, 0] != walls[:, 2])]
    return verts, np.concatenate([tri, bot[tri[:, [0, 2, 1]]], walls])


def _measures(v: F8, t: np.ndarray) -> tuple[float, float]:
    """(signed volume, area) of a mesh; the tetrahedra from the centre of the vertices, summed with `fsum`."""
    p = v - v.mean(axis=0)
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    cr = np.cross(b - a, c - a)
    vol = math.fsum(np.einsum("ij,ij->i", a, np.cross(b, c)).tolist()) / 6.0
    return vol, math.fsum((0.5 * np.sqrt(np.einsum("ij,ij->i", cr, cr))).tolist())


def _open_edges(t: np.ndarray, n: int) -> int:
    from bf_core.geo.meshes import edge_summary

    open_edges, nonmanifold = edge_summary(t, n)
    return int(open_edges + nonmanifold)


# --- the vein ------------------------------------------------------------------------------------------------------


def _beat(tick: Tick | None, share: float, text: str) -> None:
    if tick is not None:
        tick(share, text)


def _surface(uv: F8, w: F8, side: str, dense_max: int, threads: int, tick: Tick | None) -> vs.Surface:
    uv, w, _ = _merge(uv, w)
    if len(uv) < 3:
        raise _few(side, len(uv))
    try:
        return vs.fit_surface(uv, w, dense_max=dense_max, threads=threads, tick=tick)
    except vs.SurfaceError as exc:
        if exc.reason == "collinear":
            raise refuse("no_contacts", f"Контакты на {side} боку жилы лежат на одной прямой: поверхность не "
                         "определена.", "Добавьте скважины, пересекающие жилу в разных местах.",
                         reason="collinear") from None  # fmt: skip
        raise refuse("rbf_singular", f"Поверхность {side} бока жилы вырождена: {exc}",
                     "Проверьте контакты: почти совпадающие точки с разными значениями.") from None  # fmt: skip


def build_vein(hanging: Any, footwall: Any, params: Mapping[str, Any], h: float, *, threads: int = 1,
               tick: Tick | None = None, dense_max: int = DENSE_MAX) -> VeinBody | None:  # fmt: skip
    """The closed solid of a vein from its two sides; `params` — `bound` (`hull` / `polygon`), `polygon` [[u, v]…] in
    the (u, v) of `fit_plane` (metres from its origin), `offset` (widening of the hull), `t_min`. None when the vein is
    pinched out everywhere. See the module text for the refusals."""
    if not (math.isfinite(h) and h > 0.0):
        raise bad("bad_param", "Шаг h жилы: нужно положительное число.", "Задайте шаг h > 0.", "h")
    t_min = float(params.get("t_min") or 0.0)
    notes: list[str] = []
    hang, foot = _sorted(hanging, "Висячий бок", notes), _sorted(footwall, "Лежачий бок", notes)
    if len(hang) < 3:
        raise _few("висячем", len(hang))
    if len(foot) < 3:
        raise _few("лежачем", len(foot))
    _beat(tick, 0.0, "Жила: плоскость контактов")
    plane = _plane(hang, foot)
    h_uvw, f_uvw = plane.to_uvw(hang), plane.to_uvw(foot)
    n_h, n_f = len(hang), len(foot)
    _beat(tick, 0.05, "Жила: поверхность висячего бока")
    sh = _surface(h_uvw[:, :2], h_uvw[:, 2], "висячем", dense_max, threads, _within(tick, 0.05, 0.3))
    _beat(tick, 0.3, "Жила: поверхность лежачего бока")
    sf = _surface(f_uvw[:, :2], f_uvw[:, 2], "лежачем", dense_max, threads, _within(tick, 0.3, 0.55))
    _beat(tick, 0.55, "Жила: граница и сетка (u, v)")
    poly, convex = boundary_of(params, np.concatenate([h_uvw[:, :2], f_uvw[:, :2]]))
    nodes, tri = _planform(poly, convex, h)
    if len(tri) == 0:
        raise bad("bad_param", "Граница жилы меньше шага h: сетка пуста.", "Уменьшите шаг h.", "h")
    _beat(tick, 0.7, "Жила: толщина в узлах")
    wh, wf = sh.evaluate(nodes, threads=threads), sf.evaluate(nodes, threads=threads)
    if not (np.isfinite(wh).all() and np.isfinite(wf).all()):
        raise refuse("rbf_singular", "Поверхность жилы не определена в узлах сетки.", "Проверьте контакты жилы.")
    full = _planform_area(nodes, tri)
    uv2, wt, wb, tri2, meet = _clip(nodes, wh, wf, tri, t_min)
    if len(tri2) == 0:
        return None
    _beat(tick, 0.85, "Жила: сборка тела")
    verts, tris = _solid(uv2, wt, wb, tri2, meet)
    vol, area = _measures(verts, tris)
    if not (vol > 0.0) or _open_edges(tris, len(verts)):
        raise refuse("iso_check", "Тело жилы не замкнулось.", "Уменьшите шаг h или проверьте границу жилы.",
                     reason="closed")  # fmt: skip
    kept = _planform_area(uv2, tri2)
    xyz = plane.to_xyz(verts)
    thick = wh - wf
    notes += [
        f"Жила: контактов на висячем боку — {n_h}, на лежачем — {n_f}; узлов сетки — {len(nodes)}.",
        f"Опорная плоскость: падение {math.degrees(math.acos(min(1.0, abs(float(plane.w[2]))))):.1f}°, "
        f"средняя мощность {vol / kept:.3f} м.",
    ]
    if kept < full * (1.0 - 1e-9):
        notes.append(
            f"Выклинивается {100.0 * (1.0 - kept / full):.1f} % площади жилы (мощность не больше {t_min:g} м)."
        )
    stats = {
        "hanging": n_h,
        "footwall": n_f,
        "nodes": len(nodes),
        "planform_area": kept,
        "pinched_share": 1.0 - kept / full,
        "thickness": {"min": float(thick.min()), "mean": vol / kept, "max": float(thick.max())},
        "patches": {"hanging": sh.n_patches, "footwall": sf.n_patches},
        "cond1": max(sh.cond1, sf.cond1),
    }
    return VeinBody(np.ascontiguousarray(xyz), tris.astype(np.int32), vol, area, plane, stats, notes)


def _planform_area(uv: F8, tri: np.ndarray) -> float:
    a, b, c = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    return 0.5 * math.fsum(
        ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])).tolist()
    )


def _within(tick: Tick | None, start: float, stop: float) -> Tick | None:
    if tick is None:
        return None
    outer = tick
    return lambda share, text: outer(None if share is None else start + (stop - start) * share, text)


def vein_body(
    hanging: Any, footwall: Any, params: Mapping[str, Any], h: float, **kw: Any
) -> tuple[F8, np.ndarray] | None:
    """The contract of `bodies` (e19-api §11): (vertices f8[v, 3], triangles i4[t, 3]) or None — nothing left."""
    body = build_vein(hanging, footwall, params, h, **kw)
    return None if body is None else (body.vertices, body.triangles)


__all__ = ["DENSE_MAX", "Plane", "VeinBody", "boundary_of", "build_vein", "fit_plane", "vein_body"]
