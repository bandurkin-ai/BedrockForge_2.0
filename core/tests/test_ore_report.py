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
