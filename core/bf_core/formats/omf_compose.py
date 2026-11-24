"""The composition of an OMF v1 file for the import wizard (ТЗ 7.24; ADR 0017 §1): elements, kinds, sizes, data
and what will not load and why — from the JSON alone, no binary block is read (a 2 GB file in ≤ 1 s).

    compose(f)       OmfComposition of an open `omf_file.OmfFile`
    describe(path)   open + compose; raises FormatError (the file itself cannot be read) or OSError
    described(path)  the composition `describe` gave for the file as it is (path, size, time of change), else
                     `describe` — the estimate of memory of a job does not read the JSON again after the wizard

What becomes what (ADR 0017 §2): a surface (triangles or a grid) — a wireframe, a line set — a layer of the line
file, a regular volume along X, Y, Z — a block model. Not carried over: points, textures, data on the vertices and
faces of surfaces, on the vertices of lines, on the nodes of volumes, data of other kinds (colours, vectors),
mapped data with colour legends only, volumes turned about X or Y (a turn about Z loads: `grid_axes`), volumes
over `MAX_VOLUME_CELLS`. Segment data of a line
loads when it is constant along each line (`loads` None: known only once the line is read). A broken element gets
its `error` {code, message, hint}; the others are described.

Ported from BF `formats/omf.py::describe`. Differences: every datum with the reason it does not load, the
structured list `left_out` and its texts `warnings` for the log, СК and ЦТМ from the descriptions BF2 writes.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bf_core.formats import omf_file as of
from bf_core.formats.errors import FormatError
from bf_core.formats.omf_file import (
    FORMAT_NAME,
    OmfFile,
    color_hex,
    damaged,
    is_dtm,
    n_text,
    name_of,
    open_omf,
    parse_crs,
    placement,
    plain_description,
    shown,
    tensor,
    unit_of,
)

ELEMENT_TITLES = {
    "PointSetElement": "Точки",
    "LineSetElement": "Линии",
    "SurfaceElement": "Поверхность",
    "VolumeElement": "Объём",
}
KINDS = {"PointSetElement": "points", "LineSetElement": "lines", "SurfaceElement": "surface", "VolumeElement": "volume"}
LOADED_AS = {"surface": "каркас", "lines": "слой линий", "volume": "блочная модель"}


ROTATION_SKIP = "поворот вокруг X/Y в v1 не поддерживается"  # ТЗ 8.7.1
AXES_TOL = 1e-6  # unit length, right angles and the plane of the axes of a file (float32 files: ≈ 1e-7)


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float] | None:
    n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    return None if not n or n != n or n == float("inf") else (v[0] / n, v[1] / n, v[2] / n)


def z_turn(geometry: dict[str, Any], what: str) -> float | None:
    """φ (degrees, (−180; 180]) when the axes u, v, w of a VolumeGridGeometry are a turn about Z — u = (cos φ,
    sin φ, 0), v = (−sin φ, cos φ, 0), w = +Z within `AXES_TOL` (axes of any length: they are normalised);
    None — they are not (a turn about X or Y, a mirror, a skew)."""
    from bf_core.api.errors import ApiError
    from bf_core.blocks.schema import rotation_of_axes

    eye = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    axes = [of.vector(geometry.get(f"axis_{n}"), f"{what}: ось {n}", eye[i]) for i, n in enumerate("uvw")]
    unit = [_unit(a) for a in axes]
    if any(a is None for a in unit):
        return None
    u, v, w = unit
    assert u is not None and v is not None and w is not None
    try:
        return rotation_of_axes(u, v, w, tol=AXES_TOL)
    except ApiError:
        return None


def grid_axes(geometry: dict[str, Any], what: str) -> tuple[list[tuple[int, float]] | None, float] | None:
    """How the grid of a VolumeGridGeometry lies: (placed, 0.0) — every axis along +X, +Y, +Z or against them
    (`omf_file.placement`: a permutation and signs; a Z-turn by a multiple of 90° is one of them — the cells of
    such a grid are renumbered, the blocks stay where they are); (None, φ) — any other turn about Z by φ; None —
    anything else (ТЗ 8.7.1: a turn about X or Y is not loaded)."""
    placed = placement(geometry, what)
    if placed is not None:
        return placed, 0.0
    phi = z_turn(geometry, what)
    return None if phi is None else (None, phi)


@dataclass
class OmfData:
    """A datum of an element: `loads` True / False, None — only when constant along each line (segments)."""

    key: str
    name: str
    cls: str
    location: str
    loads: bool | None
    reason: str | None = None
    unit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "class": self.cls, "location": self.location, "loads": self.loads,
            "reason": self.reason, "unit": self.unit,
        }  # fmt: skip


@dataclass
class OmfEntry:
    """An element in the composition. `kind`: surface | lines | volume | points | other; `as_` — what it becomes
    (None — it does not load: `skip` says why, or `error` {code, message, hint} — the element is broken).
    Counts the JSON does not tell (vertices of a triangulated surface, segments) are None."""

    key: str
    index: int
    cls: str
    kind: str
    name: str
    title: str
    geometry: str | None = None
    color: str | None = None
    description: str = ""
    points: int | None = None
    triangles: int | None = None
    counts: list[int] | None = None
    cells: int | None = None
    data: list[OmfData] = field(default_factory=list)
    textures: int = 0
    dtm: bool = False
    skip: str | None = None
    error: dict[str, str] | None = None
    packed: dict[str, int] = field(default_factory=dict)  # compressed bytes of the geometry blocks (not shown)

    @property
    def label(self) -> str:
        return f"{self.title} «{self.name}»"

    @property
    def loads(self) -> bool:
        return self.skip is None and self.error is None

    @property
    def as_(self) -> str | None:
        return LOADED_AS.get(self.kind) if self.loads else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "index": self.index, "class": self.cls, "kind": self.kind, "name": self.name,
            "title": self.title, "as": self.as_, "geometry": self.geometry, "color": self.color,
            "description": self.description, "points": self.points, "triangles": self.triangles,
            "counts": self.counts, "cells": self.cells, "data": [d.as_dict() for d in self.data],
            "textures": self.textures, "dtm": self.dtm, "skip": self.skip, "error": self.error,
        }  # fmt: skip


@dataclass
class OmfComposition:
    file: str
    version: str
    uid: str
    project: str
    description: str
    units: str
    crs: dict[str, Any] | None
    elements: list[OmfEntry]

    def left_out(self) -> list[dict[str, Any]]:
        """What the file holds but the import does not take: [{key, element, kind, what, count, reason}]."""
        out: list[dict[str, Any]] = []
        for e in self.elements:
            base = {"key": e.key, "element": e.name, "kind": e.kind}
            if e.error is not None:
                out.append({**base, "what": "элемент", "count": 1, "reason": e.error["message"]})
                continue
            if e.skip is not None:
                out.append({**base, "what": "элемент", "count": 1, "reason": e.skip})
            if e.textures:
                out.append({**base, "what": "текстуры", "count": e.textures, "reason": "текстуры не переносятся"})
            if e.skip is not None:
                continue
            for d in e.data:
                if d.loads is False:
                    out.append({**base, "what": f"данные «{d.name}»", "count": 1, "reason": d.reason})
        return out

    def warnings(self) -> list[str]:
        """`left_out` as texts for the log of the import (and `meta.warnings`)."""
        titles = {e.key: e.label for e in self.elements}
        out: list[str] = []
        for x in self.left_out():
            count = f" ({x['count']})" if x["what"] == "текстуры" else ""
            out.append(f"{titles[x['key']]}: {x['what']}{count} — {x['reason']}")
        if self.crs is None:
            out.append("Система координат в файле OMF не указана: объекты загружены без системы координат")
        return out

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for e in self.elements:
            counts[e.title] = counts.get(e.title, 0) + 1
        parts = ", ".join(f"{t.lower()}: {c}" for t, c in counts.items())
        out = f"Проект «{self.project}», элементов {len(self.elements)}" + (f" ({parts})" if parts else "")
        skipped = sum(1 for e in self.elements if not e.loads)
        return out + (f"; не загрузятся: {skipped}" if skipped else "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_NAME, "file": self.file, "version": self.version, "uid": self.uid,
            "project": self.project, "description": self.description, "units": self.units, "crs": self.crs,
            "summary": self.summary(), "elements": [e.as_dict() for e in self.elements],
            "left_out": self.left_out(), "warnings": self.warnings(),
        }  # fmt: skip


_SKIP_DATA = {
    "surface": "данные на вершинах и гранях каркаса не переносятся",
    "lines": "данные на вершинах линий не переносятся",
    "volume": "у блочной модели переносятся только данные в ячейках",
}
_DATA_CLASSES = ("ScalarData", "MappedData", "StringData", "DateTimeData")


def _datum(f: OmfFile, uid: Any, kind: str, label: str) -> OmfData:
    obj = f.get(uid, f"{label}: данные")
    cls = str(obj.get("__class__"))
    loc = str(obj.get("location") or "")
    d = OmfData(uid, name_of(obj, cls), cls, loc, False, unit=unit_of(obj.get("description")))
    if cls not in _DATA_CLASSES:
        d.reason = f"данные типа {shown(cls, 40)} не переносятся"
    elif cls == "MappedData" and not _has_labels(f, obj, label):
        d.reason = "у данных нет легенды с подписями (только цвета)"
    elif kind == "lines" and loc == "segments":
        d.loads, d.reason = None, "переносятся, если значение постоянно вдоль линии (атрибут линии)"
    elif kind == "volume" and loc == "cells":
        d.loads = True
    else:
        d.reason = _SKIP_DATA.get(kind, "данные не переносятся")
    return d


def _has_labels(f: OmfFile, data: dict[str, Any], label: str) -> bool:
    legends = data.get("legends") or []
    if not isinstance(legends, list):
        raise damaged(f"{label}: список легенд данных имеет неверную структуру.")
    for uid in legends:
        legend = f.get(uid, f"{label}: легенда данных", "Legend")
        if f.get(legend.get("values"), f"{label}: значения легенды").get("__class__") != "ColorArray":
            return True
    return False


def _entry(f: OmfFile, k: int, uid: str, element: dict[str, Any]) -> OmfEntry:
    cls = str(element.get("__class__"))
    kind = KINDS.get(cls, "other")
    title = ELEMENT_TITLES.get(cls, "Элемент")
    e = OmfEntry(uid, k, cls, kind, name_of(element, f"{title} {k + 1}"), title)
    e.color = color_hex(element.get("color"))
    e.description = str(element.get("description") or "")
    e.dtm = is_dtm(e.description)
    try:
        if kind == "other":
            e.skip = f"тип элемента «{shown(cls, 40)}» не поддерживается"
            return e
        textures = element.get("textures") or []
        e.textures = len(textures) if isinstance(textures, list) else 0
        geometry = f.get(element.get("geometry"), f"{e.label}: геометрия")
        e.geometry = str(geometry.get("__class__"))
        data = element.get("data") or []
        if not isinstance(data, list):
            raise damaged(f"{e.label}: список данных имеет неверную структуру.")
        e.data = [_datum(f, u, kind, e.label) for u in data]
        e.packed = _packed(f, geometry)
        if kind == "points":
            e.skip = "точки не переносятся"
        elif kind == "surface" and e.geometry == "SurfaceGridGeometry":
            nu = len(tensor(geometry.get("tensor_u"), f"{e.label}: шаги u"))
            nv = len(tensor(geometry.get("tensor_v"), f"{e.label}: шаги v"))
            e.counts, e.points, e.triangles = [nu, nv], (nu + 1) * (nv + 1), 2 * nu * nv
            if e.triangles > of.MAX_GRID_TRIANGLES:
                e.skip = f"{n_text(e.triangles)} треугольников — больше предела {n_text(of.MAX_GRID_TRIANGLES)}"
        elif kind == "surface" and e.geometry != "SurfaceGeometry":
            e.skip = f"неизвестная геометрия поверхности «{shown(e.geometry, 40)}»"
        elif kind == "lines" and e.geometry != "LineSetGeometry":
            e.skip = f"неизвестная геометрия линий «{shown(e.geometry, 40)}»"
        elif kind == "volume":
            _volume_entry(e, geometry)
    except FormatError as exc:
        e.error = exc.as_dict()
    return e


def _volume_entry(e: OmfEntry, geometry: dict[str, Any]) -> None:
    if e.geometry != "VolumeGridGeometry":
        e.skip = f"неизвестная геометрия объёма «{shown(e.geometry, 40)}»"
        return
    e.counts = [len(tensor(geometry.get(f"tensor_{a}"), f"{e.label}: шаги {a}")) for a in "uvw"]
    e.cells = e.counts[0] * e.counts[1] * e.counts[2]
    if grid_axes(geometry, e.label) is None:
        e.skip = ROTATION_SKIP
    elif e.cells > of.MAX_VOLUME_CELLS:
        e.skip = f"{n_text(e.cells)} ячеек — больше предела {n_text(of.MAX_VOLUME_CELLS)}"


def _packed(f: OmfFile, geometry: dict[str, Any]) -> dict[str, int]:
    """Compressed lengths of the vertices, triangles and segments as the JSON gives them (unchecked: reading
    checks them) — the estimate of memory of a surface whose counts the JSON does not tell."""
    out: dict[str, int] = {}
    for key in ("vertices", "triangles", "segments"):
        uid = geometry.get(key)
        obj = f.objects.get(uid) if isinstance(uid, str) else None
        desc = obj.get("array") if obj is not None else None
        length = desc.get("length") if isinstance(desc, dict) else None
        if isinstance(length, int) and not isinstance(length, bool) and length > 0:
            out[key] = length
    return out


def compose(f: OmfFile) -> OmfComposition:
    """The composition of an open file from its JSON: elements, kinds, sizes, data and what will not load. No
    binary block is read; a broken element gets its `error`, the others are described."""
    return OmfComposition(
        file=f.path.name,
        version=f.version,
        uid=f.uid,
        project=name_of(f.project, f.path.stem),
        description=plain_description(f.project.get("description")),
        units=shown(f.project.get("units") or "", 20),
        crs=parse_crs(f.project.get("description")),
        elements=[_entry(f, k, u, e) for k, (u, e) in enumerate(f.elements())],
    )


CACHE_FILES = 4
CACHE_ELEMENTS = 10_000  # a larger composition is not kept
_cache: OrderedDict[tuple[str, int, int], OmfComposition] = OrderedDict()
_cache_lock = threading.Lock()


def _key(path: str | Path) -> tuple[str, int, int]:
    st = os.stat(path)
    return str(Path(path).resolve()), int(st.st_size), int(st.st_mtime_ns)


def describe(path: str | Path) -> OmfComposition:
    """`compose` of a file: raises FormatError (the file itself cannot be read) or OSError."""
    key = _key(path)
    with open_omf(path) as f:
        comp = compose(f)
    if len(comp.elements) <= CACHE_ELEMENTS:
        with _cache_lock:
            _cache[key] = comp
            _cache.move_to_end(key)
            while len(_cache) > CACHE_FILES:
                _cache.popitem(last=False)
    return comp


def described(path: str | Path) -> OmfComposition:
    """The composition `describe` gave for this very file (unchanged since), else `describe`. Read only."""
    key = _key(path)
    with _cache_lock:
        comp = _cache.get(key)
    return comp if comp is not None else describe(path)
