"""The arrays of a new version of a line layer from its base version and the body of a save (ADR 0015 §1, §2).

The body names every line of the new layer in its order: `src` — the stable number (`lid`) of the line of the
base it comes from (−1 — a new line), `touched` — its vertices are sent (else they are the slice of the base, bit
for bit: the closing vertex of a line from a file stays as the file has it, finding 2), `closed`, `count` and, for
touched and new lines only, `xyz` in the storage form (a closed line repeats its first vertex).

What a line carries over (ADR 0015 §1 «Перенос при сохранении»):

* attributes of the line — by `src` (a new line — empty); `new_attrs` {line: {field: value}} sets values, a field
  the layer has not got is added to its schema (number or text by the value);
* `vattrs` and `seq` — of an untouched line as they were; in a touched one a vertex with the coordinates of a
  vertex of its source line bit for bit keeps its row and `seq`, a moved or new vertex gets empty fields and a new
  `seq` (after the greatest of the base);
* `lid` — the old one by `src` (the first line naming a `src` keeps it, a second one — a part of a broken line, a
  copy — is new); a new line gets `next_lid`, which grows.

Pure numpy and pyarrow: no database, no files. `LineSet` checks the result (`bad_geometry` → 422).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import AttrField, LineSet

MAX_ATTR_TEXT = 2000
MAX_FIELD = 100


def bad_geometry(message: str, hint: str = "Перечитайте слой и повторите правку.") -> ApiError:
    return ApiError(422, "bad_geometry", message, hint)


@dataclass
class Edit:
    """The body of a save: one entry per line of the new layer, in its order."""

    src: np.ndarray  # <i4: lid of the source line, −1 — new
    touched: np.ndarray  # u1
    closed: np.ndarray  # u1
    count: np.ndarray  # <u4: vertices of the line in the storage form
    xyz: np.ndarray  # <f8 M×3: the vertices of the touched and new lines, in their order
    new_attrs: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.src = np.asarray(self.src, dtype=np.int64).reshape(-1)
        n = len(self.src)
        self.touched = np.asarray(self.touched, dtype=np.uint8).reshape(-1)
        self.closed = np.asarray(self.closed, dtype=np.uint8).reshape(-1)
        self.count = np.asarray(self.count, dtype=np.int64).reshape(-1)
        self.xyz = np.ascontiguousarray(np.asarray(self.xyz, dtype="<f8")).reshape(-1, 3)
        if not (len(self.touched) == len(self.closed) == len(self.count) == n):
            raise bad_geometry("Число линий в частях тела сохранения не совпадает.")
        if n and (self.src.min() < -1 or self.count.min() < 1):
            raise bad_geometry("Неверная ссылка на линию или пустая линия в теле сохранения.")
        sent = (self.touched != 0) | (self.src < 0)
        if int(self.count[sent].sum()) != len(self.xyz):
            raise bad_geometry("Число вершин изменённых линий не совпадает с переданными координатами.")
        for k in self.new_attrs:
            if not 0 <= int(k) < n:
                raise bad_geometry(f"Атрибуты заданы для линии {int(k) + 1}, а линий {n}.")


def untouched(lid: np.ndarray, closed: np.ndarray, counts: np.ndarray) -> Edit:
    """The body that keeps a layer as it is (every line untouched): the start of a move or of a test."""
    n = len(lid)
    return Edit(np.asarray(lid), np.zeros(n), closed, counts, np.zeros((0, 3)))


@dataclass
class Assembled:
    lineset: LineSet
    lid: np.ndarray  # <u4 per line
    next_lid: int


def as_lines(ls: LineSet) -> tuple[np.ndarray, np.ndarray]:
    """(offsets, closed) of a layer; a layer of points — a line of one vertex per point."""
    if ls.offsets is None or ls.closed is None:
        return np.arange(ls.point_count + 1, dtype=np.int64), np.zeros(ls.point_count, dtype=np.uint8)
    return ls.offsets, ls.closed


def _ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenated aranges [starts[i], starts[i] + counts[i]) (empty ranges allowed)."""
    keep = counts > 0
    starts, counts = np.asarray(starts, dtype=np.int64)[keep], np.asarray(counts, dtype=np.int64)[keep]
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    ends = np.cumsum(counts)
    out = np.ones(total, dtype=np.int64)
    out[0] = starts[0]
    out[ends[:-1]] = starts[1:] - (starts[:-1] + counts[:-1]) + 1
    return np.cumsum(out)


def _take(table: Any, index: np.ndarray, valid: np.ndarray) -> Any:
    """Rows `index` of a table; rows where `valid` is False are empty (null / NaN)."""
    import pyarrow as pa

    if table is None:
        return None
    idx = pa.array(np.where(valid, index, 0), type=pa.int64(), mask=~valid)
    out = table.take(idx)
    return _nan_floats(out)


