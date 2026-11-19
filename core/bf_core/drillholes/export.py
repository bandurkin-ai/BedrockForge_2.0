"""Export of a drillhole table to DAT / CSV / XLSX (ТЗ 9.12; 7.9 «Журнал операции»).

Job `export.table`: the rows shown in the panel (selected hole, filter, sort) or the whole table. Column names
come back as they were in the imported file (its column assignment is in the import history), and a table
imported from a Micromine DAT is written with that file's fields — types, widths, decimals, descriptions and
code page — so «импорт → экспорт» gives the same file (ТЗ 9.12 «цикл без потерь»).

The file is written next to the target as `<name>.part` and renamed at the end: a cancel or a failure never
leaves half a file under the chosen name.
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import export_log, import_log, objects
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

FORMATS = ("dat", "csv", "xlsx")
XLSX_ROWS = 1_048_575
#: Names of fixed columns when the table did not come from a file (Micromine habits).
DEFAULT_NAMES = {
    "hole": "HOLEID",
    "x": "EAST",
    "y": "NORTH",
    "z": "RL",
    "td": "DEPTH",
    "azimuth": "AZIMUTH",
    "dip": "DIP",
    "depth": "DEPTH",
    "from": "FROM",
    "to": "TO",
}
#: Import field key → snapshot column key.
FIELD_KEYS = {
    "hole_id": "hole",
    "x": "x",
    "y": "y",
    "z": "z",
    "td": "td",
    "azimuth": "azimuth",
    "dip": "dip",
    "depth": "depth",
    "from": "from",
    "to": "to",
}


PORTION = 50_000


def _memory(params: dict[str, Any]) -> int:
    """The snapshot of the table (≈ 0,3 KB a row with text) plus a portion being written."""
    rows = int(params.get("rows") or 0)
    return max(512, int(rows * 300 / 2**20) + 256)


def _source(conn: sa.Connection, set_id: int, kind: str, name: str) -> dict[str, Any] | None:
    """The latest import of this table: its column assignment and source file (for names and DAT fields)."""
    rows = conn.execute(
        sa.select(import_log)
        .where(import_log.c.object_id == set_id, import_log.c.target == kind)
        .order_by(import_log.c.id.desc())
    ).mappings()
    for r in rows:
        m = r["mapping"] or {}
        if kind in ("collar", "survey", "assay") or dialect.name_key(m.get("table_name") or "") == dialect.name_key(
            name
        ):
            return dict(r)
    return None


def _columns(snap: Any, keys: list[str] | None, source: dict[str, Any] | None, idx: np.ndarray) -> list[Any]:
    import pyarrow as pa

    from bf_core.formats.table import Column

    mapping = ((source or {}).get("mapping") or {}).get("mapping") or {}
    renamed = {FIELD_KEYS[k]: v for k, v in mapping.items() if k in FIELD_KEYS}
    sample_src = mapping.get("sample_id")
    out = []
    for c in snap.columns:
        if keys and c.key not in keys:
            continue
        if c.key.startswith("a:"):
            name = c.name
        elif c.role == "fixed":
            name = renamed.get(c.key) or DEFAULT_NAMES.get(c.key, c.name)
        elif c.role == "sample" and sample_src:
            name = sample_src
        else:
            name = c.name
        values = snap.values(c.key)[idx]
        if c.kind == "number":
            out.append(Column(name, "number", np.asarray(values, dtype=float)))
        else:
            out.append(
                Column(
                    name,
                    "text",
                    pa.chunked_array(
                        [pa.array([None if v in (None, "") else str(v) for v in values], type=pa.string())]
                    ),
                )
            )
    return out


def _dat_like(project_folder: Path | None, source: dict[str, Any] | None) -> Any:
    """The header of the imported DAT file, when it is still in the project storage."""
    if not source or source.get("format") != "dat" or not source.get("sha256") or not project_folder:
        return None
    from bf_core.formats.micromine_header import read_header
    from bf_core.storage.blobs import BlobStore

    try:
        return read_header(BlobStore(project_folder).path_of(source["sha256"]))
    except Exception:  # noqa: BLE001 - no header: fields are derived from the data
        return None


def _write_dat(path: Path, columns: list[Any], like: Any, tick: Any) -> list[str]:
    from bf_core.formats.micromine_write import derive_fields, write_dat

    if like is None:
        return write_dat(path, columns, progress=tick).warnings
    by_name = {f.name.casefold(): f for f in like.fields}
    derived, _w = derive_fields(columns, like.encoding if like.encoding != "bocu-1" else "cp1251")
    fields = [by_name.get(c.name.casefold(), d) for c, d in zip(columns, derived, strict=True)]
    kw: dict[str, Any] = {"fields": fields, "extended": like.extended, "progress": tick}
    if like.codepage:
        kw["codepage"] = like.codepage
    return write_dat(path, columns, **kw).warnings


def _cell_text(v: Any, kind: str, decimal: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if kind == "number":
        from bf_core.formats.numbers import number_text

        text = number_text(float(v))
        return text.replace(".", decimal) if decimal != "." else text
    return str(v)


def _write_csv(path: Path, columns: list[Any], n: int, delimiter: str, decimal: str, encoding: str, tick: Any) -> None:
    with path.open("w", encoding=encoding, newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow([c.name for c in columns])
        for start in range(0, n, PORTION):
            tick(start / max(n, 1), f"Строк записано: {start:,}".replace(",", " "))
            stop = min(n, start + PORTION)
            parts = [[_cell_text(v, c.kind, decimal) for v in _portion(c, start, stop)] for c in columns]
            w.writerows(zip(*parts, strict=True))


def too_many_rows() -> JobError:
    return JobError(
        "too_many_rows",
        f"В листе Excel не больше {XLSX_ROWS:,} строк.".replace(",", " "),
        "Выберите DAT или CSV, или задайте фильтр.",
    )


def sheet_rows(ws: Any, columns: list[Any], n: int, tick: Any) -> None:
    """The header and `n` rows of `columns` appended to a write-only sheet, portion by portion."""
    ws.append([c.name for c in columns])
    for start in range(0, n, PORTION):
        tick(start / max(n, 1), f"Строк записано: {start:,}".replace(",", " "))
        stop = min(n, start + PORTION)
        parts = [_portion(c, start, stop) for c in columns]
        for row in zip(*parts, strict=True):
            ws.append([None if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in row])


def _write_xlsx(path: Path, columns: list[Any], n: int, tick: Any) -> None:
    import openpyxl

    if n > XLSX_ROWS:
        raise too_many_rows()
    wb = openpyxl.Workbook(write_only=True)
    sheet_rows(wb.create_sheet("Таблица"), columns, n, tick)
    wb.save(path)


def _portion(col: Any, start: int, stop: int) -> list[Any]:
    """Values of rows start…stop of a column (numbers as floats, text as str/None) — a portion, not the whole."""
    if col.kind == "number":
        return col.values[start:stop].tolist()  # type: ignore[no-any-return]
    return col.values.slice(start, stop - start).to_pylist()  # type: ignore[no-any-return]


def read_table(conn: sa.Connection, set_id: int, table: str) -> tuple[Any, dict[str, Any] | None]:
    """The snapshot of a table of a set (`collar`, `survey` or the id of `dh_tables`) and its latest import."""
    from bf_core.db.schema import dh_tables

    from .snapshot import build_collars, build_rows, build_surveys

    if table == "collar":
        snap = build_collars(conn, set_id, ())
    elif table == "survey":
        snap = build_surveys(conn, set_id, ())
    else:
        snap = build_rows(conn, set_id, int(table), (), None)
    name = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id)).scalar() or ""
    tname = (
        name
        if table in ("collar", "survey")
        else conn.execute(sa.select(dh_tables.c.name).where(dh_tables.c.id == int(table))).scalar() or ""
    )
    return snap, _source(conn, set_id, snap.kind, tname)


def shown_columns(
    snap: Any,
    source: dict[str, Any] | None,
    *,
    hole: int | None = None,
    where: str | None = None,
    sort: str | None = None,
    desc: bool = False,
    columns: list[str] | None = None,
) -> tuple[list[Any], int]:
    """The columns to write (names as in the imported file) of the rows shown (hole, filter, sort) and their
    number. JobError `bad_expression` — the filter does not parse."""
    from .expr import ExprError
    from .query import _order, mask

    try:
        m = mask(snap, hole, where)
    except ExprError as exc:
        raise JobError("bad_expression", str(exc), "Проверьте выражение фильтра.") from exc
    if sort:
        order = _order(snap, sort, bool(desc))
        idx = order[m[order]]
    else:
        idx = np.flatnonzero(m)
    return _columns(snap, columns, source, idx), int(len(idx))


def write_table(
    target: Path,
    fmt: str,
    columns: list[Any],
    n: int,
    *,
    source: dict[str, Any] | None = None,
    project_folder: Path | None = None,
    delimiter: str | None = None,
    decimal: str | None = None,
    encoding: str | None = None,
    tick: Any = None,
) -> list[str]:
    """`n` rows of `columns` as one file of `fmt` (DAT, CSV, XLSX) under `target`, written as `<name>.part` and
    renamed at the end. A table imported from a DAT that is still in the project storage is written with that
    file's fields. Returns the warnings; JobError `encoding`, `file_unwritable`, `too_many_rows`."""
    from bf_core.formats.table import no_progress

    tick = tick or no_progress
    part = target.with_name(target.name + ".part")
    warnings: list[str] = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "dat":
            warnings = _write_dat(part, columns, _dat_like(project_folder, source), tick)
        elif fmt == "csv":
            _write_csv(part, columns, n, delimiter or ";", decimal or ",", encoding or "utf-8-sig", tick)
        else:
            _write_xlsx(part, columns, n, tick)
        os.replace(part, target)
    except (LookupError, UnicodeEncodeError) as exc:
        part.unlink(missing_ok=True)
        raise JobError(
            "encoding",
            f"Значения не записываются в кодировке «{encoding or 'utf-8-sig'}».",
            "Выберите кодировку UTF-8.",
        ) from exc
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise JobError(
            "file_unwritable",
            f"Файл не записан: {exc.strerror or exc}.",
            "Выберите другую папку или закройте файл в другой программе.",
        ) from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return warnings


