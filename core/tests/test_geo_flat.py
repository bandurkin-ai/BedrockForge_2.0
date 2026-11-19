"""Flat regions of a mesh before pyfqmr (ADR 0013 §4 `flat.py`, «Долги Э7»): a dense plane tilted both ways
goes down to 1/8 and 1/64 of its triangles with no new vertex (the deviation from the plane is 0, area 1e-9,
triangles of quality 0.4 and 0.1); faces and straight edges of a box keep its volume and area (1e-12);
the border of a part and of a hole stays bit for bit and the borders of neighbouring parts coincide at every
level; relief and noise above the tolerance are left to pyfqmr, which never gets the flat vertices that
stayed; strips between edges (benches of a pit) are emptied; hard meshes — slivers, triangles without area
or given twice, edges of three triangles, a patch turned over, a fan of 104 triangles — do not break it."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.geo import flat
from bf_core.geo import meshes as gm
from tests.test_viz_mesh import UTM, areas, border_vertices, cube, grid, hills, rows

pytestmark = pytest.mark.windows

SLOPE = float(np.sqrt(1.13))  # of z = 0.3 x − 0.2 y


def tilted(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 100.0 + 0.3 * x - 0.2 * y


def local_of(v: np.ndarray) -> np.ndarray:
    """Offsets from a whole-metre origin, the way a part gives them (exact in float64)."""
    return np.asarray(v - np.round((v.min(axis=0) + v.max(axis=0)) / 2))


def rotated(v: np.ndarray, degrees: tuple[float, float, float] = (25.0, 35.0, 15.0)) -> np.ndarray:
    a, b, c = np.radians(degrees)
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return np.asarray(v @ (rz @ ry @ rx).T)


def under(v: np.ndarray, t: np.ndarray) -> float:
    """The volume between the surface and z = 0: the same number for the same surface, however it is cut."""
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    plan = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    return float((plan * (a[:, 2] + b[:, 2] + c[:, 2]) / 3.0).sum())


def quality(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """1 — an equilateral triangle, 0 — a degenerate one."""
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    sides = ((b - a) ** 2).sum(axis=1) + ((c - b) ** 2).sum(axis=1) + ((a - c) ** 2).sum(axis=1)
    return np.asarray(4.0 * np.sqrt(3.0) * areas(v, t) / sides)


def looks_up(v: np.ndarray, t: np.ndarray) -> bool:
    return bool((np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])[:, 2] > 0).all())


# --- one plane -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("share", [8, 64])
def test_a_plane_tilted_both_ways_reaches_its_share_and_stays_the_same_surface(share: int) -> None:
    v, t = grid(178, 178, tilted)  # the grid of Э7: 63 368 triangles on UTM coordinates
    local = local_of(v)
    target = len(t) // share
    out_v, out_t, held = flat.thin(local, t, target)
    assert len(out_t) in (target, target - 1)  # a collapse takes two triangles
    assert out_v.dtype == np.float64 and out_t.dtype == np.int32 and held.shape == (len(out_v),)
    assert rows(out_v) <= rows(local)  # no new vertex: nothing has left the plane
    assert sorted(np.unique(out_t).tolist()) == list(range(len(out_v)))  # no vertex is left unused
    assert rows(out_v[border_vertices(out_t)]) == rows(local[border_vertices(t)])  # the border, bit for bit
    assert looks_up(out_v, out_t)
    assert areas(out_v, out_t).sum() == pytest.approx(178.0 * 178.0 * SLOPE, rel=1e-9)
    assert under(out_v, out_t) == pytest.approx(under(local, t), rel=1e-12)
    assert quality(out_v, out_t).min() >= {8: 0.4, 64: 0.1}[share] * 0.999  # well-shaped triangles, no slivers


def test_relief_and_noise_above_the_tolerance_are_left_alone() -> None:
    v, t = grid(60, 50, hills)
    local = local_of(v)
    out_v, out_t, held = flat.thin(local, t, 1)
    assert out_v is local and out_t is t and not held.any()
    rng = np.random.default_rng(5)
    noisy = local_of(grid(60, 50, tilted)[0])
    noisy[:, 2] += rng.normal(0.0, 1e-4, len(noisy))  # 0.1 mm on a cell of 1 m: 100 times the tolerance
    out_v, out_t, held = flat.thin(noisy, t, 1)
    assert out_v is noisy and out_t is t and not held.any()


def test_noise_below_the_tolerance_is_a_plane() -> None:
    v, t = grid(60, 50, tilted, offset=np.zeros(3))
    rng = np.random.default_rng(6)
    v[:, 2] += rng.uniform(-1e-8, 1e-8, len(v))  # 100 times less than the tolerance on a cell of 1 m
    out_v, out_t, _held = flat.thin(v, t, len(t) // 8)
    assert len(out_t) in (len(t) // 8, len(t) // 8 - 1) and rows(out_v) <= rows(v)
    assert np.abs(out_v[:, 2] - tilted(out_v[:, 0], out_v[:, 1])).max() <= 1e-8
    assert under(out_v, out_t) == pytest.approx(under(v, t), rel=1e-9)


def test_only_vertices_of_flat_fans_go() -> None:
    def bump(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return tilted(x, y) + np.maximum(0.0, 6.0 - np.hypot(x - 30.0, y - 25.0))

    v, t = grid(60, 50, bump, offset=np.zeros(3))
    out_v, out_t, held = flat.thin(v, t, 1)
    near = np.hypot(v[:, 0] - 30.0, v[:, 1] - 25.0) < 6.0  # the fans of these vertices are not flat
    assert rows(v[near]) <= rows(out_v) and rows(v[border_vertices(t)]) <= rows(out_v)
    assert len(out_t) < len(t) // 4 and looks_up(out_v, out_t)
    assert under(out_v, out_t) == pytest.approx(under(v, t), rel=1e-12)  # the same surface
    assert areas(out_v, out_t).sum() == pytest.approx(areas(v, t).sum(), rel=1e-12)
    # what stayed inside the plane could not go without a fold or a sliver: pyfqmr must not get it
    inside = held & ~np.isin(np.arange(len(out_v)), border_vertices(out_t))
    assert (held == inside).all()
    assert np.abs(out_v[held, 2] - tilted(out_v[held, 0], out_v[held, 1])).max(initial=0.0) <= 1e-12


def test_border_of_a_part_and_of_a_hole_stays() -> None:
    v, t = grid(40, 40, tilted)
    centre = (v[t[:, 0]] + v[t[:, 1]] + v[t[:, 2]]) / 3.0 - UTM
    t = t[np.hypot(centre[:, 0] - 20.0, centre[:, 1] - 20.0) > 7.0]  # a hole in the middle
    local = local_of(v)
    border = border_vertices(t)
    assert len(border) > 4 * 40
    out_v, out_t, _held = flat.thin(local, t, 1)
    assert rows(out_v[border_vertices(out_t)]) == rows(local[border])
    assert len(out_t) < len(t) // 3 and looks_up(out_v, out_t)
    assert areas(out_v, out_t).sum() == pytest.approx(areas(local, t).sum(), rel=1e-9)


# --- two planes: faces and straight edges of a solid -------------------------------------------------------


@pytest.mark.parametrize("turn", [False, True])
def test_faces_and_straight_edges_of_a_box_go_down_to_its_corners(turn: bool) -> None:
    v, t = cube(12, 37.5, offset=np.zeros(3))  # 1728 triangles
    v = (rotated(v) if turn else v) + UTM
    local = local_of(v)
    rel = 1e-9 if turn else 1e-12  # turned, the faces are flat to the rounding of UTM coordinates: 1e-9 m
    for target, expected in ((216, 216), (1, 12)):
        out_v, out_t, held = flat.thin(local, t, target)
        assert len(out_t) == expected and rows(out_v) <= rows(local)
        s = gm.mesh_stats(out_v, out_t)
        assert s["closed"] and s["orientation"] == "outward" and s["degenerate"] == 0
        assert s["volume"] == pytest.approx(37.5**3, rel=rel) and s["area"] == pytest.approx(6 * 37.5**2, rel=rel)
    assert len(out_v) == 8 and not held.any()  # the corners: three planes each, pyfqmr may have them


def test_a_bent_edge_and_a_corner_stay() -> None:
    def roof(x: np.ndarray, y: np.ndarray) -> np.ndarray:  # two slopes, the ridge along x = 10
        return 50.0 + 0.5 * np.minimum(x, 20.0 - x) + 0.1 * y

    v, t = grid(20, 12, roof, offset=np.zeros(3))
    out_v, out_t, _held = flat.thin(v, t, 1)
    assert rows(out_v[border_vertices(out_t)]) == rows(v[border_vertices(t)])
    assert under(out_v, out_t) == pytest.approx(under(v, t), rel=1e-12) and looks_up(out_v, out_t)
    assert len(out_t) <= 2 * (20 + 12) + 8  # the slopes and the ridge are gone, the border is what is left
    bent = v.copy()
    ridge = np.isclose(v[:, 0], 10.0) & np.isclose(v[:, 1], 6.0)
    bent[ridge, 2] += 0.25  # a ridge that is not straight at this vertex: three and more planes around it
    out_v, out_t, _held = flat.thin(bent, t, 1)
    assert rows(bent[ridge]) <= rows(out_v)
    assert under(out_v, out_t) == pytest.approx(under(bent, t), rel=1e-12) and looks_up(out_v, out_t)


# --- hard meshes -------------------------------------------------------------------------------------------


def test_hard_meshes_keep_their_surface() -> None:
    v, t = grid(30, 30, tilted, offset=np.zeros(3), seed=None)
    # every triangle with vertices of its own: all of them are on a border, nothing may go
    soup_v, soup_t = v[t.reshape(-1)], np.arange(3 * len(t), dtype=np.int32).reshape(-1, 3)
    out_v, out_t, held = flat.thin(soup_v, soup_t, 1)
    assert out_v is soup_v and out_t is soup_t and not held.any()
    # unused vertices, a triangle without area, a triangle given twice, a fin on three triangles of one edge
    extra = np.array([[5.0, 5.0, 900.0], [1e6, 1e6, 0.0]])
    fin = len(v)
    i = 15 * 31 + 15
    bad = np.array([[i, i, i + 1], [i + 40, i + 41, i + 72], [i + 40, i + 41, i + 72], [i - 200, i - 199, fin]])
    hv, ht = np.concatenate([v, extra]), np.concatenate([t, bad]).astype(np.int32)
    out_v, out_t, _held = flat.thin(hv, ht, 1)
    assert len(out_t) < len(ht) // 3 and rows(out_v) <= rows(hv)
    assert int(out_t.max()) < len(out_v) and int(out_t.min()) >= 0
    assert areas(out_v, out_t).sum() == pytest.approx(areas(hv, ht).sum(), rel=1e-12)
    assert under(out_v, out_t) == pytest.approx(under(hv, ht), rel=1e-12)
    assert rows(hv[[fin]]) <= rows(out_v)
    # triangles turned the other way in a patch: the fans across its edge are not oriented one way
    flipped = t.copy()
    patch = np.arange(len(t)) % 7 == 0
    flipped[patch] = flipped[patch][:, ::-1]
    out_v, out_t, _held = flat.thin(v, flipped, 1)
    assert areas(out_v, out_t).sum() == pytest.approx(areas(v, flipped).sum(), rel=1e-12)
    # a wheel wider than the fan buffers: its hub stays and is kept away from pyfqmr
    spokes = flat.MAX_RING + 40
    ang = np.arange(spokes) * (2 * np.pi / spokes)
    wv = np.concatenate([[[0.0, 0.0, 0.0]], np.column_stack([np.cos(ang), np.sin(ang), 0.3 * np.cos(ang)]) * 50.0])
    wt = np.array([[0, 1 + k, 1 + (k + 1) % spokes] for k in range(spokes)], dtype=np.int32)
    out_v, out_t, held = flat.thin(wv, wt, 1)
    assert out_v is wv and out_t is wt and held.tolist() == [True] + [False] * spokes
    # empty and tiny input, a target that is met already
    assert flat.thin(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 1)[1].shape == (0, 3)
    assert flat.thin(v, t, len(t))[1] is t


def test_a_fan_with_slivers_of_its_own_is_cut_no_worse_than_it_is() -> None:
    v, t = grid(30, 30, lambda x, y: 0 * x, offset=np.zeros(3))
    v[:, 0] *= 1e4  # cells of 10 km × 1 m, the way strings of sections are joined
    v[:, 2] = 0.3 * v[:, 0] - 0.2 * v[:, 1]
    before = quality(v, t).min()
    assert before < flat.QUALITY[-1] / 5  # every triangle is worse than the last step of quality
    out_v, out_t, _held = flat.thin(v, t, len(t) // 4)
    assert len(out_t) in (len(t) // 4, len(t) // 4 - 1) and rows(out_v) <= rows(v) and looks_up(out_v, out_t)
    assert quality(out_v, out_t).min() >= before * 0.999
    assert areas(out_v, out_t).sum() == pytest.approx(areas(v, t).sum(), rel=1e-12)
    assert rows(out_v[border_vertices(out_t)]) == rows(v[border_vertices(t)])


def test_the_result_does_not_depend_on_the_run_or_on_read_only_input() -> None:
    v, t = grid(50, 40, tilted)
    local = local_of(v)
    first = flat.thin(local, t, len(t) // 8)
    ro_v, ro_t = local.copy(), t.copy()
    ro_v.setflags(write=False)
    ro_t.setflags(write=False)
    again = flat.thin(ro_v, ro_t, len(t) // 8)
    assert first[0].tobytes() == again[0].tobytes() and first[1].tobytes() == again[1].tobytes()
    assert ro_t.tobytes() == t.tobytes()  # the input is not written to


# --- with pyfqmr: levels of detail -------------------------------------------------------------------------


def half(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """A plane tilted both ways up to x = 89, relief that grows from it beyond."""
    return np.where(x <= 89.0, tilted(x, y), tilted(89.0, y) + hills(x - 89.0, y) * (x - 89.0) / 30.0)


def test_pyfqmr_gets_the_rest_and_the_plane_stays_a_plane() -> None:
    v, t = grid(178, 178, half)
    t = t[gm.morton_order(v, t)]
    (p,) = gm.parts_table(v, t)
    local, tri = gm.part_mesh(v, t, p)
    made = gm.levels(local, tri)
    counts = [len(m[1]) for m in made]
    assert counts[1] <= len(tri) / 8 and counts[2] <= len(tri) / 64 * 1.02  # the border allows 1/64 here
    border = rows(local[border_vertices(tri)])
    worst = []
    for lv, lt in made[1:]:
        rel = lv + np.array(p.origin) - UTM
        assert rows(lv[border_vertices(lt)]) == border and looks_up(lv, lt)
        on_plane = rel[:, 0] < 88.5  # a column away from the relief: pyfqmr moves the vertices of the seam
        assert np.abs(rel[on_plane, 2] - tilted(rel[on_plane, 0], rel[on_plane, 1])).max() <= 1e-9
        worst.append(float(np.abs(rel[:, 2] - half(rel[:, 0], rel[:, 1])).max()))
    print("half a plane, half relief: triangles", counts, "deviation, m", [round(w, 3) for w in worst])
    assert worst[0] < 1.0 and worst[1] < 6.0  # the relief is up to 60 m high; it was 5.4 m with the plane spoilt


def pit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Benches 10 m wide and slopes 10 m wide and high, across a direction 30° off X: flat strips tilted both
    ways between bands of triangles that step over the edges."""
    s = x * np.cos(np.radians(30.0)) + y * np.sin(np.radians(30.0))
    k = np.floor(s / 20.0)
    return np.asarray(300.0 - 10.0 * k - np.maximum(s - 20.0 * k - 10.0, 0.0))


