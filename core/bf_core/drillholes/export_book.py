"""A drillhole set as files (ТЗ 7.35 Д-17; ADR 0017 §5, decision 5): the writers of `export.objects` for a set.

* CSV or DAT — a folder of the set, a file per table: «Устья», «Инклинометрия», then the interval and event
  tables of the set by their names (`write_tables`);
* XLSX — one book of the set, a sheet per table (`write_book`); a table longer than a sheet
  (1 048 575 rows) goes to a CSV next to the book, with a warning.

Every table is written whole (no filter, no sort) with the names of its columns as they were in the imported
file and, for a DAT imported from a DAT, that file's fields (`export.write_table`). The snapshot of one table
is in memory at a time.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_tables

TITLES = {"collar": "Устья", "survey": "Инклинометрия"}
SHEET_NAME = 31
_SHEET_BAD = re.compile(r"[\[\]:*?/\\\x00-\x1f]")


def tables_of(conn: sa.Connection, set_id: int) -> list[tuple[str, str]]:
    """(table, title) of every table of a set: `collar`, `survey`, then the ids of `dh_tables` by id."""
    rows = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.name)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
        .order_by(dh_tables.c.id)
    ).all()
    return [("collar", TITLES["collar"]), ("survey", TITLES["survey"]), *((str(r.id), str(r.name)) for r in rows)]


def _read(engine: Any, set_id: int, table: str) -> tuple[list[Any], int, dict[str, Any] | None]:
    from .export import read_table, shown_columns

    with dialect.read_tx(engine) as conn:
        snap, source = read_table(conn, set_id, table)
    columns, n = shown_columns(snap, source)
    return columns, n, source


def _part(tick: Callable[..., None], start: float, stop: float) -> Callable[..., None]:
    def inner(f: float, message: str | None = None) -> None:
        tick(start + (stop - start) * max(0.0, min(1.0, f)), message)

    return inner


def write_tables(
    folder: Path, set_id: int, fmt: str, *, engine: Any, project_folder: Path | None, tick: Callable[..., None]
) -> tuple[list[Path], int, list[str]]:
    """The tables of a set as files of `fmt` (`csv` or `dat`) in `folder`: (files, rows, warnings)."""
    from bf_core.exporting import names

    from .export import write_table

    with dialect.read_tx(engine) as conn:
        tables = tables_of(conn, set_id)
    folder.mkdir(parents=True, exist_ok=True)
    taken = names.Folder(len(str(folder)))
    files: list[Path] = []
    warnings: list[str] = []
    rows = 0
    for k, (table, title) in enumerate(tables):
        part = _part(tick, k / len(tables), (k + 1) / len(tables))
        part(0.0, f"Таблица «{title}»")
        columns, n, source = _read(engine, set_id, table)
        target = folder / (taken.claim(title, (f".{fmt}",)) + f".{fmt}")
        found = write_table(target, fmt, columns, n, source=source, project_folder=project_folder, tick=part)
        warnings.extend(f"{title}: {w}" for w in found)
        files.append(target)
        rows += n
    return files, rows, warnings


def sheet_name(title: str, taken: set[str]) -> str:
    """A sheet name Excel takes: no `[ ] : * ? / \\`, not «'» at the ends, ≤ 31 characters, unique without case."""
    base = _SHEET_BAD.sub("_", title).strip("' ")[:SHEET_NAME].strip("' ") or "Таблица"
    name, n = base, 1
    while name.casefold() in taken:
        n += 1
        tail = f" ({n})"
        name = base[: SHEET_NAME - len(tail)].rstrip() + tail
    taken.add(name.casefold())
    return name


def write_book(
    path: Path, set_id: int, *, engine: Any, project_folder: Path | None, tick: Callable[..., None]
) -> tuple[list[Path], int, list[str]]:
    """The tables of a set as one XLSX book at `path` (written as `<name>.part`, renamed at the end): (files —
    the book first, then CSV of the tables too long for a sheet; rows; warnings)."""
    import os

    import openpyxl

    from bf_core.exporting import names

    from .export import XLSX_ROWS, sheet_rows, write_table

    with dialect.read_tx(engine) as conn:
        tables = tables_of(conn, set_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    part_path = path.with_name(path.name + ".part")
    wb = openpyxl.Workbook(write_only=True)
    sheets: set[str] = set()
    files: list[Path] = [path]
    warnings: list[str] = []
    rows = 0
    try:
        for k, (table, title) in enumerate(tables):
            part = _part(tick, 0.9 * k / len(tables), 0.9 * (k + 1) / len(tables))
            part(0.0, f"Таблица «{title}»")
            columns, n, source = _read(engine, set_id, table)
            rows += n
            if n > XLSX_ROWS:
                stem = names.shorten(f"{path.stem} — {names.clean(title)}", names.MAX_PATH - len(str(path.parent)) - 5)
                csv_path = path.parent / f"{stem}.csv"
                write_table(csv_path, "csv", columns, n, source=source, project_folder=project_folder, tick=part)
                files.append(csv_path)
                warnings.append(
                    f"Таблица «{title}» длиннее листа Excel ({n:,} строк): записана в «{csv_path.name}»".replace(
                        ",", " "
                    )
                )
                continue
            sheet_rows(wb.create_sheet(sheet_name(title, sheets)), columns, n, part)
        if not sheets:  # a book has at least one sheet
            wb.create_sheet("Таблица")
        tick(0.95, "Запись книги")
        wb.save(part_path)
        os.replace(part_path, path)
    finally:
        part_path.unlink(missing_ok=True)
    return files, rows, warnings
