"""Export of a resource report (подшаг 20.5; ADR 0027 решения 19, 20, 28, 30): the XLSX (`resources/xlsx.py`), the CSV
(`csv_out.py`), the job `resources.export` (`export_job.py`).

Т8.10-5: the book is read by openpyxl; the numbers of the sheet «Отчёт» are the `value`s of the form, not rounded, with
the number format of the decimals of the column, and the rounding of that format is the `text` of the cell. The same
export gives the same bytes; a cancel leaves nothing (and the files that were there stay); an existing file is a 409
`files_exist` unless `overwrite`; CSV keeps text that starts like a formula from Excel; the sheet «Параметры» is full.
The reports are made by hand (`test_resources_store`), the project is the world of Э18."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.resources import csv_out, export_cols, export_job, forms, spec, templates
from bf_core.resources import store as st
from bf_core.resources import store_data as sd
from bf_core.resources import xlsx as xl
from tests.estutil import W, world
from tests.test_resources_store import (
    CONTROL,
    ELEMENTS,
    GRID,
    TOTALS,
    built,
    make,
    params_of,
    part,
    row,
)

openpyxl = pytest.importorskip("openpyxl")
AT = datetime(2026, 11, 26, 9, 30, tzinfo=UTC)
CLASSES = ("Измеренные", "Выявленные", "Предполагаемые")


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    (tmp_path / "project").mkdir()
    w = world(tmp_path / "project")
    yield w
    w.project.engine.dispose()


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


# --- the rows: a JORC report with grades that are not round ---------------------------------------------------------


def jorc_rows() -> list[dict[str, Any]]:
    """The class groups «all» and of the cut-off 1,0 with the total of each; the grade of «Предполагаемые» is 1/3."""
    data = [  # label, blocks, volume, tonnes, Σ t·g of AU, Σ t·g of CU
        (CLASSES[0], 5, 5000.0, 12500.0, 30000.0, 6000.0),
        (CLASSES[1], 3, 3000.0, 7500.0, 13750.0, 4000.0),
        (CLASSES[2], 2, 2000.0, 5000.0, 5000.0 / 3.0, 1000.0),
    ]
    rows: list[dict[str, Any]] = []
    for kind, cutoff, scale in (("all", None, 1.0), ("cutoff", 1.0, 0.8)):
        parts = [(lab, b, v * scale, t * scale, a * scale, c * scale) for lab, b, v, t, a, c in data]
        tot = [sum(p[i] for p in parts) for i in range(1, 6)]
        au, cu = part(tot[2], tot[3]), part(tot[2], tot[4], 0.01)
        rows.append(row(None, kind, cutoff, int(tot[0]), tot[1], tot[2], au, cu, total=True))
        for lab, b, v, t, a, c in parts:
            rows.append(row([lab], kind, cutoff, b, v, t, part(t, a), part(t, c, 0.01)))
    rows.append(row(["(пусто)"], "all", None, 1, 1000.0, 2500.0, part(0.0, 0.0, mt=2500.0, mn=1),
                    part(2500.0, 1000.0, 0.01)))  # fmt: skip
    return rows


def jorc_report(w: W, name: str = "Ресурсы JORC", **over: Any) -> int:
    over = {"template_id": "jorc", **over}
    return int(make(w, name, rows=jorc_rows(), **over)["id"])


def export(w: W, ident: int, path: Path, **body: Any) -> dict[str, Any]:
    """The request and the job of an export (the handler's body), `AT` as the time of the export."""
    with dialect.read_tx(w.project.engine) as conn:
        params = export_job.request(conn, w.project.folder, ident, {"format": "xlsx", "path": str(path), **body})
    return export_job.run_export(w.project.engine, w.project.folder, params, at=AT)


def book(path: Path) -> Any:
    return openpyxl.load_workbook(path)


def sheet_lines(ws: Any) -> list[list[Any]]:
    return [[c.value for c in r] for r in ws.iter_rows()]


def expected_form(w: W, ident: int, template: str = "jorc", cutoff: float | None = None) -> dict[str, Any]:
    with dialect.read_tx(w.project.engine) as conn:
        rows = st.read_table(conn, w.store, ident).to_pylist()
        tpl = templates.get(conn, template)
    return forms.build(rows, tpl, [{"name": "AU", "unit": "g/t"}, {"name": "CU", "unit": "%"}], [""], cutoff)


# --- Т8.10-5 ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("cutoff", [None, 1.0])
def test_t8_10_5_the_report_sheet_is_the_form(w: W, out: Path, cutoff: float | None) -> None:
    ident = jorc_report(w)
    got = export(w, ident, out / "jorc.xlsx", **({} if cutoff is None else {"cutoff": cutoff}))
    assert got["recorded"] is True and got["format"] == "xlsx" and got["rows"] == len(jorc_rows())
    wb = book(out / "jorc.xlsx")
    assert wb.sheetnames == list(xl.SHEETS)
    ws = wb["Отчёт"]
    form = expected_form(w, ident, cutoff=cutoff)
    keys = [c["key"] for c in form["columns"]]
    head = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r[0] == "Строка")
    assert [c.value for c in ws[head]][1:] == [c["title"] for c in form["columns"]]
    line = head + 1
    checked = 0
    for r in forms.rows_flat(form):
        assert ws.cell(line, 1).value == r["label"]
        for k, cell in enumerate(r["cells"]):
            xc = ws.cell(line, k + 2)
            col = form["columns"][k]
            assert xc.value == cell["value"]  # the same float, NOT rounded; a gap — an empty cell
            if cell["value"] is not None:
                assert xc.number_format == forms.excel_format(col["decimals"])
                assert forms.format_number(xc.value, col["decimals"]) == cell["text"]  # the rounding is `text`
                checked += 1
            else:
                assert cell["text"] == ""
        line += 1
    assert checked > 20 and keys == ["tonnes", "grade:AU", "metal:AU", "grade:CU", "metal:CU"]
    # the labels of JORC, the subtotal, the total — and a grade that is longer than its decimals
    labels = [r["label"] for r in forms.rows_flat(form)]
    assert labels[:4] == [*CLASSES[:2], "Изм. + Выяв.", CLASSES[2]] and labels[-1] == "Итого"
    third = next(r for r in forms.rows_flat(form) if r["label"] == CLASSES[2])
    grade = third["cells"][keys.index("grade:AU")]
    assert grade["value"] != round(grade["value"], 2) and grade["text"] == forms.format_number(grade["value"], 2)


