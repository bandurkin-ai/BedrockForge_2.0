"""Automatic links of the contours of a series (ТЗ 8.1.4; ADR 0016 §8, решение 5): which contours of neighbouring
sections k and k + 1 are one body.

A pair is linked when the projections of the two contours on the plane of the sections (u, w) overlap — the closed
polygons have a common point: one inside the other, or their outlines cross or touch (`link_kernels.overlap`,
exact signs, float64 from the centre of the pair) — or, when they do not, the area centroids in (u, w) are at most
Δ apart (Δ·(1 + 1e−9): a shift of exactly Δ is linked whatever the rounding). The (u, w) frame is common to every
section of a series (u = (p − P1)·ŝ, w = (p − P1)·ŵ), so a pair needs no transformation. One contour may link to
several (1 → N, N → 1: a branching); a group N : M on one pair of sections is reported as a conflict, the stitching
refuses it («разделите связи»).

    centres(uw, offsets) → (cu, cw, area, box)         area centroids, signed areas, [umin, wmin, umax, wmax]
    auto_links(uw, offsets, section, step, use, tick)  → (pairs L×2, how L) — i on k, j on k + 1
    conflicts(pairs, section)                           → the N : M groups [{sections, contours}]
    link_table(geom, sc, names, tick)                   the answer of `POST /section-series/{id}/links`

Contours with a vertex that is not finite in u or w (an empty height on a vertical series) are left out of the
links (`use`). Self-intersecting contours are not checked here: the stitching reports what it cannot build.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .contours import SeriesContours
    from .geometry import SeriesGeometry
    from .polyline import PolylineSection

Tick = Callable[[float | None, str | None], None]
LIMIT_EPS = 1e-9  # relative: a shift of the centres of exactly Δ is linked
OVERLAP, CENTRES = 1, 2
HOW = {OVERLAP: "overlap", CENTRES: "centres"}
_MESSAGE = "Связи контуров"


def _kernels() -> Any:
    from . import link_kernels

    return link_kernels


def centres(uw: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Area centroids (cu, cw), signed areas and boxes [umin, wmin, umax, wmax] of the closed polygons
    uw[offsets[i]:offsets[i+1]] (each non-empty); a polygon of zero area — the mean of its vertices."""
    m = len(offsets) - 1
    starts, counts = offsets[:-1], np.diff(offsets)
    owner = np.repeat(np.arange(m), counts)
    first = uw[starts][owner]
    q = uw - first  # from the first vertex of every polygon
    nxt = np.arange(len(uw)) + 1
    nxt[offsets[1:] - 1] = starts
    x0, y0, x1, y1 = q[:, 0], q[:, 1], q[nxt, 0], q[nxt, 1]
    cross = x0 * y1 - x1 * y0
    area2 = np.bincount(owner, cross, m)
    sx = np.bincount(owner, (x0 + x1) * cross, m)
    sy = np.bincount(owner, (y0 + y1) * cross, m)
    mean = np.column_stack([np.bincount(owner, q[:, 0], m), np.bincount(owner, q[:, 1], m)]) / counts[:, None]
    lo = np.column_stack([np.minimum.reduceat(uw[:, 0], starts), np.minimum.reduceat(uw[:, 1], starts)])
    hi = np.column_stack([np.maximum.reduceat(uw[:, 0], starts), np.maximum.reduceat(uw[:, 1], starts)])
    size = np.max(hi - lo, axis=1)
    flat = np.abs(area2) <= 1e-12 * np.maximum(size * size, 1e-300)
    safe = np.where(flat, 1.0, 3.0 * area2)
    cu = np.where(flat, mean[:, 0], sx / safe) + uw[starts, 0]
    cw = np.where(flat, mean[:, 1], sy / safe) + uw[starts, 1]
    box = np.ascontiguousarray(np.column_stack([lo, hi]))
    return cu, cw, 0.5 * area2, box


