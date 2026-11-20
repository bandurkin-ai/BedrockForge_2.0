"""What a change of the geometry of a series does to its contours (ADR 0016 decision 4, ADR 0017 §7; Э11 11.17).

A series with contours may get new P1, A, φ, s0, Δ or windows: it is allowed after a warning — how many contours of
its contour layer leave the windows of the sections. The attribute `SECTION` of a contour is not recomputed, so a
contour that stays in a window but of another section is counted too (`moved`). The rule of membership is that
of the contours (ТЗ 10.1, `SeriesGeometry.line_sections`): a closed line belongs to section k when all its vertices
are in the window of k.

`series_impact` — the contours of the series as they are (the cached `contours.cached_contours`, the same the 3D
window draws) checked against the geometry of the new parameters (`schema.merge` + `schema.check` — exactly what a
PATCH would store); only the contours in a window now are checked. 2 000 contours — well under 0,2 s.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .contours import SeriesContours, _ranges

EXAMPLES = 10  # contours named in the answer


def impact_of(sc: SeriesContours, new_geom: Any, names: list[str]) -> dict[str, Any]:
    """{total, outside, moved, examples}: of the `total` contours in a window of the series now, `outside` are in
    no window of `new_geom`, `moved` — in the window of another section; `examples` names the first ones outside
    («Разрез 5050N: линия 12 слоя «Контуры»»)."""
    inside = np.flatnonzero(sc.section >= 0)
    total = int(len(inside))
    if not total:
        return {"total": 0, "outside": 0, "moved": 0, "examples": []}
    counts = np.diff(sc.offsets)[inside]
    xyz = sc.xyz[_ranges(sc.offsets[:-1][inside], counts)]
    offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    new = np.asarray(new_geom.line_sections(xyz, offsets), dtype=np.int64)
    old = sc.section[inside]
    out = np.flatnonzero(new < 0)
    moved = int(((new >= 0) & (new != old)).sum())

    def name(k: int) -> str:
        return names[k] if 0 <= k < len(names) else str(k + 1)

    examples = [f"Разрез {name(int(old[j]))}: {sc.label(int(inside[j]))}" for j in out[:EXAMPLES]]
    return {"total": total, "outside": int(len(out)), "moved": moved, "examples": examples}


def series_impact(conn: Any, bs: Any, uid: str, row: Any, params: Any) -> dict[str, Any]:
    """The impact of `params` (the fields a PATCH would send) on the contours of the series `row`.

    {layer_id, layer, total, outside, moved, examples}; a series without a contour layer (or whose layer was deleted
    since) has no contours: zeros, no question. SeriesError for parameters a PATCH would refuse (422 with the field).
    """
    from bf_core.api.errors import ApiError

    from . import contours, store
    from .geometry import series_geometry
    from .names import section_names
    from .schema import merge, params_of

    old_meta = row.meta if isinstance(row.meta, dict) else {}
    new_meta = store._check(conn, merge(old_meta, params))  # the same check as the PATCH: errors before the impact
    layer_id = params_of(old_meta).get("contour_layer")
    none: dict[str, Any] = {"layer_id": None, "layer": None, "total": 0, "outside": 0, "moved": 0, "examples": []}
    if not isinstance(layer_id, int) or isinstance(layer_id, bool):
        return none
    try:
        sc = contours.cached_contours(conn, bs, uid, row, [layer_id])
    except ApiError as e:
        if e.status in (404, 422):  # the layer was deleted or became a cloud: the series has no contours
            return none
        raise
    names = section_names(old_meta)
    out = impact_of(sc, series_geometry(new_meta), names)
    layer = sc.layers[0].name if sc.layers else None
    return {"layer_id": layer_id, "layer": layer, **out}
