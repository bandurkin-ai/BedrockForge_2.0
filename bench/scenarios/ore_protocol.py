"""The protocol of ore intervals to XLSX — the whole job `composite.ore_protocol` (ADR 0018 §9, решения 10, 19;
ТЗ 7.35 Д-26 «Результат» (4); подшаг 12.23): 5 тыс. скважин, 500 тыс. проб — ci only (the long form of 5 million
samples is past the rows of a sheet many times over: a book of that size is not what the export is for).

The set of `_samples` with the result «Рудные интервалы» of `ore_intervals` on it (made in `prepare` by the job
itself; «Пробы» opened once after it — its snapshot is of the revision with ORE_ID and ORE_CLS, решение 16).
The route `POST /composites/results/{id}/protocol` checks the request (`parse_export`), then the job — the
function its handler calls, `ore_protocol_job.run_export` — writes the book into a folder with a Cyrillic name:
«Параметры», «Интервалы» (the rows of the table), «Сводка», «Протокол» (the long form: the protocol is not
stored — the kernel runs again with the events of every decision) and the form И1, сл. 17 of `shown` holes
«Скв. …» (50 — the most a book takes):
  xlsx_s         the request checked and the job to its end (the file in place, the row of `export_log`);
  xlsx_read_s    `read_ore`: the samples;  xlsx_lines_s — the lines of the kernel;  xlsx_table_s — the result table
                 read for «Интервалы» and «Сводка»;  xlsx_forms_s — the forms of the holes;
  xlsx_long_s    the kernel with events and the records of the long form, block by block (inside the writing);
  xlsx_write_s   the sheets written and the file put in place, with `xlsx_long_s`;
  records        records of «Протокол»;  intervals — rows of «Интервалы»;  file_mb — the book;
  xlsx_mb        the peak of the process at the end of the job (VmHWM);  xlsx_tick_gap_s — the longest wait
                 between two ticks.
What is checked (a failure stops the run): the book is a zip with the sheets in that order; «Интервалы» has a
row for every row of the table and the header, «Протокол» — for every record (a second sheet past 1 048 575
rows); the 50 sheets of the holes are there; «Сводка» read back from the zip gives Σ M of the classes with
NESTED = 0 equal to the length of the samples with a grade, and Σ MC to their metal Σ l·g — both from the arrays
of the generator, within 1e-9; one row of `export_log`.
"""

from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path
from typing import Any

from . import _samples, ore_intervals

STAGES: dict[str, tuple[str, ...]] = {"read": ("read",), "lines": ("lines",), "table": ("table",),
                                      "forms": ("forms",), "long": ("long",), "write": ("write",)}  # fmt: skip
FOLDER, FILE = "Выгрузка", "Протокол рудных интервалов.xlsx"


