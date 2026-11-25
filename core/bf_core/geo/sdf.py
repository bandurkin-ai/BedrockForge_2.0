"""The signed distance of points to a closed wireframe (ТЗ 8.8 Д-28 «расстояние блока до каркаса»; ТЗ 8.9.3
«Расширение каркаса» of Э19 takes the same kernel; ADR 0024 §Д-28).

    signed_distance(vertices, triangles, points, *, reach=inf, check=True, threads=1, tick=None) -> float64 [n]
        the distance of every point (n×3, project coordinates) to the wireframe, metres: < 0 inside the body,
        > 0 outside, 0 on the surface; NaN for a point without coordinates (never 0); ±inf for a point with no
        triangle within `reach` metres (the sign is still told: the distance is more than `reach`)
    signed_distance_to(target, points, *, reach=inf, threads=1, tick=None) -> float64 [n]   the same for a `MeshTarget`
    inside(target, points, *, threads=1, tick=None) -> bool [n]                    the sign alone
    SdfError(code, message, hint)   `wireframe_open`, `wireframe_empty` — in Russian, `.as_dict()`
    warm()

The magnitude is the distance to the nearest triangle: `geo.nearest.nearest_triangles` over the tree of boxes
a `geo.intersect.MeshTarget` has already (one tree a wireframe serves the crossings of the holes, the nearest
points and this), the squared distance of `deviation.point_triangle_d2`, so «equal distances» are equal float64
numbers and the result of a point depends on the point and the wireframe alone. What a point costs is the number
of triangles about as far from it as the nearest one: deep inside a round dense body it is all of them — give
`reach` (the distance that is of use; the contact analysis of the blocks takes D = 20 m) and the search drops
every box farther than that.

The sign is the parity of the crossings of the vertical ray from the point up to above the wireframe with its
triangles: odd — inside. The crossings are those of `tri_kernels.segment_triangle`, which takes the ray as moved
by an infinitesimal vector (up first, `LIFT`: «Simulation of Simplicity»): a ray through a vertex, along an edge or
over a face meets every triangle once or not at all, and a point on the surface is as if a little above it. The
parity is the same for any such ray, so the test does not depend on the orientation of the triangles; it does
depend on the wireframe being closed — an open one has no inside: `signed_distance` checks the edges (every one
of them in two triangles at least) unless `check=False`; a caller that holds the stored measures of the wireframe
(`wireframes.stats`: closed) passes its `MeshTarget` to `signed_distance_to`. A body with a cavity (a shell
turned inside out inside the outer one) has the cavity outside; a wireframe with a pair of nested or crossing
shells counts the shells (even — outside).

Points go in the bands of `geo.nearest` (`BAND`, fixed): `threads` > 1 runs them on Python threads over the
`nogil` kernels; the same bytes with any number of threads and in any order of the points. `tick(fraction)` is
called in the calling thread after every band (the distance takes the first half, the sign the second) and may
raise to cancel. Memory: 9 bytes a point besides the result (the tree itself — a `MeshTarget`).

Time (подшаг 18.Д1; one thread, a sphere of 99 224 triangles of ~13 m, R = 500 m, UTM coordinates): the tree 0,3 s;
points in a shell of ±20 m round the surface 69 000 a second (119 000 on 4 threads), points over the whole body
(the worst case: deep inside every triangle is about as far) 35 000 (45 000), with `reach` = 20 m over the box of
±600 m 375 000 (780 000 on 4 threads); the sign alone — 470 000–565 000 a second. 300 MB for a million points.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import meshes
from .intersect import MeshTarget, _piece_hits, _runs_near
from .nearest import BAND, _bands, _rows, nearest_triangles

__all__ = ["BAND", "SdfError", "inside", "signed_distance", "signed_distance_to", "warm"]

Tick = Callable[[float], None]

_UNKNOWN = -1  # `parity` of a point without coordinates
_ABOVE = 1.0  # m: how far over the wireframe the vertical ray ends


class SdfError(ValueError):
    """A wireframe the distance cannot be taken to: {code, message, hint} in Russian, for the user."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


