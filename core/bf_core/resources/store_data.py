"""The Parquet files of a resource report (подшаг 20.6; ADR 0027 решение 15; docs/progress/e20-api.md §3, §7).

`data` — the rows of the table of the report (every cell of the form is a sum of these), `sidecar:curve` — the curve
«тоннаж — содержание». Columns of `data` (the keys of ADR 0027 решение 15):

    g1…g<n>        the labels of the group keys («(пусто)» included; null in the row of the total), n = keys
    total          true — the total row (its own accumulators, not the sum of the groups)
    kind           `cutoff` | `all` | `balance` | `offbalance`
    cutoff, cutoff_to   the cut-off (null — «без борта»); `cutoff_to` — the upper edge of an `offbalance` row
    blocks, volume, tonnes
    <e>:wt, <e>:sum, <e>:metal, <e>:grade, <e>:missing_t, <e>:missing_n    per element e

A gap is a null, never 0 (8.0.1): NaN of an array becomes a null, a null comes back as `None`. The bytes of a file
depend on the values only (the same rows → the same SHA-256: «Перестроить» gives the same `data`).

    table = build_table(columns, elements=["AU"], nkeys=1)      # columns: {name: numpy array | list}
    table = rows_table(rows, elements, nkeys)                   # the same from the `Row` dicts of the contract
    rows = table_rows(table)                                    # back: [Row] (docs/progress/e20-api.md §3)
    staged = stage_parquet(store, table)                        # a file of the store, not yet owned by anyone

pyarrow and numpy are imported when a function runs (the core reports BF-READY before them).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa

    from bf_core.storage.blobs import BlobStore

ROW_GROUP = 65_536
LEVEL = 3
PARQUET_MEDIA = "application/vnd.apache.parquet"
ELEMENT_PARTS = ("wt", "sum", "metal", "grade", "missing_t", "missing_n")
ROW_KINDS = ("cutoff", "all", "balance", "offbalance")
_KEY_COLUMN = re.compile(r"g[1-9]\d*")


def row_columns(elements: Sequence[str], nkeys: int) -> list[tuple[str, str]]:
    """(name, type) of the columns of `data` in their order; type — `str`, `bool`, `f8` or `i8`."""
    cols = [(f"g{i + 1}", "str") for i in range(nkeys)]
    cols += [("total", "bool"), ("kind", "str"), ("cutoff", "f8"), ("cutoff_to", "f8"), ("blocks", "i8"),
             ("volume", "f8"), ("tonnes", "f8")]  # fmt: skip
    for e in elements:
        cols += [(f"{e}:{p}", "i8" if p == "missing_n" else "f8") for p in ELEMENT_PARTS]
    return cols


def curve_columns(elements: Sequence[str]) -> list[tuple[str, str]]:
    cols = [("cutoff", "f8"), ("blocks", "i8"), ("volume", "f8"), ("tonnes", "f8")]
    for e in elements:
        cols += [(f"{e}:grade", "f8"), (f"{e}:metal", "f8")]
    return cols


def _array(kind: str, values: Any, name: str) -> Any:
    import numpy as np
    import pyarrow as pa

    if kind == "str":
        return pa.array([None if v is None else str(v) for v in values], type=pa.string())
    if kind == "bool":
        return pa.array(np.asarray(values, dtype=bool))
    if kind == "i8":
        return pa.array(np.asarray(values, dtype=np.int64))
    a = np.asarray(
        [np.nan if v is None else v for v in values] if not isinstance(values, np.ndarray) else values, dtype=np.float64
    )
    return pa.array(a, mask=~np.isfinite(a))  # NaN and ±inf are gaps (a null, not 0); `result.build` warns of them


def _table(cols: Sequence[tuple[str, str]], columns: Mapping[str, Any], meta: Mapping[str, str] | None) -> pa.Table:
    import pyarrow as pa

    missing = [n for n, _ in cols if n not in columns]
    if missing:
        raise ValueError(f"no columns: {', '.join(missing)}")
    sizes = {len(columns[n]) for n, _ in cols}
    if len(sizes) > 1:
        raise ValueError("columns of different length")
    table = pa.table({n: _array(k, columns[n], n) for n, k in cols})
    if meta:
        table = table.replace_schema_metadata({k.encode(): v.encode() for k, v in meta.items()})
    return table


def build_table(columns: Mapping[str, Any], elements: Sequence[str], nkeys: int) -> pa.Table:
    """The table `data` from columns {name: array}; a column missing is an error of the caller (ValueError)."""
    return _table(row_columns(elements, nkeys), columns, None)


def rows_table(rows: Sequence[Mapping[str, Any]], elements: Sequence[str], nkeys: int) -> pa.Table:
    """The table `data` from `Row` dicts (docs/progress/e20-api.md §3): `g` — the labels (null / [] in a total row)."""
    cols: dict[str, list[Any]] = {n: [] for n, _ in row_columns(elements, nkeys)}
    for r in rows:
        g = r.get("g") or []
        for i in range(nkeys):
            cols[f"g{i + 1}"].append(None if r.get("total") or i >= len(g) else g[i])
        for key in ("total", "kind", "cutoff", "cutoff_to", "blocks", "volume", "tonnes"):
            cols[key].append(r.get(key) if key != "total" else bool(r.get("total")))
        for e in elements:
            part = (r.get("elements") or {}).get(e) or {}
            for p in ELEMENT_PARTS:
                v = part.get(p)
                cols[f"{e}:{p}"].append(0 if (v is None and p == "missing_n") else v)
    return build_table(cols, elements, nkeys)


def curve_table(curve: Mapping[str, Any], elements: Sequence[str] | None = None) -> pa.Table:
    """The curve of the contract ({field, cutoffs, blocks, volume, tonnes, elements: {e: {grade, metal}}}) as a table;
    the field of the cut-off is in the metadata of the file."""
    names = list(elements if elements is not None else (curve.get("elements") or {}))
    cols: dict[str, Any] = {"cutoff": curve["cutoffs"], "blocks": curve["blocks"], "volume": curve["volume"],
                            "tonnes": curve["tonnes"]}  # fmt: skip
    for e in names:
        part = (curve.get("elements") or {}).get(e) or {}
        cols[f"{e}:grade"], cols[f"{e}:metal"] = part["grade"], part["metal"]
    return _table(curve_columns(names), cols, {"field": str(curve.get("field") or "")})


def keys_of(table: pa.Table) -> int:
    """The number of group keys of a table `data`."""
    return sum(1 for n in table.schema.names if _KEY_COLUMN.fullmatch(n))


def elements_of(table: pa.Table) -> list[str]:
    """The elements of a table `data` (or of a curve) in their order."""
    out: list[str] = []
    for n in table.schema.names:
        if ":" in n:
            e, part = n.rsplit(":", 1)
            if part in ("wt", "grade") and e not in out:
                out.append(e)
    return out


def table_rows(table: pa.Table, elements: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """The rows of a table `data` as `Row` dicts of the contract (a null stays `None`)."""
    names = list(elements if elements is not None else elements_of(table))
    nkeys = keys_of(table)
    out = []
    for r in table.to_pylist():
        total = bool(r["total"])
        out.append({
            "g": None if total and nkeys else ([] if not nkeys else [r[f"g{i + 1}"] for i in range(nkeys)]),
            "total": total, "kind": r["kind"], "cutoff": r["cutoff"], "cutoff_to": r["cutoff_to"],
            "blocks": r["blocks"], "volume": r["volume"], "tonnes": r["tonnes"],
            "elements": {e: {p: r[f"{e}:{p}"] for p in ELEMENT_PARTS} for e in names},
        })  # fmt: skip
    return out


def table_curve(table: pa.Table) -> dict[str, Any]:
    """The curve of the contract from a stored one."""
    names = elements_of(table)
    cols = table.to_pydict()
    meta = table.schema.metadata or {}
    return {"field": meta.get(b"field", b"").decode() or None, "cutoffs": cols["cutoff"], "blocks": cols["blocks"],
            "volume": cols["volume"], "tonnes": cols["tonnes"],
            "elements": {e: {"grade": cols[f"{e}:grade"], "metal": cols[f"{e}:metal"]} for e in names}}  # fmt: skip


def write_parquet(table: pa.Table, path: Any) -> None:
    """Parquet, zstd, row groups of 2¹⁶ rows: the same table → the same bytes."""
    import pyarrow.parquet as pq

    pq.write_table(table, path, compression="zstd", compression_level=LEVEL, row_group_size=ROW_GROUP)


def stage_parquet(store: BlobStore, table: pa.Table) -> tuple[Any, str, int]:
    """The table as a file of the store waiting for its owner (`BlobStore.commit_staged`): (path, sha256, size)."""
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = store.tmp_dir / f"{uuid.uuid4().hex}.parquet"
    try:
        write_parquet(table, tmp)
        return store.stage_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def read_page(path: Any, offset: int, size: int) -> pa.Table:
    """Rows [offset, offset + size) of a Parquet file, reading only the row groups they are in."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    first, taken = 0, []
    for g in range(pf.num_row_groups):
        n = pf.metadata.row_group(g).num_rows
        if first + n > offset and first < offset + size:
            taken.append((g, first))
        first += n
    if not taken:
        return pf.schema_arrow.empty_table()
    part = pa.concat_tables([pf.read_row_group(g) for g, _ in taken])
    start = offset - taken[0][1]
    return part.slice(start, size)


def num_rows(path: Any) -> int:
    import pyarrow.parquet as pq

    return int(pq.ParquetFile(path).metadata.num_rows)