def auto_links(
    uw: np.ndarray,
    offsets: np.ndarray,
    section: np.ndarray,
    step: float,
    use: np.ndarray | None = None,
    tick: Tick | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The links of contours of neighbouring sections (see the module docstring): (pairs L×2 int64 — the contour
    on k, the contour on k + 1 — ordered by k and then by contour; how L int8 — 1 overlap, 2 centres)."""
    kernels = _kernels()
    uw = np.ascontiguousarray(uw, dtype=np.float64)
    offsets = np.ascontiguousarray(offsets, dtype=np.int64)
    section = np.asarray(section, dtype=np.int64)
    ok = section >= 0 if use is None else (section >= 0) & np.asarray(use, dtype=bool)
    if not ok.any():
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    cu, cw, _area, box = centres(uw, offsets)
    picked = np.flatnonzero(ok)
    order = picked[np.argsort(section[picked], kind="stable")]
    ks = section[order]
    present = np.unique(ks)
    limit = float(step) * (1.0 + LIMIT_EPS)
    out: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    pairs = [int(k) for k in present if k + 1 in set(present.tolist())]
    for done, k in enumerate(pairs):
        lower = order[np.searchsorted(ks, k) : np.searchsorted(ks, k, side="right")]
        upper = order[np.searchsorted(ks, k + 1) : np.searchsorted(ks, k + 1, side="right")]
        size = len(lower) * len(upper)
        a, b, how = np.empty(size, np.int64), np.empty(size, np.int64), np.empty(size, np.int8)
        n = kernels.section_pair(uw, offsets, lower, upper, box, cu, cw, limit, a, b, how)
        out.append((a[:n], b[:n], how[:n]))
        if tick is not None:
            tick((done + 1) / len(pairs), _MESSAGE)
    if not out:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
    a = np.concatenate([o[0] for o in out])
    b = np.concatenate([o[1] for o in out])
    how = np.concatenate([o[2] for o in out])
    return np.column_stack([a, b]), how


def conflicts(pairs: np.ndarray, section: np.ndarray) -> list[dict[str, Any]]:
    """Groups N : M (N, M ≥ 2) of the links on one pair of sections: [{sections: [k, k + 1], contours: [...]}]."""
    from bf_core.geo.stitch_links import bodies

    out: list[dict[str, Any]] = []
    if not len(pairs):
        return out
    ks = section[pairs[:, 0]]
    for k in np.unique(ks).tolist():
        mine = pairs[ks == k]
        used = np.unique(mine)
        local = np.searchsorted(used, mine)
        for group in bodies(len(used), local.tolist()):
            members = used[group]
            low = int((section[members] == k).sum())
            if low > 1 and len(members) - low > 1:
                out.append({"sections": [int(k), int(k) + 1], "contours": [int(c) for c in members]})
    return out


def projected(geom: SeriesGeometry | PolylineSection, xyz: np.ndarray) -> np.ndarray:
    """n×2 (u, w) of the vertices: the frame of the series (a polyline — its development)."""
    return np.ascontiguousarray(geom.local(xyz)[:, :2])


def link_table(
    geom: SeriesGeometry | PolylineSection, sc: SeriesContours, names: list[str], tick: Tick | None = None
) -> dict[str, Any]:
    """The contours of the series by sections and their automatic links (`POST /section-series/{id}/links`)."""
    from bf_core.geo.stitch_links import bodies

    from .contours import OFF_PLANE
    from .geometry import SeriesGeometry

    m = sc.count
    uw = projected(geom, sc.xyz) if m else np.zeros((0, 2))
    finite: np.ndarray = np.asarray(np.isfinite(uw).all(axis=1))
    use: np.ndarray = np.ones(m, dtype=bool)
    if m:
        use = np.asarray(np.logical_and.reduceat(finite, sc.offsets[:-1]), dtype=bool)
    step = float(geom.step) if isinstance(geom, SeriesGeometry) else 0.0
    if m:
        pairs, how = auto_links(uw, sc.offsets, sc.section, step, use, tick)
        cu, cw, area, _box = centres(np.where(finite[:, None], uw, 0.0), sc.offsets)
    else:
        pairs, how = np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int8)
        cu = cw = area = np.zeros(0)
    contours = []
    for i in range(m):
        k = int(sc.section[i])
        on = k >= 0 and bool(use[i])
        item: dict[str, Any] = {
            "i": i, "k": k, "name": names[k] if k >= 0 else None, "layer_id": int(sc.layer[i]),
            "lid": int(sc.lid[i]), "vertices": int(sc.offsets[i + 1] - sc.offsets[i]),
            "uw": [float(cu[i]), float(cw[i])] if on else None, "area": abs(float(area[i])) if on else None,
            "centre": _centre(geom, k, float(cu[i]), float(cw[i]), sc, i) if on else None,
            "off_plane": float(sc.off[i]) if np.isfinite(sc.off[i]) else None,
        }  # fmt: skip
        contours.append(item)
    groups = bodies(m, pairs.tolist()) if m else []
    found = [g for g in groups if sc.section[g[0]] >= 0]
    warnings = _warnings(sc, names, use)
    return {
        "count": int(geom.count), "step": step, "names": names,
        "layers": [{"id": layer.layer_id, "name": layer.name, "rev": layer.rev} for layer in sc.layers],
        "contours": contours,
        "links": [[int(a), int(b), HOW[int(h)]] for (a, b), h in zip(pairs.tolist(), how.tolist(), strict=True)],
        "bodies": found, "conflicts": conflicts(pairs, sc.section),
        "skipped": {"open": sc.open_lines, "short": sc.short_lines, "outside": int((sc.section < 0).sum()),
                    "no_height": int(((sc.section >= 0) & ~use).sum())},
        "off_plane_limit": OFF_PLANE, "warnings": warnings,
    }  # fmt: skip


def _centre(geom: Any, k: int, u: float, w: float, sc: SeriesContours, i: int) -> list[float]:
    from .geometry import SeriesGeometry

    if isinstance(geom, SeriesGeometry):
        p = geom.origin(k) + u * geom.s + w * geom.w + 0.0
    else:  # a polyline: the mean of the vertices
        p = sc.xyz[sc.offsets[i] : sc.offsets[i + 1]].mean(axis=0)
    return [float(x) for x in p]


def _warnings(sc: SeriesContours, names: list[str], use: np.ndarray) -> list[str]:
    from .contours import off_plane_warnings

    out = off_plane_warnings(sc, names)
    no_height = np.flatnonzero((sc.section >= 0) & ~use)
    if len(no_height):
        lines = ", ".join(sc.label(int(i)) for i in no_height[:5])
        out.append(f"Контуры без высоты у вершин не связываются: {lines}" + (" …" if len(no_height) > 5 else "."))
    return out
