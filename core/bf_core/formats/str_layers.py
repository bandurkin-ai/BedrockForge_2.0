"""Line layers as the STR readers return them, and the helpers both STR formats share (ТЗ 9.3, 9.6; ADR 0011 §1–2).

`StrLayer` keeps the lines of one layer in flat arrays, in the layout of the `line_layers` blobs (ADR 0011 §1):
  * `xyz` float64 N×3 (NaN = no value, never 0); line i is `xyz[offsets[i]:offsets[i + 1]]`;
  * `offsets` int64 L+1, `closed` bool L — a closed line repeats its first vertex at the end (17, п. 12);
  * `seq` uint32 N — the number of the record of each vertex in the source file: writers put the lines back
    in the order of the file, so that read → write gives the same bytes;
  * `attrs` — columns with one row per line (fields that do not change along any line, such as JOIN);
    `vattrs` — columns with one row per vertex (fields that do; Surpac descriptions, `.PIT` Road flags).
    Columns are `table.Column`: numbers are float64 with NaN, text is pyarrow strings with null.
`key` is what names the layer in the file: the STRING text of Micromine (None = blank), the Surpac string number.
`formats/lineset.py` (7.2) gets a thin adapter from these structures; this module does not depend on it.

Writers call `gather(layers)`: every line once, in the order of the file (by `seq`; lines without `seq` go after
them, layer by layer), with a closed line that does not repeat its first vertex closed by repeating it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bf_core.formats.table import Column, Progress

if TYPE_CHECKING:
    import numpy as np

NO_CODE = "Без кода"  # the layer of lines with a blank STRING
CLOSE_TOLERANCE = 1e-6  # first and last vertex closer than this in x, y and z: the line is closed (BF `_closed`)
LINE, VERTEX = "line", "vertex"


@dataclass
class StrLayer:
    name: str
    key: str | int | None
    xyz: np.ndarray
    offsets: np.ndarray
    closed: np.ndarray
    seq: np.ndarray | None = None
    attrs: list[Column] = field(default_factory=list)
    vattrs: list[Column] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return len(self.offsets) - 1

    @property
    def point_count(self) -> int:
        return len(self.xyz)

    def line(self, i: int) -> np.ndarray:
        return self.xyz[self.offsets[i] : self.offsets[i + 1]]  # type: ignore[no-any-return]

    def column(self, name: str) -> Column | None:
        for c in (*self.attrs, *self.vattrs):
            if c.name == name:
                return c
        return None


@dataclass
class StrRead:
    format: str  # "micromine_str" | "micromine_pit" | "surpac_str"
    layers: list[StrLayer]
    header: Any  # micromine_header.DatHeader or str_surpac.SurpacHeader: what writers need to give the same bytes
    encoding: str
    records: int  # vertex records read
    warnings: list[str] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return sum(layer.line_count for layer in self.layers)

    @property
    def point_count(self) -> int:
        return sum(layer.point_count for layer in self.layers)


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def scaled(progress: Progress, start: float, stop: float) -> Progress:
    """Progress of one stage mapped into [start, stop] of the whole operation."""

    def tick(fraction: float, message: str | None = None) -> None:
        progress(start + (stop - start) * min(max(fraction, 0.0), 1.0), message)

    return tick


# ---------------------------------------------------------------- columns


def text_codes(values: Any) -> tuple[np.ndarray, list[str]]:
    """pyarrow strings → index of each row's text (−1 = null) and the texts in order of first appearance."""
    import numpy as np
    import pyarrow as pa

    cells = values.combine_chunks() if isinstance(values, pa.ChunkedArray) else values
    encoded = cells.dictionary_encode()
    codes = encoded.indices.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int64)
    return codes, encoded.dictionary.to_pylist()


def row_keys(column: Column) -> np.ndarray:
    """An array whose equal neighbours mean equal values (float64 with NaN, or text codes)."""
    return column.values if column.kind == "number" else text_codes(column.values)[0]  # type: ignore[no-any-return]


def changes(keys: np.ndarray) -> np.ndarray:
    """True at i where row i + 1 differs from row i; NaN equals NaN (a blank field does not split a line)."""
    import numpy as np

    a, b = keys[1:], keys[:-1]
    differ = a != b
    if keys.dtype.kind == "f":
        differ &= ~(np.isnan(a) & np.isnan(b))
    return differ  # type: ignore[no-any-return]