def test_strips_between_edges_are_emptied_and_the_levels_reach_their_shares() -> None:
    v, t = grid(178, 178, pit)
    t = t[gm.morton_order(v, t)]
    (p,) = gm.parts_table(v, t)
    local, tri = gm.part_mesh(v, t, p)
    out_v, out_t, held = flat.thin(local, tri, 1)
    assert len(out_t) < len(tri) * 0.3 and not held.any()  # every vertex inside a strip has gone (fans left 9 %)
    assert under(out_v, out_t) == pytest.approx(under(local, tri), rel=1e-12)
    made = gm.levels(local, tri)
    counts = [len(m[1]) for m in made]
    assert counts[1] <= len(tri) / 8 and counts[2] <= len(tri) / 64 * 1.02
    border = rows(local[border_vertices(tri)])
    worst = []
    for lv, lt in made[1:]:
        rel = lv + np.array(p.origin) - UTM
        assert rows(lv[border_vertices(lt)]) == border
        worst.append(float(np.abs(rel[:, 2] - pit(rel[:, 0], rel[:, 1])).max()))
    print("benches of a pit: triangles", counts, "deviation, m", [round(w, 3) for w in worst])
    assert worst[0] < 0.3 and worst[1] < 0.4  # as pyfqmr alone (0.09 and 0.13); the mesh itself cuts 0.34 off


