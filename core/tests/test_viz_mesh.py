"""Parts and levels of detail of a wireframe for 3D (ADR 0011 §3, ADR 0004 §2–3): the spatial order is a
permutation that makes compact parts; parts of at most 64 000 triangles and 16 km with a whole-metre origin;
float32 offsets within 0.25 mm; levels of detail of a plane stay on the plane and keep its area (1e-9), the
border of a part is the same float32 numbers at every level; measures of a cube against the analytic answer
(1e-12); BFA1 `mesh` with u2/u4 indices, the cache of the levels, ETag and 304."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.geo import meshes as gm
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.wireframes import store as wf_store
from tests.test_import_job import API

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])
F32 = 2.5e-4  # float32 rounding of an offset below 8192 m, per axis


# --- synthetic meshes ------------------------------------------------------------------------------------


def grid(nx: int, ny: int, f, step: float = 1.0, offset: np.ndarray = UTM, seed: int | None = 0):  # type: ignore[no-untyped-def]
    """A surface z = f(x, y) on a grid of nx × ny cells (2 triangles each, normals up); `seed` shuffles the
    triangles, so nothing in their order helps the parts."""
    x, y = np.meshgrid(np.arange(nx + 1) * step, np.arange(ny + 1) * step)
    v = np.column_stack([x.ravel(), y.ravel(), f(x.ravel(), y.ravel())]) + offset
    i = (np.arange(ny)[:, None] * (nx + 1) + np.arange(nx)[None, :]).ravel()
    t = np.concatenate([np.column_stack([i, i + 1, i + nx + 2]), np.column_stack([i, i + nx + 2, i + nx + 1])])
    if seed is not None:
        t = t[np.random.default_rng(seed).permutation(len(t))]
    return v, t.astype(np.int32)


def cube(n: int, side: float, offset: np.ndarray = UTM):  # type: ignore[no-untyped-def]
    """A closed cube of `side` with n × n cells a face, vertices welded, normals outward."""
    faces_v, faces_t = [], []
    s = np.linspace(0.0, side, n + 1)
    a, b = np.meshgrid(s, s)
    a, b = a.ravel(), b.ravel()
    i = (np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]).ravel()
    quad = np.concatenate([np.column_stack([i, i + 1, i + n + 2]), np.column_stack([i, i + n + 2, i + n + 1])])
    zero, full = np.zeros_like(a), np.full_like(a, side)
    for axis, value, flip in ((2, zero, True), (2, full, False), (0, zero, True), (0, full, False),
                              (1, zero, False), (1, full, True)):  # fmt: skip
        cols = {0: (value, a, b), 1: (a, value, b), 2: (a, b, value)}[axis]
        faces_t.append((quad[:, ::-1] if flip else quad) + sum(len(p) for p in faces_v))
        faces_v.append(np.column_stack(cols))
    v = np.concatenate(faces_v)
    t = np.concatenate(faces_t)
    uniq, inverse = np.unique(np.round(v, 9), axis=0, return_inverse=True)
    return uniq + offset, inverse.reshape(-1)[t].astype(np.int32)


def hills(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 20.0 * np.sin(x / 17.0) * np.cos(y / 23.0)


def border_vertices(tri: np.ndarray) -> np.ndarray:
    """Vertices of the edges that belong to one triangle only."""
    e = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    edges, counts = np.unique(e, axis=0, return_counts=True)
    return np.unique(edges[counts == 1])


def areas(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    c = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    return np.asarray(0.5 * np.linalg.norm(c, axis=1))


def rows(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


# --- order and parts -------------------------------------------------------------------------------------


def test_morton_order_makes_compact_parts() -> None:
    v, t = grid(160, 120, hills)  # 38 400 triangles, relief of 40 m over 160 × 120 m
    order = gm.morton_order(v, t)
    assert sorted(order.tolist()) == list(range(len(t)))  # a permutation: the same triangles
    sorted_t = t[order]
    plan = 160.0 * 120.0

    def cover(tri: np.ndarray) -> float:
        """The plan boxes of the parts, summed, over the plan area of the surface: 1 — no overlap."""
        parts = gm.parts_table(v, tri, max_triangles=2400)
        assert len(parts) == 16
        return sum((p.bbox[3] - p.bbox[0]) * (p.bbox[4] - p.bbox[1]) for p in parts) / plan

    assert cover(sorted_t) < 3.0  # compact pieces of the plan (2.2), not elevation bands (5 and more)
    assert cover(t) > 12.0  # the shuffled triangles: every part spans the whole surface
    # ties keep the order given: of two equal triangles the first stays first
    place = np.argsort(gm.morton_order(v, np.concatenate([t, t])))
    assert (place[: len(t)] < place[len(t) :]).all()


@pytest.mark.parametrize(("max_triangles", "max_span"), [(64_000, 16_000.0), (500, 16_000.0), (4000, 30.0)])
def test_parts_cover_every_triangle_once(max_triangles: int, max_span: float) -> None:
    v, t = grid(100, 60, hills)
    t = t[gm.morton_order(v, t)]
    parts = gm.parts_table(v, t, max_triangles=max_triangles, max_span=max_span)
    assert [p.k for p in parts] == list(range(len(parts)))
    assert parts[0].first == 0 and parts[-1].first + parts[-1].count == len(t)
    for prev, nxt in zip(parts, parts[1:], strict=False):
        assert prev.first + prev.count == nxt.first  # consecutive runs: every triangle is in one part
    for p in parts:
        assert 1 <= p.count <= max_triangles
        pts = v[t[p.first : p.first + p.count].reshape(-1)]
        assert p.bbox == (*pts.min(axis=0).tolist(), *pts.max(axis=0).tolist())  # exact float64
        assert all(float(c).is_integer() for c in p.origin)
        centre = (np.array(p.bbox[:3]) + np.array(p.bbox[3:])) / 2
        assert np.abs(np.array(p.origin) - centre).max() <= 0.5
        span = max(p.bbox[3 + c] - p.bbox[c] for c in range(3))
        assert span <= max_span or p.count == 1
    assert gm.extent_of(parts) == [*v.min(axis=0).tolist(), *v.max(axis=0).tolist()]


def test_wide_mesh_is_cut_to_16_km_and_a_huge_triangle_stays_alone() -> None:
    v, t = grid(40, 4, lambda x, y: 0 * x, step=1000.0)  # 40 km × 4 km
    huge = np.array([[0.0, 0.0, 0.0], [90_000.0, 0.0, 0.0], [0.0, 90_000.0, 0.0]]) + UTM
    v = np.concatenate([v, huge])
    t = np.concatenate([t, [[len(v) - 3, len(v) - 2, len(v) - 1]]]).astype(np.int32)
    t = t[gm.morton_order(v, t)]
    parts = gm.parts_table(v, t)
    assert sum(p.count for p in parts) == len(t) and len(parts) >= 4
    wide = [p for p in parts if max(p.bbox[3 + c] - p.bbox[c] for c in range(3)) > gm.MAX_SPAN_M]
    assert len(wide) == 1 and wide[0].count == 1  # nothing to cut it at
    for p in parts:
        if p.count > 1:
            local, _tri = gm.part_mesh(v, t, p)
            assert np.abs(local).max() <= 8000.5


def test_part_mesh_is_exact_and_float32_is_within_a_quarter_of_a_millimetre() -> None:
    v, t = grid(90, 70, hills, step=7.3)
    t = t[gm.morton_order(v, t)]
    for p in gm.parts_table(v, t, max_triangles=3000):
        local, tri = gm.part_mesh(v, t, p)
        assert local.dtype == np.float64 and tri.dtype == np.int32 and tri.shape == (p.count, 3)
        back = (local + np.array(p.origin))[tri]
        assert back.tobytes() == v[t[p.first : p.first + p.count]].tobytes()  # the float64 vertices, bit for bit
        assert np.abs(local.astype("<f4").astype(np.float64) - local).max() <= F32


# --- levels of detail ------------------------------------------------------------------------------------


PLANES = {
    # name: (z(x, y), sqrt(1 + a² + b²), exact) — `exact`: pyfqmr places new vertices on the plane itself
    "level": (lambda x, y: 100.0 + 0 * x, 1.0, True),
    "tilted along X": (lambda x, y: 100.0 + 0.75 * x, 1.25, True),
    "tilted both ways": (lambda x, y: 100.0 + 0.3 * x - 0.2 * y, float(np.sqrt(1.13)), False),
}


@pytest.mark.parametrize("name", list(PLANES))
def test_levels_of_a_plane_stay_on_the_plane_and_keep_its_area(name: str) -> None:
    plane, slope, exact = PLANES[name]
    v, t = grid(178, 178, plane)  # 63 368 triangles: one part
    t = t[gm.morton_order(v, t)]
    (p,) = gm.parts_table(v, t)
    local, tri = gm.part_mesh(v, t, p)
    made = gm.levels(local, tri)
    assert made[0][0] is local and made[0][1] is tri
    true_area = 178.0 * 178.0 * slope
    border0 = rows(local[border_vertices(tri)].astype("<f4"))
    assert len(border0) == 4 * 178
    counts = [len(m[1]) for m in made]
    assert counts[0] > counts[1] > counts[2] >= len(border0) - 2  # the border alone needs that many
    if name == "level":  # nothing in the way: both levels reach their share (the border allows 1/64 here)
        assert counts[1] <= len(tri) / 8 * 1.02 and counts[2] <= len(tri) / 64 * 1.02
    for lv, lt in made[1:]:
        rel = lv + np.array(p.origin) - UTM
        off = np.abs(rel[:, 2] - plane(rel[:, 0], rel[:, 1])).max()
        cross = np.cross(lv[lt[:, 1]] - lv[lt[:, 0]], lv[lt[:, 2]] - lv[lt[:, 0]])
        assert (cross[:, 2] > 0).all()  # still looking up: no fold
        assert rows(lv[border_vertices(lt)].astype("<f4")) == border0  # the border, bit for bit
        if exact:
            assert off <= 1e-9 and areas(lv, lt).sum() == pytest.approx(true_area, rel=1e-9)
        else:
            # Hard data for pyfqmr (the quadric of a plane has no single best point and rounding picks one):
            # a dense, exactly flat grid tilted both ways is simplified less (about 1/2 and 1/4 here) and its
            # new vertices leave the plane by up to 0.17 of a cell. The mesh stays whole and unfolded.
            assert off <= 0.5 and areas(lv, lt).sum() == pytest.approx(true_area, rel=1e-4)


def test_levels_of_a_relief_keep_the_borders_of_neighbouring_parts() -> None:
    v, t = grid(240, 200, hills)  # 96 000 triangles
    t = t[gm.morton_order(v, t)]
    parts = gm.parts_table(v, t, max_triangles=12_000)
    assert len(parts) == 8
    seam: dict[bytes, int] = {}
    total = [0, 0, 0]
    worst = [0.0, 0.0, 0.0]  # the largest distance of a vertex from z = 20 sin(x / 17) cos(y / 23), by level
    for p in parts:
        local, tri = gm.part_mesh(v, t, p)
        made = gm.levels(local, tri)
        border = local[border_vertices(tri)]
        for lod, (lv, lt) in enumerate(made):
            total[lod] += len(lt)
            assert rows(lv[border_vertices(lt)]) == rows(border)  # float64, bit for bit, at every level
            rel = lv + np.array(p.origin) - UTM
            worst[lod] = max(worst[lod], float(np.abs(rel[:, 2] - hills(rel[:, 0], rel[:, 1])).max()))
        for r in rows(border + np.array(p.origin)):
            seam[r] = seam.get(r, 0) + 1
    assert sum(1 for n in seam.values() if n >= 2) > 500  # the parts do share their borders
    assert total[0] == len(t) and total[1] <= total[0] / 8 * 1.05 and total[2] < total[1] / 2
    print("mesh levels: triangles", total, "deviation, m", [round(w, 3) for w in worst])
    assert worst[0] <= 1e-9 and worst[1] < 0.5 and worst[2] < 6.0  # the relief is 40 m high


def test_levels_of_a_closed_cube_keep_it_closed_with_its_volume() -> None:
    v, t = cube(40, 100.0)  # 19 200 triangles, no border: every vertex may go
    t = t[gm.morton_order(v, t)]
    (p,) = gm.parts_table(v, t)
    made = gm.levels(*gm.part_mesh(v, t, p))
    assert [len(m[1]) for m in made] == [19_200, 2400, 300]
    for lv, lt in made:
        s = gm.mesh_stats(lv, lt)
        assert s["closed"] and s["orientation"] == "outward"
        assert s["volume"] == pytest.approx(1e6, rel=1e-9) and s["area"] == pytest.approx(6e4, rel=1e-9)


def test_small_or_stubborn_meshes_come_back_as_they_are() -> None:
    v, t = grid(5, 5, hills, seed=None)  # 50 triangles
    local = v - np.round(v.mean(axis=0))
    out_v, out_t = gm.simplify(local, t, 6)
    assert out_v is local and out_t is t
    v, t = grid(20, 20, hills, seed=None)
    local = v - np.round(v.mean(axis=0))
    assert gm.simplify(local, t, len(t))[1] is t  # nothing to remove
    ro_v, ro_t = local.copy(), t.copy()
    ro_v.setflags(write=False)
    ro_t.setflags(write=False)
    got_v, got_t = gm.simplify(ro_v, ro_t, len(t) // 8)  # read-only input (a view of a file) is copied
    assert len(got_t) < len(t) and got_v.dtype == np.float64 and got_t.dtype == np.int32


# --- measures and checks ---------------------------------------------------------------------------------


def test_stats_of_a_cube_and_of_an_open_surface() -> None:
    v, t = cube(12, 37.5)
    s = gm.mesh_stats(v, t)
    assert s["closed"] and s["open_edges"] == 0 and s["nonmanifold_edges"] == 0 and s["degenerate"] == 0
    assert s["volume"] == pytest.approx(37.5**3, rel=1e-12) and s["orientation"] == "outward"
    assert s["area"] == pytest.approx(6 * 37.5**2, rel=1e-12)
    assert s["plan_area"] == pytest.approx(2 * 37.5**2, rel=1e-12)  # the top and the bottom
    inward = gm.mesh_stats(v, t[:, ::-1])
    assert inward["orientation"] == "inward" and inward["volume"] == pytest.approx(37.5**3, rel=1e-12)
    opened = gm.mesh_stats(v, t[1:])
    assert not opened["closed"] and opened["open_edges"] == 3 and opened["volume"] is None  # never 0
    twice = gm.mesh_stats(v, np.concatenate([t, t[:1]]))
    assert not twice["closed"] and twice["nonmanifold_edges"] == 3
    pv, pt = grid(40, 20, lambda x, y: 5 + 0.75 * x, step=2.5)
    plane = gm.mesh_stats(pv, pt)
    assert plane["area"] == pytest.approx(100 * 50 * 1.25, rel=1e-12)  # sqrt(1 + 0.75²) = 1.25
    assert plane["plan_area"] == pytest.approx(100 * 50, rel=1e-12) and plane["open_edges"] == 2 * (40 + 20)
    flat = gm.mesh_stats(pv, np.concatenate([pt, [[0, 0, 1]]]).astype(np.int32))
    assert flat["degenerate"] == 1


def test_check_mesh_refuses_what_cannot_be_stored() -> None:
    v, t = grid(4, 4, hills, seed=None)
    cv, ct = gm.check_mesh(v.tolist(), t.astype(np.int64))
    assert cv.dtype == np.float64 and ct.dtype == np.int32 and cv.flags.c_contiguous
    assert cv.tobytes() == v.tobytes() and (ct == t).all()
    bad = v.copy()
    bad[3, 2] = np.nan
    cases = [
        (bad, t, "bad_coordinates"),
        (v, t + 1, "bad_mesh"),
        (v, -t, "bad_mesh"),
        (v, t[:0], "empty_mesh"),
        (v[:, :2], t, "bad_mesh"),
        (v, t.astype(np.float64), "bad_mesh"),
        (v, t.reshape(-1), "bad_mesh"),
    ]
    for xyz, tri, code in cases:
        with pytest.raises(gm.MeshError) as err:
            gm.check_mesh(xyz, tri)
        assert err.value.code == code and err.value.message and err.value.hint
    with pytest.raises(gm.MeshError, match="1 шт"):
        gm.check_mesh(bad, t)


# --- BFA1 ------------------------------------------------------------------------------------------------


def test_part_is_encoded_with_u2_or_u4_indices() -> None:
    v, t = grid(150, 150, hills)  # 45 000 triangles, 22 801 vertices
    t = t[gm.morton_order(v, t)]
    (p,) = gm.parts_table(v, t)
    head, arrays = binary.decode(vm.part_bodies(v, t, p, (0,))[0])
    assert head["kind"] == "mesh" and head["lod"] == 0 and head["index"] == "<u2" and not head["simplified"]
    assert head["triangles"] == head["source_triangles"] == 45_000 and head["vertices"] == 22_801
    assert arrays["idx"].dtype == np.dtype("<u2") and arrays["xyz"].dtype == np.dtype("<f4")
    world = arrays["xyz"].reshape(-1, 3).astype(np.float64) + np.array(head["origin"])
    got = world[arrays["idx"].reshape(-1, 3).astype(np.int64)]
    assert np.abs(got - v[t]).max() <= F32  # the same triangles in the same order
    soup = v[t].reshape(-1, 3)  # every triangle with its own vertices: 135 000 of them
    st = np.arange(len(soup), dtype=np.int32).reshape(-1, 3)
    (sp,) = gm.parts_table(soup, st)
    head, arrays = binary.decode(vm.part_bodies(soup, st, sp, (0,))[0])
    assert head["index"] == "<u4" and arrays["idx"].dtype == np.dtype("<u4") and head["vertices"] == 135_000
    world = arrays["xyz"].reshape(-1, 3).astype(np.float64) + np.array(head["origin"])
    assert np.abs(world[arrays["idx"].reshape(-1, 3).astype(np.int64)] - soup[st]).max() <= F32
    made = vm.part_bodies(v, t, p, vm.SIMPLIFIED)
    assert sorted(made) == [1, 2]
    h1, a1 = binary.decode(made[1])
    h2, _a2 = binary.decode(made[2])
    assert h1["simplified"] and h1["ratio"] == 8 and h2["ratio"] == 64 and h2["triangles"] < h1["triangles"]
    assert h1["triangles"] == len(a1["idx"]) // 3 <= 45_000 / 8 * 1.02 and int(a1["idx"].max()) < h1["vertices"]


# --- API -------------------------------------------------------------------------------------------------


@pytest.fixture()
def stored(pclient):  # type: ignore[no-untyped-def]
    """A project with a relief of 96 000 shuffled triangles (2 parts) and a small closed cube."""
    r = pclient.post(f"{API}/projects", json={"name": "Каркасы"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    v, t = grid(240, 200, hills)
    cv, ct = cube(2, 10.0)  # 48 triangles
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        relief = wf_store.put_wireframe(conn, store, v, t, name="Рельеф", dtm=True)
        box = wf_store.put_wireframe(conn, store, cv, ct, name="Куб", color="#AA3300")
    vm.forget()
    return pclient, project, (v, t), relief, box


def _get(c, url: str, **params):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/viz/wireframes/{url}", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def test_manifest_and_parts_through_the_api(stored) -> None:  # type: ignore[no-untyped-def]
    c, _project, (v, t), relief, box = stored
    r, head, arrays = _get(c, str(relief))
    assert arrays == {} and head["kind"] == "wireframe" and head["wireframe"] == {"id": relief, "name": "Рельеф"}
    assert head["triangles"] == 96_000 and head["points"] == len(v) and head["part_triangles"] == 64_000
    assert head["bbox"] == [*v.min(axis=0).tolist(), *v.max(axis=0).tolist()]
    assert head["lods"] == [{"lod": 0, "ratio": 1}, {"lod": 1, "ratio": 8}, {"lod": 2, "ratio": 64}]
    assert head["dtm"] == {"raster_id": None, "clip": True, "tint": "#ffffff", "opacity": 1.0, "shade": True}
    assert [p["n"] for p in head["parts"]] == [64_000, 32_000] and [p["first"] for p in head["parts"]] == [0, 64_000]
    total_area = 0.0
    seen = 0
    for p in head["parts"]:
        assert len(set(p["etags"])) == 3 and all(float(x).is_integer() for x in p["origin"])
        sizes = []
        for lod in range(3):
            pr, ph, pa = _get(c, f"{relief}/parts/{p['k']}", lod=lod)
            assert pr.headers["etag"] == p["etags"][lod]
            assert ph["kind"] == "mesh" and ph["k"] == p["k"] and ph["lod"] == lod and ph["origin"] == p["origin"]
            assert ph["bbox"] == p["bbox"] and ph["source_triangles"] == p["n"]
            xyz = pa["xyz"].reshape(-1, 3).astype(np.float64)
            idx = pa["idx"].reshape(-1, 3).astype(np.int64)
            assert len(xyz) == ph["vertices"] and len(idx) == ph["triangles"] and int(idx.max()) < len(xyz)
            assert pa["idx"].dtype == np.dtype(ph["index"]) == np.dtype("<u2" if len(xyz) <= 65_536 else "<u4")
            assert np.abs(xyz).max() <= 8000.5
            world = xyz + np.array(ph["origin"])
            box6 = np.array(ph["bbox"])
            assert (world >= box6[:3] - F32).all() and (world <= box6[3:] + F32).all()
            sizes.append(ph["triangles"])
            if lod == 0:
                assert not ph["simplified"]
                total_area += float(areas(world, idx).sum())
                seen += len(idx)
            else:
                assert ph["simplified"]
                # the surface z = 20 sin(x / 17) cos(y / 23): a simplified vertex is one of the surface or near it
                rel = world - UTM
                assert np.abs(rel[:, 2] - hills(rel[:, 0], rel[:, 1])).max() < (0.5 if lod == 1 else 6.0)
        assert sizes[0] == p["n"] and sizes[1] <= p["n"] / 8 * 1.05 and sizes[2] < sizes[1] / 2
    assert seen == 96_000
    assert total_area == pytest.approx(float(areas(v, t).sum()), rel=1e-6)  # float32 of every part
    _r, cube_head, _ = _get(c, str(box))
    assert cube_head["color"] == "#aa3300" and cube_head["dtm"] is None and len(cube_head["parts"]) == 1
    _r, ph, pa = _get(c, f"{box}/parts/0", lod=2)
    assert ph["triangles"] == 48 and not ph["simplified"]  # too small to simplify: the level is the mesh itself


def test_levels_are_cached_in_the_project_and_made_again_when_lost(stored, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    c, project, _mesh, relief, _box = stored
    with dialect.read_tx(project.engine) as conn:
        state = vm.mesh_state(conn, relief)
    folder = vm.cache_dir(project.folder, state.cache_key())
    assert not folder.exists()
    first = c.get(f"{API}/viz/wireframes/{relief}/parts/1", params={"lod": 2}).content
    assert sorted(f.name for f in folder.iterdir()) == ["1-1.bfa", "1-2.bfa"]  # both levels of the part, no temp files
    assert (folder / "1-2.bfa").read_bytes() == first
    c.get(f"{API}/viz/wireframes/{relief}/parts/0", params={"lod": 0})
    assert len(list(folder.iterdir())) == 2  # the full level is read from the blobs, never cached

    def boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise AssertionError("the cached level must be served without pyfqmr")

    monkeypatch.setattr(gm, "simplify", boom)
    assert c.get(f"{API}/viz/wireframes/{relief}/parts/1", params={"lod": 2}).content == first
    assert c.get(f"{API}/viz/wireframes/{relief}/parts/1", params={"lod": 1}).status_code == 200
    monkeypatch.undo()
    (folder / "1-2.bfa").write_bytes(first[: len(first) // 2])  # a damaged file is made again
    assert c.get(f"{API}/viz/wireframes/{relief}/parts/1", params={"lod": 2}).content == first
    vm.drop_cache(project.folder, state.cache_key())  # cache\ may be deleted without loss
    assert not folder.exists()
    assert c.get(f"{API}/viz/wireframes/{relief}/parts/1", params={"lod": 2}).content == first
    # the job that creates a wireframe makes every level at once
    vm.drop_cache(project.folder, state.cache_key())
    with dialect.read_tx(project.engine) as conn:
        v, t = wf_store.read_wireframe(conn, BlobStore(project.folder), relief)
    seen: list[float] = []
    built = vm.build_cache(project.folder, state.cache_key(), v, t, vm.parts_of(project, state), seen.append)
    assert built["parts"] == 2 and built["triangles"][0] == 96_000 and seen == [0.5, 1.0]
    assert built["triangles"][1] <= 96_000 / 8 * 1.05 and built["bytes"] == sum(
        f.stat().st_size for f in folder.iterdir()
    )
    assert sorted(f.name for f in folder.iterdir()) == ["0-1.bfa", "0-2.bfa", "1-1.bfa", "1-2.bfa"]
    assert (folder / "1-2.bfa").read_bytes() == first


def test_etag_of_a_part_follows_the_geometry_only(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, _mesh, relief, _box = stored
    r, head, _ = _get(c, str(relief))
    tag = r.headers["etag"]
    tags = head["parts"][0]["etags"]
    for _ in range(2):
        assert c.get(f"{API}/viz/wireframes/{relief}", headers={"If-None-Match": tag}).status_code == 304
        for lod in range(3):
            url = f"{API}/viz/wireframes/{relief}/parts/0"
            r304 = c.get(url, params={"lod": lod}, headers={"If-None-Match": tags[lod]})
            assert r304.status_code == 304 and r304.content == b"" and r304.headers["etag"] == tags[lod]
    wrong = c.get(f"{API}/viz/wireframes/{relief}/parts/0", params={"lod": 1}, headers={"If-None-Match": tags[2]})
    assert wrong.status_code == 200
    with dialect.write_tx(project.engine) as conn:  # a new name and a DTM setting: the next version
        wf_store.update_wireframe(conn, relief, 1, {"name": "Рельеф 2026"})
        wf_store.set_dtm(conn, relief, 2, {"tint": "#FFEEDD"})
    r2, head2, _ = _get(c, str(relief))
    assert r2.headers["etag"] != tag and head2["version"] == 3 and head2["dtm"]["tint"] == "#ffeedd"
    assert head2["wireframe"]["name"] == "Рельеф 2026"
    assert head2["parts"][0]["etags"] == tags  # the geometry did not change: the window keeps its buffers


def test_errors_are_russian_with_a_hint(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, _mesh, relief, box = stored
    for url, params, status, code in [
        ("999999", {}, 404, "wireframe_not_found"),
        (f"{relief}/parts/2", {}, 404, "part_not_found"),
        (f"{relief}/parts/-1", {}, 404, "part_not_found"),
        (f"{relief}/parts/0", {"lod": 3}, 422, "bad_lod"),
        (f"{relief}/parts/0", {"lod": -1}, 422, "bad_lod"),
    ]:
        r = c.get(f"{API}/viz/wireframes/{url}", params=params)
        assert r.status_code == status and r.json()["code"] == code, r.text
        assert r.json()["message"] and r.json()["hint"]
    with dialect.write_tx(project.engine) as conn:
        wf_store.delete_wireframe(conn, box)
    assert c.get(f"{API}/viz/wireframes/{box}").json()["code"] == "wireframe_not_found"
