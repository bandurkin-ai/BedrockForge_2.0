"""The files of «Импорт файлов» before anything is written (ТЗ 7.8, 7.18, 7.19, 7.21; ADR 0011 §3, §5; ADR 0013
§5): pairing a selection, reading a file with its companions, and the composition the wizard shows.

The files of a selection are paired folder by folder (`formats.registry.group_files`): a main file with its
companions (`.dbf .shx .prj .cpg` of a shapefile, `.mid` of a MIF, world / TAB / GRF / `.prj` of an image, the
`.str` of a Surpac `.dtm`, the points `…pt.dm` of a Datamine `…tr.dm`). An image also takes the georeference
files that lie next to it under its name, chosen or not; a chosen one wins. The two tables of a Datamine
wireframe under names that do not tell they are a pair are put together by their content (`_pair_tables`).
What belongs to nothing (a companion without its main file, an unknown extension) is listed with the reason.

`inspect_files` reads every file and writes nothing: per file its format, layers (lines, points, vertices,
attributes), wireframes (name, triangles, vertices, colour, attributes, warnings; one that cannot be read — with
the reason), CRS, warnings, and per raster its size and georeference with «Уровень растров по Z» applied
(`raster_z` {elevation, shift}; numbers with a comma or a point). The geometry of wireframes is not kept and
display copies are not made here. A DXF or a DWG gives its lines and its wireframes at once. `preliminary` marks
a reader that was not checked on real files (Datamine); `choices` — the fields of the file an option of the
wizard is chosen from (`split_by`, `layer_field`).
A point cloud (a LAS, or a table with `cloud` in its options) is told by `object_cloud.summarize_cloud`: `kind`
"cloud" and `cloud` — the header of the LAS or the head of the table. A layer of more than `CLOUD_HINT_POINTS`
points read the ordinary way is warned «загрузите как облако».
An OMF v1 file (ADR 0017 §2) is told from its JSON alone: `wireframes` — its surfaces, `block_models` — its volumes
{name, counts, cells, fields}, `omf` — the whole composition (`OmfComposition.as_dict`: elements, data, what will
not load and why), `left_out` — what the file holds that the import does not take; its line sets are counted in
`totals.layers` (their lines are known only once read).
The job `import.objects` (`objects.py`) reads the files with the same functions.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

from bf_core.formats.errors import FormatError

MAX_NOTES = 200  # warnings kept with an object and returned by the composition
MAX_LAYERS_SHOWN = 500
POINT_OPTIONS = ("x", "y", "z", "name")  # columns of a table of points (`points.read_points`)
FILE_SETTINGS = ("split_by", "layer_field")  # choices of the wizard for a file (`registry.read_objects(settings=)`)
#: Extensions of ТЗ 7.8 that come with later stages: said so, not «unknown format».
LATER: dict[str, str] = {}
NUMBER_HINT = "Введите число, например 12,5."
EMPTY = "В файле нет ни линий, ни точек, ни каркасов."
TOTALS = ("layers", "lines", "points", "vertices", "rasters", "wireframes", "triangles", "block_models", "cells")
CLOUD_HINT_POINTS = 2_000_000  # a layer of points bigger than this is better loaded as a cloud (ADR 0014 §1)

Tick = Callable[[float, str | None], None]


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


# --- the selection -----------------------------------------------------------------------------------------


def option_key(path: str | PurePath, flavour: type[PurePath] | None = None) -> str:
    """The key of the read options of a file: its path as `pathlib` spells it on this system — one kind of
    separator, none doubled — and in one case where the file system has none (Windows). The wizard sends
    `C:/data/points.csv` or `C:\\data\\points.csv` as it got them; the files of a selection are met as paths
    (`plan`), so both sides of the lookup go through here. `flavour` — the path class, this system's by default."""
    pure = (flavour or Path)(path)
    text = str(pure)
    return text.lower() if isinstance(pure, PureWindowsPath) else text


