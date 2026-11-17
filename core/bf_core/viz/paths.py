"""Paths of one part for 3D: `GET /viz/sets/{id}/paths/{k}?method=&tol=` (ADR 0007 §1).

BFA1 `paths`: `offsets u4` (n + 1) — the vertices of slot j are offsets[j]:offsets[j + 1]; `xyz f4×3` from
the origin of the part; `md f8` — the depth of every vertex. The vertices are the stations plus the arc points
of `arcs.densify`: the polyline is within `tol` of the trajectory (data for display only; calculations use
float64 in the core).
"""

from __future__ import annotations

from typing import Any

from bf_core.api.errors import ApiError

from . import binary
from .holes import layout
from .keys import FORMAT, SetState


def part_not_found(k: int) -> ApiError:
    return ApiError(
        404, "part_not_found", f"Части {k} в наборе нет.", "Набор изменился: запросите список скважин заново."
    )


def part_paths(project: Any, state: SetState, method: str, tol: float, k: int) -> bytes:
    from .arcs import densify

    lay = layout(project, state.set_id, method)
    if not 0 <= k < len(lay.parts):
        raise part_not_found(k)
    p = lay.parts[k]
    try:
        offsets, xyz, md = densify(lay.st.traj, lay.holes_of(k), tol, p.origin)
    except ValueError as e:  # too many points for this tolerance
        raise ApiError(
            422, "too_many_points", "Слишком подробные траектории для показа.", "Увеличьте допуск построения стволов."
        ) from e
    header = {
        "kind": "paths",
        "format": FORMAT,
        "set": state.set_id,
        "version": state.version,
        "method": method,
        "tol": tol,
        "k": k,
        "n": p.n,
        "points": int(len(md)),
        "origin": list(p.origin),
        "bbox": list(p.bbox),
    }
    return binary.encode(header, [("offsets", offsets, "<u4"), ("xyz", xyz, "<f4"), ("md", md, "<f8")])
