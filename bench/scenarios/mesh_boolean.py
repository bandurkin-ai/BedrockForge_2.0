"""Boolean operations of two closed solids (ТЗ 5.3: 2 × 1 million triangles ≤ 8 s and ≤ 2 GB; ADR 0015 «Замеры»).

Two UV spheres of `triangles` triangles each at UTM coordinates (`tools/gen/mesh.py`), the second shifted by
0.6 of the radius and turned, so that the two triangulations cross everywhere at random angles. Both
operations of the window go through `geo/solids.boolean` as the operation `boolean` calls it — the check of
both solids, the manifold3d boolean, the result back in project coordinates:
  difference_s     A − B;
  intersection_s   A ∩ B.
What is checked (manifold3d resolves the crossings exactly, so the parts add up to the whole):
  volume_error     |V(A − B) + V(A ∩ B) − V(A)| / V(A);
  open_edges       open and non-manifold edges of both results (0: closed solids).
Peak memory is that of the whole process (both operations, the inputs and both results).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

RADIUS = 100.0
CENTRE = (512_345.678, 6_789_012.345, 250.0)


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np
    from tools.gen.mesh import uv_sphere

    n = int(params["triangles"])
    va, ta = uv_sphere(n, RADIUS, CENTRE)
    vb, tb = uv_sphere(n + n // 7, RADIUS * 0.9, CENTRE)  # another number of rings and segments
    turn = np.radians(23.0)
    rot = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(turn), -np.sin(turn)], [0.0, np.sin(turn), np.cos(turn)]])
    vb = (vb - CENTRE) @ rot.T + CENTRE + np.array([0.6, 0.25, 0.1]) * RADIUS
    for name, arr in (("va", va), ("ta", ta.astype(np.int32)), ("vb", vb), ("tb", tb.astype(np.int32))):
        np.save(work / f"{name}.npy", arr)
    load()


def load() -> None:
    import numpy as np

    from bf_core.geo.solids import boolean

    cube_v = np.array([[x, y, z] for z in (0.0, 1.0) for y in (0.0, 1.0) for x in (0.0, 1.0)]) + CENTRE
    cube_t = np.array(
        [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
         [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]], dtype=np.int32,
    )  # fmt: skip
    boolean((cube_v, cube_t), (cube_v + 0.5, cube_t), "difference")


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.geo.meshes import edge_summary
    from bf_core.geo.solids import boolean, signed_volume

    a = (np.load(work / "va.npy"), np.load(work / "ta.npy"))
    b = (np.load(work / "vb.npy"), np.load(work / "tb.npy"))
    t0 = time.perf_counter()
    dv, dt, dr = boolean(a, b, "difference", names=["Тело A", "Тело B"])
    t1 = time.perf_counter()
    iv, it, ir = boolean(a, b, "intersection", names=["Тело A", "Тело B"])
    t2 = time.perf_counter()
    whole = abs(signed_volume(a[0] - np.asarray(CENTRE), a[1]))  # relative to the centre: no cancellation
    error = abs(dr["volume"] + ir["volume"] - whole) / whole
    bad = sum(sum(edge_summary(t, len(v))) for v, t in ((dv, dt), (iv, it)))
    return {
        "triangles_a": int(len(a[1])),
        "triangles_b": int(len(b[1])),
        "difference_s": round(t1 - t0, 4),
        "intersection_s": round(t2 - t1, 4),
        "difference_triangles": int(len(dt)),
        "intersection_triangles": int(len(it)),
        "volume_error": float(error),
        "open_edges": int(bad),
    }