def test_the_balance_form_has_its_group_line_and_the_same_numbers(w: W, out: Path) -> None:
    rows: list[dict[str, Any]] = []
    for kind, cutoff, to, scale in (("balance", 1.0, None, 1.0), ("offbalance", 0.5, 1.0, 0.25)):
        for lab, t, a in (("A", 4000.0, 8000.0), ("B", 6000.0, 9000.0), ("C1", 3000.0, 2500.0), ("C2", 1000.0, 700.0)):
            rows.append(row([lab], kind, cutoff, 2, 2000.0, t * scale, part(t * scale, a * scale / 7.0),
                            part(t * scale, 50.0 * scale, 0.01), cutoff_to=to))  # fmt: skip
    ident = int(make(w, "ГКЗ", rows=rows, template_id="gkz", balance={"cutoff": 1.0, "off_cutoff": 0.5})["id"])
    export(w, ident, out / "gkz.xlsx")
    ws = book(out / "gkz.xlsx")["Отчёт"]
    form = expected_form(w, ident, "gkz")
    cols = form["columns"]
    assert {c["group"] for c in cols} == {"Балансовые", "Забалансовые"}
    group_line = next(r for r in ws.iter_rows() if r[1].value == "Балансовые")
    assert [c.value for c in group_line if c.value] == ["Балансовые", "Забалансовые"]
    assert len(ws.merged_cells.ranges) == 2  # each group title spans the columns of its table
    head = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1) if r[0] == "Строка")
    line = head + 1
    for r in forms.rows_flat(form):
        assert ws.cell(line, 1).value == r["label"]
        for k, cell in enumerate(r["cells"]):
            assert ws.cell(line, k + 2).value == cell["value"]
            if cell["value"] is not None:
                assert forms.format_number(ws.cell(line, k + 2).value, cols[k]["decimals"]) == cell["text"]
        line += 1
    labels = [r["label"] for r in forms.rows_flat(form)]
    assert labels == ["A", "B", "C1", "A + B + C1", "C2", "Итого"]


