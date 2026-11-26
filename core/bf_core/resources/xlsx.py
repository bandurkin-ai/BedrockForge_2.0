"""The XLSX book of a resource report (подшаг 20.5; ТЗ 8.10 «Вывод»; ADR 0027 решения 19, 20) — `ore_xlsx.Book`, the
brand styles of ADR 0006 (the same calls give the same bytes).

    stats = write_book(path, BookData(...), tick=None, limit=MAX_LINES)      # → {"rows": …, "sheets": …}

Sheets, in this order:
  «Отчёт»      the form of the template (`forms.build`): the title, the notes, the table. A number is the `value`
               of the cell of the form (float64, NOT rounded) in the number format of the decimals of its column
               (`forms.excel_format`), so Excel shows what `text` of the cell is (Т8.10-5: one source of the
               numbers); a missing value is an empty cell, never 0. The subtotals and the total — the bold head
               style. Sections of the form — a line with the title; the balance tables — the group line
               «Балансовые» / «Забалансовые».
  «Таблица»    every row of the result (the Parquet `data`), streamed by batches (`export_cols`): the numbers as
               they are in the file; a table longer than a sheet (`limit` lines) goes on «Таблица (2)», … with its
               header again.
  «Кривая»     the curve «тоннаж — содержание» (100 cut-offs): cut-off, blocks, volume, tonnes, grade and metal of each
               element; a report without a curve — a note.
  «Контроль»   the control (`control.py`): Σ of the groups − the total, density, gaps of the grades, the status of the
               estimate, the wireframes, the warnings.
  «Параметры»  what the report was made of: the model and its `cells_sha`, the fields with their fingerprints, the
               density, the restrictors (`title_of`), the cut-offs, the groups, the template, the dates, the version of
               the program, ALGO.
Text that starts like a formula gets «'» (`Book(guard=True)`). A gap (None, NaN) is an empty cell. «Параметры» goes
last: the data sheets do not depend on the time of the export.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.composites.ore_xlsx import HEAD, MAX_ROWS, NOTE, Book, Sheet, num_fmt

from . import export_cols as ec
from . import forms

MAX_LINES = MAX_ROWS - 1  # the lines of a sheet of Excel under the header
SHEETS = ("Отчёт", "Таблица", "Кривая", "Контроль", "Параметры")
Tick = Callable[[float | None, str | None], None]


def _no_tick(_f: float | None, _m: str | None) -> None:
    pass


@dataclass
class BookData:
    """What the book shows: the form (`forms.build`), the file `data` (Parquet), the curve and the control (as the
    store reads them), the rows of «Параметры» and the notes under the title of «Отчёт» (the model, the cut-off, the
    template)."""

    name: str
    form: Mapping[str, Any]
    data_path: Path
    keys: Sequence[str]
    elements: Sequence[tuple[str, str | None]]
    curve: Mapping[str, Any] | None = None
    control: Mapping[str, Any] | None = None
    params: Sequence[tuple[str, Any]] = ()
    notes: Sequence[str] = field(default_factory=tuple)


def _value(v: Any) -> Any:
    """A value of a cell of «Параметры» / «Контроль»: text, a finite number or None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, int | str):
        return v
    if isinstance(v, list | tuple):
        return "; ".join(str(_value(x) if _value(x) is not None else "") for x in v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


# --- «Отчёт» ----------------------------------------------------------------------------------------------------------


def _report_sheet(book: Book, data: BookData) -> int:
    form = data.form
    columns = form["columns"]
    grouped = any(c.get("group") for c in columns)
    lead = 1 + len(data.notes) + 1 + (1 if grouped else 0) + 1  # title, notes, a gap, [groups], header
    widths = [34.0, *[max(14.0, min(34.0, len(str(c["title"])) * 0.9 + 2)) for c in columns]]
    styles: dict[tuple[str, int], int] = {}

    def style(typ: str, decimals: int) -> int:
        base = HEAD if typ in ("subtotal", "total") else 0
        key = (str(base), decimals)
        if key not in styles:
            styles[key] = book.style(fmt=forms.excel_format(decimals), base=base)
        return styles[key]

    rows = 0
    with book.sheet("Отчёт", widths=widths, freeze=lead) as sh:
        sh.row([data.name], style=HEAD)
        for note in data.notes:
            sh.row([note], style=NOTE)
        sh.row([])
        if grouped:
            line, last = [None], None
            for c in columns:
                g = c.get("group")
                line.append(g if g != last else None)
                last = g
            sh.row(line, style=HEAD)
            start = None
            for k, c in enumerate(columns):  # the group title spans the columns of its table
                g = c.get("group")
                if g is not None and start is None:
                    start = k
                if start is not None and (k + 1 == len(columns) or columns[k + 1].get("group") != g):
                    if k > start:
                        sh.merge(sh.rows - 1, sh.rows - 1, start + 1, k + 1)
                    start = None
        sh.row(["Строка", *[c["title"] for c in columns]], style=HEAD)
        keys = [c["key"] for c in columns]
        for sec in form["sections"]:
            if sec["title"] is not None:
                sh.row([sec["title"]], style=HEAD)
            for r in sec["rows"]:
                cells = [r["cells"].get(k) for k in keys]
                label_style = HEAD if r["type"] in ("subtotal", "total") else 0
                sh.row([r["label"], *[None if c is None else c["value"] for c in cells]],
                       style=[label_style, *[style(r["type"], int(c["decimals"])) for c in columns]])  # fmt: skip
                rows += 1
        if form.get("warnings"):
            sh.row([])
            for w in form["warnings"]:
                sh.row([f"Предупреждение: {w}"], style=NOTE)
    return rows


# --- «Таблица» --------------------------------------------------------------------------------------------------------


def _table_sheets(book: Book, data: BookData, tick: Tick, limit: int, lo: float, hi: float) -> tuple[int, int]:
    import pyarrow.parquet as pq

    head = ec.table_head(data.keys, data.elements)
    total = int(pq.ParquetFile(data.data_path).metadata.num_rows)
    widths = [18.0 if i < len(data.keys) else 14.0 for i in range(len(head))]
    sheets, done = 0, 0
    sh: Sheet | None = None
    for cols in ec.iter_columns(data.data_path, numeric=True):  # an error here: the caller's `Book.abort` closes
        n = len(cols[0]) if cols else 0
        at = 0
        while at < n:
            if sh is None or sh.rows - 1 >= limit:  # the header counts as a line
                if sh is not None:
                    sh.close()
                sh = book.sheet("Таблица", widths=widths)
                sh.row(head, style=HEAD)
                sheets += 1
            take = min(n - at, limit - (sh.rows - 1))
            sh.table([c[at : at + take] for c in cols])
            at += take
        done += n
        tick(lo + (hi - lo) * (done / total if total else 1.0), f"Таблица: строк {done:,}".replace(",", " "))
    if sh is None:  # a file with no rows: the header only
        sh = book.sheet("Таблица", widths=widths)
        sh.row(head, style=HEAD)
        sheets += 1
    sh.close()
    return done, sheets


# --- «Кривая» ---------------------------------------------------------------------------------------------------------


def _floats(values: Sequence[Any]) -> np.ndarray:
    return np.asarray([np.nan if v is None else v for v in values], dtype=np.float64)


def _curve_sheet(book: Book, data: BookData) -> None:
    curve = data.curve
    if not curve or not curve.get("cutoffs"):
        with book.sheet("Кривая", widths=[80.0], freeze=0) as sh:
            sh.row(["Кривая «тоннаж — содержание» не рассчитана: в параметрах отчёта задано 0 точек."], style=NOTE)
        return
    units = dict(data.elements)
    names = list(curve.get("elements") or {})
    head = ["Борт", "Блоков", "Объём, м³", "Масса, т"]
    cols: list[Any] = [_floats(curve["cutoffs"]), np.asarray(curve["blocks"], dtype=np.int64),
                       _floats(curve["volume"]), _floats(curve["tonnes"])]  # fmt: skip
    for e in names:
        unit = units.get(e)
        grade, metal = ec.unit_label(unit), ec.metal_label(unit)
        head += [f"{e}: содержание, {grade}" if grade else f"{e}: содержание",
                 f"{e}: металл, {metal}" if metal else f"{e}: металл"]  # fmt: skip
        cols += [_floats(curve["elements"][e]["grade"]), _floats(curve["elements"][e]["metal"])]
    with book.sheet("Кривая", widths=[14.0] * len(head)) as sh:
        sh.row(head, style=HEAD)
        sh.table(cols)


# --- «Контроль» -------------------------------------------------------------------------------------------------------


def _control_sheet(book: Book, data: BookData) -> None:
    ctl = data.control
    sci = book.style(fmt=num_fmt(2, "sci"))
    pct = book.style(fmt=num_fmt(2, "percent"))
    with book.sheet("Контроль", widths=[46.0, 18.0, 18.0, 18.0, 18.0], freeze=0) as sh:
        if not ctl:
            sh.row(["Контроль для этого отчёта не записан."], style=NOTE)
            return

        def head(*titles: str) -> None:
            sh.row(list(titles), style=HEAD)

        sg = ctl.get("sum_groups") or {}
        head("Сумма по группам − итог", "Сумма групп", "Итог", "Относительная разница", "В допуске")
        if sg.get("checked", True) and sg.get("tonnes"):
            tol = float(ctl.get("tolerance") or 1e-12)
            lines = [("Масса, т", sg.get("tonnes")), ("Объём, м³", sg.get("volume")),
                     *((f"Металл {e}", v) for e, v in (sg.get("metal") or {}).items())]  # fmt: skip
            for title, v in lines:
                if not v:
                    continue
                rel = float(v["rel"])
                sh.row([title, v["sum"], v["total"], rel, "да" if rel <= tol else "нет"], style=[0, 0, 0, sci, 0])
        else:
            sh.row(["Не проверялось: в отчёте нет групп."], style=NOTE)
        sh.row(["Допуск", ctl.get("tolerance")], style=[0, sci])
        sh.row([])
        nd = ctl.get("no_density") or {}
        head("Блоки без плотности", "Блоков", "Объём, м³")
        sh.row(["Их масса неизвестна (не 0)", nd.get("blocks"), nd.get("volume")])
        dd = ctl.get("default_density")
        if dd is not None:
            sh.row([])
            head("Плотность домена по умолчанию (DENS_SRC = 1)", "Блоков", "Масса, т")
            sh.row(["Блоки с плотностью по умолчанию", dd.get("blocks"), dd.get("tonnes")])
        sh.row([])
        head("Блоки без содержания", "Блоков", "Масса, т")
        for e, v in (ctl.get("no_grade") or {}).items():
            sh.row([e, v.get("blocks"), v.get("tonnes")])
        status = ctl.get("status") or {}
        if status:
            sh.row([])
            head("Статус оценки", "Код", "Блоков", "Объём, м³", "Доля объёма ограничителя")
            for e, part in status.items():
                for it in part.get("items") or []:
                    sh.row([f"{e}: {it.get('label')}", it.get("code"), it.get("blocks"), it.get("volume"),
                            it.get("share")], style=[0, 0, 0, 0, pct])  # fmt: skip
        wires = ctl.get("wireframes") or []
        if wires:
            sh.row([])
            head("Каркасы в ограничителе", "Объём в ограничителе, м³", "Объём каркаса, м³", "Разница, %", "Замечание")
            for w in wires:
                note = ("за охватом модели" if w.get("outside_extent") else "") + (
                    "; " if w.get("outside_extent") and w.get("warning") else "") + (
                    "объём отличается" if w.get("warning") else "")  # fmt: skip
                sh.row([w.get("name"), w.get("volume_restrictor"), w.get("volume_wireframe"), w.get("diff_pct"),
                        note or None])  # fmt: skip
        warns = ctl.get("warnings") or []
        if warns:
            sh.row([])
            head("Предупреждения", "Подсказка")
            for w in warns:
                sh.row([w.get("message"), w.get("hint")])


# --- «Параметры» ------------------------------------------------------------------------------------------------------


def _params_sheet(book: Book, data: BookData) -> None:
    with book.sheet("Параметры", widths=[44.0, 80.0]) as sh:
        sh.row(["Параметр", "Значение"], style=HEAD)
        for k, v in data.params:
            sh.row([k, _value(v)])


# --- the book ---------------------------------------------------------------------------------------------------------


def write_book(path: Path, data: BookData, tick: Tick | None = None, limit: int | None = None) -> dict[str, int]:
    """The five sheets into `path` (the caller writes aside and renames: a failure or a cancel — `tick` raises —
    leaves a broken file only under that name). → {rows (of the table), sheets (of the table), form_rows}."""
    tick, limit = tick or _no_tick, MAX_LINES if limit is None else limit
    with Book(path, guard=True) as book:
        tick(0.02, "Лист «Отчёт»")
        form_rows = _report_sheet(book, data)
        rows, sheets = _table_sheets(book, data, tick, limit, 0.05, 0.9)
        tick(0.92, "Листы «Кривая», «Контроль», «Параметры»")
        _curve_sheet(book, data)
        _control_sheet(book, data)
        _params_sheet(book, data)
    return {"rows": rows, "sheets": sheets, "form_rows": form_rows}
