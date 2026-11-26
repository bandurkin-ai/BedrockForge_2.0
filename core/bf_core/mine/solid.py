"""The solid of a pit: the body between the relief and the surface of a shell (подшаг 21.12; ТЗ 8.11.4 «Выходы»;
ADR 0028 решение 26, находка 6). Numpy only — no database, no files: the jobs are in `dtm_job.py`.

    tri = cell_triangles(nx, ny)             # int64 [2·(nx−1)·(ny−1), 3]: two triangles a cell, counter-clockwise
    xyz, tri = surface_mesh(xy, z, nx, ny)   # a DTM over the nodes (the column centres); a NaN node is not built
    s = between(xy, top, bottom, nx, ny)     # variant Б: top = the relief, bottom = the surface of the shell
    s = difference(xy, top, bottom, nx, ny)  # variant А (the fallback): under_solid(top) − under_solid(bottom)
    s = build(xy, top, bottom, nx, ny)       # Б, then А when Б is not a closed manifold; SolidError when neither is

`Solid`: `xyz` float64 [V, 3] (project axes), `tri` int64 [T, 3] (outward), `volume` (m³, > 0), `parts` (the pieces that
share no vertex), `method` ("between" | "difference").

Variant Б. The nodes are the centres of the columns of the grid (`surfaces.centres`), the same for the relief and for
the surface of the shell. The thickness of a node is d = T − B (B is clamped to T: a parent partly under a sloping
relief may have its bottom above the relief at the centre of its column — there is no pit there, d = 0). A triangle is
built when its three nodes are known (a NaN node is not built) and at least one of them is mined (d > 0): its top (the
relief, facing up) and its bottom (the shell, facing down). A node with d = 0 is on the SEAM: its top and its bottom are
ONE vertex (welded), so the triangles at the edge of the pit close without a wall. An edge of the plan with no
neighbour (the edge of the grid, a node without z beyond it) gets a vertical wall. Where the weld would pinch the solid
it is not made:
  * a seam vertex around which the kept triangles form several runs (two pits that touch by a corner) gets one vertex
    per run: the pieces share no vertex index, they touch at a point;
  * the two triangles on both sides of an edge whose two ends are on the seam (a knife edge: a mined node on each side)
    are not linked, so each side has its own pair of vertices when the runs are apart;
  * a seam node inside a ring of mined nodes (a column that is not mined in the middle of the pit) keeps its top and
    its bottom apart: they touch at a point.
A notch — the knife edge whose two sides are one run at both its ends — cannot be closed this way: variant Б is not a
manifold there, and variant А takes over.
The volume of the solid is Σ d·A over the nodes (the same diagonal in every cell gives an interior node the area of a
cell): the volume of the prisms of the columns of the grid of centres. The solid spans from the first to the last
centre, so a mined column at the very edge of the grid counts for less than its prism.

Variant А is the fallback: `geo.volumes.under_solid` of the relief minus `under_solid` of the surface of the shell on
one plane below both, by manifold3d (coincident faces are resolved by its symbolic perturbation). The check of both:
`geo.solids.is_closed_solid` (edges, orientation), then manifold3d `check_status` (`to_manifold`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bf_core.geo import solids as gs

Tick = gs.Tick
EPS = 1.0e-9  # m: a thickness of a node below it is no thickness (the node is on the seam)


@dataclass(frozen=True)
class Solid:
    """A closed solid: vertices in the project axes, triangles pointing outward, the volume (m³), the number of
    pieces, the way it was built."""

    xyz: np.ndarray
    tri: np.ndarray
    volume: float
    parts: int
    method: str


# --- the plan --------------------------------------------------------------------------------------------------------


def cell_triangles(nx: int, ny: int) -> np.ndarray:
    """int64 [2·(nx−1)·(ny−1), 3]: the triangles of the plan over the nodes c = i + nx·j, a cell (a, b, e) + (a, e, d)
    (the diagonal a–e; counter-clockwise in the axes of the grid, which a turn about Z keeps)."""
    if nx < 2 or ny < 2:
        return np.empty((0, 3), dtype=np.int64)
    j, i = np.divmod(np.arange((nx - 1) * (ny - 1), dtype=np.int64), nx - 1)
    a = i + nx * j
    b, d, e = a + 1, a + nx, a + nx + 1
    out = np.empty((len(a), 2, 3), dtype=np.int64)
    out[:, 0] = np.column_stack([a, b, e])
    out[:, 1] = np.column_stack([a, e, d])
    return out.reshape(-1, 3)


def _known(xy: np.ndarray, z: np.ndarray) -> np.ndarray:
    return np.isfinite(z) & np.isfinite(xy).all(axis=1)


def surface_mesh(xy: np.ndarray, z: np.ndarray, nx: int, ny: int) -> tuple[np.ndarray, np.ndarray]:
    """A DTM: (vertices float64 [V, 3], triangles int32 [T, 3]) of the nodes with a z, two triangles a cell; a node
    with a NaN z (and a node no whole triangle uses) is not built. Empty triangles — a mesh of nothing (the job
    answers for it)."""
    tri = cell_triangles(nx, ny)
    ok = _known(xy, z)
    tri = tri[ok[tri].all(axis=1)]
    used = np.zeros(len(z), dtype=bool)
    used[tri.reshape(-1)] = True
    index = np.cumsum(used) - 1
    xyz = np.column_stack([xy[used], z[used]])
    return np.ascontiguousarray(xyz, dtype=np.float64), index[tri].astype(np.int32)


# --- variant Б: the body between two surfaces on one grid of nodes ---------------------------------------------------


@dataclass(frozen=True)
class _Edges:
    """The directed edges of the kept triangles: edge e = 3·t + c is the corner c → c + 1 of the triangle t; `pair`
    — the two sides of every edge two kept triangles share; `single` — the edges of one kept triangle only."""

    u: np.ndarray
    w: np.ndarray
    first: np.ndarray
    second: np.ndarray
    single: np.ndarray


def _edges(kt: np.ndarray) -> _Edges:
    k = len(kt)
    c0 = np.tile(np.arange(3, dtype=np.int64), k)
    t = np.repeat(np.arange(k, dtype=np.int64), 3)
    u, w = kt[t, c0], kt[t, (c0 + 1) % 3]
    n = int(kt.max()) + 1
    key = np.minimum(u, w) * n + np.maximum(u, w)
    order = np.argsort(key, kind="stable")
    sk = key[order]
    same = sk[1:] == sk[:-1]  # an edge has at most two kept triangles (a planar triangulation)
    has_pair = np.zeros(len(order), dtype=bool)
    has_pair[:-1] |= same
    has_pair[1:] |= same
    at = np.flatnonzero(same)
    return _Edges(u, w, order[at], order[at + 1], order[~has_pair])


def _arcs(kt: np.ndarray, mined: np.ndarray, ed: _Edges) -> tuple[np.ndarray, np.ndarray]:
    """The runs of the kept triangles around the seam vertices: (label int64 [3K], closed bool [labels]). The item
    3·t + c is the corner c of the kept triangle t; two items of one vertex are of one run when their triangles share
    an edge whose other end is mined (not a knife edge). `closed` — a run that goes all the way round the vertex."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    e1, e2 = ed.first, ed.second
    a_nodes, b_nodes = ed.u[e1], ed.w[e1]
    t1, c1 = e1 // 3, e1 % 3
    t2, c2 = e2 // 3, e2 % 3
    ia1, ib1 = 3 * t1 + c1, 3 * t1 + (c1 + 1) % 3  # the items of the nodes a and b in the first triangle
    flip = ed.u[e2] != a_nodes
    ia2 = np.where(flip, 3 * t2 + (c2 + 1) % 3, 3 * t2 + c2)  # … and in the second one
    ib2 = np.where(flip, 3 * t2 + c2, 3 * t2 + (c2 + 1) % 3)
    link_a = ~mined[a_nodes] & mined[b_nodes]  # the vertex a is on the seam, its neighbour b is mined: linked
    link_b = ~mined[b_nodes] & mined[a_nodes]
    left = np.concatenate([ia1[link_a], ib1[link_b]])
    right = np.concatenate([ia2[link_a], ib2[link_b]])
    size_all = 3 * len(kt)
    graph = coo_matrix((np.ones(len(left), dtype=np.int8), (left, right)), shape=(size_all, size_all))
    count, label = connected_components(graph, directed=False)
    size = np.bincount(label, minlength=count)
    links = np.bincount(label[left], minlength=count)
    return label.astype(np.int64), (size == links) & (size > 2)  # a ring: as many links as corners


