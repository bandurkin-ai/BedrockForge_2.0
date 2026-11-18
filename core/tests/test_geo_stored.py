"""The exact queries on a wireframe as the project keeps it (ADR 0013 §4; `wireframes/store.py`): the blobs read
back (vertices float64, triangles int32 along the Z-order curve), the part table of `wireframes.render` and the
measures of `wireframes.stats` go into `geo/section.py`, `geo/pick.py` and `geo/intersect.py` as they are and
give what the arrays before storing give. And the three kernels against one another on the real TRIDB files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import wireframes
from bf_core.drillholes.desurvey import Collars, desurvey
from bf_core.geo import intersect as gi
from bf_core.geo import meshes
from bf_core.geo import pick as gp
from bf_core.geo import section as gs
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube
from tests.test_geo_intersect import holes
from tests.test_import_job import API

pytestmark = pytest.mark.windows


def test_stored_wireframe_section_pick_and_crossings(pclient) -> None:  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects", json={"name": "Точные расчёты"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    v, t = cube(104, 100.0)  # 129 792 shuffled triangles: more than one part of the store
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        wf = ws.put_wireframe(conn, store, v, t, name="Куб")
    with dialect.read_tx(project.engine) as conn:
        xyz, tri = ws.read_wireframe(conn, store, wf)
        row = conn.execute(sa.select(wireframes).where(wireframes.c.object_id == wf)).mappings().one()
    parts, stats = row["render"]["parts"], row["stats"]
    assert len(parts) >= 3 and stats["closed"] and stats["orientation"] == "outward"
    lo = v.min(axis=0)

    # the section: the same closed square, with the part table of the store
    point, normal = lo + [37.25, 0.0, 0.0], [1.0, 0.0, 0.0]
    cut = gs.plane_section(xyz, tri, point, normal, parts=parts)
    assert len(cut) == 1 and cut.closed.all() and cut.lengths()[0] == pytest.approx(400.0, abs=1e-8)
    plain = gs.plane_section(v, t, point, normal)
    assert sorted(map(tuple, cut.xyz.tolist())) == sorted(map(tuple, plain.xyz.tolist()))
    assert 1 <= len(gs.parts_cut(parts, point, normal)) <= len(parts)

    # the pick: the same triangle (its corners), found through the part table
    origin, direction = lo + [-40.0, 33.3, 61.7], [1.0, 0.05, -0.02]
    hit = gp.ray_pick(xyz, tri, origin, direction, parts=parts)
    ref = gp.ray_pick(v, t, origin, direction)
    assert hit is not None and ref is not None
    assert (hit.distance, hit.point, hit.dip, hit.dip_direction) == (
        ref.distance,
        ref.point,
        ref.dip,
        ref.dip_direction,
    )
    assert sorted(hit.vertices) == sorted(ref.vertices) and hit.indices == tuple(int(i) for i in tri[hit.triangle])
    assert gp.ray_pick(xyz, tri, origin, direction, parts=gp.fine_parts(xyz, tri, parts, size=32)) == hit

    # the crossings: a solid by its stored measures, the triangles in the stored order already
    traj = holes([(lo[0] + 50.3, lo[1] + 49.9, lo[2] + 130.0, 200.0), (lo[0] + 13.1, lo[1] + 77.2, lo[2] + 60.0, 90.0)])
    target = gi.MeshTarget.from_stats(xyz, tri, stats, ordered=True)
    assert target.order is None and target.solid == "outward"
    hits = gi.hole_crossings(traj, [target])
    assert hits.hole.tolist() == [0, 0, 1] and hits.event.tolist() == [gi.ENTER, gi.LEAVE, gi.LEAVE]
    assert hits.depth == pytest.approx([30.0, 130.0, 60.0], abs=1e-9)
    spans = gi.inside_intervals(traj, hits)
    assert spans.from_m == pytest.approx([30.0, 0.0], abs=1e-9) and spans.to_m == pytest.approx([130.0, 60.0], abs=1e-9)
    again = gi.hole_crossings(traj, [gi.MeshTarget(v, t, solid="outward")])  # the arrays before storing
    assert again.depth.tolist() == hits.depth.tolist() and again.event.tolist() == hits.event.tolist()
    assert np.array_equal(np.sort(t[again.triangle], axis=1), np.sort(tri[hits.triangle], axis=1))
    assert UTM[0] < hits.xyz[:, 0].min()  # project coordinates throughout


REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"


@pytest.mark.parametrize(("name", "solids"), [("ORE SEP 2025.tridb", 66), ("Rock Model.tridb", 39)])
def test_real_wireframes_section_pick_and_crossings_agree(name: str, solids: int) -> None:
    """The wireframes of the owner's TRIDB files (121 in all, 132 584 triangles; ore bodies and rock units on
    UTM coordinates). Nothing here is compared with a stored answer: the three kernels are checked against one
    another and against what a closed solid must give.

      * every line of a section of a closed solid is closed, its points are on the plane (2e-9 m);
      * a hole through a closed solid crosses it an even number of times, in and out in turn;
      * the lengths inside, times the area a hole stands for, add up to the volume of `mesh_stats`
        (a 40 × 40 grid of holes: within 1 % as the median over the solids of a file);
      * the pick from above is the first crossing of the hole below it (1e-9 m)."""
    from bf_core.formats.tridb import read_tridb

    grid_n = 40
    closed_solids = lines = picks = 0
    volume_errors = []
    for mesh in read_tridb(REAL / name):
        v, t = mesh.xyz, mesh.tri
        stats = meshes.mesh_stats(v, t)
        closed = bool(stats["closed"])
        closed_solids += closed
        st = np.ascontiguousarray(t[meshes.morton_order(v, t)])
        parts = [[p.first, p.count, *p.bbox] for p in meshes.parts_table(v, st, max_triangles=256)]
        lo, hi = v.min(axis=0), v.max(axis=0)
        for normal in ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.6, 0.64, 0.48]):
            for share in (0.21, 0.5, 0.83):
                point = lo + share * (hi - lo)
                cut = gs.plane_section(v, st, point, normal, parts=parts)
                lines += len(cut)
                if len(cut):
                    assert np.abs((cut.xyz - point) @ np.array(normal)).max() <= 2e-9, mesh.name
                assert cut.closed.all() or not closed, mesh.name
        cells = (np.arange(grid_n) + 0.5) / grid_n
        x, y = np.meshgrid(lo[0] + cells * (hi[0] - lo[0]), lo[1] + cells * (hi[1] - lo[1]))
        x, y = x.ravel(), y.ravel()
        top = hi[2] + 10.0
        traj = desurvey(Collars(np.arange(len(x)), x, y, np.full(len(x), top), np.full(len(x), top - lo[2] + 10.0)))
        hits = gi.hole_crossings(traj, [gi.MeshTarget.from_stats(v, t, stats)])
        same_hole = hits.hole[1:] == hits.hole[:-1]
        if closed:
            assert (np.bincount(hits.hole, minlength=len(x)) % 2 == 0).all(), mesh.name
            assert not (same_hole & (hits.event[1:] == hits.event[:-1])).any(), mesh.name
            spans = gi.inside_intervals(traj, hits)
            assert not spans.from_collar.any() and not spans.to_end.any()
            estimate = float((spans.to_m - spans.from_m).sum()) * (hi[0] - lo[0]) * (hi[1] - lo[1]) / grid_n**2
            volume_errors.append(abs(estimate - stats["volume"]) / stats["volume"])
        else:
            assert (hits.event == gi.CROSS).all()
        first = np.flatnonzero(np.r_[True, ~same_hole]) if len(hits) else np.zeros(0, dtype=int)
        for k in first[:: max(1, len(first) // 60)]:
            h = int(hits.hole[k])
            hit = gp.ray_pick(v, st, [x[h], y[h], top], [0.0, 0.0, -1.0], parts=parts)
            assert hit is not None and hit.distance == pytest.approx(hits.depth[k], abs=1e-9), mesh.name
            picks += 1
    assert closed_solids == solids and lines > 400 and picks > 1000
    # a thin body between the holes of a coarse grid is measured worse (18 % at worst here; 0.3 % at 640 × 640)
    assert np.median(volume_errors) < 0.01 and np.quantile(volume_errors, 0.9) < 0.1
