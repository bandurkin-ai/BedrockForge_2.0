"""OMF v1 writer (ТЗ 7.24; ADR 0017 §4): wireframes, layers of lines and regular block models into one file that
Leapfrog (OMF 0.9 / 1.0) and `omf_read` read.

    with OmfWriter(path, project_name, description, crs={"name", "epsg"}) as w:
        w.add_mesh(mesh, dtm=False)          # or add_surface(name, xyz, tri, color, description, dtm)
        warnings = w.add_lines(layer)        # a LineSet
        w.add_volume(name, grid, fields, cells, color)
    # → path (written as «path.part», renamed at the end: a failure or a cancel leaves neither)

Written as omf 1.0 does: the binary blocks as they come (zlib level 3, in portions of 16 MiB of input), then
the JSON, then its offset in the header. Every origin is zero and coordinates are float64 (`<f8`) as given, so a
file read back gives the same numbers bit for bit; indices are `<i8`.

* A wireframe — `SurfaceElement` + `SurfaceGeometry`; its attribute «Описание» is the description; a ЦТМ gets the
  line «BedrockForge: ЦТМ» (ADR 0017 решение 8).
* A layer — `LineSetElement`: the vertices of its lines one after another, a segment between neighbours; a closed
  line whose last vertex is its first bit for bit is written as a loop to the first vertex (the repeat is left out
  and comes back on reading), otherwise its last vertex stays as it is. Attributes of a line go on all its
  segments: numbers — ScalarData (NaN empty), texts — MappedData with a legend of the texts (−1 empty); the unit —
  «Единицы: …» in the description. Not written (warnings): lines of one vertex, lines with a vertex without Z (an
  empty Z never becomes 0), layers of points, vertex attributes.
* A block model — `VolumeElement` + `VolumeGridGeometry` with `origin` = the corner, axes +X, +Y, +Z and the steps
  as tensors; every field densely (n cells, Fortran order = `idx`) in portions of `VOLUME_PORTION` cells, filled
  from the occupied cells in ascending `idx` (`cells(name)` → (idx, values) portions, as `blocks.store.iter_cells`);
  an empty cell is NaN (numbers) or −1 (categories, MappedData with the labels).
* The project — its name, `units: "m"`, the description with «Система координат: ‹имя› (EPSG:…)».

`tick(bytes)` is called after every portion with the uncompressed bytes written so far; it may raise to cancel.
A colour the object does not have is written grey (OMF wants one). Ported from BF `formats/omf.py::OmfWriter`.
"""

from __future__ import annotations

import json
import os
import struct
import uuid
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.meshset import MeshSet, n_text
from bf_core.formats.omf_file import CHUNK, CRS_PREFIX, DTM_MARK, HEADER_SIZE, MAGIC, UNIT_PREFIX, VERSION

VOLUME_PORTION = 4_000_000
ZLIB_LEVEL = 3  # 1M triangles: 0.9 s at the size of level 6 (2.3 s); level 1: 0.5 s and +6 %
GREY = [128, 128, 128]
Cells = Callable[[str], Iterable[tuple[Any, Any]]]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rgb(color: str | None) -> list[int]:
    if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
        try:
            return [int(color[i : i + 2], 16) for i in (1, 3, 5)]
        except ValueError:
            pass
    return list(GREY)


def crs_line(crs: Mapping[str, Any] | None) -> str | None:
    """«Система координат: ‹имя› (EPSG:…)» of a CRS {name, epsg}; None — no CRS."""
    if not crs:
        return None
    name = " ".join(str(crs.get("name") or "").split())
    epsg = crs.get("epsg")
    tail = f" (EPSG:{int(epsg)})" if epsg not in (None, "") else ""
    return f"{CRS_PREFIX} {name}{tail}".rstrip() if name or tail else None


