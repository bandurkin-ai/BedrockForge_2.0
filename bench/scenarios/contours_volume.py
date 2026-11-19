"""Isolines of a surface and the volume between two surfaces (ТЗ 7.23 «Изолинии», «Объём»; ADR 0015 «Замеры»:
100 thousand / 1 million triangles with 100 levels and 2 × 100 thousand / 2 × 1 million — ориентир ≤ 3 s; ≤ 10 s
on full).

The surface is an open pit in a relief at UTM coordinates (`tools/gen/wireframes.pit_surface`: benches, berms,
survey noise) of `triangles` triangles. As the operations `contours` and `volume_between` call them:
  contours_s   `geo/contours.contours` with the step that gives 100 levels over its range;
  volume_s     `geo/volumes.volume_between` of the pit (top) and a level plane through the middle of its
               range (bottom), triangulated on the same nodes with the other diagonals: `triangles` more
               triangles that cross the pit everywhere (cut and fill both).
What is checked:
  levels           levels with lines (≈ 100);
  level_error_m    the largest distance in height of a point of an isoline from its level;
  volume_error     |cut − cut′| + |fill − fill′| over cut′ + fill′, where cut′, fill′ — the same volumes by the
                   prismatic method (`volume_to_level`, analytic per triangle, no booleans).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LEVELS = 100
PLANE = 0.4  # the plane at this share of the range of heights (off the levels of the benches)


def _plane(xyz: Any, tri: Any, z: float) -> tuple[Any, Any]:
    """The same nodes at height z, every cell split by its other diagonal (normals up)."""
    import numpy as np

    v = xyz.copy()
    v[:, 2] = z
    t = tri.reshape(-1, 2, 3)
    a, b, c, d = t[:, 0, 0], t[:, 0, 1], t[:, 0, 2], t[:, 1, 2]
    flipped = np.stack([np.column_stack([a, b, d]), np.column_stack([b, c, d])], axis=1).reshape(-1, 3)
    return v, flipped.astype(np.int32)


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.wireframes import pit_surface

    pit_surface(int(params["triangles"])).save(work / "pit.npz")
    load()


def load() -> None:
    from tools.gen.wireframes import pit_surface

    from bf_core.geo.contours import contours
    from bf_core.geo.volumes import volume_between, volume_to_level

    v, t = pit_surface(2_000).mesh()
    z = float(v[:, 2].min() + PLANE * (v[:, 2].max() - v[:, 2].min()))
    contours(v, t, interval=1.0)
    volume_between((v, t), _plane(v, t, z))
    volume_to_level((v, t), z)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    from tools.gen.wireframes import Surface

    from bf_core.geo.contours import contours
    from bf_core.geo.volumes import volume_between, volume_to_level

    v, t = Surface.load(work / "pit.npz").mesh()
    low, high = float(v[:, 2].min()), float(v[:, 2].max())
    interval = (high - low) / LEVELS
    t0 = time.perf_counter()
    lines = contours(v, t, interval=interval, base=low + interval / 2.0)
    contours_s = time.perf_counter() - t0
    level_error = max(float(np.abs(line["points"][:, 2] - line["z"]).max()) for line in lines)

    z = low + PLANE * (high - low)
    bottom = _plane(v, t, z)
    t0 = time.perf_counter()
    between = volume_between((v, t), bottom)
    volume_s = time.perf_counter() - t0
    prismatic = volume_to_level((v, t), z)
    error = abs(between["cut"] - prismatic["cut"]) + abs(between["fill"] - prismatic["fill"])
    return {
        "triangles": int(len(t)),
        "contours_s": round(contours_s, 4),
        "lines": len(lines),
        "levels": len({line["z"] for line in lines}),
        "level_error_m": level_error,
        "volume_s": round(volume_s, 4),
        "cut_m3": round(float(between["cut"]), 1),
        "fill_m3": round(float(between["fill"]), 1),
        "volume_error": float(error / (prismatic["cut"] + prismatic["fill"])),
    }
