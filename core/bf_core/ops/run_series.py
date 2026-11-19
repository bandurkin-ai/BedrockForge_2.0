"""«Каркас по серии» — the operation `stitch_series` (ТЗ 8.1.4, Т8.1-4; ADR 0016 §8, решение 5).

The contours are the closed lines of the layers (by default the contour layer of the series) whose vertices are all
in the window of a section (`sections/contours.py`, решение 3); open lines, lines in no window and closed ones with
fewer than 3 vertices are skipped and counted; a vertex farther than 1 mm from the plane of its section is a
warning with the name of the section and the number of the line. The links are those of the parameters (pairs of
lines {layer_id, line}) or, without them, the automatic ones (`sections/links.py`: the projections overlap, else the
centres are ≤ Δ apart). Every connected group of contours is a body (`geo/stitch_links.bodies`); all bodies are
stitched in one call of `geo/stitch.stitch_contours` (normal v̂, the sections k, the links, spacing Δ; by default
`extend` caps of Δ/2), so bodies that touch or cross each other are a warning and only a body that crosses itself
fails. The result: a wireframe per body «<серия> — тело N» (N from 1, by the first contour: section, layer, line),
or one wireframe «<серия>» of all of them («одним каркасом»: parts without shared vertices).

A failure names the sections by their names and the contours by their lines («Между контурами разрезов 5050N и
5075N получаются самопересечения … (контуры 3 («Контуры») и 7 («Контуры»))»): `StitchError` speaks of sections
k + 1 and of contours by their place in the call, both are replaced here. Nothing is created then.

`precheck` (`ops/registry.py`) reads the series and attaches it to the sources (`attach`): the run has no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from .output import MeshOut, Output, Tick, failed, staged
from .sources import Sources

MAX_NAME = 200
MAX_LINKS = 200_000  # links in the parameters
LINKS_HINT = (
    "Свяжите контуры соседних разрезов в таблице связей: каждая группа — один к одному или разветвление одного "
    "контура на несколько."
)
_SECTION = re.compile(r"(?P<word>[Рр]азрез[а-я]*) (?P<a>\d+)(?: и (?P<b>\d+))?")
_CONTOUR = re.compile(r"(?P<word>[Кк]онтур[а-я]*) (?P<nums>\d+(?:(?:, | и )\d+)*)")


@dataclass(frozen=True)
class SeriesSource:
    id: int
    name: str
    number: int | None
    version: int
    meta: dict[str, Any]


def attach(src: Sources, series: SeriesSource) -> None:
    vars(src)["series"] = series


def attached(src: Sources) -> SeriesSource:
    found = vars(src).get("series")
    assert isinstance(found, SeriesSource), "the series is attached by registry.precheck"
    return found


# --- the check of the parameters (`registry.precheck`) ------------------------------------------------------


def _series(src: Sources, value: Any) -> SeriesSource:
    """The series, recorded in the sources (role «series», with its number and version)."""
    import sqlalchemy as sa

    from bf_core.api.errors import ApiError
    from bf_core.db.schema import objects
    from bf_core.sections.geometry import SeriesError, SeriesGeometry, series_geometry
    from bf_core.sections.names import section_names
    from bf_core.sections.schema import KIND

    from . import params as P
    from .sources import _missing

    sid = P.object_id(value, "серия разрезов")
    row = None
    if sid is not None:
        row = src.conn.execute(
            sa.select(objects.c.id, objects.c.kind, objects.c.name, objects.c.version, objects.c.meta)
            .where(objects.c.id == sid)
        ).first()  # fmt: skip
    if row is None:
        raise _missing("серия разрезов", value, src.known.get("series"), "series")
    if row.kind != KIND:
        raise ApiError(
            422, "wrong_kind", f"«{row.name}» — не серия разрезов.", "Выберите серию разрезов.", {"role": "series"}
        )
    meta = dict(row.meta) if isinstance(row.meta, dict) else {}
    try:
        geom = series_geometry(meta)
        section_names(meta)
    except SeriesError as e:
        raise ApiError(422, e.code, e.message, e.hint or "Проверьте параметры серии.", {"role": "series"}) from e
    if not isinstance(geom, SeriesGeometry):
        raise P.bad(
            "series_polyline", f"«{row.name}» — ломаный разрез: это один разрез, а каркас по серии строится по "
            "серии параллельных разрезов.", "Выберите вертикальную или наклонную серию или серию планов.",
        )  # fmt: skip
    number = meta.get("number")
    number = number if isinstance(number, int) and not isinstance(number, bool) else None
    src.used.append(
        {"role": "series", "kind": KIND, "id": int(row.id), "name": str(row.name), "version": int(row.version),
         "number": number}
    )  # fmt: skip
    return SeriesSource(int(row.id), str(row.name), number, int(row.version), meta)


def _link_end(raw: Any, number: int, side: str, layers: dict[int, Any]) -> dict[str, int]:
    """{layer_id, line}: a line (`lid`) of one of the layers of the operation."""
    from . import params as P

    hint = "Обновите таблицу связей окна «Каркас по серии»."
    if not isinstance(raw, dict):
        raise P.bad("bad_links", f"Связь {number}: неверная ссылка на контур ({side}).", hint)
    layer_id = P.object_id(raw.get("layer_id"), "слой контура")
    line = raw.get("line")
    if layer_id not in layers:
        raise P.bad("bad_links", f"Связь {number}: слой контура ({side}) не выбран для операции.", hint)
    layer = layers[layer_id]
    if isinstance(line, bool) or not isinstance(line, int) or line < 0:
        raise P.bad("bad_links", f"Связь {number}: неверный номер линии ({side}).", hint)
    if line not in layer.lid_set:
        raise P.bad(
            "line_missing", f"Связь {number}: в слое «{layer.name}» нет линии {line + 1}: она удалена или слой "
            "изменён.", hint, layer_id=layer.id, lines=[line],
        )  # fmt: skip
    return {"layer_id": int(layer.id), "line": int(line)}


@dataclass
class _Picked:
    id: int
    name: str
    lid_set: set[int]


def prepare(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    """The checked parameters: {series_id, layer_ids, links (null — automatic), start_cap, end_cap, cap_distance,
    single}; the series is attached to `src` for the run."""
    from . import params as P
    from .registry import CAPS

    series = _series(src, raw.get("series_id"))
    ids = raw.get("layer_ids")
    if ids is None:
        layer_id = series.meta.get("contour_layer")
        if not isinstance(layer_id, int) or isinstance(layer_id, bool):
            raise P.bad(
                "no_contours", f"У серии «{series.name}» не задан слой контуров.",
                "Выберите слои контуров или задайте слой контуров в параметрах серии.",
            )  # fmt: skip
        ids = [layer_id]
    layers: dict[int, _Picked] = {}
    closed = 0
    for k, value in enumerate(P.id_list(ids, "Слои контуров")):
        layer = src.layer(value, f"layers[{k}]")
        layers[layer.id] = _Picked(layer.id, layer.name, set(layer.lids.tolist()))
        closed += int(layer.closed.sum())
    if not closed:
        raise P.bad(
            "too_few_contours", "В выбранных слоях нет замкнутых контуров.",
            "Замкните линии в редакторе («Замкнуть») или выберите другие слои.",
        )  # fmt: skip
    links = raw.get("links")
    if links is not None:
        if not isinstance(links, list) or len(links) > MAX_LINKS:
            raise P.bad("bad_links", "Связи контуров заданы неверно.", "Обновите таблицу связей окна «Каркас по серии».")
        checked = []
        for number, link in enumerate(links, start=1):
            if not isinstance(link, list | tuple) or len(link) != 2:
                raise P.bad("bad_links", f"Связь {number}: ожидается пара контуров.", "Обновите таблицу связей.")
            checked.append([_link_end(link[0], number, "первый", layers), _link_end(link[1], number, "второй", layers)])
        links = checked
    attach(src, series)
    return {
        "series_id": series.id,
        "layer_ids": list(layers),
        "links": links,
        "start_cap": P.choice(raw, "start_cap", "Начальный торец", CAPS, "extend"),
        "end_cap": P.choice(raw, "end_cap", "Конечный торец", CAPS, "extend"),
        "cap_distance": P.number(raw, "cap_distance", "Длина торца", 0.5, positive=True),
        "single": P.flag(raw, "single", False),
    }


# --- the run ----------------------------------------------------------------------------------------------------


def _name(text: str) -> str:
    return " ".join(text.split())[:MAX_NAME]


class Namer:
    """Section numbers k + 1 and contour numbers i + 1 of a `StitchError` → names of sections and lines."""

    def __init__(self, names: list[str], labels: list[str]) -> None:
        self.names, self.labels = names, labels

    def section(self, number: int) -> str:
        return self.names[number - 1] if 1 <= number <= len(self.names) else str(number)

    def contour(self, number: int) -> str:
        return self.labels[number - 1] if 1 <= number <= len(self.labels) else str(number)

    def text(self, message: str) -> str:
        def sections(m: re.Match[str]) -> str:
            out = f"{m['word']} {self.section(int(m['a']))}"
            return out + (f" и {self.section(int(m['b']))}" if m["b"] else "")

        def contours(m: re.Match[str]) -> str:
            return f"{m['word']} " + re.sub(r"\d+", lambda d: self.contour(int(d[0])), m["nums"])

        return _CONTOUR.sub(contours, _SECTION.sub(sections, message))


def _layers(src: Sources) -> list[Any]:
    from bf_core.sections.contours import lines_of

    out = []
    for role, layer in src.layers.items():
        if role.startswith("layers["):
            out.append(lines_of(layer.id, layer.name, layer.rev, src.lines(role)))
    return out


def _heights(sc: Any, kept: np.ndarray, names: list[str]) -> None:
    for i in kept.tolist():
        if np.isnan(sc.points(i)).any():
            raise failed(
                "empty_z", f"Разрез {names[int(sc.section[i])]}, {sc.label(i)}: у вершин нет высоты (пустая отметка "
                "не считается нулём).", "Задайте высоты вершин в редакторе или выберите другие слои.",
            )  # fmt: skip


def _degenerate(sc: Any, kept: np.ndarray, uw: np.ndarray, offsets: np.ndarray, names: list[str]) -> None:
    """A contour of zero area in the plane of its section (ADR 0016 «Риски»): refused with its line and section."""
    from bf_core.sections.links import centres

    _cu, _cw, area, box = centres(uw, offsets)
    size = np.max(box[:, 2:] - box[:, :2], axis=1)
    flat = np.flatnonzero(np.abs(area) <= 1e-12 * np.maximum(size * size, 1e-300))
    if len(flat):
        i = int(kept[flat[0]])
        raise failed(
            "degenerate_polygon", f"Разрез {names[int(sc.section[i])]}, {sc.label(i)}: контур вырожден — у него "
            "нулевая площадь в плоскости разреза.", "Поправьте контур в редакторе или удалите его.",
        )  # fmt: skip


def _given_links(sc: Any, kept: np.ndarray, links: list[Any], names: list[str], warnings: list[str]) -> np.ndarray:
    """Pairs of contour numbers (in `kept` order) from the links of the parameters."""
    where = {(int(sc.layer[i]), int(sc.lid[i])): n for n, i in enumerate(kept.tolist())}
    pairs: set[tuple[int, int]] = set()
    lost = 0
    for number, (a, b) in enumerate(links, start=1):
        ia, ib = where.get((int(a["layer_id"]), int(a["line"]))), where.get((int(b["layer_id"]), int(b["line"])))
        if ia is None or ib is None:
            lost += 1
            continue
        ka, kb = int(sc.section[kept[ia]]), int(sc.section[kept[ib]])
        if abs(ka - kb) != 1:
            raise failed(
                "stitch_links", f"Связь {number}: {sc.label(int(kept[ia]))} (разрез {names[ka]}) и "
                f"{sc.label(int(kept[ib]))} (разрез {names[kb]}) лежат не на соседних разрезах, а связываются "
                "только контуры соседних разрезов.", LINKS_HINT,
            )  # fmt: skip
        pairs.add((ia, ib) if ka < kb else (ib, ia))
    if lost:
        warnings.append(
            f"Связей пропущено: {lost} — их линии не замкнуты или не лежат в окнах разрезов серии."
        )
    return np.array(sorted(pairs), dtype=np.int64).reshape(-1, 2)


def _split(vertices: np.ndarray, triangles: np.ndarray, firsts: list[int]) -> list[tuple[np.ndarray, np.ndarray]]:
    """The connected parts of the result that hold the vertices `firsts` (the first vertex of the first contour
    of every body): bodies share no vertex; the vertices keep their order (the input ones first, bit for bit)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(vertices)
    t = triangles.astype(np.int64)
    rows = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    cols = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    _count, label = connected_components(graph, directed=False)
    out = []
    for first in firsts:
        part = label[t[:, 0]] == label[first]
        used = np.flatnonzero(label == label[first])
        remap = np.full(n, -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        out.append((vertices[used], remap[t[part]].astype(np.int32)))
    return out


def stitch_series(src: Sources, p: dict[str, Any], tick: Tick) -> Output:
    from bf_core.geo.polygon import StitchError
    from bf_core.geo.stitch import stitch_contours
    from bf_core.geo.stitch_links import bodies
    from bf_core.sections.contours import OFF_PLANE, off_plane_warnings, series_contours
    from bf_core.sections.geometry import SeriesGeometry, series_geometry
    from bf_core.sections.links import auto_links, projected
    from bf_core.sections.names import section_names

    series = attached(src)
    geom = series_geometry(series.meta)
    assert isinstance(geom, SeriesGeometry)  # a polyline is refused by the precheck
    names = section_names(series.meta)
    tick(0.0, "Контуры серии")
    layers = _layers(src)
    sc = series_contours(geom, layers, staged(tick, 0.0, 0.05, "Контуры серии"))
    kept = np.flatnonzero(sc.section >= 0)
    order = np.lexsort((np.arange(len(kept)), sc.section[kept]))  # by section, then as the layers give them
    kept = kept[order]
    outside = sc.count - len(kept)
    if not len(kept):
        raise failed(
            "no_contours", f"В окнах разрезов серии «{series.name}» нет замкнутых контуров выбранных слоёв.",
            "Нарисуйте контуры на разрезах серии или выберите другие слои.",
        )  # fmt: skip
    _heights(sc, kept, names)
    warnings = off_plane_warnings(sc, names)
    skipped = {"open": sc.open_lines, "short": sc.short_lines, "outside": int(outside)}
    if sum(skipped.values()):
        warnings.append(
            f"Пропущено линий: незамкнутых — {skipped['open']}, замкнутых меньше чем из трёх вершин — "
            f"{skipped['short']}, вне окон разрезов — {skipped['outside']}."
        )
    section = sc.section[kept]
    offsets = np.concatenate([[0], np.cumsum(np.diff(sc.offsets)[kept])]).astype(np.int64)
    uw = projected(geom, np.vstack([sc.points(int(i)) for i in kept]))
    _degenerate(sc, kept, uw, offsets, names)
    if p.get("links") is None:
        pairs, _how = auto_links(uw, offsets, section, geom.step, None, staged(tick, 0.05, 0.1, "Связи контуров"))
    else:
        pairs = _given_links(sc, kept, p["links"], names, warnings)
    found = bodies(len(kept), pairs.tolist())
    layer_names = {layer.layer_id: layer.name for layer in layers}
    labels = [f"{int(sc.lid[i]) + 1} («{layer_names[int(sc.layer[i])]}»)" for i in kept.tolist()]
    namer = Namer(names, labels)
    rings = [_clean(sc.points(int(i))) for i in kept]
    try:
        result = stitch_contours(
            rings, [], start_cap=p["start_cap"], end_cap=p["end_cap"], cap_distance=p["cap_distance"],
            normal=geom.v, groups=section.tolist(), links=pairs.tolist(), spacing=geom.step,
            progress=staged(tick, 0.1, 1.0, "Сшивка контуров серии"),
        )  # fmt: skip
    except StitchError as exc:
        raise failed(exc.code, namer.text(exc.message), exc.hint) from exc
    report = dict(result.report)
    per_body = report.get("bodies") or [{"contours": g} for g in found]
    color = next(iter(src.layers.values())).color if src.layers else None
    title = _name(series.name)
    out = Output(title)
    if p["single"]:
        out.meshes.append(MeshOut(title, result.vertices, result.triangles, color))
        body_names = [title] * len(per_body)
    else:
        starts = np.concatenate([[0], np.cumsum([len(r) for r in rings])])
        parts = _split(result.vertices, result.triangles, [int(starts[b["contours"][0]]) for b in per_body])
        body_names = [_name(f"{series.name} — тело {n}") for n in range(1, len(parts) + 1)]
        out.meshes = [MeshOut(name, v, t, color) for name, (v, t) in zip(body_names, parts, strict=True)]
    out.report = {
        "series": {"id": series.id, "number": series.number, "name": series.name, "version": series.version},
        "contours": int(len(kept)), "skipped": skipped, "links": int(len(pairs)),
        "auto_links": p.get("links") is None, "single": bool(p["single"]), "closed": report.get("closed"),
        "volume": report.get("volume"), "normal": report.get("normal"),
        "bodies": [
            {"name": name, "contours": len(b["contours"]),
             "sections": [names[int(section[b["contours"][0]])], names[int(section[b["contours"]].max())]],
             "lines": [namer.contour(c + 1) for c in b["contours"][:50]], "closed": report.get("closed"),
             "triangles": b.get("triangles"), "volume": b.get("volume")}
            for name, b in zip(body_names, per_body, strict=True)
        ],
    }  # fmt: skip
    out.tolerances = {"off_plane": OFF_PLANE, "tie": report.get("tolerance")}
    out.warnings = warnings + [namer.text(str(w)) for w in report.get("warnings") or []]
    return out


def _clean(p: np.ndarray) -> np.ndarray:
    """As `geo/polygon.clean_polygon` does: a repeated last vertex dropped (so the input vertices of the result are
    exactly these, in order)."""
    return p[:-1] if len(p) > 1 and np.array_equal(p[0], p[-1]) else p