def take(column: Column, index: np.ndarray, name: str | None = None) -> Column:
    import pyarrow as pa

    if column.kind == "number":
        values: Any = column.values[index]
    else:
        values = pa.chunked_array([column.values.take(pa.array(index, type=pa.int64()))], type=pa.string())
    return Column(name or column.name, column.kind, values, source=column.source)


def blank(kind: str, rows: int, name: str) -> Column:
    import numpy as np
    import pyarrow as pa

    if kind == "number":
        return Column(name, "number", np.full(rows, np.nan))
    return Column(name, "text", pa.chunked_array([pa.nulls(rows, type=pa.string())], type=pa.string()))


def as_text(column: Column) -> Column:
    """A number column as text (12.0 → "12", NaN → null); text stays as it is."""
    import pyarrow as pa

    if column.kind == "text":
        return column
    return Column(column.name, "text", pa.chunked_array([pa.array(column.text(), type=pa.string())]))


def concat(columns: Sequence[Column], name: str) -> Column:
    import numpy as np
    import pyarrow as pa

    if all(c.kind == "number" for c in columns):
        return Column(name, "number", np.concatenate([c.values for c in columns]) if columns else np.empty(0))
    chunks = [chunk for c in columns for chunk in as_text(c).values.chunks]
    return Column(name, "text", pa.chunked_array(chunks, type=pa.string()))


# ---------------------------------------------------------------- reading: records → layers