def options_by_file(options: Mapping[str, Any] | None, flavour: type[PurePath] | None = None) -> dict[str, Any]:
    """The read options {path: {...}} of a selection under `option_key` of their paths."""
    return {option_key(path, flavour): value for path, value in (options or {}).items()}


@dataclass
class Group:
    main: Path
    formats: tuple[str, ...]  # keys of `registry.OBJECT_FORMATS` the extension allows
    sidecars: dict[str, Path] = field(default_factory=dict)  # suffix → the companion chosen with the file


def _why(path: Path) -> dict[str, str]:
    """Why a file of the selection is not loaded."""
    from bf_core.formats.registry import SUPPORTED, detect_object

    suffix = path.suffix.lower()
    code, message, hint = "unsupported", "Файл не загружается.", ""
    if suffix in LATER:
        message = f"Формат «{suffix}» ({LATER[suffix]}) в этой версии не загружается."
        hint = SUPPORTED
    else:
        try:
            detect_object(path)  # a companion or an unknown extension: refused by the name, the file is not read
        except FormatError as exc:
            code, message, hint = exc.code, exc.message, exc.hint
        except OSError:
            code, message, hint = "file_unreadable", "Файл не читается.", "Проверьте путь и доступ к файлу."
    return {"path": str(path), "file": path.name, "code": code, "message": message, "hint": hint}


def plan(paths: Sequence[str]) -> tuple[list[Group], list[dict[str, str]]]:
    """The files of a selection as main files with their companions, and what belongs to nothing."""
    from bf_core.formats.registry import group_files

    by_folder: dict[Path, list[str]] = {}
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if str(p) not in seen:
            seen.add(str(p))
            by_folder.setdefault(p.parent, []).append(p.name)
    groups: list[Group] = []
    rest: list[dict[str, str]] = []
    for folder, names in by_folder.items():
        found, loose = group_files(names)
        groups += [Group(folder / g.main, g.formats, {s: folder / n for s, n in g.sidecars.items()}) for g in found]
        rest += [_why(folder / name) for name in loose]
    return _pair_tables(groups), rest


def _pair_tables(groups: list[Group]) -> list[Group]:
    """Datamine: the two tables of a wireframe chosen under names that do not tell they are a pair (`ore.dm`,
    `points.dm`) are one group — the triangles with their points, as the reader itself finds them in the
    folder; read apart, each would bring the same wireframe."""
    from bf_core.formats import registry as reg

    loose = [g for g in groups if reg.DATAMINE in g.formats and not g.sidecars]
    if len(loose) < 2:
        return groups
    from bf_core.formats.datamine import POINTS, TRIANGLES
    from bf_core.formats.datamine_pair import find_pair, kind_of

    kinds = [kind_of(g.main, None) for g in loose]
    triangles = {os.path.normcase(str(g.main)): g for g, kind in zip(loose, kinds, strict=True) if kind == TRIANGLES}
    gone: set[int] = set()
    for g, kind in zip(loose, kinds, strict=True):
        if kind != POINTS:
            continue
        try:
            other = find_pair(g.main)
        except (FormatError, OSError):  # several tables to choose from: the reader says so
            continue
        owner = triangles.get(os.path.normcase(str(other))) if other is not None else None
        if owner is not None and not owner.sidecars:
            owner.sidecars[".dm"] = g.main
            gone.add(id(g))
    return [g for g in groups if id(g) not in gone]


