"""The files of the protocol of ore intervals (ТЗ 7.35 Д-26 «Результат» (4)–(5); ADR 0018 §9, решения 10, 19, 30;
подшаг 12.23): what goes into the XLSX book and the CSV files, and their writing — each file as `<name>.<random>.part`
next to the target, all renamed at the end (`os.replace`), so a cancel or a failure leaves no file.

XLSX («Рудные интервалы»): «Параметры» (the parameters of the result and of the export), «Интервалы» (the
rows of the result table as the table holds them, manual edits too), «Сводка» (by class, and by domain and
class: число, Σ M, среднее M, среднее C = Σ MC / Σ M, Σ MC — from those rows), «Протокол» (the long form,
`ore_protocol.LONG_HEAD`, a record per decision; past 1 048 575 rows — «Протокол (2)», …), «Скв. ‹имя›» (the
form И1, сл. 17 of up to 50 holes: `HoleProtocol.grid()` with the tones of the classes and the merged cells).
CSV (UTF-8 with BOM, «;», decimal comma — as the table export): the long form in the chosen file and the rows
in «‹имя› · интервалы.csv» next to it. «Анализ кондиций»: «Параметры» and «Кондиции» (И1, сл. 24) / one CSV.
Numbers that do not apply are empty cells, never zero.
"""

from __future__ import annotations

import csv
import math
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ore_xlsx import HEAD, NOTE, Book, tone_style

XLSX_ROWS = 1_048_575  # rows under the header of a sheet
HOLE_SHEETS = 50
PORTION = 50_000
Tick = Callable[[float | None, str | None], None]

CLASS_TITLES = {
    "ore": "руда",
    "subgrade": "некондиционная руда",
    "waste_in": "внутренняя пустая порода",
    "interlayer": "пустой прослой",
    "waste": "пустая порода",
    "frame": "обрамление",
}
SUMMARY_HEAD = ["Домен", "Класс", "Вложенный", "Число интервалов", "Σ M, м", "Среднее M, м", "Среднее C", "Σ MC"]
CONDITIONS_HEAD = [
    "Мрт, м",
    "Мпп, м",
    "Сб",
    "МС",
    "Смин",
    "Число интервалов",
    "Σ M, м",
    "Среднее M, м",
    "Среднее C",
    "Σ MC",
    "Шаг Σ M, %",
    "C прирезки",
    "Шаг вне 10–15 %",
    "Примечание",
]


@dataclass
class Table:
    """A sheet (or a CSV file): the header and the rows (a generator is read once)."""

    name: str
    head: list[str]
    rows: Iterable[Sequence[Any]]
    widths: list[float] | None = None
    note: str | None = None  # a line under the rows (italic)


@dataclass
class HoleSheet:
    """The form И1, сл. 17 of one hole (`HoleProtocol.grid()`)."""

    hole: str
    grid: dict[str, Any]


@dataclass
class Report:
    sheets: list[Table | HoleSheet] = field(default_factory=list)
    csv: list[tuple[Path, Table]] = field(default_factory=list)


def files_for(target: Path, fmt: str, op: str) -> list[Path]:
    """The files an export writes: XLSX — the chosen one; CSV of «Рудные интервалы» — also «… · интервалы.csv»."""
    if fmt == "csv" and op == "ore":
        return [target, target.with_name(f"{target.stem} · интервалы{target.suffix}")]
    return [target]


def _no_tick(_f: float | None, _m: str | None) -> None:
    pass


# --- the sheets ---------------------------------------------------------------------------------------------