def _nan_floats(table: Any) -> Any:
    """An empty number is NaN in the layer tables (lineset `_clean_table`), not null."""
    import pyarrow as pa
    import pyarrow.compute as pc

    cols = []
    for col in table.columns:
        if pa.types.is_floating(col.type) and col.null_count:
            col = pc.fill_null(col, np.nan)
        cols.append(col)
    return pa.table(cols, names=table.column_names)


def _value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, int | float) and not isinstance(v, bool):
        return float(v)
    return str(v)[:MAX_ATTR_TEXT]


def _set_attrs(table: Any, rows: int, new_attrs: Mapping[int, Mapping[str, Any]]) -> Any:
    """Values of `new_attrs` written into the table of line attributes; a missing field is added."""
    import pyarrow as pa

    if not new_attrs:
        return table
    data: dict[str, list[Any]] = {}
    types: dict[str, Any] = {}
    if table is not None:
        for name, col in zip(table.column_names, table.columns, strict=True):
            data[name] = col.to_pylist()
            types[name] = col.type
    for line, values in new_attrs.items():
        for key, raw in values.items():
            name = " ".join(str(key).split())[:MAX_FIELD]
            if not name:
                continue
            v = _value(raw)
            if name not in data:
                number = isinstance(v, float) or v is None
                data[name] = [np.nan if number else None] * rows
                types[name] = pa.float64() if number else pa.string()
            if pa.types.is_floating(types[name]) and isinstance(v, str):
                try:
                    v = float(v.replace(",", "."))
                except ValueError as exc:
                    raise ApiError(
                        422, "bad_attr", f"Поле «{name}» числовое, а задано «{v}».", "Введите число или очистите поле."
                    ) from exc
            if pa.types.is_floating(types[name]):
                data[name][int(line)] = np.nan if v is None else v
            else:
                data[name][int(line)] = None if v is None else (repr(v) if isinstance(v, float) else v)
    return pa.table({n: pa.array(vals, type=types[n]) for n, vals in data.items()})


def assemble(base: LineSet, base_lid: np.ndarray, next_lid: int, edit: Edit, *, name: str | None = None) -> Assembled:
    """The new layer: base slices for untouched lines, the body for the others; see the module docstring."""
    offsets, closed = as_lines(base)
    base_counts = np.diff(offsets)
    pos = {int(v): i for i, v in enumerate(np.asarray(base_lid).tolist())}
    n = len(edit.src)
    src_pos = np.full(n, -1, dtype=np.int64)
    for i, s in enumerate(edit.src.tolist()):
        if s >= 0:
            if s not in pos:
                raise bad_geometry(f"Линии {s} в слое нет: слой изменился.")
            src_pos[i] = pos[s]
    keep = (src_pos >= 0) & (edit.touched == 0)
    if keep.any():
        if np.any(base_counts[src_pos[keep]] != edit.count[keep]) or np.any(closed[src_pos[keep]] != edit.closed[keep]):
            raise bad_geometry("Нетронутая линия не совпадает с базовой версией слоя.")
    counts = edit.count
    new_off = np.zeros(n + 1, dtype=np.int64)
    new_off[1:] = np.cumsum(counts)
    total = int(new_off[-1])
    # Every output vertex: from the base (index < len(base)) or from the body (index − len(base)).
    nb = base.point_count
    sent = ~keep
    starts = np.zeros(n, dtype=np.int64)
    starts[keep] = offsets[:-1][src_pos[keep]]
    body_starts = np.cumsum(np.where(sent, counts, 0)) - np.where(sent, counts, 0)
    starts = np.where(keep, starts, nb + body_starts)
    take = _ranges(starts, counts)
    from_base = take < nb
    xyz = np.concatenate([base.xyz, edit.xyz])[take] if total else np.zeros((0, 3))
    # Rows of vattrs and seq: untouched — as they were; touched — a vertex equal bit for bit to one of its source
    # line keeps that vertex's row and seq.
    vrow = np.where(from_base, take, -1)
    base_seq = np.asarray(base.seq, dtype=np.int64)
    for i in np.flatnonzero(sent & (src_pos >= 0)):
        a, b = int(offsets[src_pos[i]]), int(offsets[src_pos[i] + 1])
        found: dict[bytes, int] = {}
        for k in range(a, b):
            found.setdefault(base.xyz[k].tobytes(), k)
        lo, hi = int(new_off[i]), int(new_off[i + 1])
        for k in range(lo, hi):
            vrow[k] = found.get(xyz[k].tobytes(), -1)
        if edit.closed[i] and closed[src_pos[i]] and xyz[hi - 1].tobytes() == base.xyz[b - 1].tobytes():
            vrow[hi - 1] = b - 1  # the closing vertex keeps the closing vertex, not the first one
    has = vrow >= 0
    seq = np.zeros(total, dtype=np.int64)
    seq[has] = base_seq[vrow[has]]
    fresh = int(base_seq.max()) + 1 if len(base_seq) else 0
    seq[~has] = np.arange(fresh, fresh + int((~has).sum()))
    if len(seq) and seq.max() > np.iinfo(np.uint32).max:
        raise bad_geometry("Слишком много вершин в слое.")
    # lid: the first line naming a source keeps its number.
    lid = np.zeros(n, dtype=np.int64)
    used: set[int] = set()
    nxt = int(next_lid)
    for i, s in enumerate(edit.src.tolist()):
        if s >= 0 and s not in used:
            lid[i] = s
            used.add(s)
        else:
            lid[i] = nxt
            nxt += 1
    attrs = _take(base.attrs, src_pos, src_pos >= 0)
    attrs = _set_attrs(attrs, n, edit.new_attrs)
    vattrs = _take(base.vattrs, vrow, has)
    out_closed = edit.closed.astype(np.uint8)
    points = base.points_only and bool(np.all(counts == 1)) and not out_closed.any()
    units = {f.name: f.unit for f in (AttrField.from_dict(d) for d in base.attr_schema()) if f.unit is not None}
    try:
        ls = LineSet(
            name or base.name,
            xyz,
            None if points else new_off,
            None if points else out_closed,
            seq.astype(np.uint32),
            attrs,
            None if points else vattrs,
            base.color,
            units,
        )
    except FormatError as exc:
        raise bad_geometry(exc.message) from exc
    return Assembled(ls, lid.astype(np.uint32), nxt)


