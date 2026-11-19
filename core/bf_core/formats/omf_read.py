"""Elements of an OMF v1 file as BF2 objects, one at a time (ТЗ 7.24, 9.8; ADR 0017 §2).

    open_reader(path, progress)      OmfReader of an open file (its composition is read at once, from the JSON)
    OmfReader.elements(keys, kinds)  MeshSet | LineSet | OmfVolume in the order of the file
    column(f, data, n, what)         a datum as numbers or categories (ScalarData, MappedData, StringData,
                                     DateTimeData), n values checked; `omf_volume` reads cell data with it too

* `SurfaceElement` → `MeshSet`: SurfaceGeometry — the vertices and triangles of the file as they are (not welded,
  unused vertices kept); SurfaceGridGeometry — nodes `origin + u·axis_u + v·axis_v + offset_w·(axis_u × axis_v)`,
  u fastest (Fortran), two triangles a cell; a node without `offset_w` (NaN) drops its triangles (never a 0).
  The description (without the lines BF2 adds) is the attribute «Описание»; «BedrockForge: ЦТМ» —
  `source["dtm"] = True`. Data on vertices and faces and textures are not carried over.
* `LineSetElement` → `LineSet` (a layer of the one line file of the import): segments where each one starts at the
  end of the previous one are chained into a line; a line back to its first vertex is closed and keeps the repeat
  (ТЗ 17.3.12). Segment data constant along every line become attributes of the line (numbers; categories and
  texts as text; «Единицы: …» of the description — the unit); data that changes along a line, data on vertices
  and vertices no segment uses are listed as not carried over. Layer names differ (« (2)»).
* `VolumeElement` → `OmfVolume`: the element, its origin and its cell data for `omf_volume` (Э11.3).

Coordinates: the array of the file (float64) + (origin of the project + origin of the geometry), added in float64;
with both origins zero the array is kept bit for bit (−0.0 too). СК — `composition.crs` from the description of
the project. What is not carried over: `left_out` [{key, element, kind, what, count, reason}] and its texts
`warnings` (the log of the import, `meta.warnings`) — the composition's, then what reading finds.

Errors are `FormatError` {code, message, hint}; `skip_bad=True` puts an element that cannot be read into
`unread` and goes on. `progress(fraction, message)` is called inside big blocks (every ≤ 16 MiB) and may raise
to cancel.

Ported from BF `formats/omf.py` (read_omf, _surface, _grid_surface, _lines, _column_values, _legend_labels).
Differences: BF2 models (`MeshSet`, `LineSet`), one element at a time, segment data checked with numpy, nodes
without a height, the origin kept exact, sizes of the data checked against the geometry.
"""

from __future__ import annotations

from collections.abc import Container, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, detect_closed
from bf_core.formats.mesh_io import unique_name
from bf_core.formats.meshset import MeshSet, drop_unused, source_of
from bf_core.formats.omf_compose import OmfComposition, OmfData, OmfEntry, compose
from bf_core.formats.omf_file import (
    FORMAT,
    Block,
    OmfFile,
    damaged,
    n_text,
    open_omf,
    out_of_memory,
    plain_description,
    tensor,
    vector,
)
from bf_core.formats.table import Progress, no_progress

Element = MeshSet | LineSet


@dataclass
class OmfVolume:
    """A volume to read with `omf_volume`: its entry (name, counts, colour), origin of the project + of the
    geometry, the geometry and the cell data that load (with their JSON objects)."""

    entry: OmfEntry
    origin: np.ndarray
    geometry: dict[str, Any]
    data: list[tuple[OmfData, dict[str, Any]]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.entry.key

    @property
    def name(self) -> str:
        return self.entry.name


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return ""
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v).strip()


