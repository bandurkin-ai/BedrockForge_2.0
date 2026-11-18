"""The files of «Импорт файлов» before anything is written (ТЗ 7.8, 7.18, 7.19; ADR 0011 §3, §5): pairing a
selection, reading a file with its companions, and the composition the wizard shows.

The files of a selection are paired folder by folder (`formats.registry.group_files`): a main file with its
companions (`.dbf .shx .prj .cpg` of a shapefile, `.mid` of a MIF, world / TAB / GRF / `.prj` of an image). An
image also takes the georeference files that lie next to it under its name, chosen or not; a chosen one wins.
What belongs to nothing (a companion without its main file, an unknown extension) is listed with the reason.

`inspect_files` reads every file and writes nothing: per file its format, layers (lines, points, vertices,
attributes), CRS, warnings, what is left out, and per raster its size and georeference with «Уровень растров по
Z» applied (`raster_z` {elevation, shift}; numbers with a comma or a point). Display copies are not made here.
The job `import.objects` (`objects.py`) reads the files with the same functions.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bf_core.formats.errors import FormatError

MAX_NOTES = 200  # warnings kept with an object and returned by the composition
MAX_LAYERS_SHOWN = 500
POINT_OPTIONS = ("x", "y", "z", "name")  # columns of a table of points (`points.read_points`)
#: Extensions of ТЗ 7.8 that come with later stages: said so, not «unknown format».
LATER = {
    ".tridb": "каркасы", ".obj": "каркасы", ".stl": "каркасы", ".dtm": "каркасы", ".dwg": "чертежи DWG",
    ".omf": "проекты OMF",
}  # fmt: skip
NUMBER_HINT = "Введите число, например 12,5."

Tick = Callable[[float, str | None], None]


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


# --- the selection -----------------------------------------------------------------------------------------


@dataclass
class Group:
    main: Path
    formats: tuple[str, ...]  # keys of `registry.OBJECT_FORMATS` the extension allows
    sidecars: dict[str, Path] = field(default_factory=dict)  # suffix → the companion chosen with the file


def _why(path: Path) -> dict[str, str]:
    """Why a file of the selection is not loaded."""
    from bf_core.formats.registry import detect_object

    suffix = path.suffix.lower()
    code, message, hint = "unsupported", "Файл не загружается.", ""
    if suffix in LATER:
        message = f"Формат «{suffix}» ({LATER[suffix]}) в этой версии не загружается."
        hint = "Линии и точки загружаются из .str, .dxf, .shp, .mif и таблиц XYZ; растры — с файлом привязки."
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
    return groups, rest


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
) -> Any:
    """`registry.ObjectsRead` of a main file with its companions. Raises FormatError / OSError."""
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
    return reg.read_objects(
        g.main, progress, encoding=options.get("encoding") or None, sidecars=sidecars, options=extra
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
    """What a file holds but the import does not take (the composition says so)."""
    meshes = int(getattr(read.detail, "meshes", 0) or 0)
    if not meshes:
        return []
    return [{"what": "Каркасы (сетки DXF)", "count": meshes, "reason": "каркасы — этап Э8, сейчас не загружаются"}]


def summarize(g: Group, read: Any, elevation: float | None = None, shift: float = 0.0) -> dict[str, Any]:
    """The composition of a read file (also what the journal of the import keeps of it)."""
    from bf_core.formats.registry import OBJECT_FORMATS, POINTS_KIND, RASTER_KIND

    layers = [_layer_summary(ls) for ls in read.layers if ls.point_count]
    rasters = [_raster_summary(r, elevation, shift) for r in read.rasters]
    notes = [*read.warnings, *(n for _s, _g, extra in rasters for n in extra)]
    kind = OBJECT_FORMATS[read.format].kind
    if layers and all(s["points_only"] for s in layers):
        kind = POINTS_KIND
    names = sorted({p.name for p in g.sidecars.values()} | {n for s, _g, _n in rasters for n in s["sidecars"]})
    crs = read.crs
    return {
        "path": str(g.main), "file": g.main.name, "ok": True, "format": read.format, "label": read.label,
        "kind": RASTER_KIND if rasters else kind,
        "encoding": getattr(read.detail, "encoding", None),
        "crs": None if crs is None else {"name": crs.name, "epsg": crs.epsg, "wkt": crs.wkt},
        "sidecars": names,
        "layers": layers[:MAX_LAYERS_SHOWN],
        "rasters": [s for s, _g, _n in rasters],
        "totals": {
            "layers": len(layers), "lines": sum(s["lines"] for s in layers),
            "points": sum(s["points"] for s in layers), "vertices": sum(s["vertices"] for s in layers),
            "rasters": len(rasters),
        },
        "warnings": notes[:MAX_NOTES],
        "left_out": _left_out(read),
    }  # fmt: skip


def _failure(path: Path, exc: BaseException) -> dict[str, str]:
    if isinstance(exc, FormatError):
        code, message, hint = exc.code, exc.message, exc.hint
    else:
        reason = getattr(exc, "strerror", None) or str(exc)
        code, message, hint = "file_unreadable", f"Файл не читается ({reason}).", "Проверьте путь и доступ к файлу."
    return {"path": str(path), "file": path.name, "code": code, "message": message, "hint": hint}


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
) -> dict[str, Any]:
    """The composition of a selection (ТЗ 7.8 «состав и то, что не загрузится»). Nothing is written."""
    elevation, shift = parse_level(raster_z)
    groups, rest = plan(paths)
    files: list[dict[str, Any]] = []
    totals = dict.fromkeys(("files", "failed", "layers", "lines", "points", "vertices", "rasters"), 0)
    for g in groups:
        try:
            read = read_group(g, (options or {}).get(str(g.main)), tick, displays=False, section=section)
            if not read.rasters and not any(ls.point_count for ls in read.layers):
                raise FormatError("empty_file", "В файле нет ни линий, ни точек.", "Проверьте файл.")
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
