"""Clipping of a closed solid by a plane, a polygon and a surface; lines where two surfaces cross (ТЗ 7.23
«Обрезка», «Линии пересечения»; ADR 0015 «Замеры»: 100 thousand / 1 million triangles, by the first measurement —
ориентир ≤ 8 s on 1 million).

A UV sphere of `triangles` triangles at UTM coordinates (`tools/gen/mesh.py`) is cut, both parts kept, as the
operations `clip_plane`, `clip_polygon`, `clip_surface` call `geo/clip.py`:
  plane_s      by an inclined plane off the centre;
  polygon_s    by the vertical prism through a star of 14 points in plan (not convex);
  surface_s    by an undulating open surface of a tenth of the triangles over the whole body (a DTM).
  crossing_s   `geo/crossing.intersection_lines` of two undulating surfaces of `triangles` triangles each,
               on grids of other steps and shifted against each other (the lines cross both triangulations).
What is checked:
  volume_error    the largest |V₁ + V₂ − V| / V of the three cuts (the parts add up to the solid);
  crossing_error_m  the largest distance in height of a point of the lines from either surface — the plane of
                  its triangle over the point (`tools/gen/wireframes.Surface.height`, float64).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

RADIUS = 100.0
CENTRE = (512_345.678, 6_789_012.345, 250.0)


def _surface(triangles: int, side: float, origin: tuple[float, float, float], shape: Any) -> Any:
    import numpy as np
    from tools.gen.wireframes import Surface

    n = max(4, round((triangles / 2) ** 0.5))
    step = side / n
    x, y = np.meshgrid(np.arange(n + 1) * step, np.arange(n + 1) * step)
    return Surface(n, n, step, origin, origin[2] + shape(x, y), side, side)


def _star() -> Any:
    import numpy as np

    a = np.linspace(0.0, 2.0 * np.pi, 14, endpoint=False) + 0.1
    r = np.where(np.arange(14) % 2 == 0, 85.0, 45.0)
    return np.column_stack([CENTRE[0] + r * np.cos(a), CENTRE[1] + r * np.sin(a)])


def _inputs(triangles: int) -> dict[str, Any]:
    import numpy as np
    from tools.gen.mesh import uv_sphere

    v, t = uv_sphere(triangles, RADIUS, CENTRE)
    cx, cy, cz = CENTRE
    dtm = _surface(max(2_000, triangles // 10), 320.0, (cx - 160.0, cy - 160.0, cz),
                   lambda x, y: 20.0 * np.sin(x / 40.0) * np.cos(y / 55.0) + 7.0)  # fmt: skip
    a = _surface(triangles, 1000.0, (cx - 500.0, cy - 500.0, cz), lambda x, y: 20 * np.sin(x / 60) * np.cos(y / 45))
    b = _surface(round(triangles * 0.93), 990.0, (cx - 497.0, cy - 502.0, cz),
                 lambda x, y: 15 * np.cos(x / 50) + 0.01 * y - 5)  # fmt: skip
    return {"solid": (v, t.astype(np.int32)), "dtm": dtm, "a": a, "b": b}


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    data = _inputs(int(params["triangles"]))
    np.save(work / "v.npy", data["solid"][0])
    np.save(work / "t.npy", data["solid"][1])
    for name in ("dtm", "a", "b"):
        data[name].save(work / f"{name}.npz")
    load()


def load() -> None:
    from bf_core.geo.clip import clip_by_plane, clip_by_polygon, clip_by_surface
    from bf_core.geo.crossing import intersection_lines

    small = _inputs(2_000)
    solid, dtm = small["solid"], small["dtm"].mesh()
    clip_by_plane(solid, CENTRE, (0.3, 0.5, 0.81))
    clip_by_polygon(solid, _star(), keep="both")
    clip_by_surface(solid, dtm)
    intersection_lines(small["a"].mesh(), small["b"].mesh())


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    from tools.gen.wireframes import Surface

    from bf_core.geo.clip import clip_by_plane, clip_by_polygon, clip_by_surface
    from bf_core.geo.crossing import intersection_lines

    solid = (np.load(work / "v.npy"), np.load(work / "t.npy"))
    dtm, a, b = (Surface.load(work / f"{name}.npz") for name in ("dtm", "a", "b"))
    dtm_mesh, a_mesh, b_mesh = dtm.mesh(), a.mesh(), b.mesh()
    out: dict[str, Any] = {"triangles": int(len(solid[1])), "surface_triangles": int(len(dtm_mesh[1]))}
    reports = []
    for name, cut in (
        ("plane_s", lambda: clip_by_plane(solid, np.add(CENTRE, (13.7, -8.1, 5.3)), (0.3, 0.5, 0.81))),
        ("polygon_s", lambda: clip_by_polygon(solid, _star(), keep="both")),
        ("surface_s", lambda: clip_by_surface(solid, dtm_mesh)),
    ):
        t0 = time.perf_counter()
        result = cut()
        out[name] = round(time.perf_counter() - t0, 4)
        reports.append(result["report"])
    out["volume_error"] = max(float(r["sum_error"]) / float(r["volume"]) for r in reports)
    t0 = time.perf_counter()
    lines = intersection_lines(a_mesh, b_mesh)
    out["crossing_s"] = round(time.perf_counter() - t0, 4)
    p = np.vstack([line["points"] for line in lines])
    out["crossing_lines"] = len(lines)
    out["crossing_points"] = int(len(p))
    out["crossing_error_m"] = float(
        max(np.nanmax(np.abs(s.height(p[:, 0], p[:, 1]) - p[:, 2])) for s in (a, b))
    )
    return out