def _num(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    return v


def params_table(rows: Sequence[tuple[str, Any]]) -> Table:
    return Table("Параметры", ["Параметр", "Значение"], [[k, _num(v)] for k, v in rows], widths=[42, 60])


def summary_table(
    cls: Sequence[Any],
    nested: Sequence[Any],
    m: Sequence[Any],
    mc: Sequence[Any],
    domain: Sequence[Any] | None,
    unit: str | None,
) -> Table:
    """«Сводка» of the rows of the table (the classes in their order, then by domain and class)."""
    from .ore_set import CLASS_NAMES

    groups: dict[tuple[Any, str], tuple[list[float], list[float], bool]] = {}
    for k, c in enumerate(cls):
        if c is None:
            continue
        dom = None if domain is None else domain[k]
        for key in ((None, str(c)), *(((dom, str(c)),) if dom not in (None, "") else ())):
            g = groups.setdefault(key, ([], [], bool(nested[k])))
            if m[k] is not None and math.isfinite(float(m[k])):
                g[0].append(float(m[k]))
            if mc[k] is not None and math.isfinite(float(mc[k])):
                g[1].append(float(mc[k]))

    def order(key: tuple[Any, str]) -> tuple[Any, ...]:
        d, c = key
        at = CLASS_NAMES.index(c) if c in CLASS_NAMES else len(CLASS_NAMES)
        return (d is not None, str(d) if d is not None else "", at)

    rows = []
    for key in sorted(groups, key=order):
        ms, mcs, is_nested = groups[key]
        sm, smc = math.fsum(ms), math.fsum(mcs)
        count = len(ms)
        rows.append(
            [
                "все" if key[0] is None else key[0],
                CLASS_TITLES.get(key[1], key[1]),
                "да" if is_nested else "",
                count,
                sm,
                sm / count if count else None,
                smc / sm if sm > 0 else None,
                smc,
            ]
        )
    head = list(SUMMARY_HEAD)
    if unit:
        head[6], head[7] = f"Среднее C, {unit}", f"Σ MC, м·{unit}"
    note = "Вложенные классы (внутренняя пустая порода, обрамление) лежат поверх других строк: их Σ M не складывается."
    return Table("Сводка", head, rows, widths=[16, 26, 11, 16, 12, 14, 14, 14], note=note)


def conditions_table(variants: Sequence[dict[str, Any]], unit: str | None) -> Table:
    """«Кондиции» — the variants of the analysis (И1, сл. 24) by (Мрт, Мпп, Сб)."""
    head = list(CONDITIONS_HEAD)
    if unit:
        head[2], head[8], head[9] = f"Сб, {unit}", f"Среднее C, {unit}", f"Σ MC, м·{unit}"
    keys = (
        "m_min",
        "m_waste_max",
        "cutoff",
        "mc_min",
        "c_min",
        "count",
        "m_sum",
        "m_mean",
        "c_mean",
        "mc_sum",
        "step_pct",
        "c_trim",
    )
    rows = [
        [*(_num(v.get(k)) for k in keys), "да" if v.get("step_flag") else "", v.get("mark") or None]
        for v in sorted(variants, key=lambda v: (v["m_min"], v["m_waste_max"], v["cutoff"]))
    ]
    return Table("Кондиции", head, rows, widths=[9, 9, 9, 9, 9, 12, 11, 12, 12, 12, 11, 11, 10, 60])


# --- XLSX ---------------------------------------------------------------------------------------------------


def _table_sheets(book: Book, t: Table, tick: Tick) -> None:
    rows = iter(t.rows)
    part = 1
    done = 0
    while True:
        with book.sheet(t.name if part == 1 else f"{t.name} ({part})", widths=t.widths) as sh:
            sh.row(t.head, style=HEAD)
            more = False
            for row in rows:
                sh.row(row)
                done += 1
                if done % PORTION == 0:
                    tick(None, f"«{t.name}»: строк {done:,}".replace(",", " "))
                if sh.rows > XLSX_ROWS:
                    more = True
                    break
            if t.note and not more:
                sh.row([])
                sh.row([t.note], style=NOTE)
        if not more:
            return
        part += 1


def _hole_sheet(book: Book, h: HoleSheet) -> None:
    g = h.grid
    head = g["head"]
    fixed = 6
    widths = [5.0, 8.0, 8.0, 7.0, 9.0, 30.0] + [18.0] * (len(head) - fixed)
    with book.sheet(f"Скв. {h.hole}", widths=widths) as sh:
        sh.row(head, style=HEAD)
        for texts, tones in zip(g["body"], g["tones"], strict=True):
            sh.row(texts, style=[tone_style(t) for t in tones])
        for r0, r1, col in g["merges"]:
            sh.merge(r0 + 1, r1 + 1, col)


def write_xlsx(path: Path, sheets: Sequence[Table | HoleSheet], tick: Tick = _no_tick) -> None:
    with Book(path) as book:
        for s in sheets:
            if isinstance(s, HoleSheet):
                _hole_sheet(book, s)
            else:
                _table_sheets(book, s, tick)


# --- CSV ----------------------------------------------------------------------------------------------------


def _text(v: Any) -> str:
    from bf_core.formats.numbers import number_text

    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        x = float(v)
        return "" if not math.isfinite(x) else number_text(x).replace(".", ",")
    return _safe(str(v))


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _safe(s: str) -> str:
    """Text that starts like a formula (= + - @, tab, CR) gets «'»: Excel shows it, never runs it (as
    `audit/report._cell`). Numbers are not text here: −1,5 stays a number."""
    return "'" + s if s[:1] in _FORMULA else s


def write_csv(path: Path, t: Table, tick: Tick = _no_tick) -> None:
    """UTF-8 with BOM (Excel opens it in Russian), «;», decimal comma."""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([_safe(str(h)) for h in t.head])
        batch: list[list[str]] = []
        done = 0
        for row in t.rows:
            batch.append([_text(v) for v in row])
            if len(batch) >= PORTION:
                w.writerows(batch)
                done += len(batch)
                batch = []
                tick(None, f"«{t.name}»: строк {done:,}".replace(",", " "))
        w.writerows(batch)


# --- the files ----------------------------------------------------------------------------------------------


def _part(target: Path) -> Path:
    """A new empty file of our own next to the target (`tempfile`): a «‹name›.part» of someone else is never hit."""
    fd, name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".part", dir=target.parent)
    os.close(fd)
    return Path(name)


def _swap(parts: Sequence[Path], targets: Sequence[Path]) -> None:
    """The parts become the targets, all or none: the targets there already are moved aside first and come back
    if a rename fails."""
    aside: list[tuple[Path, Path]] = []
    done: list[Path] = []
    try:
        for t in targets:
            if t.exists():
                b = _part(t)
                os.replace(t, b)
                aside.append((b, t))
        for p, t in zip(parts, targets, strict=True):
            os.replace(p, t)
            done.append(t)
    except BaseException:
        for t in done:
            t.unlink(missing_ok=True)
        for b, t in aside:
            os.replace(b, t)
        raise
    for b, _t in aside:
        b.unlink(missing_ok=True)


def write_files(targets: Sequence[Path], write: Callable[[list[Path]], None]) -> None:
    """`write(parts)` writes every file under a `.part` name of its own (`_part`); then all are renamed to the
    targets (`_swap`). A failure or a cancel (any exception) removes the parts and leaves the targets as they were."""
    parts: list[Path] = []
    try:
        parts = [_part(t) for t in targets]
        write(parts)
        _swap(parts, targets)
    except BaseException:
        for p in parts:
            p.unlink(missing_ok=True)
        raise
