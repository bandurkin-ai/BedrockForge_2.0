"""«Расширение каркаса» (ТЗ 8.9.1, 8.9.3; ADR 0026 решение 18): the surface d − Δ = 0, d the signed distance to a closed
wireframe (`geo.sdf`, 18.Д1), made by the same bricks as every implicit surface (`geo.iso`). Δ > 0 grows the body
by Δ metres on every side, Δ < 0 shrinks it (what is thinner than 2·|Δ| is gone: an empty surface is a result).

    expand_field(v, t, delta, *, H, threads=1) -> FieldFn     f(points) = d − Δ, float64; the wireframe is checked now
    expand_box(v, delta, H) -> (lo, hi)                       a box that holds the surface and a brick round it
    expand_surface(v, t, delta, *, h, levels=2, threads=1, tick=None) -> IsoMesh        the whole: field, box, bricks
    NEAR                                                      the `near` of `isosurface` for a distance (8.9.3 п. 2)
    ExpandError(code, message, hint)                          `bad_expand`; an open or empty wireframe is `sdf.SdfError`

The field. d is the distance to the nearest triangle, signed by the parity of the ray (`sdf.signed_distance_to`:
inside < 0): the same value for a point whatever the other points are, so the bricks that share a node agree and the
field is bit for bit under any number of threads. Only the points within `reach` = |Δ| + 2·H of the wireframe are
measured (the search of a triangle stops there); farther, the field is ±H by the sign (inside −H, outside +H): the
true value is beyond ±2·H there, the surface is nowhere near, and the coarse grid of the isosurface (the step H) sees
the sign. A point that is not finite is +H (8.9.3 п. 6). The wireframe is the closed one: `sdf.SdfError`
`wireframe_open` / `wireframe_empty` at the construction of the field, not at its first call.

numba comes with `geo.sdf`: it is imported by `expand_field`, not by this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from bf_core.geo.iso import IsoMesh

__all__ = ["NEAR", "ExpandError", "FieldFn", "expand_box", "expand_field", "expand_surface"]

FieldFn = Callable[[np.ndarray], np.ndarray]
Tick = Callable[[float, str], None] | None

NEAR = 1.5  # 8.9.3 п. 2: for a distance the cells with min |f| < 1,5·H join the band


class ExpandError(ValueError):
    """A parameter of the expansion that cannot be used: {code, message, hint} in Russian."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def _checked(delta: float, step: float, name: str) -> tuple[float, float]:
    d, s = float(delta), float(step)
    if not math.isfinite(d):
        raise ExpandError(
            "bad_expand", "Расстояние расширения не число.", "Задайте Δ в метрах (отрицательное — сжатие)."
        )
    if not (math.isfinite(s) and s > 0.0):
        raise ExpandError("bad_expand", f"{name} должен быть положительным числом.", "Задайте шаг сетки в метрах.")
    return d, s


def expand_field(
    v: Any,
    t: Any,
    delta: float,
    *,
    H: float,
    threads: int = 1,  # noqa: N803 - the notation of 8.9.3
) -> FieldFn:
    """The field f(points) = d − Δ of the closed wireframe (v float64 n×3, t int m×3) for `isosurface` (`H` — the step
    of its coarse grid, 2^L·h). float64 (q,); NaN in → +H. `sdf.SdfError` — the wireframe is open or empty."""
    from bf_core.geo import meshes, sdf
    from bf_core.geo.intersect import MeshTarget

    d, big = _checked(delta, H, "Шаг грубой сетки H")
    target = MeshTarget(v, t)
    if target.triangles == 0:
        raise sdf.SdfError("wireframe_empty", "В каркасе нет треугольников.", "Выберите непустой каркас.")
    open_edges, _ = meshes.edge_summary(target.tri, len(target.xyz))
    if open_edges:
        raise sdf.SdfError(
            "wireframe_open",
            f"Каркас не замкнут: рёбер, общих для одного треугольника, — {open_edges}. Знак расстояния "
            "(внутри или снаружи) для него не определён.",
            "Запустите «Исправить каркас» и повторите.",
        )
    reach = abs(d) + 2.0 * big
    workers = max(int(threads), 1)

    def field(points: np.ndarray) -> np.ndarray:
        p = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 3)
        from bf_core.geo.iso import field_threads

        dist = sdf.signed_distance_to(target, p, reach=reach, threads=field_threads(workers))
        out = dist - d
        out[np.isposinf(dist)] = big
        out[np.isneginf(dist)] = -big
        out[np.isnan(dist)] = big
        return out

    return field


def expand_box(v: Any, delta: float, H: float) -> tuple[np.ndarray, np.ndarray]:  # noqa: N803
    """The box [lo, hi] of the surface d = Δ: the extent of the wireframe grown by Δ (not below 0) and a brick H."""
    d, big = _checked(delta, H, "Шаг грубой сетки H")
    pts = np.asarray(v, dtype=np.float64).reshape(-1, 3)
    grow = max(d, 0.0) + big
    return pts.min(axis=0) - grow, pts.max(axis=0) + grow


def expand_surface(
    v: Any, t: Any, delta: float, *, h: float, levels: int = 2, threads: int = 1, tick: Tick = None
) -> IsoMesh:
    """The surface of the wireframe expanded by Δ: `isosurface(expand_field(...), expand_box(...), h, near=NEAR)`.
    The surface of a body shrunk to nothing has no triangles."""
    from bf_core.geo.iso import isosurface

    d, step = _checked(delta, h, "Шаг h")
    big = step * 2 ** int(levels)
    lo, hi = expand_box(v, d, big)
    field = expand_field(v, t, d, H=big, threads=threads)
    return isosurface(field, lo, hi, step, levels=levels, near=NEAR, threads=threads, tick=tick)
