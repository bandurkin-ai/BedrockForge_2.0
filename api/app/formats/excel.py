"""Excel workbooks (.xlsx, .xlsm)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from app.formats.tabular import ReadOptions, TableRead, frame_from_rows


def read_excel(path: Path, options: ReadOptions) -> TableRead:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = workbook.sheetnames
        if options.sheet is not None and options.sheet not in sheets:
            raise ValueError(f"В книге нет листа «{options.sheet}»")
        # By default the first sheet that has any data.
        name = options.sheet
        rows: list[list] = []
        for candidate in [options.sheet] if options.sheet else sheets:
            rows = [list(r) for r in workbook[candidate].iter_rows(values_only=True)]
            if any(any(v not in (None, "") for v in r) for r in rows):
                name = candidate
                break
        name = name or sheets[0]
        # Trailing empty rows are common: trim them before choosing the header.
        while rows and all(v in (None, "") for v in rows[-1]):
            rows.pop()
    finally:
        workbook.close()

    notes: list[str] = []
    frame = frame_from_rows(rows, options.header_row, notes)
    return TableRead(frame, "excel", {"sheet": name, "header_row": options.header_row}, sheets=sheets, notes=notes)
