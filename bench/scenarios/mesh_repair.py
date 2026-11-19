"""«Исправить каркас», all the steps (ТЗ 7.23 `repair`; ADR 0015 «Замеры»: 1 / 10 million triangles, by the first
measurement — ориентир ≤ 15 s / ≤ 150 s, ≤ 2 GB).

A cube of side 1 000 m at UTM coordinates, n × n cells a face, as a damaged export gives it: every face meshed
on its own (the vertices of the edges and corners repeated — the weld joins them), 25 triangles turned over, a
square hole of 200 × 200 m in the top face, 1 000 triangles repeated, the triangles shuffled. `geo/repair.repair`
with every step and the default tolerances, the self-intersections counted before and after (as the operation
does):
  repair_s         the whole call.
What is checked against the cube:
  volume_error     |V − 1000³| / 1000³ of the result (the hole is closed by a plane patch: exact);
  defects          open and non-manifold edges, orientation conflicts, degenerate and repeated triangles and
                   self-intersections left (0), plus 1 if the solid is not outward or not one part.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SIDE = 1000.0
UTM = (512_345.678_901, 6_234_567.123_456, 412.345_678)


def _damaged_cube(triangles: int) -> tuple[Any, Any]:
    import numpy as np

    n = max(4, round((triangles / 12) ** 0.5))
    a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    a, b = a.ravel(), b.ravel()
    i = (np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]).ravel()
    quad = np.concatenate([np.column_stack([i, i + 1, i + n + 2]), np.column_stack([i, i + n + 2, i + n + 1])])
    zero, full = np.zeros_like(a), np.full_like(a, n)
    faces_v, faces_t, first = [], [], 0
    for axis, value, flip in ((2, zero, True), (2, full, False), (0, zero, True), (0, full, False),
                              (1, zero, False), (1, full, True)):  # fmt: skip
        cols = {0: (value, a, b), 1: (a, value, b), 2: (a, b, value)}[axis]
        t = quad[:, ::-1] if flip else quad
        if axis == 2 and value[0] == n:  # the top face: a hole in the middle
            c = np.column_stack(cols)[t].mean(axis=1) * (SIDE / n)
            t = t[~((np.abs(c[:, 0] - SIDE / 2) < 100.0) & (np.abs(c[:, 1] - SIDE / 2) < 100.0))]
        faces_t.append(t + first)
        faces_v.append(np.column_stack(cols))
        first += len(a)
    v = np.concatenate(faces_v) * (SIDE / n) + np.asarray(UTM)
    t = np.concatenate(faces_t)
    rng = np.random.default_rng(3)
    flips = rng.choice(len(t), 25, replace=False)
    t[flips] = t[flips][:, ::-1]
    t = np.concatenate([t, t[rng.choice(len(t), 1000, replace=False)]])
    return v, t[rng.permutation(len(t))].astype(np.int32)


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    v, t = _damaged_cube(int(params["triangles"]))
    np.save(work / "v.npy", v)
    np.save(work / "t.npy", t)
    load()


def load() -> None:
    from bf_core.geo.repair import repair

    repair(*_damaged_cube(2_000))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.geo.repair import repair

    v, t = np.load(work / "v.npy"), np.load(work / "t.npy")
    t0 = time.perf_counter()
    _rv, rt, report = repair(v, t)
    seconds = time.perf_counter() - t0
    before, after = report["before"], report["after"]
    defects = sum(int(after[k] or 0) for k in ("open_edges", "nonmanifold_edges", "orientation_conflicts",
                                               "degenerate", "duplicate", "self_intersections"))  # fmt: skip
    defects += int(after["orientation"] != "outward") + int(after["components"] != 1)
    return {
        "triangles": int(len(t)),
        "triangles_after": int(len(rt)),
        "repair_s": round(seconds, 4),
        "open_edges_before": int(before["open_edges"]),
        "conflicts_before": int(before["orientation_conflicts"]),
        "volume_error": abs(float(after["volume"]) - SIDE**3) / SIDE**3,
        "defects": int(defects),
    }