def closed_flags(xyz: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """A line of 3 or more vertices whose last vertex repeats the first (within CLOSE_TOLERANCE) is closed."""
    import numpy as np

    counts = np.diff(offsets)
    closed = counts > 2
    if closed.any():
        first, last = xyz[offsets[:-1][closed]], xyz[offsets[1:][closed] - 1]
        closed[closed] = (np.abs(first - last) <= CLOSE_TOLERANCE).all(axis=1)
    return closed  # type: ignore[no-any-return]


def ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenation of arange(s, s + c) for every (s, c) without a Python loop."""
    import numpy as np

    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    shift = np.repeat(starts - (np.cumsum(counts) - counts), counts)
    return shift + np.arange(total, dtype=np.int64)  # type: ignore[no-any-return]


def build_layers(
    xyz: np.ndarray,
    starts: np.ndarray,
    layer_code: np.ndarray,
    layer_keys: Sequence[str | int | None],
    layer_names: Sequence[str],
    columns: Sequence[Column],
    line_only: Sequence[str] = (),
    progress: Progress | None = None,
) -> list[StrLayer]:
    """Records in file order → layers.

    `starts` — bool per record, True where a new line begins (the first record included); `layer_code` — the
    index into `layer_keys` / `layer_names` of each record. A column that does not change along any line
    becomes a line attribute (`attrs`), any other a vertex attribute (`vattrs`); `line_only` are always line
    attributes (they cannot change along a line by how the lines were split).
    Layers come in the order of their first line; lines and vertices in the order of the file.
    """
    import numpy as np

    n = len(xyz)
    first = np.flatnonzero(starts).astype(np.int64)
    offsets = np.append(first, n).astype(np.int64)
    closed = closed_flags(xyz, offsets)
    within = ~starts[1:]
    line_cols: list[Column] = []
    vertex_cols: list[Column] = []
    for column in columns:
        if column.name in line_only or not (changes(row_keys(column)) & within).any():
            line_cols.append(take(column, first))
        else:
            vertex_cols.append(column)

    line_layer = layer_code[first]
    order = np.argsort(line_layer, kind="stable")
    present, first_line = np.unique(line_layer, return_index=True)
    bounds = np.searchsorted(line_layer[order], present)
    bounds = np.append(bounds, len(order))
    layers: list[StrLayer] = []
    for rank, k in enumerate(np.argsort(first_line, kind="stable").tolist()):
        if progress is not None and rank % 64 == 0:
            progress(rank / len(present), f"Слои: {n_text(rank)} из {n_text(len(present))}")
        lines = order[bounds[k] : bounds[k + 1]]
        counts = offsets[lines + 1] - offsets[lines]
        if len(present) == 1:
            vertices = np.arange(n, dtype=np.int64)
        else:
            vertices = ranges(offsets[lines], counts)
        code = int(present[k])
        layers.append(
            StrLayer(
                name=layer_names[code],
                key=layer_keys[code],
                xyz=xyz[vertices] if len(present) > 1 else xyz,
                offsets=np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
                closed=closed[lines],
                seq=vertices.astype(np.uint32),
                attrs=[take(c, lines) for c in line_cols],
                vattrs=[take(c, vertices) for c in vertex_cols],
            )
        )
    return layers


# ---------------------------------------------------------------- writing: layers → records


@dataclass
class Gathered:
    """Every vertex to write, in the order of the file; indices refer to the concatenation of all layers."""

    xyz: np.ndarray  # M×3
    vertex: np.ndarray  # M — index into the concatenated vertices (vertex attributes)
    line: np.ndarray  # M — index into the concatenated lines (line attributes)
    layer: np.ndarray  # M — index of the layer
    line_order: np.ndarray  # L — concatenated line index of each written line
    counts: np.ndarray  # L — vertices written for each line (a closing repeat included)
    closed_added: int  # closed lines that did not repeat their first vertex: it was added

    def column(self, layers: Sequence[StrLayer], name: str, level: str) -> Column:
        """One attribute for every written vertex, whatever layer it is in (missing = NaN / null)."""
        merged = merged_column(layers, name, level)
        return take(merged, self.vertex if level == VERTEX else self.line)


def attribute_names(layers: Sequence[StrLayer]) -> list[tuple[str, str]]:
    """(name, level) of every attribute, in order of first appearance; vertex level wins over line level."""
    found: dict[str, str] = {}
    for layer in layers:
        for c in layer.attrs:
            found.setdefault(c.name, LINE)
        for c in layer.vattrs:
            found[c.name] = VERTEX
    return list(found.items())


def merged_column(layers: Sequence[StrLayer], name: str, level: str) -> Column:
    """An attribute over all layers: rows are concatenated lines (LINE) or vertices (VERTEX)."""
    import numpy as np

    parts: list[Column] = []
    kinds = {c.kind for layer in layers for c in (*layer.attrs, *layer.vattrs) if c.name == name}
    kind = "number" if kinds == {"number"} else "text"
    for layer in layers:
        rows = layer.point_count if level == VERTEX else layer.line_count
        found = layer.column(name)
        if found is None:
            parts.append(blank(kind, rows, name))
        elif level == VERTEX and any(c is found for c in layer.attrs):
            parts.append(take(found, np.repeat(np.arange(layer.line_count), np.diff(layer.offsets))))
        else:
            parts.append(found)
    return concat(parts, name)


def gather(layers: Sequence[StrLayer], tick: Callable[[float, str | None], None] | None = None) -> Gathered:
    import numpy as np

    line_base = np.cumsum([0, *(layer.line_count for layer in layers)])
    vertex_base = np.cumsum([0, *(layer.point_count for layer in layers)])
    xyz = np.concatenate([np.asarray(layer.xyz, dtype=np.float64).reshape(-1, 3) for layer in layers]) if layers else (
        np.empty((0, 3))
    )
    starts = np.concatenate([layer.offsets[:-1] + vertex_base[i] for i, layer in enumerate(layers)]).astype(np.int64)
    counts = np.concatenate([np.diff(layer.offsets) for layer in layers]).astype(np.int64)
    closed = np.concatenate([np.asarray(layer.closed, dtype=bool) for layer in layers])
    layer_of = np.repeat(np.arange(len(layers)), np.diff(line_base))
    # Order: the record number of the first vertex; lines without one after all others, layer by layer.
    first_seq = np.full(len(starts), np.inf)
    for i, layer in enumerate(layers):
        if layer.seq is not None and layer.line_count:
            lo, hi = line_base[i], line_base[i + 1]
            has = counts[lo:hi] > 0
            first_seq[lo:hi][has] = np.asarray(layer.seq)[layer.offsets[:-1][has]].astype(np.float64)
    order = np.argsort(first_seq, kind="stable")
    order = order[counts[order] > 0]
    s, c = starts[order], counts[order]
    if tick is not None:
        tick(0.5, None)
    # A closed line must repeat its first vertex at the end; lines built by hand may not.
    open_end = np.zeros(len(order), dtype=bool)
    flagged = closed[order] & (c >= 2)
    if flagged.any():
        a, b = xyz[s[flagged]], xyz[s[flagged] + c[flagged] - 1]
        open_end[flagged] = ~(np.abs(a - b) <= CLOSE_TOLERANCE).all(axis=1)
    total = c + open_end
    within = ranges(np.zeros(len(order), dtype=np.int64), total)
    vertex = np.repeat(s, total) + np.where(within < np.repeat(c, total), within, 0)
    line = np.repeat(order, total)
    return Gathered(xyz[vertex], vertex, line, layer_of[line], order, total, int(open_end.sum()))
