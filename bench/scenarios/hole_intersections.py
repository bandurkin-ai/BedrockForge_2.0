"""Where the holes of a set cross wireframes (ADR 0013 «Замеры»: «Пересечения стволов — 5 000 стволов × 1 млн
треугольников: по первому замеру, ориентир ≤ 5 с»; full — 50 000 стволов; ТЗ 7.15, ADR 0007 §5).

Holes drilled from a pit surface (`tools/gen/wireframes.py`: 150–400 m long, 20 survey stations each, dips of
45–90° that wander; every fifth collar is a vertex of the surface — as a DTM built through the collars has it —
and its hole goes into the ground there; the others stand above the surface) against two wireframes as the
store keeps them (triangles in spatial order): the surface with 90 % of the triangles and a closed ore body
under it. In a fresh process, as the marks of
`GET /viz/sets/{id}/intersections` are found the first time:
  desurvey_s   the trajectories (minimum curvature);
  tree_s       `geo/intersect.py::MeshTarget` of both wireframes: the trees of boxes;
  cross_s      `hole_crossings`: the crossings with their depths and points;
  intervals_s  `inside_intervals`: the stretches inside the ore body.
What is checked against the surface the wireframe was made from (`Surface.height`, float64):
  plane_error_m  the largest distance of the point of a crossing from the plane of the triangle it is said to
                 cross: the depth is refined on the curved path itself (to 1e-8 m along the hole);
  max_error_m    … and from the surface, by height (ТЗ 7.15 asks for 1 mm): the path is tested by straight
                 pieces that stay within 1 mm of it, so a crossing next to an edge may be told by the plane
                 of the triangle across the edge — a fraction of a millimetre off the surface;
  grazes         pairs «out and in again» of one hole within 2 mm of depth: a hole that runs a few millimetres
                 under a crease of the surface — its straight pieces come out where the path itself does not
                 (the limit of the kernel, `geo/intersect.py`: «what the 1 mm of the pieces can change»). They
                 are counted and left out of the two errors above; `graze_error_m` — how far their points are
                 from the surface;
  parity_errors  holes whose number of crossings does not fit their ends: a hole from above the surface to
                 below it crosses it an odd number of times, a collar on the surface counting as above it —
                 nothing slips between two triangles;
  collar_errors  holes whose collar is not told right: a hole collared on a vertex of the surface crosses it
                 at depth 0 exactly, once (the six triangles around the vertex are one crossing), a hole
                 collared above the surface — never at depth 0;
  the crossings of the ore body lie on it (not outside its ellipsoid) and the holes enter it as often as they
  leave it or end inside.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOLE_KEYS = ("x", "y", "z", "td", "s_hole", "s_depth", "s_az", "s_dip")
GRAZE_M = 2e-3


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np
    from tools.gen.wireframes import body_of, drillholes, ore_body, pit_surface

    from bf_core.geo import meshes

    n = int(params["triangles"])
    surface = pit_surface(round(0.9 * n))
    surface.save(work / "surface.npz")
    for name, (v, t) in (("surface", surface.mesh()), ("body", ore_body(round(0.1 * n), **body_of(surface, 0)))):
        v, t = meshes.check_mesh(v, t)
        np.save(work / f"{name}_xyz.npy", v)
        np.save(work / f"{name}_tri.npy", np.ascontiguousarray(t[meshes.morton_order(v, t)]))  # as the store
    np.savez(work / "holes.npz", **drillholes(surface, int(params["holes"])))
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np

    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
    from bf_core.geo import intersect

    v = np.array([[-5.0, -5.0, 70.0], [5.0, -5.0, 70.0], [5.0, 5.0, 70.0], [-5.0, 5.0, 70.0]])
    t = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    box_v = np.array([[x, y, z] for z in (20.0, 40.0) for y in (-3.0, 3.0) for x in (-3.0, 3.0)])
    box_t = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7],
                      [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], dtype=np.int32)  # fmt: skip
    hole = np.zeros(3, dtype=np.int64)
    collars = Collars(hole[:1], np.array([0.5]), np.array([1.0]), np.array([100.0]), np.array([90.0]))
    surveys = Surveys(hole, np.array([0.0, 40.0, 80.0]), np.array([10.0, 20.0, 30.0]), np.array([-88.0, -85.0, -80.0]))
    traj = desurvey(collars, surveys)
    targets = [intersect.MeshTarget(v, t, ordered=True), intersect.MeshTarget(box_v, box_t, solid="outward")]
    intersect.inside_intervals(traj, intersect.hole_crossings(traj, targets))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    from tools.gen.wireframes import Surface, body_distance, body_of

    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
    from bf_core.geo import intersect

    with np.load(work / "holes.npz") as f:
        d = {k: f[k] for k in HOLE_KEYS}
    n = len(d["x"])
    t0 = time.perf_counter()
    traj = desurvey(
        Collars(np.arange(n, dtype=np.int64), d["x"], d["y"], d["z"], d["td"]),
        Surveys(d["s_hole"], d["s_depth"], d["s_az"], d["s_dip"]),
    )
    t1 = time.perf_counter()
    arrays = {name: np.load(work / f"{name}.npy") for name in ("surface_xyz", "surface_tri", "body_xyz", "body_tri")}
    t2 = time.perf_counter()
    targets = [
        intersect.MeshTarget(arrays["surface_xyz"], arrays["surface_tri"], ordered=True),
        intersect.MeshTarget(arrays["body_xyz"], arrays["body_tri"], solid="outward", ordered=True),
    ]
    t3 = time.perf_counter()
    hits = intersect.hole_crossings(traj, targets)
    t4 = time.perf_counter()
    spans = intersect.inside_intervals(traj, hits)
    t5 = time.perf_counter()

    # the crossings of the surface: on it, and as many as the ends of the hole ask for
    surface = Surface.load(work / "surface.npz")
    on_surface = hits.mesh == 0
    p = hits.xyz[on_surface]
    off_surface = np.abs(surface.height(p[:, 0], p[:, 1]) - p[:, 2])
    a, b, c = (arrays["surface_xyz"][arrays["surface_tri"][hits.triangle[on_surface], k]] for k in range(3))
    normal = np.cross(b - a, c - a)
    off_plane = np.abs(np.einsum("ij,ij->i", p - a, normal) / np.linalg.norm(normal, axis=1))
    hole, depth, side = hits.hole[on_surface], hits.depth[on_surface], hits.side[on_surface]
    close = (hole[1:] == hole[:-1]) & (depth[1:] - depth[:-1] <= GRAZE_M) & (side[1:] == -side[:-1]) & (side[1:] != 0)
    graze = np.zeros(len(p), dtype=bool)
    for i in np.flatnonzero(close).tolist():  # (a crossing is in one pair)
        if not graze[i]:
            graze[i] = graze[i + 1] = True
    error, plane_error = float(off_surface[~graze].max()), float(off_plane[~graze].max())
    graze_error = float(off_surface[graze].max()) if graze.any() else 0.0
    crossed = np.bincount(hits.hole[on_surface], minlength=n)
    lengths = traj.lengths()
    built = np.flatnonzero(np.isfinite(lengths))
    end = traj.positions(built, lengths[built])
    below = surface.height(end[:, 0], end[:, 1]) - end[:, 2]  # NaN — the hole ends outside the surface
    known = ~np.isnan(below)
    parity_errors = int(np.count_nonzero((crossed[built][known] % 2 == 1) != (below[known] > 0.0)))
    on_node = np.arange(n) % 5 == 0  # these collars are vertices of the surface
    at_zero = np.bincount(hits.hole[on_surface & (hits.depth == 0.0)], minlength=n)
    collar_errors = int(np.count_nonzero(at_zero != on_node))
    at_collar = int(at_zero.sum())

    # the crossings of the ore body: on its facets — inside its ellipsoid, never outside
    q = hits.xyz[hits.mesh == 1]
    shell = body_distance(q, **body_of(surface, 0)) if len(q) else np.ones(1)
    enters = int(np.count_nonzero(hits.event == intersect.ENTER))
    leaves = int(np.count_nonzero(hits.event == intersect.LEAVE))
    touches = int(np.count_nonzero((hits.mesh == 1) & (hits.event == intersect.CROSS)))

    assert len(built) == n and error <= 1e-3 and plane_error <= 1e-7, (len(built), error, plane_error)
    assert parity_errors == 0 and collar_errors == 0 and at_collar > 0, (parity_errors, collar_errors, at_collar)
    assert float(shell.max()) <= 1.0 + 1e-9 and float(shell.min()) > 0.98, (float(shell.min()), float(shell.max()))
    assert not spans.from_collar.any() and enters - leaves == int(spans.to_end.sum()), (enters, leaves)
    return {
        "holes": n,
        "stations": int(len(traj.md)),
        "triangles": int(sum(t.triangles for t in targets)),
        "desurvey_s": round(t1 - t0, 3),
        "tree_s": round(t3 - t2, 3),
        "cross_s": round(t4 - t3, 3),
        "intervals_s": round(t5 - t4, 4),
        "crossings": int(len(hits)),
        "holes_through_surface": int(np.count_nonzero(crossed)),
        "at_collar": at_collar,
        "ends_outside": int(np.count_nonzero(~known)),
        "enters": enters,
        "leaves": leaves,
        "touches": touches,
        "intervals": int(len(spans)),
        "ends_inside": int(spans.to_end.sum()),
        "plane_error_m": plane_error,
        "max_error_m": error,
        "grazes": int(graze.sum()) // 2,
        "graze_error_m": graze_error,
        "parity_errors": parity_errors,
        "collar_errors": collar_errors,
    }