def legend_labels(f: OmfFile, data: dict[str, Any], what: str) -> list[str] | None:
    """Labels of MappedData: the first legend that is not colours (numbers and dates become text)."""
    for uid in data.get("legends") or []:
        legend = f.get(uid, f"{what}: легенда", "Legend")
        if f.get(legend.get("values"), f"{what}: значения легенды").get("__class__") == "ColorArray":
            continue
        values = f.array(legend.get("values"), f"{what}: значения легенды")
        if isinstance(values, np.ndarray):
            if values.ndim != 1:
                raise damaged(f"{what}: значения легенды должны быть одним столбцом.")
            values = values.tolist()
        return [_text(v) for v in values]
    return None


def text_codes(values: list[Any]) -> tuple[np.ndarray, list[str]]:
    """Codes of texts in the order of first appearance; an empty text is −1."""
    labels: dict[str, int] = {}
    codes = np.empty(len(values), dtype=np.int32)
    for i, v in enumerate(values):
        s = _text(v)
        codes[i] = labels.setdefault(s, len(labels)) if s else -1
    return codes, list(labels)


def column(f: OmfFile, data: dict[str, Any], n: int, what: str) -> tuple[str, np.ndarray, list[str] | None] | None:
    """(kind, values, labels) of a datum of n values: «number» — float64, NaN empty; «category» — int32 codes,
    −1 empty, and their labels. None — no counterpart (another class, MappedData with colour legends only)."""
    cls = data.get("__class__")
    if cls == "ScalarData":
        v = f.array(data.get("array"), what, rows=n)
        if not isinstance(v, np.ndarray) or v.ndim != 1 or v.dtype.kind not in "fiub":
            raise damaged(f"{what}: числовые данные должны быть одним столбцом чисел.")
        return "number", v.astype(np.float64, copy=False), None
    if cls == "MappedData":
        idx = f.array(data.get("array"), what, rows=n)
        if not isinstance(idx, np.ndarray) or idx.ndim != 1 or idx.dtype.kind not in "fiu":
            raise damaged(f"{what}: номера категорий должны быть одним столбцом чисел.")
        labels = legend_labels(f, data, what)
        if labels is None:
            return None
        with np.errstate(invalid="ignore"):
            valid = (idx >= 0) & (idx < len(labels))  # NaN is neither
        codes = np.full(len(idx), -1, dtype=np.int32)
        codes[valid] = idx[valid].astype(np.int32)
        return "category", codes, labels
    if cls in ("StringData", "DateTimeData"):
        values = f.array(data.get("array"), what, rows=n)
        if isinstance(values, np.ndarray):
            values = values.tolist()
        codes, labels = text_codes(values)
        return "category", codes, labels
    return None


def _xyz(f: OmfFile, uid: Any, what: str) -> np.ndarray:
    v = f.array(uid, what)
    if not isinstance(v, np.ndarray) or v.ndim != 2 or v.shape[1] != 3 or v.dtype.kind not in "fiu":
        raise damaged(f"{what}: вершины должны быть тройками чисел (Vector3Array).")
    return v.astype(np.float64, copy=False)


def _indices(f: OmfFile, uid: Any, width: int, what: str) -> np.ndarray:
    t = f.array(uid, what)
    if not isinstance(t, np.ndarray) or t.ndim != 2 or t.shape[1] != width or t.dtype.kind not in "iu":
        raise damaged(f"{what}: номера вершин должны быть целыми числами по {width}.")
    return t


