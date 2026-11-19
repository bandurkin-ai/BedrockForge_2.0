"""A layer of lines or points: what every line reader returns and every line writer takes (ADR 0011 §1).

Ported from BF `formats/lines.py` (`Polyline`, `LineLayer`, `pack`, `line_stats`) and `lines/edit.py::validate`,
packed from the start: one vertex array per layer instead of a list of polylines.

* `xyz` — float64 N×3, X/Y finite; Z may be NaN (no elevation), never replaced by 0.
* `offsets` — int64, lines + 1 entries, `offsets[0] = 0`, `offsets[-1] = N`, no empty lines; a line of one
  vertex is a point shown as a mark (ТЗ 7.18).
* `closed` — uint8 per line. A closed line repeats its first vertex at the end (ТЗ 17 п. 12; within `CLOSE_TOL`,
  the last vertex as the file has it) and has at least three other vertices; `close` / `from_lines` add the
  repeat when a file only flags the line closed.
* A layer of points (`points_only`) has neither `offsets` nor `closed`: every vertex is a point.
* `seq` — uint32 per vertex: the number of its record in the source file, so a writer can restore the record
  order of a file whose layers were interleaved (lines go by the `seq` of their first vertex, as
  `str_layers.gather`). A vertex added to close a line gets the `seq` of the first vertex: it was not in the file.
* `attrs` — a pyarrow Table, one row per feature; `vattrs` — one row per vertex (only fields that change
  along a line: M of SHP, Surpac descriptions). Columns are float64 (NaN = empty) or strings (null = empty).
  Given as a Table, a mapping {name: values} or a list of `table.Column` (what the STR readers return).

Nothing heavy is imported at module import time except numpy (ADR 0002: pyarrow loads inside functions).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.formats.errors import FormatError

if TYPE_CHECKING:
    import pyarrow as pa

FEATURE = "feature"
VERTEX = "vertex"
MAX_NAME = 200
#: First and last vertex closer than this in X, Y and Z: the line repeats its first vertex (BF `_closed`,
#: `str_layers.CLOSE_TOLERANCE`). The last vertex stays as the file has it: export is bit-exact.
CLOSE_TOL = 1e-6


def _near(a: np.ndarray, b: np.ndarray, tol: float = CLOSE_TOL) -> np.ndarray:
    """Per row: every axis within `tol` (an empty Z matches an empty Z only)."""
    with np.errstate(invalid="ignore"):
        return np.asarray(((np.abs(a - b) <= tol) | (np.isnan(a) & np.isnan(b))).all(axis=-1))


def _bad(message: str, hint: str = "Проверьте файл: геометрия слоя повреждена.") -> FormatError:
    return FormatError("bad_geometry", message, hint)


@dataclass(frozen=True)
class AttrField:
    name: str
    kind: str  # "number" | "text"
    unit: str | None = None
    scope: str = FEATURE  # "feature": a row per line (per point) | "vertex": a row per vertex

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "unit": self.unit, "scope": self.scope}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> AttrField:
        return AttrField(str(d["name"]), str(d["kind"]), d.get("unit"), str(d.get("scope") or FEATURE))


def _clean_table(table: Any, rows: int, what: str) -> pa.Table | None:
    """Columns as float64 (NaN = empty) or string (null = empty); names unique and not blank."""
    import pyarrow as pa
    import pyarrow.compute as pc

    if table is None:
        return None
    if isinstance(table, list | tuple):  # [table.Column]: name + values (float64 NaN / pyarrow strings)
        table = {c.name: c.values for c in table}
    if isinstance(table, Mapping):
        table = pa.table({str(k): _column(v) for k, v in table.items()}) if table else None
        if table is None:
            return None
    if not isinstance(table, pa.Table):
        raise TypeError(f"{what}: a pyarrow.Table, a mapping or a list of columns is expected")
    if table.num_columns == 0:
        return None
    if table.num_rows != rows:
        raise _bad(f"Число строк атрибутов ({table.num_rows}) не равно числу {what} ({rows}).")
    names: list[str] = []
    cols: list[pa.ChunkedArray] = []
    seen: set[str] = set()
    for name, col in zip(table.column_names, table.columns, strict=True):
        key = " ".join(str(name).split())
        if not key:
            raise _bad("У атрибута нет имени.", "Задайте имена всем полям атрибутов.")
        if key.casefold() in seen:
            raise _bad(f"Атрибут «{key}» повторяется.", "Имена полей атрибутов должны быть разными.")
        seen.add(key.casefold())
        t = col.type
        if pa.types.is_dictionary(t):
            col, t = col.cast(t.value_type), t.value_type
        if pa.types.is_floating(t) or pa.types.is_integer(t) or pa.types.is_boolean(t) or pa.types.is_decimal(t):
            col = pc.fill_null(col.cast(pa.float64()), math.nan)  # empty numbers are NaN, never 0
        elif pa.types.is_null(t):
            col = pa.chunked_array([pa.nulls(len(col), pa.string())])
        elif not pa.types.is_string(t):
            col = col.cast(pa.string())
        names.append(key)
        cols.append(col)
    return pa.table(cols, names=names)


def _column(values: Any) -> Any:
    """A column given as a list / numpy array: numbers → float64 NaN-filled, anything else → strings."""
    import pyarrow as pa

    if isinstance(values, pa.Array | pa.ChunkedArray):
        return values
    if isinstance(values, np.ndarray) and values.dtype.kind in "fiub":
        return pa.array(values.astype(np.float64))
    vals = [v.item() if isinstance(v, np.generic) else v for v in values]
    filled = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in filled):
        return pa.array([math.nan if v is None else float(v) for v in vals], type=pa.float64())
    return pa.array([None if v is None or (isinstance(v, float) and math.isnan(v)) else _text(v) for v in vals])


def _text(v: Any) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))  # 12.0 → "12": identifiers typed as numbers
    return repr(v) if isinstance(v, float) else str(v)


@dataclass
class LineSet:
    name: str
    xyz: np.ndarray
    offsets: np.ndarray | None = None
    closed: np.ndarray | None = None
    seq: np.ndarray | None = None
    attrs: Any = None  # pyarrow.Table | mapping of columns | None
    vattrs: Any = None
    color: str | None = None
    units: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = " ".join(str(self.name).split())[:MAX_NAME] or "Без имени"
        xyz = np.ascontiguousarray(np.asarray(self.xyz, dtype="<f8"))
        if xyz.size == 0:
            xyz = xyz.reshape(0, 3)
        if xyz.ndim != 2 or xyz.shape[1] != 3:
            raise _bad("Координаты слоя должны быть тройками X, Y, Z.")
        self.xyz = xyz
        if (self.offsets is None) != (self.closed is None):
            raise _bad("У слоя линий должны быть и начала линий, и признаки замкнутости.")
        if self.offsets is not None:
            self.offsets = np.ascontiguousarray(np.asarray(self.offsets, dtype="<i8"))
            self.closed = np.ascontiguousarray(np.asarray(self.closed, dtype=np.uint8))
        n = self.feature_count
        if self.seq is None:
            self.seq = np.arange(len(self.xyz), dtype="<u4")
        else:
            self.seq = np.ascontiguousarray(np.asarray(self.seq, dtype="<u4"))
        self.validate()
        self.attrs = _clean_table(self.attrs, n, "точек" if self.points_only else "линий")
        self.vattrs = _clean_table(self.vattrs, len(self.xyz), "вершин")
        if self.color is not None:
            self.color = str(self.color).strip().lower() or None

    # --- shape --------------------------------------------------------------------------------------

    @property
    def points_only(self) -> bool:
        return self.offsets is None

    @property
    def point_count(self) -> int:
        return int(len(self.xyz))

    @property
    def line_count(self) -> int:
        return 0 if self.offsets is None else int(len(self.offsets) - 1)

    @property
    def feature_count(self) -> int:
        """Rows of `attrs`: lines, or points of a point layer."""
        return self.point_count if self.offsets is None else self.line_count

    @property
    def closed_count(self) -> int:
        return 0 if self.closed is None else int(np.count_nonzero(self.closed))

    def line(self, i: int) -> np.ndarray:
        if self.offsets is None:
            return self.xyz[i : i + 1]
        return self.xyz[int(self.offsets[i]) : int(self.offsets[i + 1])]

    def validate(self) -> None:
        xyz, offsets, closed = self.xyz, self.offsets, self.closed
        n = len(xyz)
        if n and not np.isfinite(xyz[:, :2]).all():
            raise _bad("В координатах X или Y есть пустые или нечисловые значения.")
        if np.isinf(xyz[:, 2]).any():
            raise _bad("В координатах Z есть бесконечные значения.")
        if offsets is not None and closed is not None:
            lines = len(offsets) - 1
            if offsets.ndim != 1 or lines < 0 or len(closed) != lines:
                raise _bad("Число линий и признаков замкнутости не совпадает.")
            if offsets[0] != 0 or offsets[-1] != n or (lines and np.any(np.diff(offsets) < 1)):
                raise _bad("Начала линий не соответствуют числу вершин (пустых линий быть не должно).")
            if np.any(closed > 1):
                raise _bad("Признак замкнутости — 0 или 1.")
            idx = np.flatnonzero(closed)
            if len(idx):
                a, b = offsets[idx], offsets[idx + 1] - 1
                bad = idx[(b - a < 3) | ~_near(xyz[a], xyz[b])]
                if len(bad):
                    raise _bad(
                        f"Замкнутая линия {int(bad[0]) + 1} должна иметь не меньше трёх вершин и повторять "
                        "первую вершину в конце."
                    )
        if self.seq is not None and len(self.seq) != n:
            raise _bad("Номер записи в файле задан не для каждой вершины.")

    # --- measures -----------------------------------------------------------------------------------

    def lengths(self) -> np.ndarray:
        """Length of every line; a segment with an empty Z counts in plan (its Z is unknown, not 0)."""
        if self.offsets is None or self.line_count == 0:
            return np.zeros(self.line_count)
        d = np.diff(self.xyz, axis=0)
        dz = np.where(np.isnan(d[:, 2]), 0.0, d[:, 2])
        seg = np.sqrt(d[:, 0] ** 2 + d[:, 1] ** 2 + dz**2)
        csum = np.concatenate([[0.0], np.cumsum(seg)])  # csum[k]: along the layer from vertex 0 to vertex k
        return np.asarray(csum[self.offsets[1:] - 1] - csum[self.offsets[:-1]])

    def flat(self) -> bool:
        """Every line at one level (points: all at one level); empty Z do not count."""
        z = self.xyz[:, 2]
        if self.offsets is None or self.line_count == 0:
            fin = z[np.isfinite(z)]
            return bool(len(fin) == 0 or fin.min() == fin.max())
        zmin = np.where(np.isnan(z), np.inf, z)
        zmax = np.where(np.isnan(z), -np.inf, z)
        starts = self.offsets[:-1]
        lo, hi = np.minimum.reduceat(zmin, starts), np.maximum.reduceat(zmax, starts)
        empty = np.isinf(lo)
        return bool(np.all(empty | (lo == hi)))

    def extent(self) -> list[float | None] | None:
        """[xmin, ymin, zmin, xmax, ymax, zmax]; Z bounds are None when no vertex has a Z."""
        if not len(self.xyz):
            return None
        lo, hi = self.xyz[:, :2].min(axis=0), self.xyz[:, :2].max(axis=0)
        z = self.xyz[:, 2]
        fin = z[np.isfinite(z)]
        zlo: float | None = float(fin.min()) if len(fin) else None
        zhi: float | None = float(fin.max()) if len(fin) else None
        return [float(lo[0]), float(lo[1]), zlo, float(hi[0]), float(hi[1]), zhi]

    def origin(self) -> list[float]:
        """The centre of the extent rounded to a whole metre (as viz/parts.py): the base of float32 offsets."""
        ext = self.extent()
        if ext is None:
            return [0.0, 0.0, 0.0]
        out = []
        for lo, hi in ((ext[0], ext[3]), (ext[1], ext[4]), (ext[2], ext[5])):
            out.append(0.0 if lo is None or hi is None else float(np.round((lo + hi) * 0.5)) + 0.0)
        return out

    def stats(self) -> dict[str, Any]:
        return {
            "line_count": self.line_count,
            "point_count": self.point_count,
            "length": float(self.lengths().sum()),
            "closed_count": self.closed_count,
            "flat": self.flat(),
            "points_only": self.points_only,
        }

    def attr_schema(self) -> list[dict[str, Any]]:
        import pyarrow as pa

        out: list[dict[str, Any]] = []
        for scope, table in ((FEATURE, self.attrs), (VERTEX, self.vattrs)):
            if table is None:
                continue
            for name, col in zip(table.column_names, table.columns, strict=True):
                kind = "number" if pa.types.is_floating(col.type) else "text"
                out.append(AttrField(name, kind, self.units.get(name), scope).as_dict())
        return out


# --- building ---------------------------------------------------------------------------------------


def detect_closed(xyz: np.ndarray, offsets: np.ndarray, tol: float = CLOSE_TOL) -> np.ndarray:
    """uint8 per line: first and last vertices coincide (within `tol` per axis) and there are ≥ 4 vertices;
    `tol` = 0 — bit for bit. Nothing is moved: the repeated vertex stays as the file has it."""
    xyz = np.asarray(xyz, dtype=np.float64)
    offsets = np.asarray(offsets, dtype=np.int64)
    if len(offsets) < 2:
        return np.zeros(0, dtype=np.uint8)
    a, b = offsets[:-1], offsets[1:] - 1
    pa_, pb = xyz[a], xyz[b]
    return ((b - a >= 3) & _near(pa_, pb, tol)).astype(np.uint8)


def close(
    xyz: np.ndarray, offsets: np.ndarray, closed: Any
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    """Lines flagged closed whose last vertex is not their first get the first vertex appended; a flagged line
    that would still have fewer than four vertices stays open. Returns (xyz, offsets, closed, take): `take` —
    the source vertex of every output vertex (None when nothing was added), to carry vertex attributes."""
    xyz = np.asarray(xyz, dtype=np.float64)
    offsets = np.asarray(offsets, dtype=np.int64)
    flags = np.asarray(closed, dtype=bool).copy()
    if not flags.any():
        return xyz, offsets, flags.astype(np.uint8), None
    a, b = offsets[:-1], offsets[1:] - 1
    same = _near(xyz[a], xyz[b]) & (b > a)
    count = (b - a + 1) + np.where(same, 0, 1)
    flags &= count >= 4
    add = flags & ~same
    if not add.any():
        return xyz, offsets, flags.astype(np.uint8), None
    lines = np.flatnonzero(add)
    take = np.insert(np.arange(len(xyz), dtype=np.int64), offsets[1:][lines], offsets[:-1][lines])
    new_off = offsets.copy()
    new_off[1:] += np.cumsum(add)
    return xyz[take], new_off, flags.astype(np.uint8), take


def from_lines(
    name: str,
    lines: Iterable[Any],
    closed: Sequence[bool] | np.ndarray | None = None,
    *,
    attrs: Any = None,
    vattrs: Any = None,
    seq: Any = None,
    color: str | None = None,
    units: dict[str, str] | None = None,
) -> LineSet:
    """A layer from separate polylines (k×3 each). `closed` None: closed where the first vertex repeats;
    given: flagged lines get the repeat of the first vertex (and of its vertex attributes and `seq`).
    `seq` and `vattrs` are given per source vertex."""
    parts = [np.asarray(v, dtype=np.float64).reshape(-1, 3) for v in lines]
    offsets = np.zeros(len(parts) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum([len(p) for p in parts])
    xyz = np.vstack(parts) if parts else np.zeros((0, 3))
    if closed is None:
        flags = detect_closed(xyz, offsets)
    else:
        if len(closed) != len(parts):
            raise _bad("Число линий и признаков замкнутости не совпадает.")
        n_src = len(xyz)
        xyz, offsets, flags, take = close(xyz, offsets, closed)
        if take is not None and vattrs is not None:
            vattrs = _clean_table(vattrs, n_src, "вершин")
            if vattrs is not None:
                vattrs = vattrs.take(take)
        if take is not None:
            seq = (np.arange(n_src, dtype=np.uint32) if seq is None else np.asarray(seq, dtype=np.uint32))[take]
    return LineSet(name, xyz, offsets, flags, seq, attrs, vattrs, color, dict(units or {}))


def from_points(
    name: str,
    xyz: Any,
    *,
    attrs: Any = None,
    seq: Any = None,
    color: str | None = None,
    units: dict[str, str] | None = None,
) -> LineSet:
    pts = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    return LineSet(name, pts, None, None, seq, attrs, None, color, dict(units or {}))


class LineSetBuilder:
    """Lines (or points) added one by one by a streaming reader; `build()` packs them into a LineSet.
    Attribute values: numbers (NaN / None = empty) or text (None = empty); a field missing on a line is empty.
    `seq` of `add`: None — vertices numbered in the order added; an int — the record of the first vertex (the
    next ones follow it); a sequence — the record of every vertex."""

    def __init__(self, name: str, *, points: bool = False, color: str | None = None) -> None:
        self.name, self.points, self.color = name, points, color
        self._xyz: list[np.ndarray] = []
        self._closed: list[int] = []
        self._seq: list[np.ndarray] = []
        self._attrs: dict[str, list[Any]] = {}
        self._vattrs: dict[str, list[Any]] = {}
        self._features = 0
        self._vertices = 0
        self.units: dict[str, str] = {}

    def __len__(self) -> int:
        return self._features

    def add(
        self,
        xyz: Any,
        closed: bool = False,
        *,
        seq: int | Sequence[int] | None = None,
        attrs: Mapping[str, Any] | None = None,
        vattrs: Mapping[str, Sequence[Any]] | None = None,
    ) -> None:
        v = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
        if self.points and (len(v) != 1 or closed):
            raise ValueError("a point layer takes single vertices")
        if len(v) == 0:
            return  # an empty line is not stored (ТЗ 7.18: no empty lines)
        if seq is None:
            rec = np.arange(self._vertices, self._vertices + len(v), dtype=np.int64)
        elif isinstance(seq, int | np.integer):
            rec = np.arange(int(seq), int(seq) + len(v), dtype=np.int64)
        else:
            rec = np.asarray(seq, dtype=np.int64).reshape(-1)
            if len(rec) != len(v):
                raise ValueError(f"seq: {len(rec)} record numbers for {len(v)} vertices")
        if closed and not self.points:
            first, last = v[0], v[-1]
            same = len(v) > 1 and bool(_near(first, last))
            if len(v) + (0 if same else 1) < 4:
                closed = False  # fewer than three distinct vertices: not a closed line
            elif not same:
                v = np.vstack([v, v[:1]])
                rec = np.append(rec, rec[0])  # not a record of the file: the seq of the first vertex
                if vattrs:
                    vattrs = {k: [*list(col), list(col)[0]] for k, col in vattrs.items()}
        self._xyz.append(v)
        self._closed.append(1 if closed else 0)
        self._seq.append(rec)
        self._put(self._attrs, attrs or {}, self._features, 1)
        self._put(self._vattrs, vattrs or {}, self._vertices, len(v), per_row=True)
        self._features += 1
        self._vertices += len(v)

    @staticmethod
    def _put(
        store: dict[str, list[Any]], values: Mapping[str, Any], before: int, n: int, per_row: bool = False
    ) -> None:
        for key, val in values.items():
            col = store.get(key)
            if col is None:
                col = store[key] = [None] * before
            if per_row:
                vals = list(val)
                if len(vals) != n:
                    raise ValueError(f"vertex attribute {key!r}: {len(vals)} values for {n} vertices")
                col.extend(vals)
            else:
                col.append(val)
        for key, col in store.items():
            if key not in values:
                col.extend([None] * n)

    def build(self) -> LineSet:
        xyz = np.vstack(self._xyz) if self._xyz else np.zeros((0, 3))
        attrs = {k: _column(v) for k, v in self._attrs.items()} or None
        vattrs = {k: _column(v) for k, v in self._vattrs.items()} or None
        seq = np.concatenate(self._seq).astype(np.uint32) if self._seq else np.zeros(0, dtype=np.uint32)
        if self.points:
            return LineSet(self.name, xyz, None, None, seq, attrs, None, self.color, dict(self.units))
        offsets = np.zeros(len(self._xyz) + 1, dtype=np.int64)
        offsets[1:] = np.cumsum([len(p) for p in self._xyz])
        closed = np.asarray(self._closed, dtype=np.uint8)
        return LineSet(self.name, xyz, offsets, closed, seq, attrs, vattrs, self.color, dict(self.units))