# --- the sign ----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _parity_band(xyz, tri, leaf, leaves, box, pad, points, a, b, out):  # type: ignore[no-untyped-def]
    """The points a ≤ q < b against the wireframe (`MeshTarget.kernel_args`): out[q] = 1 — inside (an odd number
    of triangles crossed by the ray up to above the wireframe), 0 — outside, −1 — the point has no coordinates."""
    mesh = (xyz, tri, leaf, leaves, box, pad)
    stack = np.empty(128, dtype=np.int64)
    near = np.empty(256, dtype=np.int64)
    hits = (np.empty(64, np.float64), np.empty(64, np.int64), np.empty(64, np.int8))
    top = box[0, 5] + _ABOVE
    for q in range(a, b):
        px, py, pz = points[q, 0], points[q, 1], points[q, 2]
        if not math.isfinite(px + py + pz):
            out[q] = _UNKNOWN
            continue
        out[q] = 0
        # the root box holds every triangle (padded): outside it — nothing to cross, nothing to be inside of
        if px < box[0, 0] or px > box[0, 3] or py < box[0, 1] or py > box[0, 4] or pz < box[0, 2] or pz > box[0, 5]:
            continue
        count, near = _runs_near(mesh, stack, near, px, py, pz, px, py, top, 0.0)
        if count == 0:
            continue
        found, hits = _piece_hits(mesh, near, count, False, px, py, pz, px, py, top, hits)
        out[q] = found & 1


# --- the distance ------------------------------------------------------------------------------------------


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    """`tick` of a stage that takes the share [lo, hi] of the whole."""
    if tick is None:
        return None
    return lambda f: tick(lo + (hi - lo) * float(f))


def inside(target: MeshTarget, points: Any, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
    """bool [n]: the point is inside the closed wireframe `target` (the parity of the vertical ray — see the
    module docstring); False for a point without coordinates and for a point outside the box of the wireframe."""
    return _parity(target, _rows(points, "points"), threads, tick) == 1


def _parity(target: MeshTarget, p: np.ndarray, threads: int, tick: Tick | None) -> np.ndarray:
    out = np.zeros(len(p), dtype=np.int8)
    xyz, tri, leaf, leaves, box, pad = target.kernel_args()
    _bands(lambda a, b: _parity_band(xyz, tri, leaf, leaves, box, pad, p, a, b, out), len(p), threads, tick)
    return out


def signed_distance_to(
    target: MeshTarget, points: Any, *, reach: float = math.inf, threads: int = 1, tick: Tick | None = None
) -> np.ndarray:
    """The signed distance of `points` (n×3) to the closed wireframe `target`, metres: inside < 0 < outside, 0 on
    the surface, NaN — the point has no coordinates, ±inf — no triangle within `reach` metres (0 — only a point on
    the surface; inf — any). The wireframe is taken as closed, as the caller's stored measures say
    (`signed_distance` checks the edges itself)."""
    p = _rows(points, "points")
    near = nearest_triangles(target, p, reach, threads=threads, tick=_part(tick, 0.0, 0.5))
    out = np.sqrt(near.d2)
    out[(near.triangle < 0) & np.isfinite(p).all(axis=1)] = math.inf  # nothing within `reach`: farther than it
    inner = _parity(target, p, threads, _part(tick, 0.5, 1.0)) == 1
    np.negative(out, out=out, where=inner)
    out[out == 0.0] = 0.0  # no −0.0 on the surface
    return out


def signed_distance(
    vertices: Any, triangles: Any, points: Any, *, reach: float = math.inf, check: bool = True, threads: int = 1,
    tick: Tick | None = None,
) -> np.ndarray:  # fmt: skip
    """The signed distance of `points` (n×3) to the closed wireframe (vertices float64 n×3, triangles int m×3):
    inside < 0 < outside, metres; NaN — a point without coordinates. SdfError `wireframe_empty`, and
    `wireframe_open` (unless `check` is False) when an edge belongs to one triangle only; ValueError — a
    structure that is no wireframe or a triangle of a vertex that is not there."""
    target = MeshTarget(vertices, triangles)
    if target.triangles == 0:
        raise SdfError("wireframe_empty", "В каркасе нет треугольников.", "Выберите непустой каркас.")
    if check:
        open_edges, _ = meshes.edge_summary(target.tri, len(target.xyz))
        if open_edges:
            raise SdfError(
                "wireframe_open",
                f"Каркас не замкнут: рёбер, общих для одного треугольника, — {open_edges}. Знак расстояния "
                "(внутри или снаружи) для него не определён.",
                "Запустите «Исправить каркас» и повторите.",
            )
    return signed_distance_to(target, points, reach=reach, threads=threads, tick=tick)


# --- the warm-up -------------------------------------------------------------------------------------------


def warm() -> None:
    """Compile the kernels on a tetrahedron, with writable and with read-only arrays (a memory map of the blobs of
    a wireframe is read-only: numba compiles that signature apart)."""
    v = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 4.0]])
    t = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32)
    p = np.array([[0.5, 0.5, 0.5], [5.0, 5.0, 5.0], [np.nan, 0.0, 0.0]])
    for read_only in (False, True):
        vv, tt, pp = v.copy(), t.copy(), p.copy()
        if read_only:
            for a in (vv, tt, pp):
                a.setflags(write=False)
        signed_distance(vv, tt, pp)