def with_result(params: dict[str, Any]) -> _samples.More:
    """`more` of `_samples.build`: the ore intervals on the set, «Пробы» opened once after them."""

    def more(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
        from bf_core.composites import ore_job

        out = ore_job.run_ore(engine, folder, {"body": ore_intervals.body(meta, params), "rows": meta["rows"]},
                              job_id=1, tick=_samples._noop)  # fmt: skip
        _samples.snapshot(engine, folder, meta["set_id"], [meta["samples"]])
        return {"result_id": out["result_id"], "ore_table": out["table_id"], "ore_rows": out["rows"]}

    return more


def hole_names(meta: dict[str, Any], shown: int) -> list[str]:
    """`shown` holes spread over the set (`tools/gen/drillholes.py::arrays` names them)."""
    step = max(1, meta["holes"] // max(shown, 1))
    return [f"СКВ-{k + 1:05d}" for k in range(0, meta["holes"], step)][:shown]


def export(
    meta: dict[str, Any], engine: Any, folder: Path, target: Path, shown: int, job_id: int = 2
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks]:
    """The check of the route and `run_export` with the clocks of its stages."""
    from bf_core.composites import ore_conditions_job, ore_job, ore_protocol, ore_protocol_job, ore_report, routes_ore
    from bf_core.db import dialect

    long_form = ore_protocol.long_form
    spent = {"long": 0.0}

    def blocks(*args: Any, **kw: Any) -> Any:  # the generator of the long form: its time is inside the writing
        found = iter(long_form(*args, **kw))
        while True:
            t0 = time.perf_counter()
            try:
                block = next(found)
            except StopIteration:
                return
            finally:
                spent["long"] += time.perf_counter() - t0
            yield block

    ticks = _samples.Ticks()
    ore_protocol.long_form = blocks  # type: ignore[assignment]
    try:
        with _samples.stages(
            (ore_job, "read_ore", "read"), (ore_conditions_job, "ore_lines", "lines"),
            (ore_protocol_job, "_intervals", "table"), (ore_protocol, "hole_protocol", "forms"),
            (ore_report, "write_files", "write"),
        ) as took:  # fmt: skip
            t0 = time.perf_counter()
            with dialect.read_tx(engine) as conn:
                body = {"format": "xlsx", "path": str(target), "holes": hole_names(meta, shown), "overwrite": True}
                rec = routes_ore.record_of(conn, meta["result_id"])
                params, _title = ore_protocol_job.parse_export(conn, rec, body)
            out = ore_protocol_job.run_export(engine, folder, params, job_id=job_id, tick=ticks)
            took["done"] = time.perf_counter() - t0
    finally:
        ore_protocol.long_form = long_form
    took["long"] = spent["long"]
    return out, took, ticks


def sheets(path: Path) -> list[tuple[str, int]]:
    """(name, rows) of every sheet of the book, read from the zip (the rows are counted, not parsed)."""
    out = []
    with zipfile.ZipFile(path) as z:
        names = re.findall(r'<sheet name="([^"]*)"', z.read("xl/workbook.xml").decode("utf-8"))
        for k, name in enumerate(names):
            rows, tail = 0, b""
            with z.open(f"xl/worksheets/sheet{k + 1}.xml") as fh:
                while chunk := fh.read(1 << 22):
                    rows += (tail + chunk).count(b"<row ")
                    tail = chunk[-4:]
            out.append((name, rows))
    return out


def summary(path: Path) -> tuple[float, float]:
    """Σ M and Σ MC of the classes that are not nested — the rows «все» of the sheet «Сводка» (`ore_report.
    summary_table`: Домен, Класс, Вложенный, Число, Σ M, Среднее M, Среднее C, Σ MC), read from the zip: the
    cells of the sheet and the shared strings they point at."""
    import html
    import math

    with zipfile.ZipFile(path) as z:
        names = re.findall(r'<sheet name="([^"]*)"', z.read("xl/workbook.xml").decode("utf-8"))
        sheet = z.read(f"xl/worksheets/sheet{names.index('Сводка') + 1}.xml").decode("utf-8")
        strings = re.findall(rb"<si><t[^>]*>(.*?)</t></si>", z.read("xl/sharedStrings.xml"), re.S)

    def value(kind: str, raw: str) -> Any:
        if kind == "s":
            return html.unescape(strings[int(raw)].decode("utf-8"))
        return float(raw) if raw else None

    cell = re.compile(r'<c(?: s="\d+")?(?: t="(\w)")?(?:/>|><v>([^<]*)</v></c>)')
    lines = re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S)
    rows = [[value(*found) for found in cell.findall(line)] for line in lines]
    assert str(rows[0][4]).startswith("Σ M") and str(rows[0][7]).startswith("Σ MC"), rows[0]
    main = [r for r in rows[1:] if len(r) >= 8 and r[0] == "все" and not r[2]]
    assert main and len(main) < len(rows) - 2, rows[:4]  # the nested classes and the note are there too
    return math.fsum(r[4] for r in main), math.fsum(r[7] for r in main)


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()  # the job of `more` compiles (or loads) its kernels here, as `child` does
    _samples.build(work, params, more=with_result(params))


def load() -> None:
    import tempfile

    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="bf-bench-warm-") as tmp:
            target = Path(tmp) / FILE
            export(meta, engine, folder, target, 3)
            sheets(target)
            summary(target)

    _samples.warm(once, more=with_result({}))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import export_log

    meta, engine, folder = _samples.opened(work)
    target = work / FOLDER / FILE
    target.parent.mkdir(exist_ok=True)
    shown = int(params.get("shown", 50))
    try:
        with dialect.read_tx(engine) as conn:
            logged = conn.execute(sa.select(sa.func.count()).select_from(export_log)).scalar_one()
        out, took, ticks = export(meta, engine, folder, target, shown)
        peak = _samples.peak_mb()
        with dialect.read_tx(engine) as conn:
            logged = conn.execute(sa.select(sa.func.count()).select_from(export_log)).scalar_one() - logged
    finally:
        engine.dispose()
    assert out["format"] == "xlsx" and out["files"] == [str(target)] and target.is_file() and logged == 1, out
    assert out["intervals"] == meta["ore_rows"] and out["holes"] == meta["holes"] and out["sheets"] == shown, out
    found = sheets(target)
    names = [name for name, _rows in found]
    longs = [rows for name, rows in found if name.startswith("Протокол")]
    assert names[:4] == ["Параметры", "Интервалы", "Сводка", "Протокол"], names[:6]
    assert found[1][1] == out["intervals"] + 1 and sum(longs) == out["records"] + len(longs) > 1, (found[1], longs)
    assert len([n for n in names if n.startswith("Скв. ")]) == shown and len(names) == 3 + len(longs) + shown, names
    m, mc = summary(target)
    ref = meta["ref"]
    assert _samples.close(m, ref["au_m"], 1e-9) and _samples.close(mc, ref["metal_au"], 1e-9), (m, mc, ref)
    return {
        "xlsx_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, "xlsx_"),
        "records": out["records"],
        "intervals": out["intervals"],
        "sheets": len(names),
        "file_mb": round(target.stat().st_size / 2**20, 1),
        "xlsx_mb": peak,
        "xlsx_tick_gap_s": round(ticks.gap, 3),
        "rows": meta["rows"],
    }
