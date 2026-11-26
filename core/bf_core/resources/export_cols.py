"""The table of the rows of a resource report as the files show it (подшаг 20.5; ТЗ 8.10 «Вывод»; ADR 0027 решение 20):
the header and the columns of the Parquet `data`, shared by the sheet «Таблица» of the XLSX (`xlsx.py`) and the CSV
(`csv_out.py`).

    names = element_names(params, table_names)     # [(name, unit)] of the elements in the order of the file
    head = table_head(keys, names)                 # the titles of the columns, the order of `store_data.row_columns`
    for cols in iter_columns(path, numeric=True): …    # a batch: a column per title of `head`
    unit_label(unit) -> «г/т», metal_label(unit) -> «г» | «т» | None

The columns are exactly those of the file (every number of the row, not rounded; a gap is a null, never 0): the keys
`g1…g<n>` (null in the row of the total), «Итог» («да» in a row of the total), «Вид» (борт / без борта /
балансовые / забалансовые), «Борт», «Борт до», «Блоков», «Объём, м³», «Масса, т», then for each element: the
tonnes with a grade, Σ t·g, the metal (in g or t), the grade, the tonnes and the blocks without a grade.
`numeric` — numpy arrays for the numbers (the XLSX writes a column at once), else plain lists (the CSV).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import forms

BATCH = 65_536
KIND_TITLES = {"cutoff": "борт", "all": "без борта", "balance": "балансовые", "offbalance": "забалансовые"}
BASE_LABEL = {"g": "г", "t": "т"}
TOTAL_TEXT = "да"
BENCH_TITLE = "Уступ"


def key_titles(params: Mapping[str, Any], count: int) -> list[str]:
    """The titles of the key columns: the field of a key, «Уступ» for the benches; «Группа i» when the parameters are
    not known (count of keys of the file)."""
    out: list[str] = []
    for i, g in enumerate(params.get("groups") or []):
        out.append(
            BENCH_TITLE
            if isinstance(g, Mapping) and g.get("kind") == "bench"
            else str((g.get("field") if isinstance(g, Mapping) else g) or f"Группа {i + 1}")
        )
    while len(out) < count:
        out.append(f"Группа {len(out) + 1}")
    return out[:count]


def element_names(params: Mapping[str, Any], names: Sequence[str]) -> list[tuple[str, str | None]]:
    """[(name, unit)] for the elements `names` of the file: the unit from the parameters of the report (no case)."""
    units: dict[str, str | None] = {}
    for e in params.get("elements") or []:
        if isinstance(e, Mapping) and e.get("field") is not None:
            units[str(e["field"]).casefold()] = e.get("unit")
    return [(n, units.get(n.casefold())) for n in names]


def unit_label(unit: Any) -> str:
    return forms.grade_unit(unit)[0]


def metal_label(unit: Any) -> str | None:
    base = forms.grade_unit(unit)[1]
    return None if base is None else BASE_LABEL[base]


def table_head(keys: Sequence[str], elements: Sequence[tuple[str, str | None]]) -> list[str]:
    """The titles of the columns of the table, in the order of the Parquet `data` (`store_data.row_columns`)."""
    head = [*keys, "Итог", "Вид", "Борт", "Борт до", "Блоков", "Объём, м³", "Масса, т"]
    for name, unit in elements:
        grade, metal = unit_label(unit), metal_label(unit)
        head += [f"{name}: масса с содержанием, т", f"{name}: Σ масса × содержание",
                 f"{name}: металл, {metal}" if metal else f"{name}: металл",
                 f"{name}: содержание, {grade}" if grade else f"{name}: содержание",
                 f"{name}: масса без содержания, т", f"{name}: блоков без содержания"]  # fmt: skip
    return head


def _column(arr: Any, name: str, numeric: bool) -> Any:
    import numpy as np
    import pyarrow as pa

    t = arr.type
    if name == "total":
        return [TOTAL_TEXT if v else None for v in arr.to_pylist()]
    if name == "kind":
        return [KIND_TITLES.get(v, v) for v in arr.to_pylist()]
    if pa.types.is_floating(t):
        if numeric:
            return arr.to_numpy(zero_copy_only=False).astype(np.float64, copy=False)  # a null → NaN → an empty cell
        return arr.to_pylist()
    if pa.types.is_integer(t):
        if numeric and arr.null_count == 0:
            return arr.to_numpy(zero_copy_only=False)
        return arr.to_pylist()
    return arr.to_pylist()


def iter_columns(path: Path, numeric: bool = False, batch: int = BATCH) -> Iterator[list[Any]]:
    """The batches of the file `data`: a list of columns in the order of the file (= `table_head`)."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    names = pf.schema_arrow.names
    for b in pf.iter_batches(batch_size=batch):
        yield [_column(b.column(i), n, numeric) for i, n in enumerate(names)]
