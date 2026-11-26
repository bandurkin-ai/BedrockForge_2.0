"""The files of a pit scenario (подшаг 21.10; ADR 0028 решения 20–21; docs/progress/e21-api.md §6, §8).

Five files of the object `pit_scenario` (owner `object`, existing roles of blobs, no migration):

    data              Parquet, the increments ΔC_k of the shells (a row per shell k = 1…N at the base price RF = 1)
    sidecar:benches   Parquet, the worst case: a row per bench (level) of C_n for every n (n, level, ore_t, waste_t, cf)
    sidecar:zgrid     Parquet, the z-grids of the shells: i, j, z1…zN (null — outside of the plan of the terrain)
    sidecar:template  JSON, the offsets of the template and φ_eff(az) per domain (`GET …/template`)
    sidecar:control   JSON, the control of the run (`air`, `empty_cells`, `no_grade`, `no_density`, …)

Columns of `data` (a gap is a null, never 0, 8.0.1: NaN and ±inf of an array become nulls):

    k, rf                                        the number of the shell (the value of the field PIT) and its RF
    blocks, ore_t, waste_t                       the increment (parents of C_k \\ C_(k−1); ore — at RF = 1, ORE)
    revenue, cost_mine, cost_process, cost_sales, royalty, cf         the sums of the increment (`cf` — the identity)
    <e>:grade, <e>:metal, <e>:rec_metal          per element e (the grade — by the tonnes of the blocks with a value)
    cum_blocks, cum_ore_t, …, cum_cf             the same, accumulated over C_k
    cum_<e>:grade, cum_<e>:metal, cum_<e>:rec_metal

`strip` (W/O) is not stored: it is waste_t / ore_t, null at ore_t = 0 (`table_shells`). `value` (Σ v of the closure) and
the NPV (a view of the calendar, решение 19) are not in the file either: `meta.shells` and the report have them. The
bytes of a file depend on the values only (the same table → the same SHA-256: «Перестроить» gives the same `data`).

    table = shells_table(shells, ["AU"])        # `Shell` dicts of the contract (§7) → the table `data`
    shells = table_shells(table)                # back (`strip` made again; `best` / `worst` are not in the file)
    table = data_table(columns, ["AU"])         # the same from columns {name: array}
    table = benches_table(columns); table = zgrid_table(i, j, z)      # z — (N, cells) or N arrays
    staged = stage_parquet(store, table); staged = stage_json(store, doc)

pyarrow and numpy are imported when a function runs (the core reports BF-READY before them).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from bf_core.resources.store_data import PARQUET_MEDIA, read_page, stage_parquet, write_parquet

if TYPE_CHECKING:
    import pyarrow as pa

    from bf_core.storage.blobs import BlobStore

__all__ = ["BENCH_COLUMNS", "ELEMENT_PARTS", "JSON_MEDIA", "PARQUET_MEDIA", "SHELL_PARTS", "benches_table",
           "clean_json", "data_columns", "data_table", "elements_of", "json_bytes", "num_rows", "read_page",
           "shells_table", "stage_json", "stage_parquet", "stage_zgrid", "table_benches", "table_shells", "table_zgrid",
           "write_parquet", "zgrid_table"]  # fmt: skip

JSON_MEDIA = "application/json"
SHELL_PARTS = ("blocks", "ore_t", "waste_t", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty", "cf")
ELEMENT_PARTS = ("grade", "metal", "rec_metal")
BENCH_COLUMNS = (("n", "i8"), ("level", "i8"), ("ore_t", "f8"), ("waste_t", "f8"), ("cf", "f8"))
_ZCOLUMN = re.compile(r"z[1-9]\d*")


def _kind(part: str) -> str:
    return "i8" if part == "blocks" else "f8"


def data_columns(elements: Sequence[str]) -> list[tuple[str, str]]:
    """(name, type) of the columns of `data` in their order; type — `f8` or `i8`."""
    cols = [("k", "i8"), ("rf", "f8")] + [(p, _kind(p)) for p in SHELL_PARTS]
    cols += [(f"{e}:{p}", "f8") for e in elements for p in ELEMENT_PARTS]
    cols += [(f"cum_{p}", _kind(p)) for p in SHELL_PARTS]
    cols += [(f"cum_{e}:{p}", "f8") for e in elements for p in ELEMENT_PARTS]
    return cols


def elements_of(table: pa.Table) -> list[str]:
    """The elements of a table `data` in their order."""
    out: list[str] = []
    for n in table.schema.names:
        if ":" in n and not n.startswith("cum_"):
            e, part = n.rsplit(":", 1)
            if part == "grade" and e not in out:
                out.append(e)
    return out


def _array(kind: str, values: Any) -> Any:
    import numpy as np
    import pyarrow as pa

    if kind in ("i8", "i4"):
        return pa.array(np.asarray(values, dtype=np.int64 if kind == "i8" else np.int32))
    if isinstance(values, np.ndarray):
        a = np.asarray(values, dtype=np.float64)
    else:
        a = np.asarray([np.nan if v is None else v for v in values], dtype=np.float64)
    return pa.array(a, mask=~np.isfinite(a))  # NaN and ±inf are gaps (a null, not 0)


def _table(cols: Sequence[tuple[str, str]], columns: Mapping[str, Any]) -> pa.Table:
    import pyarrow as pa

    missing = [n for n, _ in cols if n not in columns]
    if missing:
        raise ValueError(f"no columns: {', '.join(missing)}")
    if len({len(columns[n]) for n, _ in cols}) > 1:
        raise ValueError("columns of different length")
    return pa.table({n: _array(k, columns[n]) for n, k in cols})


def data_table(columns: Mapping[str, Any], elements: Sequence[str]) -> pa.Table:
    """The table `data` from columns {name: array}; a column missing is an error of the caller (ValueError)."""
    return _table(data_columns(elements), columns)


def shells_table(shells: Sequence[Mapping[str, Any]], elements: Sequence[str]) -> pa.Table:
    """The table `data` from the `Shell` dicts of the contract (§7: `elements`, `cum`); `strip`, `best`, `worst` and
    anything else the dict has are not stored."""
    cols: dict[str, list[Any]] = {n: [] for n, _ in data_columns(elements)}
    for s in shells:
        cum = s.get("cum") or {}
        cols["k"].append(s["k"])
        cols["rf"].append(s["rf"])
        for p in SHELL_PARTS:
            cols[p].append(s.get(p))
            cols[f"cum_{p}"].append(cum.get(p))
        for e in elements:
            part, cpart = (s.get("elements") or {}).get(e) or {}, (cum.get("elements") or {}).get(e) or {}
            for p in ELEMENT_PARTS:
                cols[f"{e}:{p}"].append(part.get(p))
                cols[f"cum_{e}:{p}"].append(cpart.get(p))
    return data_table(cols, elements)


def _strip(ore: float | None, waste: float | None) -> float | None:
    return None if ore is None or waste is None or ore == 0 else waste / ore


def table_shells(table: pa.Table, elements: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """The `Shell` dicts of the contract from a stored `data` (a null stays `None`; `strip` is waste_t / ore_t)."""
    names = list(elements if elements is not None else elements_of(table))
    out = []
    for r in table.to_pylist():
        cum = {p: r[f"cum_{p}"] for p in SHELL_PARTS}
        cum["strip"] = _strip(cum["ore_t"], cum["waste_t"])
        cum["elements"] = {e: {p: r[f"cum_{e}:{p}"] for p in ELEMENT_PARTS} for e in names}
        out.append({
            "k": r["k"], "rf": r["rf"], **{p: r[p] for p in SHELL_PARTS}, "strip": _strip(r["ore_t"], r["waste_t"]),
            "elements": {e: {p: r[f"{e}:{p}"] for p in ELEMENT_PARTS} for e in names}, "cum": cum,
        })  # fmt: skip
    return out


def benches_table(columns: Mapping[str, Any]) -> pa.Table:
    """The worst case: n (the shell the closure C_n is of), level (the bench, from the top), ore_t, waste_t, cf."""
    return _table(BENCH_COLUMNS, columns)


def table_benches(table: pa.Table) -> dict[str, list[Any]]:
    return {k: list(v) for k, v in table.to_pydict().items()}


def zgrid_table(i: Any, j: Any, z: Any) -> pa.Table:
    """The z-grids of the shells: `i`, `j` — the column of the plan, `z` — (N, cells) or N arrays; NaN is null."""
    import numpy as np
    import pyarrow as pa

    rows = len(i)
    if len(j) != rows or any(len(s) != rows for s in z):
        raise ValueError("columns of different length")
    cols: dict[str, Any] = {"i": _array("i4", np.asarray(i)), "j": _array("i4", np.asarray(j))}
    for k, s in enumerate(z, 1):
        cols[f"z{k}"] = _array("f8", np.asarray(s, dtype=np.float64))
    return pa.table(cols)


def stage_zgrid(store: BlobStore, nx: int, ny: int, z: Any) -> tuple[Any, str, int]:
    """`sidecar:zgrid` from the array `z` float64 (N, ny·nx), column c = i + nx·j, written in row groups (the same
    columns and row groups as `zgrid_table` + `stage_parquet`): no second and third copy of N·columns values as a
    table and as a file buffer (21.21б-2). The file is the one the readers of old projects know."""
    import uuid

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from bf_core.resources.store_data import LEVEL, ROW_GROUP

    z = np.asarray(z, dtype=np.float64)
    rows = int(nx) * int(ny)
    if z.ndim != 2 or z.shape[1] != rows:
        raise ValueError("z must have one row per shell and one column per node of the plan")
    schema = pa.schema([("i", pa.int32()), ("j", pa.int32()), *((f"z{k}", pa.float64()) for k in range(1, len(z) + 1))])
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = store.tmp_dir / f"{uuid.uuid4().hex}.parquet"
    try:
        with pq.ParquetWriter(tmp, schema, compression="zstd", compression_level=LEVEL) as writer:
            for lo in range(0, max(rows, 1), ROW_GROUP):
                hi = min(lo + ROW_GROUP, rows)
                c = np.arange(lo, hi, dtype=np.int64)
                cols: dict[str, Any] = {"i": _array("i4", c % nx), "j": _array("i4", c // nx)}
                cols.update({f"z{k}": _array("f8", z[k - 1, lo:hi]) for k in range(1, len(z) + 1)})
                writer.write_table(pa.table(cols, schema=schema))
        return store.stage_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def table_zgrid(table: pa.Table) -> dict[str, Any]:
    """{"i", "j": int32 arrays, "z": float64 (N, cells) with NaN for a null, "shells": [the numbers of the rows of
    `z`]}; N = the number of the columns z1…zN in the table (a table of some of the shells has only those)."""
    import numpy as np

    names = [n for n in table.schema.names if _ZCOLUMN.fullmatch(n)]
    z = np.full((len(names), table.num_rows), np.nan)
    for k, n in enumerate(names):
        z[k] = table.column(n).to_numpy(zero_copy_only=False)  # a null comes back as NaN
    return {"i": table.column("i").to_numpy(), "j": table.column("j").to_numpy(), "z": z,
            "shells": [int(n[1:]) for n in names]}  # fmt: skip


def num_rows(path: Any) -> int:
    import pyarrow.parquet as pq

    return int(pq.ParquetFile(path).metadata.num_rows)


# --- JSON -------------------------------------------------------------------------------------------------------------


def clean_json(value: Any) -> Any:
    """The value as plain JSON: numpy scalars and arrays as numbers and lists, NaN and ±inf as null (never 0)."""
    if hasattr(value, "tolist"):  # a numpy array or scalar
        value = value.tolist()
    if isinstance(value, Mapping):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def json_bytes(doc: Mapping[str, Any]) -> bytes:
    """The canonical bytes of a JSON file (keys sorted, no spaces, Russian text as is): the same document, same file."""
    return json.dumps(clean_json(doc), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode()  # fmt: skip


def stage_json(store: BlobStore, doc: Mapping[str, Any]) -> tuple[Any, str, int]:
    """A JSON document as a file of the store waiting for its owner: (path, sha256, size)."""
    return store.stage_bytes(json_bytes(doc))