@handler("export.table", "Экспорт таблицы", queue="heavy", estimate_memory=_memory)
def export_table(ctx: JobContext) -> dict[str, Any]:
    p = ctx.params
    fmt = p["format"]
    target = Path(p["path"])
    if target.suffix.lower() != f".{fmt}":
        target = target.with_name(target.name + f".{fmt}")
    set_id, table = int(p["set_id"]), str(p["table"])
    engine = ctx.engine()
    ctx.tick(0.0, "Чтение таблицы")
    with dialect.read_tx(engine) as conn:
        snap, source = read_table(conn, set_id, table)
    columns, n = shown_columns(
        snap, source, hole=p.get("hole"), where=p.get("where"), sort=p.get("sort"), desc=bool(p.get("desc")),
        columns=p.get("columns"),
    )  # fmt: skip
    del snap
    warnings = write_table(
        target, fmt, columns, n, source=source, project_folder=ctx.project_folder, delimiter=p.get("delimiter"),
        decimal=p.get("decimal"), encoding=p.get("encoding"), tick=lambda f, msg=None: ctx.tick(f, msg),
    )  # fmt: skip
    for w in warnings:
        ctx.log(w, "warn")
    with dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(export_log).values(
                object_id=set_id,
                job_id=ctx.id,
                file_name=target.name,
                format=fmt,
                settings={
                    k: p.get(k)
                    for k in ("table", "hole", "where", "sort", "desc", "columns", "delimiter", "decimal", "encoding")
                },
                counts={"rows": n, "columns": len(columns)},
                created_at=dialect.utcnow(),
            )
        )
    ctx.tick(1.0, "Готово")
    return {"path": str(target), "rows": n, "columns": len(columns), "warnings": warnings}