class OmfWriter:
    def __init__(
        self,
        path: str | Path,
        name: str,
        description: str = "",
        crs: Mapping[str, Any] | None = None,
        *,
        part: bool = True,
        tick: Callable[[int], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.target = self.path.with_name(self.path.name + ".part") if part else self.path
        self.tick = tick
        self.written = 0  # uncompressed bytes of the blocks
        self.warnings: list[str] = []
        self._objects: dict[str, dict[str, Any]] = {}
        self._elements: list[str] = []
        self._uid = uuid.uuid4()
        self._name = name
        self._description = "\n".join(x for x in (description.strip(), crs_line(crs)) if x)
        self._f: BinaryIO = self.target.open("wb")
        self._f.write(b"\x00" * HEADER_SIZE)

    def __enter__(self) -> OmfWriter:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, _e: BaseException | None, _tb: TracebackType | None
    ) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()

    @property
    def element_count(self) -> int:
        return len(self._elements)

    # --- objects and blocks ---------------------------------------------------------------------------

    def _add(self, cls: str, **fields: Any) -> str:
        uid = str(uuid.uuid4())
        now = _now()
        self._objects[uid] = {"date_created": now, "date_modified": now, **fields, "__class__": cls}
        return uid

    def _content(self, cls: str, name: str, description: str = "", **fields: Any) -> str:
        return self._add(cls, name=name, description=description, **fields)

    def _block(self, parts: Iterable[np.ndarray], dtype: str) -> dict[str, Any]:
        """One zlib stream of the arrays given (each converted to `dtype`), portion by portion."""
        start = self._f.tell()
        c = zlib.compressobj(ZLIB_LEVEL)
        step = max(CHUNK // np.dtype(dtype).itemsize, 1)
        for part in parts:
            flat = np.asarray(part).reshape(-1)
            for s in range(0, len(flat), step):
                piece = np.ascontiguousarray(flat[s : s + step], dtype=dtype)
                self._f.write(c.compress(memoryview(piece).cast("B")))
                self.written += piece.nbytes
                if self.tick is not None:
                    self.tick(self.written)
        self._f.write(c.flush())
        return {"start": start, "dtype": dtype, "length": self._f.tell() - start}

    def _array(self, cls: str, values: Any, dtype: str) -> str:
        parts = values if isinstance(values, Iterator) else [values]
        return self._add(cls, array=self._block(parts, dtype))

    def _scalar(self, name: str, values: Any, location: str, unit: str | None = None) -> str:
        return self._content("ScalarData", name, f"{UNIT_PREFIX} {unit}" if unit else "", location=location,
                             array=self._array("ScalarArray", values, "<f8"))  # fmt: skip

    def _mapped(self, name: str, codes: Any, labels: Sequence[str], location: str, unit: str | None = None) -> str:
        values = self._add("StringArray", array=[str(x) for x in labels])
        legend = self._content("Legend", name, values=values)
        return self._content("MappedData", name, f"{UNIT_PREFIX} {unit}" if unit else "", location=location,
                             array=self._array("ScalarArray", codes, "<i8"), legends=[legend])  # fmt: skip

    def _element(self, cls: str, name: str, geometry: str, color: str | None, subtype: str, data: list[str],
                 description: str = "", textures: bool = False) -> None:  # fmt: skip
        fields: dict[str, Any] = {"data": data, "color": rgb(color), "subtype": subtype, "geometry": geometry}
        if textures:
            fields["textures"] = []
        self._elements.append(self._content(cls, name, description, **fields))

    # --- surfaces -------------------------------------------------------------------------------------

    def add_surface(self, name: str, xyz: Any, tri: Any, color: str | None = None, description: str = "",
                    dtm: bool = False) -> None:  # fmt: skip
        v = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
        t = np.asarray(tri).reshape(-1, 3)
        geometry = self._add("SurfaceGeometry", origin=[0.0, 0.0, 0.0], vertices=self._array("Vector3Array", v, "<f8"),
                             triangles=self._array("Int3Array", t, "<i8"))  # fmt: skip
        text = "\n".join(x for x in (description.strip(), DTM_MARK if dtm else "") if x)
        self._element("SurfaceElement", name, geometry, color, "surface", [], text, textures=True)

    def add_mesh(self, mesh: MeshSet, dtm: bool = False) -> None:
        """A wireframe; its attribute «Описание» (a text) is the description of the element."""
        text = next((str(a["value"]) for a in mesh.attrs if a["name"] == "Описание" and a["value"] is not None), "")
        self.add_surface(mesh.name, mesh.xyz, mesh.tri, mesh.color, text, dtm or bool(mesh.source.get("dtm")))

    # --- lines ----------------------------------------------------------------------------------------

    def add_lines(self, ls: LineSet) -> list[str]:
        """A layer as one LineSetElement; returns (and keeps in `warnings`) what was not written."""
        notes: list[str] = []
        label = f"Линии «{ls.name}»"
        if ls.points_only or ls.offsets is None or ls.closed is None:
            notes.append(f"{label}: слой точек не записывается в OMF (точки не переносятся)")
            self.warnings += notes
            return notes
        offsets, closed, xyz = ls.offsets, ls.closed, ls.xyz
        size = np.diff(offsets)
        nan_z = np.zeros(len(size), dtype=bool)
        if len(size):
            nan_z = np.add.reduceat(np.isnan(xyz[:, 2]).astype(np.int64), offsets[:-1]) > 0
        first, last = offsets[:-1], offsets[1:] - 1
        loop = (closed == 1) & np.all(xyz[first].view(np.uint64) == xyz[last].view(np.uint64), axis=1)
        keep = (size >= 2) & ~nan_z
        if int((size < 2).sum()):
            notes.append(f"{label}: линий из одной точки {n_text(int((size < 2).sum()))} — не записаны "
                         "(в OMF линия — это сегменты)")  # fmt: skip
        if int(((size >= 2) & nan_z).sum()):
            notes.append(f"{label}: линий с вершинами без Z {n_text(int(((size >= 2) & nan_z).sum()))} — не записаны")
        if ls.vattrs is not None and ls.vattrs.num_columns:
            notes.append(f"{label}: атрибуты вершин не записываются (в OMF — только атрибуты линий)")
        lines = np.flatnonzero(keep)
        if not len(lines):
            notes.append(f"{label}: нет линий из двух и более точек с Z — слой не записан")
            self.warnings += notes
            return notes
        count = size[lines] - loop[lines]  # vertices written: the repeat of a loop is left out
        new_off = np.concatenate([[0], np.cumsum(count)]).astype(np.int64)
        total = int(new_off[-1])
        take = np.repeat(first[lines] - new_off[:-1], count) + np.arange(total, dtype=np.int64)
        starts = np.ones(total, dtype=bool)
        starts[new_off[1:] - 1] = False  # the last vertex of a line starts no segment
        a = np.flatnonzero(starts)
        segs = np.column_stack([a, a + 1])
        loops = np.flatnonzero(loop[lines])
        if len(loops):
            ends = np.column_stack([new_off[1:][loops] - 1, new_off[:-1][loops]])
            seg_line = np.concatenate([np.repeat(np.arange(len(lines)), count - 1), loops])
            segs = np.concatenate([segs, ends])
            order = np.argsort(seg_line, kind="stable")  # a loop's closing segment right after its others
            segs, seg_line = segs[order], seg_line[order]
        else:
            seg_line = np.repeat(np.arange(len(lines)), count - 1)
        data = self._line_data(ls, lines[seg_line])
        geometry = self._add("LineSetGeometry", origin=[0.0, 0.0, 0.0],
                             vertices=self._array("Vector3Array", xyz[take], "<f8"),
                             segments=self._array("Int2Array", segs, "<i8"))  # fmt: skip
        self._element("LineSetElement", ls.name, geometry, ls.color, "line", data)
        self.warnings += notes
        return notes

    def _line_data(self, ls: LineSet, owner: np.ndarray) -> list[str]:
        """Attributes of the lines on their segments (`owner` — the line of every segment)."""
        import pyarrow as pa

        out: list[str] = []
        if ls.attrs is None:
            return out
        for name, col in zip(ls.attrs.column_names, ls.attrs.columns, strict=True):
            unit = ls.units.get(name)
            if pa.types.is_floating(col.type):
                values = col.to_numpy(zero_copy_only=False).astype(np.float64)
                out.append(self._scalar(name, values[owner], "segments", unit))
            else:
                texts = col.to_pylist()
                labels = sorted({str(t) for t in texts if t is not None})
                pos = {s: i for i, s in enumerate(labels)}
                codes = np.array([-1 if t is None else pos[str(t)] for t in texts], dtype=np.int64)
                out.append(self._mapped(name, codes[owner], labels, "segments", unit))
        return out

    # --- block models ---------------------------------------------------------------------------------

    def add_volume(self, name: str, grid: Mapping[str, Any], fields: Sequence[Mapping[str, Any]], cells: Cells,
                   color: str | None = None, description: str = "") -> None:  # fmt: skip
        """A regular block model: `grid` and `fields` as `objects.meta` (`blocks/schema.py`), `cells(name)` — the
        occupied cells of one field in ascending idx: (idx, values) portions. Cells out of order, of other sizes or
        a file that cannot be read (pyarrow's `ArrowInvalid` is a ValueError) — FormatError `block_model_broken`."""
        counts = [int(c) for c in grid["counts"]]
        n = counts[0] * counts[1] * counts[2]
        if "tensors" in grid:
            tensors = [[float(x) for x in grid["tensors"][a]] for a in "xyz"]
        else:
            tensors = [[float(grid["size"][k])] * counts[k] for k in range(3)]
        data: list[str] = []
        for f in fields:
            fname, unit = str(f["name"]), f.get("unit")
            try:
                if f.get("kind", "number") == "number":
                    dense = _dense(n, np.nan, np.float64, cells(fname))
                    data.append(self._scalar(fname, dense, "cells", unit))
                else:
                    dense = _dense(n, -1, np.int64, cells(fname))
                    data.append(self._mapped(fname, dense, list(f.get("labels") or []), "cells", unit))
            except (ValueError, IndexError, KeyError) as exc:
                raise FormatError(
                    "block_model_broken",
                    f"Блочная модель «{name}»: файл ячеек повреждён (поле «{fname}») — модель не записана.",
                    "Загрузите модель в проект заново.",
                ) from exc
        geometry = self._add("VolumeGridGeometry", origin=[float(c) for c in grid["corner"]],
                             tensor_u=tensors[0], tensor_v=tensors[1], tensor_w=tensors[2],
                             axis_u=[1.0, 0.0, 0.0], axis_v=[0.0, 1.0, 0.0], axis_w=[0.0, 0.0, 1.0])  # fmt: skip
        self._element("VolumeElement", name, geometry, color, "volume", data, description)

    # --- the end --------------------------------------------------------------------------------------

    def close(self) -> Path:
        """Writes the JSON and the header, renames «.part» to the file. Returns the path of the file."""
        if self._f.closed:
            return self.path
        try:
            now = _now()
            self._objects[str(self._uid)] = {
                "date_created": now, "date_modified": now, "name": self._name, "description": self._description,
                "author": "", "revision": "", "date": now, "units": "m", "elements": self._elements,
                "origin": [0.0, 0.0, 0.0], "__class__": "Project",
            }  # fmt: skip
            json_start = self._f.tell()
            self._f.write(json.dumps(self._objects, ensure_ascii=False).encode("utf-8"))
            self._f.seek(0)
            self._f.write(MAGIC + struct.pack("<32s", VERSION) + self._uid.bytes + struct.pack("<Q", json_start))
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            if self.target != self.path:
                os.replace(self.target, self.path)
        except BaseException:
            self.abort()
            raise
        return self.path

    def abort(self) -> None:
        """Closes and removes what was written (the «.part», or the file itself without one)."""
        if not self._f.closed:
            self._f.close()
        self.target.unlink(missing_ok=True)


def _dense(n: int, fill: Any, dtype: Any, batches: Iterable[tuple[Any, Any]],
           portion: int = VOLUME_PORTION) -> Iterator[np.ndarray]:  # fmt: skip
    """Dense values of n cells in portions, from the occupied cells in ascending idx; the rest is `fill`."""
    buf = np.full(min(portion, n), fill, dtype=dtype)
    s, e = 0, min(n, portion)
    last = -1
    for idx_raw, values_raw in batches:
        idx = np.asarray(idx_raw, dtype=np.int64)
        values = np.asarray(values_raw)
        if len(idx) != len(values):
            raise ValueError("cells: as many values as cell numbers are expected")
        if len(idx) and (int(idx[0]) <= last or int(idx[-1]) >= n or bool((np.diff(idx) <= 0).any())):
            raise ValueError("cells: numbers must ascend within the grid")
        last = int(idx[-1]) if len(idx) else last
        pos = 0
        while pos < len(idx):
            j = pos + int(np.searchsorted(idx[pos:], e))
            buf[idx[pos:j] - s] = values[pos:j]
            pos = j
            if pos < len(idx):
                yield buf[: e - s]
                buf.fill(fill)
                s, e = e, min(n, e + portion)
    while s < n:
        yield buf[: e - s]
        buf.fill(fill)
        s, e = e, min(n, e + portion)
