"""CSV of a resource report (подшаг 20.5; ТЗ 8.10 «Вывод»; ADR 0027 решение 20): UTF-8 with BOM (Excel opens it in
Russian), «;», the decimal comma, CRLF, a gap — an empty cell (as the CSV of the protocol of ore intervals and the
CSV of Э17). Numbers are written in full precision (the shortest text that gives the same double), never rounded:
the files are for other programs; the shown, rounded numbers of a template are in the XLSX (the sheet «Отчёт»).

    write_table(path, head, batches, tick=None, rows=None) -> int      # the rows of the result (`export_cols`)
    write_form(path, form) -> int                                      # the form of a template (`forms.build`)
    files_for(target, what) -> [(path, "table" | "form")]              # `what`: table (default) | form | both
    cell_text(value) -> str                                            # the text of one cell

Text that starts like a formula (= + - @, tab, CR) gets «'» in front (Excel shows it, never runs it, as
`audit/report._cell` and the protocol of ore intervals): the names of the groups, the titles, a label of a template.
Numbers are not text: −1,5 stays a number.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bf_core.formats.numbers import number_text

from . import forms

WHATS = ("table", "form", "both")
FORM_SUFFIX = " · форма"
TABLE_SUFFIX = " · таблица"
_FORMULA = ("=", "+", "-", "@", "\t", "\r")
Tick = Callable[[float | None, str | None], None]


def safe(text: str) -> str:
    """Text that starts like a formula gets «'»."""
    return "'" + text if text[:1] in _FORMULA else text


def cell_text(v: Any) -> str:
    """One cell: a gap (None, NaN, ±inf) — empty; a number — its shortest text with the comma; a bool — 1 / 0."""
    if v is None:
        return ""
    if isinstance(v, str):
        return safe(v)
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int | float) or hasattr(v, "dtype"):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return safe(str(v))
        return number_text(x).replace(".", ",") if math.isfinite(x) else ""
    return safe(str(v))


def files_for(target: Path, what: str) -> list[tuple[Path, str]]:
    """The files of a CSV export: `table` — the chosen one; `form` — the chosen one; `both` — the chosen one is the
    table, «‹имя› · форма.csv» the form."""
    if what == "both":
        return [(target, "table"), (target.with_name(f"{target.stem}{FORM_SUFFIX}{target.suffix}"), "form")]
    return [(target, what)]


def write_table(path: Path, head: Sequence[str], batches: Iterable[Sequence[Any]], tick: Tick | None = None,
                rows: int | None = None) -> int:  # fmt: skip
    """The header and the batches (a list of columns each) as rows; returns the number of rows. `tick(fraction, text)`
    after each batch (`rows` — the total, for the fraction); a cancel raises there and the caller drops the file."""
    done = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([safe(str(h)) for h in head])
        for cols in batches:
            w.writerows(zip(*[[cell_text(v) for v in c] for c in cols], strict=True))
            done += len(cols[0]) if cols else 0
            if tick is not None:
                tick(None if not rows else min(done / rows, 1.0), f"Таблица: строк {done:,}".replace(",", " "))
    return done


def form_lines(form: Mapping[str, Any]) -> tuple[list[str], list[list[Any]]]:
    """The header and the rows of the form for a CSV: «Раздел» (only when the form has sections), «Строка», then
    the columns (a column of the balance tables is named «Балансовые: Руда, тыс. т»); the cells are the `value`s."""
    columns = form["columns"]
    sectioned = any(s["title"] is not None for s in form["sections"])
    head = [*(["Раздел"] if sectioned else []), "Строка"]
    head += [f"{c['group']}: {c['title']}" if c.get("group") else str(c["title"]) for c in columns]
    lines: list[list[Any]] = []
    for r in forms.rows_flat(form):
        cells = [None if c is None else c["value"] for c in r["cells"]]
        lines.append([*([r["section"]] if sectioned else []), r["label"], *cells])
    return head, lines


def write_form(path: Path, form: Mapping[str, Any]) -> int:
    """The form of the template as a CSV (the `value`s, not rounded); returns the number of rows."""
    head, lines = form_lines(form)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([safe(str(h)) for h in head])
        w.writerows([cell_text(v) for v in line] for line in lines)
    return len(lines)