def test_the_sheet_keeps_a_gap_empty_and_the_subtotal_is_the_sum(w: W, out: Path) -> None:
    ident = jorc_report(w)
    export(w, ident, out / "a.xlsx")
    ws = book(out / "a.xlsx")["Отчёт"]
    lines = {r[0]: r for r in ws.iter_rows(values_only=True) if r[0]}
    tonnes = {k: lines[k][1] for k in (*CLASSES, "Изм. + Выяв.", "Итого")}
    assert tonnes["Изм. + Выяв."] == pytest.approx((12500.0 + 7500.0) / 1000.0, rel=1e-12)  # kt
    assert tonnes["Итого"] == pytest.approx(tonnes[CLASSES[0]] + tonnes[CLASSES[1]] + tonnes[CLASSES[2]] + 2.5,
                                            rel=1e-12)  # «Прочие» ((пусто)) is in the total  # fmt: skip
    assert lines["Прочие"][2] is None  # no tonnes with a grade: the grade of «(пусто)» is empty, not 0


# --- the sheets -------------------------------------------------------------------------------------------------------


def test_the_table_sheet_has_every_row_as_it_is_in_the_file(w: W, out: Path) -> None:
    ident = jorc_report(w)
    export(w, ident, out / "t.xlsx")
    ws = book(out / "t.xlsx")["Таблица"]
    lines = sheet_lines(ws)
    rows = jorc_rows()
    assert len(lines) == len(rows) + 1
    assert lines[0][:8] == ["DOM", "Итог", "Вид", "Борт", "Борт до", "Блоков", "Объём, м³", "Масса, т"]
    assert lines[0][8:14] == [
        "AU: масса с содержанием, т", "AU: Σ масса × содержание", "AU: металл, г",
        "AU: содержание, г/т", "AU: масса без содержания, т", "AU: блоков без содержания",
    ]  # fmt: skip
    assert lines[0][14 + 2] == "CU: металл, т"
    first, empty = lines[1], lines[len(rows)]
    assert first[0] is None and first[1] == "да" and first[2] == "без борта" and first[3] is None  # the total
    assert lines[2][0] == CLASSES[0] and lines[2][1] is None and lines[2][9] == 30000.0
    assert empty[0] == "(пусто)" and empty[11] is None and empty[8] == 0.0  # the empty grade is a gap, wt = 0 is 0
    assert empty[12] == 2500.0 and empty[13] == 1  # the tonnes and the blocks without a grade
    cut = lines[len(rows) // 2 + 1]
    assert cut[2] == "борт" and cut[3] == 1.0


def test_the_curve_the_control_and_the_parameters_sheets(w: W, out: Path) -> None:
    ident = jorc_report(w, filter_id=w.flt, restrict={"cond": {"field": "AU", "op": ">", "value": 0.5}},
                        cutoffs=[None, 1.0], balance={"cutoff": 1.0, "off_cutoff": 0.5})  # fmt: skip
    export(w, ident, out / "p.xlsx")
    wb = book(out / "p.xlsx")
    curve = sheet_lines(wb["Кривая"])
    assert curve[0][:4] == ["Борт", "Блоков", "Объём, м³", "Масса, т"] and len(curve) == 4
    assert curve[1][:4] == [0.2, 9, 9000.0, 22500.0] and curve[3][4] is None  # the grade of the last cut-off: a gap
    assert curve[0][4:6] == ["AU: содержание, г/т", "AU: металл, г"]
    control = sheet_lines(wb["Контроль"])
    texts = [r[0] for r in control if r[0]]
    assert "Сумма по группам − итог" in texts and "Блоки без плотности" in texts and "Допуск" in texts
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
    params = {r[0]: r[1] for r in sheet_lines(wb["Параметры"])[1:] if r[0]}
    assert params["Отпечаток ячеек модели (cells_sha)"] == info["model"]["cells_sha"] and info["model"]["cells_sha"]
    assert params["Модель"] == "Модель" and params["Номер модели"] == w.model
    for name, fp in info["fields"].items():
        assert params[f"Поле {name}: отпечаток"] == fp
    assert params["Элемент AU: единица"] == "г/т" and params["Элемент CU: единица"] == "%"
    assert params["Поле борта"] == "AU" and params["Борта"] == "без борта; 1"
    assert params["Плотность"] == "2,5 т/м³" and params["Группы"] == "DOM"
    assert params["Баланс"] == "балансовые: борт 1; забалансовые: от 0,5"
    assert params["Фильтр библиотеки"].startswith("Руда") and "AU" in params["Фильтр библиотеки"]
    assert params["Быстрый ограничитель"] == "AU > 0.5" or "AU" in params["Быстрый ограничитель"]
    assert params["Правила (ALGO)"] == spec.ALGO and params["Хэш параметров"] == info["params_hash"]
    assert params["Шаблон формы"] == "JORC (версия 1)" and params["Строк в таблице"] == len(jorc_rows())
    assert params["Выгружено"] and params["Программа"].startswith("BedrockForge 2 ")
    assert params["Версии библиотек"] == "numpy 2" and params["Точек кривой"] == 100
    assert params["Отчёт"] == "Ресурсы JORC" and params["Версия отчёта"] == 1 and params["Отчёт создан"]
    assert params["Сетка: число ячеек"] == "10; 8; 6" and params["Сетка: поворот, °"] == 30


def test_a_report_without_a_curve_or_a_template_still_gives_every_sheet(w: W, out: Path) -> None:
    ident = int(make(w, "Без шаблона", curve=False)["id"])  # no template, no curve; groups DOM
    export(w, ident, out / "n.xlsx")
    wb = book(out / "n.xlsx")
    assert wb.sheetnames == list(xl.SHEETS)
    assert "не рассчитана" in wb["Кривая"]["A1"].value
    ws = wb["Отчёт"]
    labels = [r[0] for r in ws.iter_rows(values_only=True) if r[0]]
    assert (
        "руда" in labels
        and "порода" in labels
        and "(пусто)" in labels
        and labels[-4:][-1] != "Итого"
        or "Итого" in labels
    )
    assert any(str(x).startswith("Шаблон: По умолчанию") for x in labels)


def test_no_groups_the_total_row_only(w: W, out: Path) -> None:
    rows = [row(None, "all", None, 9, 9000.0, 22500.0, part(20000.0, 30000.0), part(22500.0, 11000.0, 0.01),
                total=True)]  # fmt: skip
    table = sd.rows_table(rows, ELEMENTS, 0)
    staged = st.stage(w.store, table, None, CONTROL)
    b = st.Built(params_of(w, groups=[], cutoffs=[None]), {"id": w.model, "name": "Модель", "grid": GRID},
                 ("AU", "CU"), TOTALS, staged)  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        ident = int(st.create(conn, w.store, "Без групп", b)["id"])
    export(w, ident, out / "g.xlsx")
    wb = book(out / "g.xlsx")
    labels = [r[0] for r in wb["Отчёт"].iter_rows(values_only=True) if r[0]]
    assert "Итого" in labels and "Все блоки" not in labels  # a row of the template with zeros would be a lie
    t = sheet_lines(wb["Таблица"])
    assert len(t) == 2 and t[0][0] == "Итог" and t[1][0] == "да"


def test_a_table_longer_than_a_sheet_goes_on(w: W, out: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = jorc_report(w)
    monkeypatch.setattr(xl, "MAX_LINES", 4)
    got = export(w, ident, out / "long.xlsx")
    n = len(jorc_rows())
    assert got["sheets"] == -(-n // 4)
    wb = book(out / "long.xlsx")
    names = [s for s in wb.sheetnames if s.startswith("Таблица")]
    assert names[:2] == ["Таблица", "Таблица (2)"] and len(names) == got["sheets"]
    heads = [sheet_lines(wb[s])[0][:3] for s in names]
    assert all(h == heads[0] for h in heads)  # the header again
    assert sum(len(sheet_lines(wb[s])) - 1 for s in names) == n


# --- the same bytes, atomic, 409 --------------------------------------------------------------------------------------


def test_the_same_export_gives_the_same_bytes_and_the_report_remembers_it(w: W, out: Path) -> None:
    ident = jorc_report(w)
    a = export(w, ident, out / "a.xlsx")
    b = export(w, ident, out / "b.xlsx")
    assert (out / "a.xlsx").read_bytes() == (out / "b.xlsx").read_bytes()
    assert a["bytes"] == b["bytes"] == (out / "a.xlsx").stat().st_size and a["events"][0][0] == "resources.changed"
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
        sha = st.xlsx_sha(conn, ident)
    assert [f["path"] for f in info["files"]] == [str(out / "a.xlsx"), str(out / "b.xlsx")]
    assert {f["format"] for f in info["files"]} == {"xlsx"} and info["files"][0]["sha256"] == sha
    assert info["xlsx"] is True and info["version"] == 1  # the last book is kept; the numbers did not change
    import hashlib

    assert hashlib.sha256((out / "a.xlsx").read_bytes()).hexdigest() == sha
    assert sorted(p.name for p in out.iterdir()) == ["a.xlsx", "b.xlsx"]  # no `.part` left


def test_the_time_of_the_export_is_the_only_difference(w: W, out: Path) -> None:
    ident = jorc_report(w)
    export(w, ident, out / "a.xlsx")
    with dialect.read_tx(w.project.engine) as conn:
        params = export_job.request(conn, w.project.folder, ident, {"format": "xlsx", "path": str(out / "b.xlsx")})
    export_job.run_export(w.project.engine, w.project.folder, params, at=datetime(2026, 12, 1, 8, 0, tzinfo=UTC))
    a, b = book(out / "a.xlsx"), book(out / "b.xlsx")
    for name in xl.SHEETS[:-1]:
        assert sheet_lines(a[name]) == sheet_lines(b[name])
    pa, pb = sheet_lines(a["Параметры"]), sheet_lines(b["Параметры"])
    assert [r for r, s in zip(pa, pb, strict=True) if r != s] == [r for r in pa if r[0] == "Выгружено"]


def test_files_exist_unless_overwrite(w: W, out: Path) -> None:
    ident = jorc_report(w)
    target = out / "x.xlsx"
    target.write_bytes(b"mine")
    with dialect.read_tx(w.project.engine) as conn, pytest.raises(ApiError) as e:
        export_job.request(conn, w.project.folder, ident, {"path": str(target), "format": "xlsx"})
    assert (e.value.status, e.value.code) == (409, "files_exist") and e.value.extra == {"files": [str(target)]}
    assert "x.xlsx" in e.value.message and e.value.hint
    with dialect.read_tx(w.project.engine) as conn:  # the job checks again: a file may come after the request
        params = export_job.request(conn, w.project.folder, ident, {"format": "xlsx", "path": str(out / "y.xlsx")})
    (out / "y.xlsx").write_bytes(b"came meanwhile")
    with pytest.raises(ApiError) as e2:
        export_job.run_export(w.project.engine, w.project.folder, params, at=AT)
    assert e2.value.code == "files_exist" and (out / "y.xlsx").read_bytes() == b"came meanwhile"
    got = export(w, ident, target, overwrite=True)
    assert got["recorded"] and target.read_bytes()[:2] == b"PK"  # replaced
    assert sorted(p.name for p in out.iterdir()) == ["x.xlsx", "y.xlsx"]


def test_a_cancel_leaves_nothing_and_what_was_there_stays(w: W, out: Path) -> None:
    ident = jorc_report(w)
    mine = out / "keep.xlsx"
    mine.write_bytes(b"the old book")
    with dialect.read_tx(w.project.engine) as conn:
        params = export_job.request(
            conn, w.project.folder, ident, {"format": "xlsx", "path": str(mine), "overwrite": True}
        )
    seen: list[str] = []

    def tick(_f: float | None, msg: str | None) -> None:
        seen.append(str(msg))
        if str(msg).startswith("Таблица"):  # the book is half written
            raise JobCancelled

    with pytest.raises(JobCancelled):
        export_job.run_export(w.project.engine, w.project.folder, params, tick=tick, at=AT)
    assert any(m.startswith("Таблица") for m in seen)
    assert mine.read_bytes() == b"the old book" and [p.name for p in out.iterdir()] == ["keep.xlsx"]  # no `.part`
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
    assert info["files"] == [] and info["xlsx"] is False  # nothing recorded


def test_a_cancel_of_the_csv_leaves_nothing(w: W, out: Path) -> None:
    ident = jorc_report(w)
    with dialect.read_tx(w.project.engine) as conn:
        params = export_job.request(conn, w.project.folder, ident, {"path": str(out / "c.csv"), "format": "csv",
                                                                    "what": "both"})  # fmt: skip

    def tick(_f: float | None, msg: str | None) -> None:
        if str(msg).startswith("Таблица"):
            raise JobCancelled

    with pytest.raises(JobCancelled):
        export_job.run_export(w.project.engine, w.project.folder, params, tick=tick, at=AT)
    assert list(out.iterdir()) == []


def test_a_report_rebuilt_meanwhile_is_not_listed(w: W, out: Path) -> None:
    ident = jorc_report(w)
    with dialect.read_tx(w.project.engine) as conn:
        params = export_job.request(conn, w.project.folder, ident, {"format": "xlsx", "path": str(out / "r.xlsx")})

    def tick(_f: float | None, msg: str | None) -> None:
        if str(msg).startswith("Листы"):  # after the read, before the record: the report is rebuilt
            with dialect.write_tx(w.project.engine) as conn:
                st.replace(conn, w.store, ident, built(w, rows=jorc_rows(), template_id="jorc"))

    got = export_job.run_export(w.project.engine, w.project.folder, params, tick=tick, at=AT)
    assert got["recorded"] is False and "events" not in got and (out / "r.xlsx").is_file()  # the file is the user's
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
    assert info["files"] == [] and info["version"] == 2 and info["xlsx"] is False
    assert (
        [p.name for p in (w.project.folder / ".blobs" / ".tmp").glob("*")] == []
        if (w.project.folder / ".blobs" / ".tmp").exists()
        else True
    )  # the staged copy of the book is dropped


def test_the_refusals_of_the_request(w: W, out: Path) -> None:
    ident = jorc_report(w)

    def refused(body: Any, code: str, status: int = 422, report: int | None = None) -> ApiError:
        with dialect.read_tx(w.project.engine) as conn, pytest.raises(ApiError) as e:
            export_job.request(conn, w.project.folder, ident if report is None else report, body)
        assert (e.value.status, e.value.code) == (status, code), e.value
        assert e.value.message and e.value.hint
        return e.value

    ok = {"format": "xlsx", "path": str(out / "z.xlsx")}
    assert refused({**ok, "format": "pdf"}, "bad_param").extra == {"param": "format"}
    assert refused({**ok, "path": "relative.xlsx"}, "bad_param").extra == {"param": "path"}
    assert refused({**ok, "path": str(out / "no" / "z.xlsx")}, "bad_param").extra == {"param": "path"}
    assert refused({**ok, "what": "all"}, "bad_param").extra == {"param": "what"}
    assert refused({**ok, "cutoff": "abc"}, "bad_param").extra == {"param": "cutoff"}
    assert refused({**ok, "path": str(w.project.folder / "z.xlsx")}, "bad_path").extra == {"param": "path"}
    assert refused({**ok, "template": "nope"}, "template_not_found", 404)
    assert refused(ok, "resource_report_not_found", 404, report=9999)
    (out / "dir.xlsx").mkdir()
    assert refused({**ok, "path": str(out / "dir.xlsx")}, "bad_param").extra == {"param": "path"}
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_building(conn, ident)
    refused(ok, "report_building", 409)
    assert [p.name for p in out.iterdir()] == ["dir.xlsx"]  # the folder of the test; nothing was written


def test_the_extension_is_added(w: W, out: Path) -> None:
    ident = jorc_report(w)
    got = export(w, ident, out / "plain", format="xlsx")
    assert got["path"] == str(out / "plain.xlsx") and (out / "plain.xlsx").is_file()
    got = export(w, ident, out / "plain", format="csv")
    assert got["path"] == str(out / "plain.csv")


# --- CSV --------------------------------------------------------------------------------------------------------------


def csv_lines(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), delimiter=";"))


def test_the_csv_table(w: W, out: Path) -> None:
    ident = jorc_report(w)
    got = export(w, ident, out / "t.csv", format="csv")
    assert got["format"] == "csv" and got["what"] == "table" and got["rows"] == len(jorc_rows())
    lines = csv_lines(out / "t.csv")
    assert lines[0][:3] == ["DOM", "Итог", "Вид"] and len(lines) == len(jorc_rows()) + 1
    assert all(len(r) == len(lines[0]) for r in lines)
    assert lines[1][:4] == ["", "да", "без борта", ""]  # the total: no group, no cut-off — empty cells
    assert lines[3][0] == CLASSES[1]
    third = next(r for r in lines if r[0] == CLASSES[2] and r[2] == "без борта")
    au_sum = third[9]
    assert au_sum == csv_out.cell_text(5000.0 / 3.0) and "," in au_sum and len(au_sum) > 10  # not rounded, a comma
    empty = next(r for r in lines if r[0] == "(пусто)")
    assert empty[11] == "" and empty[8] == "0"  # the empty grade is empty, not 0
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
    assert [f["format"] for f in info["files"]] == ["csv"] and info["xlsx"] is False  # a CSV is not the book


def test_the_csv_form_and_both(w: W, out: Path) -> None:
    ident = jorc_report(w)
    got = export(w, ident, out / "f.csv", format="csv", what="form")
    assert got["files"] == [str(out / "f.csv")]
    form = expected_form(w, ident)
    lines = csv_lines(out / "f.csv")
    flat = forms.rows_flat(form)
    assert lines[0][0] == "Строка" and len(lines) == len(flat) + 1
    third = next(r for r in lines if r[0] == CLASSES[2])
    value = next(r for r in flat if r["label"] == CLASSES[2])["cells"][1]["value"]
    assert third[2] == csv_out.cell_text(value)  # the value, not the text
    got = export(w, ident, out / "both.csv", format="csv", what="both")
    assert got["files"] == [str(out / "both.csv"), str(out / "both · форма.csv")]
    assert csv_lines(out / "both.csv")[0][0] == "DOM" and csv_lines(out / "both · форма.csv")[0][0] == "Строка"
    with dialect.read_tx(w.project.engine) as conn:
        info = st.info(conn, ident)
    assert [f["path"] for f in info["files"]][-2:] == got["files"]


def test_text_that_looks_like_a_formula_is_safe(w: W, out: Path) -> None:
    labels = ["=1+1", "+cmd", "-10…0", "@SUM(A1)", "\t tab", "plain"]
    rows = [row([lab], "all", None, 1, 1000.0, 2500.0, part(2500.0, 2500.0), part(2500.0, 100.0, 0.01))
            for lab in labels]  # fmt: skip
    rows.append(row(["x"], "cutoff", -1.5, 1, 1000.0, 2500.0, part(2500.0, 2500.0), part(2500.0, 100.0, 0.01)))
    ident = int(make(w, "Формулы", rows=rows)["id"])
    export(w, ident, out / "s.csv", format="csv")
    export(w, ident, out / "s.xlsx")
    lines = csv_lines(out / "s.csv")
    cells = [r[0] for r in lines[1:-1]]
    assert cells == ["'=1+1", "'+cmd", "'-10…0", "'@SUM(A1)", "'\t tab", "plain"]
    assert lines[-1][3] == "-1,5"  # a negative NUMBER stays a number: no quote
    ws = book(out / "s.xlsx")["Таблица"]
    got = [ws.cell(i, 1) for i in range(2, 8)]
    assert [c.value for c in got] == [
        "'=1+1",
        "'+cmd",
        "'-10…0",
        "'@SUM(A1)",
        "\t tab",
        "plain",
    ]  # a sheet cell is text
    assert all(c.data_type == "s" for c in got)  # text, never a formula
    assert ws.cell(8, 4).value == -1.5 and ws.cell(8, 4).data_type == "n"
    form_ws = book(out / "s.xlsx")["Отчёт"]
    assert all(c.data_type != "f" for r in form_ws.iter_rows() for c in r)


def test_the_cells_of_the_csv() -> None:
    import math

    import numpy as np

    assert [csv_out.cell_text(v) for v in (None, math.nan, math.inf, 1.0, 0.1, -2.5, 12, True, np.float64(3.25),
                                           np.int64(7), "a;b", "")] == ["", "", "", "1", "0,1", "-2,5", "12", "1",
                                                                         "3,25", "7", "a;b", ""]  # fmt: skip
    assert csv_out.safe("=x") == "'=x" and csv_out.safe("x=") == "x=" and csv_out.safe("") == ""
    assert csv_out.files_for(Path("/a/b.csv"), "both")[1][0] == Path("/a/b · форма.csv")


# --- the job ----------------------------------------------------------------------------------------------------------


def ctx(w: W, params: dict[str, Any], cancel_at: str | None = None) -> Any:
    log: list[str] = []

    def tick(_f: float | None = None, msg: str | None = None) -> None:
        if cancel_at is not None and str(msg).startswith(cancel_at):
            raise JobCancelled

    return SimpleNamespace(id=1, params=params, project_folder=w.project.folder, engine=lambda: w.project.engine,
                           tick=tick, log=lambda m, level="info": log.append(m), logs=log)  # fmt: skip


def test_the_job_is_registered_by_its_module_and_runs(w: W, out: Path) -> None:
    from bf_core.jobs import registry

    kind = registry.KINDS[export_job.KIND]
    assert (kind.kind, kind.queue) == ("resources.export", "quick") and kind.title
    ident = jorc_report(w)
    c = ctx(w, {"report_id": ident, "format": "xlsx", "path": str(out / "j.xlsx"), "what": "table"})
    res = kind.run(c)
    assert res is not None and res["recorded"] is True and (out / "j.xlsx").is_file()
    assert {"path", "files", "format", "bytes", "rows", "sheets", "seconds", "events"} <= set(res) and c.logs
    with pytest.raises(JobError) as e:
        kind.run(ctx(w, {"report_id": 9999, "format": "xlsx", "path": str(out / "k.xlsx")}))
    assert (e.value.code, e.value.retry) == ("resource_report_not_found", False) and e.value.message and e.value.hint
    with pytest.raises(JobCancelled):
        kind.run(ctx(w, {"report_id": ident, "format": "csv", "path": str(out / "k.csv")}, cancel_at="Таблица"))
    assert sorted(p.name for p in out.iterdir()) == ["j.xlsx"]


def test_a_file_that_cannot_be_written_is_a_job_error(w: W, out: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.jobs import registry

    ident = jorc_report(w)

    def broken(*_a: Any, **_k: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(xl, "write_book", broken)
    with pytest.raises(JobError) as e:
        registry.KINDS[export_job.KIND].run(ctx(w, {"report_id": ident, "format": "xlsx",
                                                   "path": str(out / "d.xlsx")}))  # fmt: skip
    assert e.value.code == "file_unwritable" and e.value.hint and list(out.iterdir()) == []


def test_the_columns_of_the_table_are_the_columns_of_the_file(w: W) -> None:
    ident = jorc_report(w)
    with dialect.read_tx(w.project.engine) as conn:
        path = w.store.path_of(st.data_sha(conn, ident))
    table = sd.rows_table(jorc_rows(), ELEMENTS, 1)
    head = export_cols.table_head(["DOM"], [("AU", "g/t"), ("CU", "%")])
    assert len(head) == len(sd.row_columns(ELEMENTS, 1)) == len(next(export_cols.iter_columns(path)))
    assert head[7] == "Масса, т" and head[14] == "CU: масса с содержанием, т"
    assert table.num_rows == len(jorc_rows())
    assert export_cols.key_titles({"groups": [{"kind": "bench"}, "CLASS"]}, 3) == ["Уступ", "CLASS", "Группа 3"]
    assert export_cols.metal_label("%") == "т" and export_cols.metal_label("ppm") == "г"
    assert export_cols.metal_label("m3/t") is None
