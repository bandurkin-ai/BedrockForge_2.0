"""Export of the protocol of ore intervals (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 §9, решения 10, 19, 30; подшаг
12.23): the book XLSX (read with `openpyxl`: «Параметры», «Интервалы» = the rows of the table, «Сводка», «Протокол»
— the long form, «Скв. …» — the form И1, сл. 17), the CSV files (UTF-8 with BOM), a cancel leaves no file, 409
`files_exist` / `stale`, a path with Cyrillic letters (решение 30: also on Windows), the job in a worker."""

from __future__ import annotations

import csv
import io
import itertools
from pathlib import Path
from typing import Any

import openpyxl
import pytest
import sqlalchemy as sa

from bf_core.composites import ore_protocol_job as pj
from bf_core.composites.ore_protocol import LONG_HEAD
from bf_core.db import dialect
from bf_core.db.schema import dh_results, export_log
from bf_core.jobs.context import JobCancelled
from tests.test_composites_jobs import rows_of
from tests.test_import_job import wait_job
from tests.test_ore_job import C, body, cols, gkz, ore_here  # noqa: F401 - the fixture

_ids = itertools.count(98_001)


def export_here(project: Any, result_id: int, path: Path, fmt: str = "xlsx", cancel_at: int | None = None,
                seen: list[int] | None = None, **kw: Any) -> dict[str, Any]:  # fmt: skip
    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    with dialect.read_tx(project.engine) as conn:
        rec = dict(conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().one())
        params, _title = pj.parse_export(conn, rec, {"format": fmt, "path": str(path), **kw})
    return pj.run_export(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def sheet(wb: Any, name: str) -> list[tuple[Any, ...]]:
    return list(wb[name].iter_rows(values_only=True))


def events_of(c: Any, rid: int, holes: list[str]) -> int:
    got = [c.get(f"{C}/results/{rid}/protocol", params={"hole": h}).json() for h in holes]
    return sum(len(g["protocol"]["events"]) for g in got)


def test_book_numbers_are_the_table(gkz, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid, tid = done["result_id"], str(done["table_id"])
    split = {"hole": holes[1], "action": "split", "from": 3.0, "to": 7.0}
    assert c.post(f"{C}/results/{rid}/edit", json=split).status_code == 200
    folder = tmp_path / "Протоколы ГКЗ"
    folder.mkdir()
    out = export_here(project, rid, folder / "Рудные интервалы · протокол", holes=[holes[1]])
    path = folder / "Рудные интервалы · протокол.xlsx"
    assert out["path"] == str(path) and path.is_file() and not list(folder.glob("*.part"))
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Параметры", "Интервалы", "Сводка", "Протокол", f"Скв. {holes[1]}"]

    got = rows_of(c, set_id, tid)  # «Интервалы» — the rows of the table, number for number (the edit too)
    k = cols(got)
    rows = sheet(wb, "Интервалы")
    head = list(rows[0])
    assert head[3:] == [x["name"] for x in got["columns"] if x["key"] not in ("hole", "from", "to")]
    assert len(rows) - 1 == len(got["rows"]) == out["intervals"]
    for r, want in zip(rows[1:], got["rows"], strict=True):
        assert (r[0], r[1], r[2]) == (want["hole"], want["from"], want["to"])
        for name, v in zip(head[3:], r[3:], strict=True):
            assert v == want[k[name]], (name, v, want[k[name]])
    assert any(r[head.index("MANUAL")] == 1 for r in rows[1:])

    summary = {(r[0], r[1]): r for r in sheet(wb, "Сводка")[1:] if r[0] is not None}
    ore = summary["все", "руда"]  # 9 intervals, Σ M 54 m; the split one of hole 2 — one more
    assert ore[3] == 10 and abs(ore[4] - 54.0) < 1e-9 and abs(ore[7] - 3 * 43.0) < 1e-9
    assert abs(ore[6] - ore[7] / ore[4]) < 1e-12 and summary["все", "обрамление"][2] == "да"

    long = sheet(wb, "Протокол")
    assert list(long[0]) == list(LONG_HEAD) and len(long) - 1 == out["records"] == events_of(c, rid, holes)
    assert {r[0] for r in long[1:]} == set(holes) and any(r[3] == "ручная правка" for r in long[1:])
    assert all(r[10] is None or r[10] > 0 for r in long[1:])  # M that does not apply — empty, never zero

    form = wb[f"Скв. {holes[1]}"]
    assert form["A1"].value == "№" and form.merged_cells.ranges and form["A2"].value == "1"
    params = dict(sheet(wb, "Параметры")[1:])
    assert params["Поле содержания"] == "AU" and params["Минимальный метропроцент МС, м·г/т"] == 3.0
    assert params["Ручных правок"] == 1 and params["Листов «Скв. …»"] == 1
    with dialect.read_tx(project.engine) as conn:
        logged = conn.execute(sa.select(export_log.c.file_name, export_log.c.format)).all()
    assert [tuple(x) for x in logged] == [(path.name, "xlsx")]


def test_csv_with_bom_and_a_cancel_leaves_no_file(gkz, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid = done["result_id"]
    tmp_path = tmp_path / "выгрузка"  # the project lives in tmp_path too
    tmp_path.mkdir()
    target = tmp_path / "протокол.csv"
    seen = [0]
    out = export_here(project, rid, target, "csv", seen=seen)
    files = [target, tmp_path / "протокол · интервалы.csv"]
    assert out["files"] == [str(f) for f in files]
    for f in files:
        assert f.read_bytes()[:3] == b"\xef\xbb\xbf"
    long = list(csv.reader(io.StringIO(files[0].read_text("utf-8-sig")), delimiter=";"))
    assert long[0] == list(LONG_HEAD) and len(long) - 1 == out["records"] == events_of(c, rid, holes)
    iv = list(csv.reader(io.StringIO(files[1].read_text("utf-8-sig")), delimiter=";"))
    at = iv[0].index("C")
    assert "2,8375" in [r[at] for r in iv[1:]] and "8,0 м @ 2,84 г/т (МС 22,7)" in [r[-1] for r in iv[1:]]

    r = c.post(f"{C}/results/{rid}/protocol", json={"format": "csv", "path": str(target)})  # «Заменить?»
    assert r.status_code == 409 and r.json()["code"] == "files_exist", r.text
    assert r.json()["files"] == [str(f) for f in files]
    for f in files:
        f.unlink()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.delete(export_log))
    assert seen[0] >= 3
    for fmt in ("csv", "xlsx"):
        for at in range(1, seen[0] + 1):
            with pytest.raises(JobCancelled):
                export_here(project, rid, tmp_path / f"протокол.{fmt}", fmt, cancel_at=at)
            assert sorted(p.name for p in tmp_path.iterdir()) == [], (fmt, at)
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(export_log)).scalar() == 0


def test_the_route_and_the_job(gkz, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import results

    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    url = f"{C}/results/{done['result_id']}/protocol"
    cases = [
        ({"format": "pdf", "path": str(tmp_path / "x.pdf")}, 422, "bad_format"),
        ({"format": "xlsx", "path": "x.xlsx"}, 422, "path_not_absolute"),
        ({"format": "xlsx", "path": str(tmp_path / "нет" / "x.xlsx")}, 404, "folder_not_found"),
        ({"format": "xlsx", "path": str(tmp_path / "x.xlsx"), "holes": ["нет такой"]}, 422, "bad_param"),
        ({"format": "xlsx", "path": str(tmp_path / "x.xlsx"), "holes": [f"H{i}" for i in range(51)]}, 422, "bad_param"),
    ]
    for req, status, code in cases:
        r = c.post(url, json=req)
        assert r.status_code == status and r.json()["code"] == code, (req, r.text)
    (tmp_path / "x.xlsx").write_bytes(b"old")
    r = c.post(url, json={"format": "xlsx", "path": str(tmp_path / "x.xlsx")})
    assert r.status_code == 409 and r.json()["code"] == "files_exist", r.text
    r = c.post(url, json={"format": "xlsx", "path": str(tmp_path / "x.xlsx"), "overwrite": True})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    wb = openpyxl.load_workbook(tmp_path / "x.xlsx")  # ≤ 50 holes: a sheet for every hole
    assert wb.sheetnames[4:] == [f"Скв. {h}" for h in sorted(holes)] and job["result"]["sheets"] == 3
    with dialect.write_tx(project.engine) as conn:
        results.bump_table(conn, set_id, int(table))
    r = c.post(url, json={"format": "xlsx", "path": str(tmp_path / "y.xlsx")})
    assert r.status_code == 409 and r.json()["code"] == "stale", r.text


@pytest.mark.windows
def test_cyrillic_path_on_windows(gkz, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """Решение 30: the book is written into a folder with Cyrillic letters and spaces (also on Windows CI)."""
    _c, project, set_id, table, _holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    folder = tmp_path / "Отчёты ГКЗ" / "Участок «Северный»"
    folder.mkdir(parents=True)
    out = export_here(project, done["result_id"], folder / "Протокол оконтуривания.xlsx")
    wb = openpyxl.load_workbook(out["path"])
    assert wb.sheetnames[:4] == ["Параметры", "Интервалы", "Сводка", "Протокол"]
    assert sheet(wb, "Интервалы")[1][0] is not None


# --- the review of 12.33, part 1 (подшаг 12.34) ---------------------------------------------------------------


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def test_sheet_names_with_quotes_and_the_limits_of_excel(tmp_path: Path) -> None:
    """№ 1: a hole named `A"1 & <B>` does not break `workbook.xml`; № 7: the name is cut to 31 UTF-16 units first
    and stripped of «'» after, «History» is reserved by Excel."""
    from bf_core.composites.ore_xlsx import Book, sheet_name

    path = tmp_path / "кавычки.xlsx"
    with Book(path) as book:
        for name in ('Скв. A"1 & <B>', "Скв. O'Neil"):
            with book.sheet(name) as sh:
                sh.row([name])
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ['Скв. A"1 & <B>', "Скв. O'Neil"]
    assert sheet(wb, 'Скв. A"1 & <B>') == [('Скв. A"1 & <B>',)]
    taken: set[str] = set()
    cut = sheet_name("a" * 30 + "'b", taken)
    assert cut == "a" * 30 and not cut.endswith("'")
    wide = sheet_name("𝔸" * 20, taken)  # two UTF-16 units a letter
    assert _u16(wide) <= 31 and wide == "𝔸" * 15
    again = sheet_name("𝔸" * 20, taken)
    assert _u16(again) <= 31 and again.endswith(" (2)")
    for name in ("History", "history"):
        assert sheet_name(name, taken).casefold() != "history"
    assert sheet_name("''", taken) == "Лист"


def test_csv_cells_are_never_formulas(tmp_path: Path) -> None:
    """№ 2: text that starts like a formula (=, +, -, @, tab, CR) gets «'» in the CSV; numbers stay numbers."""
    from bf_core.composites import ore_report as rep

    path = tmp_path / "формулы.csv"
    texts = ["=1+1", "+A1", "-2+3", "@SUM(A1)", "\tцмд", "\rcmd", "руда"]
    rep.write_csv(path, rep.Table("Т", ["=HEAD", "N"], [[t, -1.5] for t in texts]))
    got = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig"), newline=""), delimiter=";"))
    assert got[0] == ["'=HEAD", "N"]
    assert [r[0] for r in got[1:]] == ["'" + t for t in texts[:-1]] + ["руда"]
    assert {r[1] for r in got[1:]} == {"-1,5"}


def test_parts_are_unique_and_a_failed_rename_keeps_the_old_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """№ 8: a file «X.csv.part» of someone else is never touched; if the second rename fails, the first target
    is as it was."""
    import os

    from bf_core.composites import ore_report as rep

    targets = [tmp_path / "X.csv", tmp_path / "X · интервалы.csv"]
    for k, t in enumerate(targets):
        t.write_text(f"old{k}")
    foreign = tmp_path / "X.csv.part"
    foreign.write_text("чужой")

    def write(parts: list[Path]) -> None:
        assert all(p.parent == tmp_path for p in parts) and foreign not in parts
        for k, p in enumerate(parts):
            p.write_text(f"new{k}")

    real = os.replace

    def fail_second(src: Any, dst: Any) -> None:
        if Path(dst) == targets[1] and Path(src).name.endswith(".part") and Path(src).read_text() == "new1":
            raise OSError("занято")
        real(src, dst)

    monkeypatch.setattr(rep.os, "replace", fail_second)
    with pytest.raises(OSError):
        rep.write_files(targets, write)
    assert [t.read_text() for t in targets] == ["old0", "old1"] and foreign.read_text() == "чужой"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*(t.name for t in targets), foreign.name])
    monkeypatch.setattr(rep.os, "replace", real)
    rep.write_files(targets, write)
    assert [t.read_text() for t in targets] == ["new0", "new1"] and foreign.read_text() == "чужой"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*(t.name for t in targets), foreign.name])