def _placed(xyz: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """The array + origin in float64; a zero origin keeps the array bit for bit."""
    return xyz + origin if origin.any() else xyz


class OmfReader:
    def __init__(self, path: str | Path, progress: Progress | None = None) -> None:
        self.file = open_omf(path)
        try:
            self.composition: OmfComposition = compose(self.file)
            self.base = np.asarray(self.file.origin(), dtype=np.float64)
        except BaseException:
            self.file.close()
            raise
        self.progress: Progress = progress or no_progress
        self.crs = self.composition.crs
        self.left_out: list[dict[str, Any]] = self.composition.left_out()
        self.warnings: list[str] = self.composition.warnings()
        self.unread: list[dict[str, Any]] = []
        self._label = ""
        self._layer_names: set[str] = set()

    def __enter__(self) -> OmfReader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.file.close()

    def _note(self, e: OmfEntry, what: str, reason: str, count: int = 1) -> None:
        self.left_out.append({"key": e.key, "element": e.name, "kind": e.kind, "what": what, "count": count,
                              "reason": reason})  # fmt: skip
        self.warnings.append(f"{e.label}: {what} — {reason}")

    # --- choosing and weighing ------------------------------------------------------------------------

    def entries(self, kinds: Container[str] | None = None) -> list[OmfEntry]:
        """Elements that load (of these kinds), in the order of the file."""
        return [e for e in self.composition.elements if e.loads and (kinds is None or e.kind in kinds)]

    def weight(self, e: OmfEntry) -> int:
        """Compressed bytes reading the element inflates (shares of progress; a volume — its cell data)."""
        f = self.file
        try:
            element = f.objects[e.key]
            geometry = f.get(element.get("geometry"), e.label)
            uids = [geometry.get(k) for k in ("vertices", "triangles", "segments", "offset_w")]
            uids += [f.objects[d.key].get("array") for d in e.data if d.loads is not False]
            blocks = [f.block_of(u, e.label) for u in uids if isinstance(u, str)]
        except (FormatError, KeyError):
            return 0
        return sum(b.length for b in blocks if isinstance(b, Block))

    # --- reading --------------------------------------------------------------------------------------

    def elements(
        self, keys: Container[str] | None = None, kinds: Container[str] | None = None, skip_bad: bool = False
    ) -> Iterator[MeshSet | LineSet | OmfVolume]:
        """The elements that load (of `keys`, of `kinds`), one at a time. A volume is given unread."""
        todo = [e for e in self.entries(kinds) if keys is None or e.key in keys]
        weights = [0 if e.kind == "volume" else self.weight(e) for e in todo]
        total = max(sum(weights), 1)
        start = self.file.read_bytes
        self.file.tick = lambda b: self.progress(min(1.0, (b - start) / total), self._label)
        for e in todo:
            self._label = f"Чтение: {e.label}"
            self.progress(min(1.0, (self.file.read_bytes - start) / total), self._label)
            try:
                obj = self.read(e)
            except FormatError as exc:
                if not skip_bad:
                    raise
                self.unread.append({"key": e.key, "name": e.name, "kind": e.kind, "error": exc.as_dict()})
                self._note(e, "элемент", f"не загружен: {exc.message}")
                continue
            if obj is not None:
                yield obj
        self.file.tick = None
        self.progress(1.0, f"Прочитано элементов OMF: {n_text(len(todo))}")

    def surfaces(self, keys: Container[str] | None = None, skip_bad: bool = False) -> Iterator[MeshSet]:
        for obj in self.elements(keys, ("surface",), skip_bad):
            assert isinstance(obj, MeshSet)
            yield obj

    def line_layers(self, keys: Container[str] | None = None, skip_bad: bool = False) -> Iterator[LineSet]:
        for obj in self.elements(keys, ("lines",), skip_bad):
            assert isinstance(obj, LineSet)
            yield obj

    def volumes(self, keys: Container[str] | None = None) -> list[OmfVolume]:
        return [v for v in self.elements(keys, ("volume",)) if isinstance(v, OmfVolume)]

    def read(self, e: OmfEntry) -> MeshSet | LineSet | OmfVolume | None:
        """One element; None — nothing to load (a note in `left_out`)."""
        element = self.file.get(e.key, e.label)
        geometry = self.file.get(element.get("geometry"), f"{e.label}: геометрия")
        origin = self.base + np.asarray(vector(geometry.get("origin"), f"{e.label}: начало координат", (0, 0, 0)))
        try:
            if e.kind == "surface":
                return self._surface(e, element, geometry, origin)
            if e.kind == "lines":
                return self._lines(e, geometry, origin)
        except MemoryError:
            raise out_of_memory(f"{e.label}: не хватило памяти на элемент.") from None
        if e.kind == "volume":
            data = [(d, self.file.objects[d.key]) for d in e.data if d.loads]
            return OmfVolume(e, origin, geometry, data)
        return None

    # --- surfaces -------------------------------------------------------------------------------------

    def _surface(
        self, e: OmfEntry, element: dict[str, Any], geometry: dict[str, Any], origin: np.ndarray
    ) -> MeshSet | None:
        f = self.file
        notes: list[str] = []
        if e.geometry == "SurfaceGridGeometry":
            xyz, tri, holes = _grid(f, geometry, origin, e.label)
            if holes:
                xyz, tri, _dropped = drop_unused(xyz, tri)
                notes.append(f"узлов грида без высоты: {n_text(holes)} — треугольники с ними не загружены")
        else:
            xyz = _placed(_xyz(f, geometry.get("vertices"), f"{e.label}: вершины"), origin)
            tri = _indices(f, geometry.get("triangles"), 3, f"{e.label}: треугольники")
        for note in notes:
            self._note(e, "геометрия", note)
        if not len(tri):
            self._note(e, "элемент", "в поверхности нет треугольников — не загружена")
            return None
        raw = element.get("name") if isinstance(element.get("name"), str) else None
        source = source_of(FORMAT, raw, e.name, element=e.key)
        if e.dtm:
            source["dtm"] = True
        text = plain_description(e.description)
        attrs = [{"name": "Описание", "value": text[:2000]}] if text else []
        return MeshSet(e.name, xyz, tri, e.color, attrs, source)

    # --- lines ----------------------------------------------------------------------------------------

    def _lines(self, e: OmfEntry, geometry: dict[str, Any], origin: np.ndarray) -> LineSet | None:
        f = self.file
        vertices = _placed(_xyz(f, geometry.get("vertices"), f"{e.label}: вершины"), origin)
        seg = _indices(f, geometry.get("segments"), 2, f"{e.label}: сегменты").astype(np.int64, copy=False)
        if len(seg) and (int(seg.min()) < 0 or int(seg.max()) >= len(vertices)):
            raise damaged(f"{e.label}: номера вершин сегментов вне списка вершин.")
        if not len(seg):
            self._note(e, "элемент", "в элементе нет сегментов — слой не загружен")
            return None
        # Runs of segments where each one starts at the end of the previous one: a line each.
        breaks = np.flatnonzero(seg[1:, 0] != seg[:-1, 1]) + 1
        starts = np.concatenate([[0], breaks]).astype(np.int64)
        ends = np.concatenate([breaks, [len(seg)]]).astype(np.int64)
        idx = np.insert(seg[:, 1], starts, seg[starts, 0])  # first vertex of a run, then the end of every segment
        offsets = np.concatenate([starts + np.arange(len(starts)), [len(seg) + len(starts)]]).astype(np.int64)
        used = np.zeros(len(vertices), dtype=bool)
        used[idx] = True
        unused = len(vertices) - int(np.count_nonzero(used))
        if unused:
            self._note(e, "вершины", f"{n_text(unused)} вершин без сегментов не загружены", unused)
        xyz = vertices[idx]
        attrs, units = self._line_attrs(e, len(seg), starts, ends)
        name = unique_name(e.name, self._layer_names)
        closed = detect_closed(xyz, offsets)
        return LineSet(name, xyz, offsets, closed, attrs=attrs or None, color=e.color, units=units)

    def _line_attrs(
        self, e: OmfEntry, n: int, starts: np.ndarray, ends: np.ndarray
    ) -> tuple[dict[str, Any], dict[str, str]]:
        attrs: dict[str, Any] = {}
        units: dict[str, str] = {}
        taken: set[str] = set()
        for d in e.data:
            if d.loads is not None:  # on vertices, of another kind: listed by the composition
                continue
            col = column(self.file, self.file.objects[d.key], n, f"{e.label}: данные «{d.name}»")
            if col is None:
                self._note(e, f"данные «{d.name}»", "у данных нет легенды с подписями (только цвета)")
                continue
            kind, values, labels = col
            per_line = _constant(values, starts, ends, kind)
            if per_line is None:
                self._note(e, f"данные «{d.name}»", "значения меняются вдоль линии — у линии BF2 только атрибуты "
                           "всей линии")  # fmt: skip
                continue
            name = unique_name(d.name, taken)
            if kind == "number":
                attrs[name] = per_line
            else:
                assert labels is not None
                attrs[name] = [labels[c] if c >= 0 else None for c in per_line.tolist()]
            if d.unit:
                units[name] = d.unit
        return attrs, units


def _constant(values: np.ndarray, starts: np.ndarray, ends: np.ndarray, kind: str) -> np.ndarray | None:
    """The value of every run when it is the same along the run (numbers: all empty counts as one), else None."""
    if kind == "number":
        empty = np.isnan(values)
        lo = np.minimum.reduceat(np.where(empty, np.inf, values), starts)
        hi = np.maximum.reduceat(np.where(empty, -np.inf, values), starts)
        n_empty = np.add.reduceat(empty.astype(np.int64), starts)
        size = ends - starts
        ok = ((n_empty == 0) & (lo == hi)) | (n_empty == size)
        return values[starts] if bool(ok.all()) else None
    lo = np.minimum.reduceat(values, starts)
    hi = np.maximum.reduceat(values, starts)
    return values[starts] if bool((lo == hi).all()) else None


def _grid(f: OmfFile, geometry: dict[str, Any], origin: np.ndarray, label: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Nodes and triangles of a SurfaceGridGeometry (u fastest) and the number of nodes without `offset_w`."""
    tu = tensor(geometry.get("tensor_u"), f"{label}: шаги u")
    tv = tensor(geometry.get("tensor_v"), f"{label}: шаги v")
    au = np.asarray(vector(geometry.get("axis_u"), f"{label}: ось u", (1.0, 0.0, 0.0)))
    av = np.asarray(vector(geometry.get("axis_v"), f"{label}: ось v", (0.0, 1.0, 0.0)))
    nu_, nv_ = float(np.linalg.norm(au)), float(np.linalg.norm(av))
    if nu_ == 0 or nv_ == 0:
        raise damaged(f"{label}: оси грида не могут быть нулевыми.")
    au, av = au / nu_, av / nv_
    if abs(float(au @ av)) > 1e-6:
        raise damaged(f"{label}: оси грида не перпендикулярны.")
    aw = np.cross(au, av)
    nu, nv = len(tu) + 1, len(tv) + 1
    u = np.concatenate([[0.0], np.cumsum(tu)])
    v = np.concatenate([[0.0], np.cumsum(tv)])
    uu = np.tile(u, nv)
    vv = np.repeat(v, nu)
    xyz = origin + uu[:, None] * au + vv[:, None] * av
    holes = 0
    hole: np.ndarray | None = None
    if geometry.get("offset_w") is not None:
        w = f.array(geometry.get("offset_w"), f"{label}: offset_w", rows=nu * nv)
        if not isinstance(w, np.ndarray) or w.ndim != 1 or w.dtype.kind not in "fiu":
            raise damaged(f"{label}: offset_w должен быть одним столбцом чисел.")
        w = w.astype(np.float64, copy=False)
        hole = ~np.isfinite(w)
        holes = int(np.count_nonzero(hole))
        xyz = xyz + np.where(hole, 0.0, w)[:, None] * aw
    i = np.tile(np.arange(nu - 1, dtype=np.int64), nv - 1)
    j = np.repeat(np.arange(nv - 1, dtype=np.int64), nu - 1)
    a = i + j * nu
    b, c = a + 1, a + nu
    d = c + 1
    tri = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])])
    if holes and hole is not None:
        tri = tri[~hole[tri].any(axis=1)]
    return np.ascontiguousarray(xyz), np.ascontiguousarray(tri, dtype=np.int32), holes


def open_reader(path: str | Path, progress: Progress | None = None) -> OmfReader:
    return OmfReader(path, progress)