def between(xy: np.ndarray, top: np.ndarray, bottom: np.ndarray, nx: int, ny: int) -> Solid:
    """Variant Б (module docstring). `xy` float64 [nx·ny, 2] (the centres of the columns, column c = i + nx·j), `top`
    and `bottom` float64 [nx·ny] (NaN — no node). SolidError `empty_mesh` — nothing is mined."""
    top = np.asarray(top, dtype=np.float64)
    bottom = np.asarray(bottom, dtype=np.float64)
    known = _known(xy, top) & _known(xy, bottom)
    thick = np.where(known, top - np.minimum(bottom, top), 0.0)
    mined = known & (thick > EPS)
    bottom = np.where(mined, top - thick, top)  # a node on the seam: the bottom IS the top, bit for bit
    tri = cell_triangles(nx, ny)
    tri = tri[known[tri].all(axis=1)]
    kt = tri[mined[tri].any(axis=1)]
    if len(kt) == 0:
        raise gs.empty_error("Карьер")
    k = len(kt)
    nodes = kt.reshape(-1)
    ed = _edges(kt)
    label, closed = _arcs(kt, mined, ed)
    seam = ~mined[nodes]
    weld = seam & ~closed[label]  # a seam vertex inside a ring of mined nodes is not welded (top and bottom apart)
    split = ~weld  # the corners that have a top vertex and a bottom vertex of their own (those of the node)
    node_ids = np.unique(nodes[split])
    top_id = np.full(len(top), -1, dtype=np.int64)
    top_id[node_ids] = np.arange(len(node_ids))
    bot_id = np.full(len(top), -1, dtype=np.int64)
    bot_id[node_ids] = len(node_ids) + np.arange(len(node_ids))
    base = 2 * len(node_ids)
    runs, first, arc_of = np.unique(label[weld], return_index=True, return_inverse=True)
    ids_top = np.empty(3 * k, dtype=np.int64)
    ids_bot = np.empty(3 * k, dtype=np.int64)
    ids_top[split] = top_id[nodes[split]]
    ids_bot[split] = bot_id[nodes[split]]
    ids_top[weld] = ids_bot[weld] = base + arc_of.reshape(-1)
    welded_nodes = nodes[weld][first]  # one corner of every run: its node holds the coordinates
    xyz = np.empty((base + len(runs), 3), dtype=np.float64)
    xyz[: len(node_ids), :2] = xy[node_ids]
    xyz[: len(node_ids), 2] = top[node_ids]
    xyz[len(node_ids) : base, :2] = xy[node_ids]
    xyz[len(node_ids) : base, 2] = bottom[node_ids]
    xyz[base:, :2] = xy[welded_nodes]
    xyz[base:, 2] = top[welded_nodes]
    top_tri = ids_top.reshape(k, 3)
    bot_tri = ids_bot.reshape(k, 3)[:, ::-1]  # the bottom faces down
    # the walls: an edge of one kept triangle with a mined end has no neighbour in the plan (the edge of the grid, or
    # a node without a z beyond it): a vertical wall between its top and its bottom closes the solid there
    wall = ed.single[mined[ed.u[ed.single]] | mined[ed.w[ed.single]]]
    cu, cw = ids_top[wall], ids_top[3 * (wall // 3) + (wall % 3 + 1) % 3]  # the top vertices of the edge u → w
    bu, bw = ids_bot[wall], ids_bot[3 * (wall // 3) + (wall % 3 + 1) % 3]
    walls = np.concatenate([np.column_stack([cw, cu, bu]), np.column_stack([cw, bu, bw])])
    walls = walls[(walls[:, 0] != walls[:, 1]) & (walls[:, 1] != walls[:, 2]) & (walls[:, 0] != walls[:, 2])]
    return _solid(xyz, np.concatenate([top_tri, bot_tri, walls]), "between")


def _solid(xyz: np.ndarray, tri: np.ndarray, method: str) -> Solid:
    """Check a mesh as a closed solid (`is_closed_solid`, then manifold3d) and measure it. SolidError."""
    closed, reason = gs.is_closed_solid(xyz, tri)
    if not closed:
        raise gs.SolidError("wireframe_open", f"Тело карьера {reason}.", gs.FIX_HINT)
    origin = gs.local_origin(xyz)
    local = xyz - origin
    gs.to_manifold(local, tri, "Карьер")  # manifold3d accepts it (check_status)
    volume = gs.signed_volume(local, tri)
    if volume < 0:
        raise gs.SolidError("wireframe_orientation", "Тело карьера вывернуто наизнанку.", gs.FIX_HINT)
    xyz2, tri2 = gs.compact(xyz, tri)
    return Solid(xyz2, tri2, float(volume), gs.part_count(tri2, len(xyz2)), method)


# --- variant А: the difference of two solids under surfaces (the fallback) -------------------------------------------


def difference(xy: np.ndarray, top: np.ndarray, bottom: np.ndarray, nx: int, ny: int, tick: Tick = None) -> Solid:
    """Variant А: under_solid(relief) − under_solid(surface of the shell) down to one plane below both. The nodes
    known in both surfaces only (the triangles of both are over the same nodes)."""
    from bf_core.geo import volumes

    top = np.asarray(top, dtype=np.float64)
    bottom = np.minimum(np.asarray(bottom, dtype=np.float64), top)
    known = _known(xy, top) & _known(xy, bottom)
    z_top, z_bot = np.where(known, top, np.nan), np.where(known, bottom, np.nan)
    vt, tt = surface_mesh(xy, z_top, nx, ny)
    vb, tb = surface_mesh(xy, z_bot, nx, ny)
    if len(tt) == 0 or len(tb) == 0:
        raise gs.empty_error("Карьер")
    low = float(min(vt[:, 2].min(), vb[:, 2].min()))
    base = low - max(1.0, 0.01 * float(max(vt[:, 2].max(), vb[:, 2].max()) - low))
    a = volumes.under_solid(vt, tt, base)  # SurfaceError is a SolidError
    b = volumes.under_solid(vb, tb, base)
    v, t, _report = gs.boolean(a, b, "difference", names=["Рельеф", "Поверхность оболочки"], tick=tick)
    if len(t) == 0:
        raise gs.empty_error("Карьер")
    t = t.astype(np.int64)
    closed, reason = gs.is_closed_solid(v, t)
    if not closed:
        raise gs.SolidError("wireframe_open", f"Тело карьера {reason}.", gs.FIX_HINT)
    local = v - gs.local_origin(v)
    return Solid(v, t, abs(float(gs.signed_volume(local, t))), gs.part_count(t, len(v)), "difference")


def build(xy: np.ndarray, top: np.ndarray, bottom: np.ndarray, nx: int, ny: int, tick: Tick = None) -> Solid:
    """Variant Б; when it is not a closed manifold — variant А. SolidError (the last one) when neither is. An empty
    pit (nothing is mined) is not retried."""
    try:
        return between(xy, top, bottom, nx, ny)
    except gs.SolidError as first:
        if first.code == "empty_mesh":
            raise
        try:
            return difference(xy, top, bottom, nx, ny, tick)
        except gs.SolidError:
            raise first from None
