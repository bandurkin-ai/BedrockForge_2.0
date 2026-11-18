"""A wireframe as every wireframe reader returns it and every wireframe writer takes it (ADR 0013 §2–3).

`MeshSet` — one wireframe of a file with its geometry:

* `xyz` — float64 N×3, the vertices of the file bit for bit; every coordinate is a number (a vertex without a
  coordinate is refused, never replaced by 0).
* `tri` — int32 M×3, vertex numbers from 0, the triangles **in the order of the file** and with the corners in
  their order. The store keeps them along a Z-order curve and the order of the file in the blob `seq`
  (`wireframes/source.py`), so a writer gets the file back triangle for triangle.
* `name` — cleaned for the project (spaces collapsed, ≤ 200 characters); the name exactly as the file has it
  is `source["name"]`. `color` — "#rrggbb" or None.
* `attrs` — [{name, value}] in the order of the file, as `wireframes.attrs`; a value is text, a number, yes/no
  or None (empty: an empty text and NaN are None, never "" or 0). `clean_attrs` makes them from what a file
  has and tells what it had to change.
* `source` — what goes to `objects.meta.source` (JSON values only): {format, name, …} and the rows of the
  file a writer needs to give the same file back (TRIDB: `tridb.py`). `name` is the name exactly as the file
  has it (None — the file has none); `as` — the name of the mesh that stands for it when it is not just the
  cleaned name (a fallback of the reader, a name taken in the project). `name_in_file` gives a writer the name
  of the file while the mesh is still called that.
* `warnings` — Russian texts for the log of the operation; `stats` — the measures when they are known
  (`geo.meshes.mesh_stats`), so a writer that stores an area or a volume does not compute them again.

`MeshEntry` — a wireframe in the composition of a file: no geometry, what the import wizard shows.

Coincident vertices are welded **exactly** (`weld_exact`, `ExactWelder`): vertices with equal float64 (−0.0 is
0.0) become one — the first of them in the order given, bit for bit — so the geometry does not change and a
cycle import → export → import stays bit for bit. Welding with a tolerance is «Исправить» (Э9). The welder
takes the corners in portions and keeps only the different vertices and its hash table, so a soup of 30 million
corners (3DFACE, STL) is never in memory twice.

Ported from BF `formats/wireframes.py` (`Mesh`, `weld`, `_validate`). Differences: the weld is exact and keeps
the order of first appearance (BF rounded to 1e-6 and sorted), attributes are an ordered list with empty values,
the order of the file and the source rows are part of the model.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

MAX_NAME = 200
MAX_ATTRS = 200  # the limits of `wireframes.store` (a test keeps them equal)
MAX_ATTR_NAME = 100
MAX_ATTR_TEXT = 2000
WELD_SLICE = 1 << 20  # corners welded at once: bounds the growth of the table (≈ 32 MB a slice)
_COLOR = re.compile(r"#[0-9a-f]{6}")
_HINT = "Проверьте исходный файл каркаса."


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def clean_name(value: Any, fallback: str = "Каркас") -> str:
    """A name as the project stores it: spaces collapsed, at most `MAX_NAME` characters, never empty."""
    return " ".join(str(value if value is not None else "").split())[:MAX_NAME] or fallback


def source_of(fmt: str, raw_name: Any, name: str, **more: Any) -> dict[str, Any]:
    """`MeshSet.source` of a wireframe a reader named `name`: {format, name as the file has it, as?, …}."""
    raw = raw_name if isinstance(raw_name, str) else None
    out: dict[str, Any] = {"format": fmt, "name": raw}
    if raw is None or clean_name(raw) != name:
        out["as"] = name
    out.update(more)
    return out


def name_in_file(mesh: MeshSet) -> str | None:
    """The name a writer puts into a file: the one the source file had (None — it had none) while the mesh is
    called as the import called it, else the name of the mesh."""
    source = mesh.source
    if "name" in source:
        raw = source["name"]
        stands = source.get("as", clean_name(raw) if isinstance(raw, str) else None)
        if mesh.name == stands and (raw is None or isinstance(raw, str)):
            return raw
    return mesh.name


def name_key(name: str) -> str:
    """The key names are compared by, as `db.dialect.name_key`: NFC and no case."""
    return unicodedata.normalize("NFC", name.strip()).casefold()


def attr_value(value: Any) -> Any:
    """A value of an attribute: text (stripped; empty → None), int, float (NaN and ±inf → None), bool or None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value.strip() or None
    raise FormatError("bad_attrs", "Значение атрибута каркаса — текст, число, да/нет или пусто.", _HINT)