def take_lines(ls: LineSet, index: Sequence[int]) -> LineSet:
    """Lines `index` of a layer (in that order) with their attributes, vertex attributes and `seq`."""
    offsets, closed = as_lines(ls)
    idx = np.asarray(index, dtype=np.int64)
    counts = np.diff(offsets)[idx]
    take = _ranges(offsets[:-1][idx], counts)
    off = np.zeros(len(idx) + 1, dtype=np.int64)
    off[1:] = np.cumsum(counts)
    ok = np.ones(len(idx), dtype=bool)
    vok = np.ones(len(take), dtype=bool)
    assert ls.seq is not None
    return LineSet(
        ls.name, ls.xyz[take], off, closed[idx], ls.seq[take], _take(ls.attrs, idx, ok), _take(ls.vattrs, take, vok),
        ls.color, dict(ls.units),
    )  # fmt: skip


def _union(a: Any, na: int, b: Any, nb: int) -> Any:
    """Rows of `a` then of `b`, columns of both (a column only one has is empty in the other; a number column
    meeting a text one becomes text)."""
    import pyarrow as pa

    if a is None and b is None:
        return None
    names: list[str] = []
    types: dict[str, Any] = {}
    for t in (a, b):
        if t is None:
            continue
        for name, col in zip(t.column_names, t.columns, strict=True):
            if name not in types:
                names.append(name)
                types[name] = col.type
            elif types[name] != col.type:
                types[name] = pa.string()
    cols = {}
    for name in names:
        parts = []
        for t, rows in ((a, na), (b, nb)):
            if t is not None and name in t.column_names:
                col = t.column(name)
                if col.type != types[name]:
                    col = pa.array([None if v is None else repr(v) for v in col.to_pylist()], type=pa.string())
                parts.append(col)
            else:
                fill = np.nan if pa.types.is_floating(types[name]) else None
                parts.append(pa.array([fill] * rows, type=types[name]))
        cols[name] = pa.chunked_array(parts, type=types[name]).combine_chunks()
    return pa.table(cols)


def concat(a: LineSet, b: LineSet) -> LineSet:
    """The lines of `a`, then those of `b` (a move into another layer): `seq` of `b` follow the greatest of `a`."""
    oa, ca = as_lines(a)
    ob, cb = as_lines(b)
    off = np.concatenate([oa, ob[1:] + oa[-1]])
    sa_ = np.asarray(a.seq, dtype=np.int64)
    sb = np.asarray(b.seq, dtype=np.int64)
    shift = (int(sa_.max()) + 1 if len(sa_) else 0) - (int(sb.min()) if len(sb) else 0)
    units = {**dict(b.units), **dict(a.units)}
    return LineSet(
        a.name, np.concatenate([a.xyz, b.xyz]), off, np.concatenate([ca, cb]),
        np.concatenate([sa_, sb + shift]).astype(np.uint32),
        _union(a.attrs, len(ca), b.attrs, len(cb)), _union(a.vattrs, a.point_count, b.vattrs, b.point_count),
        a.color, units,
    )  # fmt: skip
