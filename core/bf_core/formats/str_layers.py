"""What both STR formats share: records in file order → `lineset.LineSet` layers, and back (ТЗ 9.3, 9.6; ADR 0011 §1–2).

Reading (`build_layers`): the records of a file come as flat arrays — xyz, a "new line starts here" flag, the layer
of every record and the other fields as `table.Column`s. They become one `LineSet` per layer:
  * layers in the order of their first line, lines and vertices in the order of the file;
  * a record without X or Y is left out (the caller reports how many) — a LineSet holds finite X/Y only;
    an empty Z stays NaN, never 0;
  * closed = the last vertex repeats the first (within `lineset.CLOSE_TOL`), 4 vertices or more
    (`lineset.detect_closed`): the repeat stays in `xyz` as the file has it, so the export gives the same bytes;
  * `seq` = the record number of every vertex: writers put the lines back in the file order;
  * a field that does not change along any line is a line attribute (`attrs`), any other one is per vertex
    (`vattrs`): Surpac descriptions, Road flags of a `.PIT`.
Writing (`gather`): every line once, by the `seq` of its first vertex when the layers come from one file (those
`seq` do not repeat), otherwise layer by layer; a points-only layer is written as lines of one vertex.
Attributes are merged over the layers by name (`Gathered.column`), a layer without one gives NaN / null.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bf_core.formats.lineset import FEATURE, VERTEX, LineSet, detect_closed
from bf_core.formats.table import Column, Progress

if TYPE_CHECKING:
    import numpy as np

NO_CODE = "Без кода"  # the layer of lines with a blank STRING


@dataclass
class StrRead:
    format: str  # "micromine_str" | "micromine_pit" | "surpac_str"
    layers: list[LineSet]
    header: Any  # micromine_header.DatHeader or str_surpac.SurpacHeader: what a writer needs for the same bytes
    encoding: str
    records: int  # vertex records in the file
    warnings: list[str] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return sum(layer.line_count for layer in self.layers)

    @property
    def point_count(self) -> int:
        return sum(layer.point_count for layer in self.layers)


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def norm(name: str) -> str:
    """A name as LineSet keeps it: whitespace collapsed."""
    return " ".join(str(name).split())


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


def take(column: Column, index: np.ndarray) -> Column:
    import pyarrow as pa

    if column.kind == "number":
        return Column(column.name, "number", column.values[index], source=column.source)
    values = pa.chunked_array([column.values.take(pa.array(index, type=pa.int64()))], type=pa.string())
    return Column(column.name, "text", values, source=column.source)


def blank(kind: str, rows: int, name: str) -> Column:
    import numpy as np
    import pyarrow as pa

    if kind == "number":
        return Column(name, "number", np.full(rows, np.nan))
    return Column(name, "text", pa.chunked_array([pa.nulls(rows, type=pa.string())], type=pa.string()))


def strings(column: Column) -> Any:
    """Text of a column as pyarrow strings (numbers as typed: 12.0 → "12", NaN → null)."""
    import pyarrow as pa

    if column.kind == "text":
        return column.values
    return pa.chunked_array([pa.array(column.text(), type=pa.string())])


def _table(columns: Sequence[Column]) -> Any:
    """Columns → pyarrow Table (float64 / string); names unique without regard to case, as LineSet wants."""
    import pyarrow as pa

    if not columns:
        return None
    names: list[str] = []
    seen: set[str] = set()
    for c in columns:
        base = name = norm(c.name) or "FIELD"
        k = 1
        while name.casefold() in seen:
            k += 1
            name = f"{base}_{k}"
        seen.add(name.casefold())
        names.append(name)
    arrays = [pa.array(c.values, type=pa.float64()) if c.kind == "number" else c.values for c in columns]
    return pa.table(arrays, names=names)


def from_table(table: Any, name: str) -> Column:
    import pyarrow as pa

    col = table.column(name)
    if pa.types.is_floating(col.type):
        return Column(name, "number", col.to_numpy())
    return Column(name, "text", col.cast(pa.string()))


# ---------------------------------------------------------------- reading: records → layers


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
    layer_names: Sequence[str],
    columns: Sequence[Column],
    line_only: Sequence[str] = (),
    progress: Progress | None = None,
) -> list[LineSet]:
    """Records in file order → layers (see the module docstring).

    `starts` — bool per record, True where a new line begins; `layer_code` — index into `layer_names` of every
    record; `line_only` — columns that are line attributes whatever they hold (they split the lines).
    """
    import numpy as np

    line_id = np.cumsum(starts) - 1
    keep = np.isfinite(xyz[:, :2]).all(axis=1)
    record = np.flatnonzero(keep) if not keep.all() else np.arange(len(xyz))
    if len(record) < len(xyz):
        xyz, line_id, layer_code = xyz[record], line_id[record], layer_code[record]
        columns = [take(c, record) for c in columns]
    n = len(xyz)
    if n == 0:
        return []
    begins = np.ones(n, dtype=bool)
    begins[1:] = line_id[1:] != line_id[:-1]
    first = np.flatnonzero(begins).astype(np.int64)
    offsets = np.append(first, n).astype(np.int64)
    closed = detect_closed(xyz, offsets)
    within = ~begins[1:]
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
    bounds = np.append(np.searchsorted(line_layer[order], present), len(order))
    single = len(present) == 1
    layers: list[LineSet] = []
    for rank, k in enumerate(np.argsort(first_line, kind="stable").tolist()):
        if progress is not None and rank % 64 == 0:
            progress(rank / len(present), f"Слои: {n_text(rank)} из {n_text(len(present))}")
        lines = order[bounds[k] : bounds[k + 1]]
        counts = offsets[lines + 1] - offsets[lines]
        vertices = np.arange(n, dtype=np.int64) if single else ranges(offsets[lines], counts)
        layers.append(
            LineSet(
                name=layer_names[int(present[k])],
                xyz=xyz if single else xyz[vertices],
                offsets=np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
                closed=closed[lines],
                seq=(record if single else record[vertices]).astype(np.uint32),
                attrs=_table([take(c, lines) for c in line_cols]),
                vattrs=_table(vertex_cols if single else [take(c, vertices) for c in vertex_cols]),
            )
        )
    return layers


# ---------------------------------------------------------------- writing: layers → records


def _offsets(layer: LineSet) -> np.ndarray:
    import numpy as np

    return np.arange(layer.point_count + 1, dtype=np.int64) if layer.offsets is None else layer.offsets


@dataclass
class Gathered:
    """Every vertex to write, in the order of the file; indices refer to the concatenation of all layers."""

    xyz: np.ndarray  # M×3
    vertex: np.ndarray  # M — index into the concatenated vertices (vertex attributes)
    line: np.ndarray  # M — index into the concatenated lines (line attributes)
    layer: np.ndarray  # M — index of the layer
    counts: np.ndarray  # L — vertices of every written line, in the written order

    @property
    def first(self) -> np.ndarray:
        """Position of the first vertex of every written line."""
        import numpy as np

        return np.cumsum(self.counts) - self.counts  # type: ignore[no-any-return]

    def column(self, layers: Sequence[LineSet], name: str, scope: str) -> Column:
        """One attribute for every written vertex, whatever layer it is in (missing = NaN / null)."""
        return take(merged_column(layers, name, scope), self.vertex if scope == VERTEX else self.line)


def attribute_names(layers: Sequence[LineSet]) -> list[tuple[str, str]]:
    """(name, scope) of every attribute, in order of first appearance; vertex scope wins over line scope."""
    found: dict[str, str] = {}
    for layer in layers:
        for table, scope in ((layer.attrs, FEATURE), (layer.vattrs, VERTEX)):
            for name in table.column_names if table is not None else ():
                if scope == VERTEX or name not in found:
                    found[name] = scope
    return list(found.items())


def merged_column(layers: Sequence[LineSet], name: str, scope: str) -> Column:
    """An attribute over all layers: rows are concatenated lines (FEATURE) or vertices (VERTEX)."""
    import numpy as np
    import pyarrow as pa

    found: list[tuple[Column | None, str]] = []
    for layer in layers:
        if layer.vattrs is not None and name in layer.vattrs.column_names:
            found.append((from_table(layer.vattrs, name), VERTEX))
        elif layer.attrs is not None and name in layer.attrs.column_names:
            found.append((from_table(layer.attrs, name), FEATURE))
        else:
            found.append((None, ""))
    kind = "number" if all(c is None or c.kind == "number" for c, _ in found) else "text"
    parts: list[Column] = []
    for layer, (column, own) in zip(layers, found, strict=True):
        offsets = _offsets(layer)
        rows = layer.point_count if scope == VERTEX else len(offsets) - 1
        if column is None:
            parts.append(blank(kind, rows, name))
        elif scope == VERTEX and own == FEATURE:
            parts.append(take(column, np.repeat(np.arange(len(offsets) - 1), np.diff(offsets))))
        else:
            parts.append(column)
    if kind == "number":
        return Column(name, "number", np.concatenate([c.values for c in parts]))
    chunks = [chunk for c in parts for chunk in strings(c).chunks]
    return Column(name, "text", pa.chunked_array(chunks, type=pa.string()))


def gather(layers: Sequence[LineSet]) -> Gathered:
    import numpy as np

    offs = [_offsets(layer) for layer in layers]
    line_base = np.cumsum([0, *(len(o) - 1 for o in offs)])
    vertex_base = np.cumsum([0, *(layer.point_count for layer in layers)])
    xyz = np.concatenate([layer.xyz for layer in layers]) if layers else np.empty((0, 3))
    starts = np.concatenate([o[:-1] + vertex_base[i] for i, o in enumerate(offs)]).astype(np.int64)
    counts = np.concatenate([np.diff(o) for o in offs]).astype(np.int64)
    seq = np.concatenate([np.asarray(layer.seq, dtype=np.int64) for layer in layers])[starts]
    layer_of = np.repeat(np.arange(len(layers)), np.diff(line_base))
    # Layers of one file have distinct record numbers: restore the file order. Otherwise layer by layer.
    order = np.argsort(seq, kind="stable") if len(np.unique(seq)) == len(seq) else np.lexsort((seq, layer_of))
    s, c = starts[order], counts[order]
    vertex = ranges(s, c)
    line = np.repeat(order, c)
    return Gathered(xyz[vertex], vertex, line, layer_of[line], c)