def clean_attrs(pairs: Iterable[tuple[Any, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Attributes (name, value) of a file as the model and the store take them, and warnings about what had to
    change: a repeated name gets « (2)», an empty one — «Атрибут N», long names and texts are cut, attributes
    over `MAX_ATTRS` are left out."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    unnamed = renamed = long_names = long_texts = dropped = 0
    for raw_name, raw in pairs:
        if len(out) >= MAX_ATTRS:
            dropped += 1
            continue
        name = " ".join(str(raw_name if raw_name is not None else "").split())
        if not name:
            name, unnamed = f"Атрибут {len(out) + 1}", unnamed + 1
        if len(name) > MAX_ATTR_NAME:
            name, long_names = name[:MAX_ATTR_NAME], long_names + 1
        base, k = name, 1
        while name_key(name) in seen:
            k += 1
            suffix = f" ({k})"
            name = base[: MAX_ATTR_NAME - len(suffix)] + suffix
        renamed += k > 1
        seen.add(name_key(name))
        value = attr_value(raw)
        if isinstance(value, str) and len(value) > MAX_ATTR_TEXT:
            value, long_texts = value[:MAX_ATTR_TEXT], long_texts + 1
        out.append({"name": name, "value": value})
    warnings: list[str] = []
    if unnamed:
        warnings.append(f"Атрибуты без названия: {n_text(unnamed)} — названы «Атрибут N»")
    if renamed:
        warnings.append(f"Атрибуты с одинаковыми названиями: {n_text(renamed)} — к названию добавлен номер")
    if long_names:
        warnings.append(f"Названия атрибутов длиннее {MAX_ATTR_NAME} знаков: {n_text(long_names)} — сокращены")
    if long_texts:
        warnings.append(f"Значения атрибутов длиннее {MAX_ATTR_TEXT} знаков: {n_text(long_texts)} — сокращены")
    if dropped:
        warnings.append(f"Атрибутов больше {MAX_ATTRS}: {n_text(dropped)} не загружено")
    return out, warnings


def _checked_attrs(raw: Any) -> list[dict[str, Any]]:
    """Attributes given to a `MeshSet`: [{name, value}] with names that are not empty and differ."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in raw or []:
        if not isinstance(a, Mapping) or "name" not in a:
            raise FormatError("bad_attrs", "Атрибут каркаса — это название и значение.", _HINT)
        name = " ".join(str(a["name"] if a["name"] is not None else "").split())
        if not name or len(name) > MAX_ATTR_NAME or name_key(name) in seen:
            raise FormatError(
                "bad_attrs",
                f"Названия атрибутов каркаса должны быть непустыми, не длиннее {MAX_ATTR_NAME} знаков и разными"
                f" (атрибут «{name[:40]}»).",
                _HINT,
            )
        seen.add(name_key(name))
        out.append({"name": name, "value": attr_value(a.get("value"))})
    if len(out) > MAX_ATTRS:
        raise FormatError("bad_attrs", f"Атрибутов каркаса больше {MAX_ATTRS}.", _HINT)
    return out


@dataclass
class MeshSet:
    name: str
    xyz: np.ndarray
    tri: np.ndarray
    color: str | None = None
    attrs: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        from bf_core.geo.meshes import MeshError, check_mesh

        self.name = clean_name(self.name)
        try:
            self.xyz, self.tri = check_mesh(self.xyz, self.tri)
        except MeshError as exc:
            raise FormatError(exc.code, f"Каркас «{self.name}»: {exc.message}", exc.hint or _HINT) from exc
        color = str(self.color).strip().lower() if self.color is not None else ""
        self.color = color if _COLOR.fullmatch(color) else None
        self.attrs = _checked_attrs(self.attrs)
        self.source = dict(self.source or {})
        self.warnings = list(self.warnings or [])

    @property
    def point_count(self) -> int:
        return int(len(self.xyz))

    @property
    def triangle_count(self) -> int:
        return int(len(self.tri))

    def extent(self) -> list[float]:
        """[xmin, ymin, zmin, xmax, ymax, zmax] of the vertices (all of them: a file may have unused ones)."""
        lo, hi = self.xyz.min(axis=0), self.xyz.max(axis=0)
        return [float(lo[0]), float(lo[1]), float(lo[2]), float(hi[0]), float(hi[1]), float(hi[2])]


@dataclass
class MeshEntry:
    """A wireframe in the composition of a file. `key` — what the reader takes to read it (TRIDB: its ID);
    `points` / `triangles` — None when the file does not tell without reading the geometry; `error` —
    {code, message, hint} of a wireframe that cannot be read (the others of the file can)."""

    key: int | str
    name: str
    points: int | None
    triangles: int | None
    color: str | None = None
    attrs: list[dict[str, Any]] = field(default_factory=list)
    extent: list[float] | None = None
    warnings: list[str] = field(default_factory=list)
    error: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "points": self.points, "triangles": self.triangles,
            "color": self.color, "attrs": self.attrs, "extent": self.extent, "warnings": self.warnings,
            "error": self.error,
        }  # fmt: skip


# --- exact welding ---------------------------------------------------------------------------------------


class ExactWelder:
    """Numbers vertices given in portions: equal float64 triples (−0.0 = 0.0) get one number, in the order of
    first appearance. Memory: the different vertices (24 bytes each, the store doubles) and a table of int32
    slots (at least two a vertex)."""

    def __init__(self, capacity: int = 1 << 16) -> None:
        self.count = 0
        self._verts = np.empty((max(int(capacity), 1), 3), dtype=np.uint64)
        self._table = np.full(4, -1, dtype=np.int32)

    def _room(self, k: int) -> None:
        from bf_core.formats import _weldkernel

        need = self.count + k
        if need > np.iinfo(np.int32).max:
            raise FormatError(
                "too_many_vertices",
                f"В каркасе больше {n_text(int(np.iinfo(np.int32).max))} вершин.",
                "Разделите каркас на несколько объектов.",
            )
        if need > len(self._verts):
            grown = np.empty((max(need, 2 * len(self._verts)), 3), dtype=np.uint64)
            grown[: self.count] = self._verts[: self.count]
            self._verts = grown
        if 2 * need > len(self._table):
            self._table = np.full(1 << (2 * need - 1).bit_length(), -1, dtype=np.int32)  # 2–4 slots a vertex
            _weldkernel.rehash(self._table, self._verts, self.count)

    def add(self, xyz: Any) -> np.ndarray:
        """int32 per row of `xyz` (k×3): the number of its vertex."""
        from bf_core.formats import _weldkernel

        bits = np.ascontiguousarray(xyz, dtype=np.float64).reshape(-1, 3).view(np.uint64)
        out = np.empty(len(bits), dtype=np.int32)
        if len(bits):
            self._room(len(bits))
            self.count = int(_weldkernel.weld_chunk(bits, self._table, self._verts, self.count, out))
        return out

    def vertices(self) -> np.ndarray:
        """The vertices kept: float64 count×3, each as it was first given."""
        return np.ascontiguousarray(self._verts[: self.count]).view(np.float64)


def weld_exact(xyz: Any, tri: Any, progress: Progress | None = None) -> tuple[np.ndarray, np.ndarray, int]:
    """Weld coincident vertices exactly. Returns (vertices, triangles int32, merged): the first vertex of every
    group stays, in the order given; the triangles and their order do not change (a triangle whose corners
    became one vertex stays too: it is counted as degenerate by the measures). Nothing to merge — the arrays
    given come back. `progress(fraction, message)` may raise to cancel."""
    tick = progress or no_progress
    v = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    n = len(v)
    welder = ExactWelder(capacity=min(max(n // 4, 1), WELD_SLICE))
    index = np.empty(n, dtype=np.int32)
    for start in range(0, n, WELD_SLICE):
        index[start : start + WELD_SLICE] = welder.add(v[start : start + WELD_SLICE])
        tick(min(1.0, (start + WELD_SLICE) / n), "Сшивка совпадающих вершин")
    if welder.count == n:
        return xyz, tri, 0
    return welder.vertices(), np.ascontiguousarray(index[np.asarray(tri)], dtype=np.int32), n - welder.count


def drop_unused(xyz: Any, tri: Any) -> tuple[np.ndarray, np.ndarray, int]:
    """Leave out the vertices no triangle uses (OBJ: one vertex list for every group). Returns (vertices,
    triangles int32, dropped); the order of the vertices is kept; nothing to drop — the arrays given."""
    t = np.asarray(tri)
    v = np.asarray(xyz)
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    kept = int(np.count_nonzero(used))
    if kept == len(v):
        return xyz, tri, 0
    number = np.cumsum(used, dtype=np.int64) - 1
    return np.ascontiguousarray(v[used]), np.ascontiguousarray(number[t], dtype=np.int32), len(v) - kept
