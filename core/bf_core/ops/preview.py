"""«Собрать из контуров»: the preview of a stitch (ТЗ 7.23; ADR 0015 §6 «Предпросмотр», decision 11).

`POST /geometry/stitch/preview {contours, ties, start_cap, end_cap, cap_distance}` — coordinates from the working
copy of the editor (the contours may be unsaved): at most `MAX_CONTOURS` contours and `MAX_VERTICES` vertices,
else 422 `preview_too_large` («создайте каркас операцией»). It runs in the process of the API, so without the
search for self-intersections (`check=False`: ~90 ms of the 288 ms of 20 × 500 vertices, PROGRESS «Э9 С»); the
operation `stitch` checks them. The answer is BFA1 `mesh`: `xyz` <f4 — offsets from the whole-metre origin of
the result (float32 only for the view, ui/src/viz/origin.ts), `idx` <u4, and in the header {origin, triangles,
vertices, closed, volume, warnings, contours, ties}. Nothing is stored.

`warm()` compiles the kernels of the stitch into the cache of numba once (the quick worker calls it at start:
then the process of the API loads them from the cache instead of compiling while the user waits).
"""

from __future__ import annotations

import math
from typing import Any

from bf_core.api.errors import ApiError

MAX_CONTOURS = 500
MAX_VERTICES = 50_000
MAX_TIES = 2_000
TOO_LARGE_HINT = "Создайте каркас операцией «Сшить контуры»: она считает в фоне."


def _points(raw: Any, what: str, least: int) -> Any:
    import numpy as np

    try:
        a = np.asarray([[math.nan if c is None else float(c) for c in p] for p in raw], dtype=np.float64)
    except (TypeError, ValueError):
        raise ApiError(422, "bad_geometry", f"{what}: координаты заданы неверно.", "Выберите контуры заново.") from None
    if a.ndim != 2 or a.shape[1] != 3 or len(a) < least:
        raise ApiError(
            422, "bad_geometry", f"{what}: нужно не меньше {least} вершин X, Y, Z.", "Выберите контуры заново."
        )
    if not np.isfinite(a[:, :2]).all():
        raise ApiError(422, "bad_geometry", f"{what}: в координатах X или Y есть пустые значения.", "Проверьте линию.")
    if np.isnan(a[:, 2]).any():
        raise ApiError(
            422, "empty_z", f"{what}: у вершин нет высоты (пустая отметка не считается нулём).",
            "Задайте высоты вершин в редакторе.",
        )  # fmt: skip
    return a


def stitch_preview(
    contours: list[Any], ties: list[Any], *, start_cap: str = "flat", end_cap: str = "flat", cap_distance: float = 0.5
) -> bytes:
    import numpy as np

    from bf_core.geo.meshes import MeshError
    from bf_core.geo.stitch import CAPS, stitch_contours
    from bf_core.viz import binary

    if len(contours) > MAX_CONTOURS or sum(len(c) for c in contours) > MAX_VERTICES or len(ties) > MAX_TIES:
        raise ApiError(
            422, "preview_too_large",
            f"Для предпросмотра слишком много: не больше {MAX_CONTOURS} контуров и {MAX_VERTICES:,} вершин."
            .replace(",", " "), TOO_LARGE_HINT,
        )  # fmt: skip
    if len(contours) < 2:
        raise ApiError(422, "too_few_contours", "Выберите не меньше двух контуров.", "Щёлкните по контурам по порядку.")
    for cap in (start_cap, end_cap):
        if cap not in CAPS:
            raise ApiError(422, "bad_choice", f"Неизвестный торец «{str(cap)[:20]}».", "Выберите торец из списка.")
    if not (math.isfinite(cap_distance) and cap_distance > 0):
        raise ApiError(422, "bad_param", "«Длина торца» должна быть больше нуля.", "Введите положительное число.")
    rings = [_points(c, f"Контур {k + 1}", 3) for k, c in enumerate(contours)]
    links = [_points(t, f"Тай-линия {k + 1}", 2)[[0, -1]] for k, t in enumerate(ties)]
    try:
        result = stitch_contours(
            rings, links, start_cap=start_cap, end_cap=end_cap, cap_distance=cap_distance, check=False
        )
    except MeshError as exc:  # StitchError too
        raise ApiError(422, exc.code, exc.message, exc.hint, {"contours": list(getattr(exc, "contours", ()))}) from exc
    v, t = result.vertices, result.triangles
    origin = np.round((v.min(axis=0) + v.max(axis=0)) / 2.0) + 0.0
    report = result.report
    volume = report.get("volume")
    header = {
        "kind": "mesh",
        "format": 1,
        "preview": True,
        "origin": origin.tolist(),
        "triangles": int(len(t)),
        "vertices": int(len(v)),
        "closed": bool(report.get("closed")),
        "volume": None if volume is None else float(volume),
        "warnings": [str(w) for w in report.get("warnings") or []],
        "contours": len(rings),
        "ties": len(links),
        "index": "<u4",
    }
    return binary.encode(header, [("xyz", (v - origin).astype("<f4"), "<f4"), ("idx", t.astype("<u4"), "<u4")])


def warm() -> None:
    """Two small contours through the stitch with every check and every cap: the kernels into the cache."""
    import numpy as np

    from bf_core.geo.stitch import stitch_contours

    a = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]]) + [500_000.0, 7e6, 0.0]
    b = a + [1.0, 0.5, 10.0]
    c = np.vstack([a[:2], (a[1:2] + a[2:3]) / 2.0, a[2:]]) + [0.0, 0.0, 20.0]  # another number of vertices
    stitch_contours([a, b, c], [np.array([a[0], b[0]])], start_cap="point", end_cap="extend")
    stitch_contours([a, b], start_cap="line", end_cap="flat", check=False)