def number(value: Any, label: str) -> float | None:
    """«12,5» and «12.5» are 12.5; empty — None. A ValueError with a Russian text otherwise."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"«{label}»: нужно число.")
    if isinstance(value, int | float):
        out = float(value)
    else:
        text = "".join(str(value).split()).replace("−", "-").replace(",", ".")
        if not text:
            return None
        try:
            out = float(text)
        except ValueError:
            raise ValueError(f"«{label}»: «{value}» — не число.") from None
    if not math.isfinite(out):
        raise ValueError(f"«{label}»: нужно конечное число.")
    return out


def parse_level(raw: Mapping[str, Any] | None) -> tuple[float | None, float]:
    """«Уровень растров по Z» (ТЗ 7.19): (elevation — instead of the one of the georeference, shift ± m)."""
    raw = raw or {}
    return number(raw.get("elevation"), "Высота растров"), number(raw.get("shift"), "Сдвиг растров по Z") or 0.0


def usable_section(section: Mapping[str, Any] | None) -> dict[str, float] | None:
    """The placement on the section line when it is complete: both edges and two different heights."""
    if not section:
        return None
    keys = ("x1", "y1", "x2", "y2", "z_top", "z_bottom")
    if any(section.get(k) is None for k in keys):
        return None
    out = {k: float(section[k]) for k in keys}
    return out if abs(out["z_top"] - out["z_bottom"]) >= 1e-6 else None


# --- reading -----------------------------------------------------------------------------------------------


def read_group(
    g: Group,
    options: Mapping[str, Any] | None,
    progress: Tick | None,
    *,
    displays: bool,
    section: Mapping[str, Any] | None = None,
    inspect: bool = False,
    converter: str | None = None,
) -> Any:
    """`registry.ObjectsRead` of a main file with its companions. Raises FormatError / OSError. `inspect` —
    the composition: wireframes without their geometry; otherwise they come one at a time (`ObjectsRead.meshes`:
    an error of the file may then come while they are taken). `converter` — the ODA File Converter of
    «Настройка», for a DWG."""
    from bf_core.formats import registry as reg

    options = options or {}
    extra: dict[str, Any] = {}
    sidecars: dict[str, Path] | None = dict(g.sidecars) or None
    if reg.RASTER in g.formats:
        from bf_core.formats.georef import find_sidecars
        from bf_core.rasters.store import section_georef

        sidecars = {**find_sidecars(g.main), **g.sidecars}
        extra["displays"] = displays
        place = usable_section(section)
        if place is not None:
            extra["fallback"] = lambda w, h: section_georef(place, w, h)
    elif reg.POINTS in g.formats:
        extra = {k: str(options[k]) for k in POINT_OPTIONS if options.get(k) is not None}
    settings = {k: str(options[k]) for k in FILE_SETTINGS if options.get(k)}
    if converter:
        settings["converter"] = converter
    return reg.read_objects(
        g.main, progress, encoding=options.get("encoding") or None, sidecars=sidecars, options=extra,
        settings=settings, inspect=inspect,
    )  # fmt: skip


def has_meshes(read: Any) -> bool:
    """The file gives wireframes to read: a reader that has not started yet, or a list that is not empty."""
    return bool(read.meshes) if isinstance(read.meshes, list) else read.meshes is not None


def has_content(read: Any) -> bool:
    """The file gave something to load: a raster, a layer with points, a wireframe (read or still to read)."""
    return bool(
        read.rasters or read.entries or read.volumes or has_meshes(read) or any(ls.point_count for ls in read.layers)
    )


def _layer_summary(ls: Any) -> dict[str, Any]:
    st = ls.stats()
    points = st["point_count"] if st["points_only"] else 0
    return {
        "name": ls.name, "lines": st["line_count"], "points": points, "vertices": st["point_count"] - points,
        "closed": st["closed_count"], "length": st["length"], "points_only": st["points_only"], "flat": st["flat"],
        "color": ls.color, "attributes": ls.attr_schema(), "extent": ls.extent(),
    }  # fmt: skip


def _raster_summary(r: Any, elevation: float | None, shift: float) -> tuple[dict[str, Any], Any, list[str]]:
    """(the composition entry, the georeference with the level applied, notes for the journal)."""
    from bf_core.formats import georef as gr
    from bf_core.rasters import store as rs

    geo, notes = rs.apply_level(r.georef, elevation, shift)
    if r.georef.source == gr.SECTION:
        notes.insert(0, rs.SECTION_NOTE)
    described, level, vertical, extent = rs.placement(geo, r.width, r.height, r.checks)
    described["source_label"] = gr.SOURCE_LABELS.get(described["source"], described["source"])
    out = {
        "name": r.name, "width": r.width, "height": r.height, "image": r.info.format, "bits": r.info.bits,
        "georef": described, "elevation": level, "vertical": vertical, "extent": extent,
        "has_elevation": bool(geo.affine_z) or level is not None,
        "sidecars": sorted(p.name for p in r.sidecars.values()),
    }  # fmt: skip
    return out, geo, notes


def _fields(detail: Any) -> list[dict[str, Any]]:
    """The field schema of the source, in the order of the file: name, type, width, decimals, description."""
    header = getattr(detail, "header", None)
    header = getattr(header, "header", None) or header  # points of a DAT: table.ReadInfo → DatHeader
    out: list[dict[str, Any]] = []
    for f in (getattr(header, "fields", None) or getattr(header, "columns", None) or [])[:2000]:
        kind = getattr(f, "kind", None)
        if kind is None or not hasattr(f, "name"):
            continue
        out.append(
            {
                "name": str(f.name),
                "type": str(kind),
                "width": int(getattr(f, "width", 0) or 0),
                "decimals": int(getattr(f, "decimals", 0) or 0),
                "description": str(getattr(f, "description", "")),
            }  # fmt: skip
        )
    return out


def _left_out(read: Any) -> list[dict[str, Any]]:
    """What a file holds but the import does not take (the composition says so): [{what, count, reason}, …].
    OMF: {key, element, kind, what, count, reason} of its reader; the others — nothing (the meshes of a drawing
    are its wireframes, Э8; what a reader skips is in its warnings)."""
    return [dict(x) for x in read.left_out[:MAX_NOTES]]


def _block_models(read: Any) -> list[dict[str, Any]]:
    """The volumes of an OMF file that load: {name, counts, cells, fields}."""
    return [
        {"name": e.name, "counts": e.counts, "cells": e.cells, "fields": [d.name for d in e.data if d.loads]}
        for e in read.volumes
    ]


def summarize(g: Group, read: Any, elevation: float | None = None, shift: float = 0.0) -> dict[str, Any]:
    """The composition of a read file (also what the journal of the import keeps of it)."""
    from bf_core.formats.registry import OBJECT_FORMATS, OMF, OMF_KIND, POINTS_KIND, RASTER_KIND, WIREFRAMES_KIND

    layers = [_layer_summary(ls) for ls in read.layers if ls.point_count]
    rasters = [_raster_summary(r, elevation, shift) for r in read.rasters]
    meshes = [e.as_dict() for e in read.entries]
    loadable = [m for m in meshes if not m["error"]]
    notes = [*read.warnings, *(n for _s, _g, extra in rasters for n in extra)]
    notes += [
        f"В слое «{s['name']}» {n_text(s['points'])} точек: загрузите его как облако («Главная › Загрузка › Облако "
        "точек») — показ и фильтр земли рассчитаны на облака"
        for s in layers
        if s["points"] > CLOUD_HINT_POINTS
    ]
    kind = OBJECT_FORMATS[read.format].kind
    omf = read.format == OMF
    blocks = _block_models(read)
    if omf:
        kind = OMF_KIND
    elif layers and all(s["points_only"] for s in layers):
        kind = POINTS_KIND
    elif meshes and not layers:
        kind = WIREFRAMES_KIND  # also a drawing that holds nothing but meshes
    names = sorted({p.name for p in g.sidecars.values()} | {n for s, _g, _n in rasters for n in s["sidecars"]})
    crs = read.crs
    omf_layers = sum(1 for e in read.detail.elements if e.kind == "lines" and e.loads) if omf and not layers else 0
    return {
        "path": str(g.main), "file": g.main.name, "ok": True, "format": read.format, "label": read.label,
        "kind": RASTER_KIND if rasters else kind,
        "encoding": getattr(read.detail, "encoding", None),
        "crs": None if crs is None else {"name": crs.name, "epsg": crs.epsg, "wkt": crs.wkt},
        "sidecars": names,
        "layers": layers[:MAX_LAYERS_SHOWN],
        "rasters": [s for s, _g, _n in rasters],
        "wireframes": meshes[:MAX_LAYERS_SHOWN],
        "totals": {
            "layers": len(layers) or omf_layers, "lines": sum(s["lines"] for s in layers),
            "points": sum(s["points"] for s in layers), "vertices": sum(s["vertices"] for s in layers),
            "rasters": len(rasters), "wireframes": len(loadable),
            "triangles": sum(int(m["triangles"] or 0) for m in loadable),
            "block_models": len(blocks), "cells": sum(int(b["cells"] or 0) for b in blocks),
        },
        "block_models": blocks[:MAX_LAYERS_SHOWN],
        **({"omf": read.detail.as_dict()} if omf and read.detail is not None else {}),
        "unread": len(meshes) - len(loadable),  # wireframes of the file that will not load (their `error` says why)
        "preliminary": bool(read.preliminary),
        "choices": dict(read.choices),
        "warnings": notes[:MAX_NOTES],
        "left_out": _left_out(read),
    }  # fmt: skip


def _failure(path: Path, exc: BaseException) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if isinstance(exc, FormatError):
        code, message, hint, extra = exc.code, exc.message, exc.hint, exc.extra
    else:
        reason = getattr(exc, "strerror", None) or str(exc)
        code, message, hint = "file_unreadable", f"Файл не читается ({reason}).", "Проверьте путь и доступ к файлу."
    # `extra` (a block file the grid cannot hold: `diag`, `choices` — ADR 0030) goes beside the four fields
    return {**extra, "path": str(path), "file": path.name, "code": code, "message": message, "hint": hint}


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def inspect_files(
    paths: Sequence[str],
    options: Mapping[str, Mapping[str, Any]] | None = None,
    raster_z: Mapping[str, Any] | None = None,
    section: Mapping[str, Any] | None = None,
    tick: Tick | None = None,
    converter: str | None = None,
) -> dict[str, Any]:
    """The composition of a selection (ТЗ 7.8 «состав и то, что не загрузится»). Nothing is written.
    `converter` — the ODA File Converter of «Настройка» (a DWG without one is a file that failed, with the
    text of ADR 0009)."""
    elevation, shift = parse_level(raster_z)
    groups, rest = plan(paths)
    by_file = options_by_file(options)
    files: list[dict[str, Any]] = []
    totals = dict.fromkeys(("files", "failed", *TOTALS), 0)
    from . import object_blockfile as obf
    from . import object_cloud as oc

    for g in groups:
        chosen = by_file.get(option_key(g.main))
        try:
            if obf.is_blockfile(g.main, chosen):  # a block model (ADR 0023 §7): the roles of the columns, the grid
                entry = obf.inspect_entry(g.main, chosen, tick)
            elif oc.wanted(g, chosen):  # a cloud: the header of a LAS, the head of a table
                entry = oc.summarize_cloud(g, chosen)
            else:
                read = read_group(
                    g, chosen, tick, displays=False, section=section, inspect=True, converter=converter
                )  # fmt: skip
                if not has_content(read):
                    raise FormatError("empty_file", EMPTY, "Проверьте файл.")
                entry = summarize(g, read, elevation, shift)
                del read
        except (FormatError, OSError) as exc:
            failure = _failure(g.main, exc)
            entry = {"path": failure["path"], "file": failure["file"], "ok": False}
            entry["error"] = {k: failure[k] for k in ("code", "message", "hint")}
            totals["failed"] += 1
        else:
            totals["files"] += 1
            for k, v in entry["totals"].items():
                totals[k] += v
        entry["size"] = _size(g.main)
        files.append(entry)
    return {
        "files": files,
        "rest": rest,
        "totals": totals,
        "raster_z": {"applies": totals["rasters"] > 0, "elevation": elevation, "shift": shift},
        "section": usable_section(section),
    }