def wheel(spokes: int, rings: int, fins: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A flat disc tilted both ways — a hub and `spokes` vertices on its rim — in the middle of relief on
    `rings` rings around it; the last ring is the border. `fins` puts a third sheet — two triangles — on two
    edges of the rim that meet: edges of three triangles. Returns (xyz, tri, rim)."""
    ang = np.arange(spokes) * (2 * np.pi / spokes)
    pts = [np.array([[0.0, 0.0, 0.0]])]
    for j in range(rings + 1):
        r = 50.0 + 12.0 * j
        z = 0.3 * r * np.cos(ang) - 0.2 * r * np.sin(ang) + (3.0 * np.sin(5 * ang + j) * min(j, 2) if j else 0.0)
        pts.append(np.column_stack([r * np.cos(ang), r * np.sin(ang), z]))
    v = np.concatenate(pts)
    ring = lambda j: 1 + j * spokes + np.arange(spokes)  # noqa: E731
    nxt = lambda a: np.roll(a, -1)  # noqa: E731
    tri = [np.column_stack([np.zeros(spokes, dtype=np.int64), ring(0), nxt(ring(0))])]
    for j in range(rings):
        a, b = ring(j), ring(j + 1)
        tri += [np.column_stack([a, b, nxt(b)]), np.column_stack([a, nxt(b), nxt(a)])]
    t = np.concatenate(tri)
    if fins:
        top = len(v)
        v = np.concatenate([v, [[60.0, 3.0, 40.0]]])
        t = np.concatenate([t, [[1, 2, top], [2, 3, top]]])
    return v + UTM, t.astype(np.int32), ring(0)


def test_a_flat_vertex_that_stayed_is_kept_away_from_pyfqmr_and_joined_back() -> None:
    spokes = flat.MAX_RING + 40
    v, t, rim = wheel(spokes, 6)
    local = local_of(v)
    _v, _t, held = flat.thin(local, t, len(t) // 8)
    assert held.tolist() == [True] + [False] * (len(local) - 1)  # the hub: its fan is longer than the buffers
    out_v, out_t = gm.simplify(local, t, len(t) // 8)
    assert len(out_t) < len(t) / 2 and out_t.dtype == np.int32 and int(out_t.max()) < len(out_v)
    disc = np.concatenate([[0], rim])
    assert rows(local[disc]) <= rows(out_v)  # the hub and the rim of its fan are where they were
    at = {r.tobytes(): i for i, r in enumerate(out_v)}
    hub = at[local[0].tobytes()]
    fan = out_t[(out_t == hub).any(axis=1)]
    assert len(fan) == spokes and rows(out_v[np.unique(fan)]) == rows(local[disc])  # the disc as it was
    assert gm.edge_summary(out_t, len(out_v)) == (spokes, 0)  # one piece: only the last ring is a border
    assert rows(out_v[border_vertices(out_t)]) == rows(local[border_vertices(t)])


def test_a_cut_that_is_no_border_gives_the_mesh_to_pyfqmr_whole() -> None:
    v, t, rim = wheel(flat.MAX_RING + 40, 6, fins=True)
    local = local_of(v)
    _v, _t, held = flat.thin(local, t, len(t) // 8)
    keep = held[t].any(axis=1)
    used = np.unique(t[~keep])
    seam = np.searchsorted(used, np.intersect1d(t[keep], used))
    rest = np.searchsorted(used, t[~keep]).astype(np.int32)
    assert held[0] and not flat.bordered(rest, seam)  # a vertex of the rim: every edge at it has two triangles left
    assert flat.bordered(rest, np.delete(seam, 1))
    out_v, out_t = gm.simplify(local, t, len(t) // 8)
    assert len(out_t) < len(t) / 2  # not left as it was
    assert rows(out_v[border_vertices(out_t)]) <= rows(local[border_vertices(t)])


def test_borders_of_neighbouring_parts_of_a_plane_coincide_at_every_level() -> None:
    v, t = grid(178, 178, tilted)
    t = t[gm.morton_order(v, t)]
    parts = gm.parts_table(v, t, max_triangles=8000)
    assert len(parts) == 8
    seam: dict[bytes, int] = {}
    total = [0, 0, 0]
    area = [0.0, 0.0, 0.0]
    for p in parts:
        local, tri = gm.part_mesh(v, t, p)
        made = gm.levels(local, tri)
        border = local[border_vertices(tri)]
        for lod, (lv, lt) in enumerate(made):
            total[lod] += len(lt)
            area[lod] += float(areas(lv, lt).sum())
            assert rows(lv[border_vertices(lt)]) == rows(border)  # float64, bit for bit, at every level
            assert rows(lv) <= rows(local) and looks_up(lv, lt)  # no vertex has moved
        for r in rows(border + np.array(p.origin)):
            seam[r] = seam.get(r, 0) + 1
    assert sum(1 for n in seam.values() if n >= 2) > 500  # the parts do share their borders
    assert total[1] <= total[0] / 8 * 1.02 and total[2] < total[1] / 2  # 1/64 is below what the borders need
    for a in area:
        assert a == pytest.approx(178.0 * 178.0 * SLOPE, rel=1e-9)


def test_join_puts_the_kept_triangles_back_on_the_vertices_of_the_rest() -> None:
    v, t = grid(6, 4, hills, offset=np.zeros(3), seed=None)
    kept = t[:10]
    used = np.unique(t[10:])
    seam = np.intersect1d(kept, used)
    assert 0 < len(seam) < len(np.unique(kept))  # some vertices of the kept triangles are theirs alone
    rest_v = v[used][::-1].copy()  # another order of the vertices, as a decimation gives
    rest_t = (len(used) - 1 - np.searchsorted(used, t[10:])).astype(np.int32)
    joined = flat.join(v, kept, seam, rest_v, rest_t)
    assert joined is not None
    out_v, out_t = joined
    assert out_t.dtype == np.int32 and len(out_t) == len(t) and len(out_v) == len(v)
    assert {r.tobytes() for r in out_v[out_t].reshape(-1, 9)} == {r.tobytes() for r in v[t].reshape(-1, 9)}
    assert rows(out_v[border_vertices(out_t)]) == rows(v[border_vertices(t)])  # welded: no seam inside
    moved = rest_v.copy()
    moved[len(used) - 1 - np.searchsorted(used, seam[0])] += 1e-3  # a vertex of the seam is not where it was
    assert flat.join(v, kept, seam, moved, rest_t) is None
