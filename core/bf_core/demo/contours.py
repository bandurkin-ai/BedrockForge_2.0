"""Demo part of Э9 (ТЗ 7.5, 7.23; ADR 0015 §6–§7, решение 6): contours drawn on sections, the solid stitched from
them and the isolines of the relief — what «Сшить контуры» and «Изолинии» give, without running the jobs.

  «Контуры по разрезам»   a line file with the layer «Контуры рудного тела 3»: closed contours of a lower ore body
                          on the section lines Y = 8000 … 8250 of the hole grid — one contour (the trunk) on the
                          first three sections, two (the western and the eastern branch) on the last three, so
                          the series branches 1 : 2 between Y = 8100 and Y = 8150 — and four tie lines: lines of
                          two vertices with `ROLE = TIE` between vertices of contours of neighbouring sections;
  «Рудное тело 3»         the closed solid `geo/stitch.py` makes of that layer (the ties become its edges), some
                          50 m under «Рудное тело 1», with the record `meta.built` an operation `stitch` writes;
  «Рельеф (изолинии)»     the isolines of the DTM «Рельеф» every 2 m (every 10 m — «Основные»), attribute ELEV,
                          with the record of an operation `contours`.

Nothing is taken from the random stream of the demo: the parts before it stay as they were. Coordinates are
float64 in centimetres, as the strings of Э7 are; the vertices of the contours reach the solid bit for bit.
The kernels (numba) are imported inside the functions: this module is imported with the demo, before BF-READY.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from .geo import DTM

CONTOURS_FILE, CONTOURS = "Контуры по разрезам", "Контуры рудного тела 3"
BODY_3, ISOLINES_FILE = "Рудное тело 3", "Рельеф (изолинии)"
CONTOURS_COLOR, BODY_3_COLOR, ISOLINES_COLOR = "#c77dd6", "#b06ac4", "#8a6a4a"
SECTIONS = tuple(8000.0 + 50.0 * k for k in range(6))  # the section lines of the hole grid
TRUNK = 3  # sections with one contour; the other three carry two
ROLE, TIE = "ROLE", "TIE"
CAP = "point"  # both ends pinch out to a point half a section spacing out
INTERVAL, MAJOR_EVERY = 2.0, 5  # isolines every 2 m, every fifth (10 m) — «Основные»


def _contour(cx: float, cz: float, a: float, b: float, n: int, y: float, phase: float) -> np.ndarray:
    """A closed contour (n × 3, without a repeated vertex) on the section Y = y: an ellipse of semi-axes a (along
    X) and b (along Z), gently lobed and tilted 6° down to the east; vertex 0 is the top, n/2 the bottom."""
    tilt = math.radians(-6.0)
    c, s = math.cos(tilt), math.sin(tilt)
    pts = []
    for j in range(n):
        t = math.pi / 2 - 2 * math.pi * j / n  # clockwise from the top in XZ
        r = 1.0 + 0.07 * math.sin(3 * t + phase)
        u, w = a * r * math.cos(t), b * r * math.sin(t)
        pts.append((cx + c * u - s * w, y, cz + s * u + c * w))
    return np.round(np.array(pts, dtype=np.float64), 2)


def section_contours() -> list[tuple[np.ndarray, str]]:
    """(contour, its section) in the order of the layer: the trunk on the first sections, then both branches."""
    out = []
    for k, y in enumerate(SECTIONS):
        label = f"Y {y:.0f}"
        if k < TRUNK:  # the trunk grows to the north
            out.append(
                (_contour(5160.0, 236.0 - 1.5 * k, 62.0 + 6.0 * k, 15.0 + 1.0 * k, 20 + 2 * k, y, 0.4 * k), label)
            )
        else:  # the branches part: west keeps the level, east sinks
            j = k - TRUNK
            out.append(
                (_contour(5108.0 - 4.0 * j, 239.0, 36.0 - 3.0 * j, 13.0 - 1.0 * j, 16 - 2 * j, y, 1.0 + j), label)
            )
            out.append(
                (_contour(5226.0 + 5.0 * j, 226.0 - 4.0 * j, 34.0 - 2.0 * j, 12.0 - 1.0 * j, 14, y, 2.0 + j), label)
            )
    return out


def tie_lines(contours: list[np.ndarray]) -> list[tuple[np.ndarray, str]]:
    """Ties within the strips (1 : 1), never across the branching: the tops of the trunk between its first two
    sections, its bottoms between the next two, the tops of the western branch and the bottoms of the eastern."""
    trunk, west, east = contours[:TRUNK], contours[TRUNK::2], contours[TRUNK + 1 :: 2]

    def tie(a: np.ndarray, i: int, b: np.ndarray, j: int) -> tuple[np.ndarray, str]:
        return np.vstack([a[i], b[j]]), f"Y {a[i, 1]:.0f}–{b[j, 1]:.0f}"

    return [
        tie(trunk[0], 0, trunk[1], 0),
        tie(trunk[1], len(trunk[1]) // 2, trunk[2], len(trunk[2]) // 2),
        tie(west[0], 0, west[1], 0),
        tie(east[1], len(east[1]) // 2, east[2], len(east[2]) // 2),
    ]


def _file(conn: sa.Connection, store: BlobStore, name: str, meta: dict[str, Any]) -> int:
    from bf_core.lines import store as ls

    return ls.create_file(conn, store, name=name, meta=meta)


def _layer_source(conn: sa.Connection, layer_id: int, role: str) -> dict[str, Any]:
    from bf_core.lines import store as ls

    info = ls.layer_info(conn, layer_id)
    return {"role": role, "kind": "line_layer", "id": layer_id, "name": info["name"],
            "version": int(info["rev"] or 1), "lines": None}  # fmt: skip


def add_contours(conn: sa.Connection, store: BlobStore, _rng: random.Random) -> None:
    from bf_core.formats.lineset import from_lines
    from bf_core.geo.stitch import stitch_contours
    from bf_core.lines import store as ls
    from bf_core.ops.provenance import record
    from bf_core.wireframes import store as wf

    drawn = section_contours()
    contours = [c for c, _ in drawn]
    ties = tie_lines(contours)
    lines = [*contours, *(t for t, _ in ties)]
    layer = from_lines(
        CONTOURS,
        lines,
        closed=[True] * len(contours) + [False] * len(ties),
        attrs={
            "Разрез": [s for _, s in drawn] + [s for _, s in ties],
            ROLE: [None] * len(contours) + [TIE] * len(ties),
        },
        color=CONTOURS_COLOR,
    )
    meta: dict[str, Any] = {"format": "demo", "label": "демо-данные", "filename": None, "fields": [], "warnings": []}
    layer_id = ls.put_layer(conn, store, layer, file_id=_file(conn, store, CONTOURS_FILE, meta))

    # «Сшить контуры» on the whole layer: the closed lines are the contours, the lines marked TIE the ties
    params = {
        "contours": [{"layer_id": layer_id, "lines": None}],
        "start_cap": CAP,
        "end_cap": CAP,
        "cap_distance": 0.5,
    }
    result = stitch_contours(contours, [t for t, _ in ties], start_cap=CAP, end_cap=CAP, cap_distance=0.5)
    report = result.report
    built = record(
        op="stitch", title="Сшить контуры", params=params, sources=[_layer_source(conn, layer_id, "contours[0]")],
        report=report, tolerances={"section": report["section_tolerance"], "tie": report["tolerance"]},
    )  # fmt: skip
    wf.put_wireframe(
        conn, store, result.vertices, result.triangles, name=BODY_3, color=BODY_3_COLOR, sort_key=4.0,
        attrs=[{"name": "Тип", "value": "Руда"}, {"name": "Плотность", "value": 2.7}], meta={"built": built},
    )  # fmt: skip


def add_isolines(conn: sa.Connection, store: BlobStore, _rng: random.Random) -> None:
    from bf_core.geo.contours import contour_layers, contours
    from bf_core.lines import store as ls
    from bf_core.ops.provenance import record
    from bf_core.wireframes import store as wf

    row = conn.execute(
        sa.select(objects.c.id, objects.c.version).where(objects.c.kind == "wireframe", objects.c.name == DTM)
    ).one()
    v, t = wf.read_wireframe(conn, store, int(row.id))
    found = contours(v, t, interval=INTERVAL, base=0.0, major_every=MAJOR_EVERY)
    layers = contour_layers(found, color=ISOLINES_COLOR)
    params = {"wireframe_id": int(row.id), "interval": INTERVAL, "base": 0.0, "major_every": MAJOR_EVERY}
    source = {"role": "wireframe", "kind": "wireframe", "id": int(row.id), "name": DTM, "version": int(row.version),
              "triangles": len(t)}  # fmt: skip
    report = {
        "levels": len({c["z"] for c in found}), "lines": len(found), "closed": sum(c["closed"] for c in found),
        "major": sum(c["major"] for c in found), "interval": INTERVAL,
    }  # fmt: skip
    built = record(op="contours", title="Изолинии", params=params, sources=[source], report=report)
    file_id = _file(conn, store, ISOLINES_FILE, {"format": "operation", "built": built})
    ids = [ls.put_layer(conn, store, layer, file_id=file_id, sort_key=float(k)) for k, layer in enumerate(layers)]
    conn.execute(sa.update(objects).where(objects.c.id.in_(ids)).values(meta={"built": built}))