def test_stale_conditions_are_not_exported(gkz, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """№ 9 (решение 19): «Анализ кондиций» out of date — 409 `stale`, no file «устарел: …»."""
    from bf_core.composites import ore_conditions_job as ocj
    from bf_core.composites import results
    from tests.test_ore_job import cond_body

    c, project, set_id, table, _holes = gkz
    with dialect.read_tx(project.engine) as conn:
        params, _t = ocj.parse_conditions(conn, cond_body(set_id, table))
    out = ocj.run_conditions(project.engine, project.folder, params, job_id=next(_ids), tick=lambda *_a: None)
    rid = out["result_id"]
    with dialect.write_tx(project.engine) as conn:
        results.bump_table(conn, set_id, int(table))
    r = c.post(f"{C}/results/{rid}/protocol", json={"format": "xlsx", "path": str(tmp_path / "к.xlsx")})
    assert r.status_code == 409 and r.json()["code"] == "stale", r.text
    with dialect.read_tx(project.engine) as conn:  # the job checks it again: the result went stale after the start
        rec = dict(conn.execute(sa.select(dh_results).where(dh_results.c.id == rid)).mappings().one())
    from bf_core.api.errors import ApiError

    with pytest.raises(ApiError) as err:
        params = {"result_id": rid, "format": "xlsx", "path": str(tmp_path / "к.xlsx"), "holes": None, "rows": 0}
        pj.run_export(project.engine, project.folder, params, job_id=next(_ids), tick=lambda *_a: None)
    assert err.value.status == 409 and err.value.code == "stale" and rec["op"] == "conditions"
    assert not (tmp_path / "к.xlsx").exists()
