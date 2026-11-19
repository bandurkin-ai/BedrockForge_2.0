"""Operations with lines and surfaces (ТЗ 7.23; ADR 0015 §6–7): сшить контуры, выдавить, линии пересечения,
изолинии, объём. Contours and polygons come from layers by the stable numbers of their lines (`lid`); a closed
line comes without its repeated vertex. «Построить ЦТМ» is the job `model.dtm` (`registry.precheck`).

Tie lines of a stitch are read from the same layers again at every run (and so at «Перестроить»): an open line
marked `ROLE = TIE` (the editor's tool `tie`), or an unmarked line of two vertices whose both ends lie on
vertices of the contours (≤ `TIE_SNAP` m); any other open line is skipped with a warning. A vertex without a
height is refused, never taken for 0. Ported from BF `ops/operations.py` (parts B and C).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .output import MeshOut, Output, Tick, failed, staged
from .sources import Line, Sources

MAX_NAME = 200
TIE_SNAP = 1e-3  # m, as geo/stitch.py


def _name(text: str) -> str:
    return " ".join(text.split())[:MAX_NAME]


def _picked(src: Sources, p: dict[str, Any]) -> list[Line]:
    return [line for k in range(len(p["contours"])) for line in src.lines(f"contours[{k}]")]


def _heights(lines: list[Line]) -> None:
    for line in lines:
        if np.isnan(line.points[:, 2]).any():
            raise failed(
                "empty_z", f"У вершин: {line.where} — нет высоты (пустая отметка не считается нулём).",
                "Задайте высоты вершин в редакторе или выберите другие линии.",
            )  # fmt: skip


def _layers(lines: list[Line]) -> str:
    return ", ".join(dict.fromkeys(line.layer for line in lines))


def is_marked_tie(line: Line) -> bool:
    return not line.closed and str(line.attrs.get("ROLE") or "").strip().upper() == "TIE"


def stitch(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from scipy.spatial import cKDTree

    from bf_core.geo.polygon import StitchError
    from bf_core.geo.stitch import stitch_contours

    lines = _picked(src, p)
    contours = [line for line in lines if line.closed and len(line.points) >= 3]
    _heights(contours)
    tree = cKDTree(np.vstack([c.points for c in contours]))
    ties: list[np.ndarray] = []
    skipped = 0
    for line in lines:
        if line.closed:
            continue
        ends = line.points[[0, -1]]
        if is_marked_tie(line) or (len(line.points) == 2 and float(tree.query(ends)[0].max()) <= TIE_SNAP):
            _heights([line])
            ties.append(ends)
        else:
            skipped += 1
    try:
        result = stitch_contours(
            [c.points for c in contours], ties, start_cap=p["start_cap"], end_cap=p["end_cap"],
            cap_distance=p["cap_distance"], progress=staged(tick, 0.0, 1.0, "Сшивка контуров"),
        )  # fmt: skip
    except StitchError as exc:
        where = [contours[k].where for k in exc.contours if 0 <= k < len(contours)][:6]
        message = exc.message + (f" ({'; '.join(where)})" if where else "")
        raise failed(exc.code, message, exc.hint) from exc
    name = _name(f"Тело: {_layers(contours)}")
    report = dict(result.report)
    out = Output(name, [MeshOut(name, result.vertices, result.triangles, src.layers["contours[0]"].color)])
    out.report = {**report, "contours": len(contours), "ties": len(ties)}
    out.tolerances = dict(report.get("tolerances") or {})
    out.warnings = [str(w) for w in report.get("warnings") or []]
    if skipped:
        out.warnings.append(
            f"Незамкнутых линий пропущено: {skipped} (тай-линия — линия с меткой ROLE = TIE или линия из двух "
            "вершин, концы которой стоят на вершинах контуров)."
        )
    return out


def extrude(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.extrude import extrude_polygons

    lines = _picked(src, p)
    polygons = [line for line in lines if line.closed and len(line.points) >= 3]
    _heights(polygons)
    v, t = extrude_polygons(
        [g.points for g in polygons], distance=p["distance"], vector=p["vector"],
        vertical=p["direction"] == "vertical", both_sides=p["both_sides"],
        progress=staged(tick, 0.0, 1.0, "Выдавливание"),
    )  # fmt: skip
    name = _name(f"Выдавливание: {_layers(polygons)}")
    out = Output(name, [MeshOut(name, v, t, src.layers["contours[0]"].color)])
    out.report = {"polygons": len(polygons), "distance": p["distance"], "direction": p["direction"],
                  "vector": p["vector"], "both_sides": p["both_sides"]}  # fmt: skip
    if len(lines) > len(polygons):
        out.warnings.append(f"Незамкнутых линий пропущено: {len(lines) - len(polygons)}.")
    return out


def intersection_lines(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.formats.lineset import from_lines
    from bf_core.geo.crossing import intersection_lines as crossing

    a, b = src.wfs[int(p["a"])], src.wfs[int(p["b"])]
    found = crossing(src.mesh(a), src.mesh(b), tick=staged(tick, 0.0, 0.9, "Линии пересечения"))
    if not found:
        raise failed("no_crossing", f"Каркасы «{a.name}» и «{b.name}» не пересекаются.", "Выберите другие каркасы.")
    name = _name(f"Пересечение: {a.name} × {b.name}")
    layer = from_lines(name, [f["points"] for f in found], closed=[bool(f["closed"]) for f in found], color=a.color)
    out = Output(name, layers=[layer])
    out.report = {"lines": len(found), "closed": sum(1 for f in found if f["closed"]),
                  "length": float(layer.lengths().sum())}  # fmt: skip
    return out


def contours(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.contours import contour_layers
    from bf_core.geo.contours import contours as isolines

    wf = src.wfs[int(p["wireframe_id"])]
    v, t = src.mesh(wf)
    found = isolines(
        v, t, interval=p["interval"], base=p["base"], major_every=p["major_every"],
        tick=staged(tick, 0.0, 0.9, "Изолинии"),
    )  # fmt: skip
    if not found:
        raise failed(
            "no_levels", f"В пределах высот «{wf.name}» нет ни одного уровня с шагом {p['interval']:g} м.",
            "Уменьшите шаг или измените отсчёт.",
        )  # fmt: skip
    out = Output(_name(f"Изолинии: {wf.name}"), layers=contour_layers(found))
    out.report = {"levels": len({float(f["z"]) for f in found}), "lines": len(found),
                  "major": sum(1 for f in found if f["major"]), "interval": p["interval"], "base": p["base"],
                  "major_every": p["major_every"]}  # fmt: skip
    return out


def volume_between(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.volumes import volume_between as between
    from bf_core.geo.volumes import volume_to_level

    top = src.wfs[int(p["top"])]
    polygon = None
    if p.get("polygon") is not None:
        (line,) = src.lines("polygon")
        polygon = line.points[:, :2]
    bodies = not p["report_only"]
    progress = staged(tick, 0.0, 1.0, "Объём")
    if p.get("bottom") is not None:
        bottom = src.wfs[int(p["bottom"])]
        report = between(src.mesh(top), src.mesh(bottom), polygon=polygon, bodies=bodies, tick=progress)
        title = f"«{top.name}» – «{bottom.name}»"
    else:
        report = volume_to_level(src.mesh(top), float(p["level"]), polygon=polygon, bodies=bodies, tick=progress)
        title = f"«{top.name}» – уровень {p['level']:g}"
    made = report.pop("bodies", None) or {}
    out = Output(_name(f"Объём: {title}"), report=report)
    out.tolerances = {"volume": report.get("tolerance")}
    for body, mesh in made.items():
        if mesh is not None and len(mesh[1]):
            out.meshes.append(MeshOut(_name(f"{body}: {title}"), mesh[0], mesh[1], top.color))
    if bodies and not out.meshes:
        out.warnings.append("Выемки и насыпи нет: поверхности совпадают в общем контуре.")
    return out


RUNS = {
    "stitch": stitch,
    "extrude": extrude,
    "intersection_lines": intersection_lines,
    "contours": contours,
    "volume_between": volume_between,
}
