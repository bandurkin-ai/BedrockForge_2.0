"""Import of drillholes: cases found in the Э3 review (several files of one step, retries, replace by type,
DAT text in number fields, survey attributes, archive round trip, secrets in errors)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_rows, dh_surveys, dh_tables, import_log, objects
from bf_core.projects.service import projects_of
from tests.test_import_job import API, _check, wait_job

pytestmark = pytest.mark.windows


def _spec(c, path: Path, **extra):  # type: ignore[no-untyped-def]
    chk = _check(c, path, **extra)
    return {"path": str(path), "kind": chk["kind"], "mapping": chk["mapping"], "components": chk["components"], **extra}


def _run(c, body: dict) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/drillholes/import", json=body)
    assert r.status_code == 200, r.text
    return wait_job(c, r.json()["source"], r.json()["id"])


def test_two_collar_files_are_joined_and_a_hole_in_both_fails(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Два файла"})
    a, b, c = (tmp_path / f"collar_{k}.csv" for k in "abc")
    a.write_text("HoleID,X,Y,Z,Depth\nD1,1,2,3,10\n", encoding="utf-8")
    b.write_text("HoleID,X,Y,Z,Depth\nD2,4,5,6,20\n", encoding="utf-8")
    c.write_text("HoleID,X,Y,Z,Depth\nD2,7,8,9,30\n", encoding="utf-8")
    job = _run(pclient, {"set_name": "Н", "files": [_spec(pclient, a), _spec(pclient, b)]})
    assert job["status"] == "done", job
    assert pclient.get(f"{API}/projects/current").json()["project"]["summary"]["holes"] == 2
    job = _run(pclient, {"set_name": "Н2", "files": [_spec(pclient, b), _spec(pclient, c)]})
    assert job["status"] == "failed" and job["error"]["code"] == "hole_in_two_files", job
    assert "D2" in job["error"]["message"]


def test_two_files_into_one_table_are_refused(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Две таблицы"})
    a, b = tmp_path / "assay_2023.csv", tmp_path / "assay_2024.csv"
    for p in (a, b):
        p.write_text("Hole_ID,From,To,Au_ppm,SampleID\nD1,0,1,1.5,S1\n", encoding="utf-8")
    r = pclient.post(
        f"{API}/drillholes/import", json={"set_name": "Н", "files": [_spec(pclient, a), _spec(pclient, b)]}
    )
    assert r.status_code == 409 and r.json()["code"] == "files_clash"
    assert "assay_2023.csv" in r.json()["message"]


def test_assay_replaces_the_assay_table_whatever_its_name(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Замена"})
    a, b = tmp_path / "assay_2023.csv", tmp_path / "assay_2024.csv"
    a.write_text("Hole_ID,From,To,Au_ppm,SampleID\nD1,0,1,1.5,S1\nD1,1,2,2,S2\n", encoding="utf-8")
    b.write_text("Hole_ID,From,To,Au_ppm,SampleID\nD1,0,2,3,S9\n", encoding="utf-8")
    assert (
        _run(pclient, {"set_name": "Н", "files": [{**_spec(pclient, a), "table_name": "assay_2023"}]})["status"]
        == "done"
    )
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    job = _run(pclient, {"set_id": set_id, "files": [{**_spec(pclient, b), "table_name": "assay_2024"}]})
    assert job["status"] == "done", job
    tables = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["tables"]
    assert tables == [{"name": "assay_2024", "kind": "assay", "rows": 1}]
    assert pclient.get(f"{API}/projects/current").json()["project"]["summary"]["assays"] == 1


def test_retry_after_the_final_transaction_does_not_import_again(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing.jobs import import_drillholes
    from bf_core.jobs.context import JobContext

    pclient.post(f"{API}/projects", json={"name": "Повтор"})
    a = tmp_path / "collar.csv"
    a.write_text("HoleID,X,Y,Z,Depth\nD1,1,2,3,10\n", encoding="utf-8")
    body = {"set_name": "Н", "files": [_spec(pclient, a)]}
    r = pclient.post(f"{API}/drillholes/import", json=body).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done"
    p = projects_of(pclient.core).require_current()
    params = pclient.get(f"{API}/jobs/{r['source']}/{r['id']}").json().get("params") or {
        "set_id": None,
        "set_name": "Н",
        "crs": None,
        "files": [{**f, "options": {}} for f in body["files"]],
    }
    sent: list = []
    ctx = JobContext({"id": r["id"], "params": params, "project_folder": str(p.folder)}, sent.append, threading.Event())
    try:
        again = import_drillholes(ctx)  # what the worker would run on attempt 2
    finally:
        ctx.close()
    assert again["set_id"] == job["result"]["set_id"] and again["files"][0]["collars"] == 1
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 1
        assert conn.execute(sa.select(import_log.c.job_id)).scalars().all() == [r["id"]]


def test_dat_text_in_a_number_field_is_reported_like_csv(tmp_path: Path) -> None:
    from bf_core.formats.micromine import write_dat
    from bf_core.formats.micromine_header import Field
    from bf_core.formats.registry import read_table
    from bf_core.importing.service import normalize

    csv_good = tmp_path / "good.csv"
    csv_good.write_text("HOLE_ID,FROM,TO,AU\nD1,0,1,1.5\nD1,1,2,777.25\n", encoding="utf-8")
    dat = tmp_path / "assay.dat"
    fields = [Field("HOLE_ID", "C", 8, 0), Field("FROM", "N", 10, 2), Field("TO", "N", 10, 2), Field("AU", "N", 10, 2)]
    write_dat(dat, read_table(csv_good).columns, fields=fields)
    raw = dat.read_bytes()
    assert raw.count(b"777.25") == 1
    dat.write_bytes(raw.replace(b"777.25", b"   abc"))  # what a hand-edited DAT holds
    csv_bad = tmp_path / "bad.csv"
    csv_bad.write_text("HOLE_ID,FROM,TO,AU\nD1,0,1,1.5\nD1,1,2,abc\n", encoding="utf-8")
    mapping = {"hole_id": "HOLE_ID", "from": "FROM", "to": "TO"}
    comps = [{"column": "AU", "unit": "g/t"}]
    for path in (dat, csv_bad):
        t = read_table(path)
        res, issues = normalize(t, "assay", mapping, comps)
        assert res is None and [i.code for i in issues if i.severity == "error"] == ["non_numeric"], (path, issues)
        assert "«abc»" in issues[0].message
        res, issues = normalize(t, "assay", mapping, comps, force=True)
        assert res is not None and res.table is not None
        src = res.table.values[[c.name for c in res.table.columns].index("AU (исходное)")]
        assert list(src) == [None, "abc"], path


def test_survey_attributes_are_kept_and_readings_without_depth_counted(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Инклинометрия"})
    s = tmp_path / "survey.csv"
    s.write_text(
        "HOLEID,DEPTH,AZIMUTH,DIP,TOOL\nD1,0,90,-60,гиро\nD1,,91,-61,гиро\nD1,30,92,-62,гиро\n", encoding="utf-8"
    )
    chk = _check(pclient, s, attributes=["TOOL"])
    assert chk["counts"]["skipped"] == 1 and any(i["code"] == "survey_without_depth" for i in chk["issues"])
    assert chk["preview"][0]["TOOL"] == "гиро"
    job = _run(pclient, {"set_name": "Н", "files": [{**_spec(pclient, s, attributes=["TOOL"])}]})
    assert job["status"] == "done", job
    p = projects_of(pclient.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        attrs = conn.execute(sa.select(dh_surveys.c.attrs).order_by(dh_surveys.c.depth)).scalars().all()
    assert attrs == [{"TOOL": "гиро"}, {"TOOL": "гиро"}]


def test_archive_keeps_drillholes_and_leaves_out_tables_being_built(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes import store as dh

    pclient.post(f"{API}/projects", json={"name": "Архив"})
    a = tmp_path / "assay.csv"
    a.write_text(
        "Hole_ID,From,To,Au_ppm,SampleID\n" + "".join(f"D{i % 7},{i},{i + 1},{i / 10},S{i}\n" for i in range(12_000)),
        encoding="utf-8",
    )
    s = tmp_path / "survey.csv"
    s.write_text("HOLEID,DEPTH,AZIMUTH,DIP\nD1,0,90,-60\nD1,30,92,-62\n", encoding="utf-8")
    spec = {**_spec(pclient, a), "components": [{"column": "Au_ppm", "unit": "ppm"}]}
    assert _run(pclient, {"set_name": "Н", "files": [spec, _spec(pclient, s)]})["status"] == "done"
    p = projects_of(pclient.core).require_current()
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    data = dh.TableData("Литология", "interval", ["NEW"], [0.0], [1.0], [dh.ColumnSpec("LITH", "text")], [["гранит"]])
    dh.write_table(p.engine, set_id, data, job_id=4242, tick=lambda _f, _m: None, swap=False)  # a running import

    r = pclient.post(f"{API}/projects/export", json={"path": str(tmp_path / "out"), "password": None}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    r = pclient.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job
    pclient.post(f"{API}/projects/{job['result']['uid']}/open")
    q = projects_of(pclient.core).require_current()
    with dialect.read_tx(q.engine) as conn:
        assert conn.execute(sa.select(dh_tables.c.name, dh_tables.c.state)).all() == [("Пробы (Assay)", "ready")]
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one() == 12_000
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_surveys)).scalar_one() == 2
        names = set(conn.execute(sa.select(dh_holes.c.name)).scalars())
        assert names == {f"D{i}" for i in range(7)}  # «NEW» came only with the table being built
        hole = dict(conn.execute(sa.select(dh_holes.c.name, dh_holes.c.id)).all())
        rows = conn.execute(sa.select(dh_rows.c.hole_id, dh_rows.c.from_m).where(dh_rows.c.from_m == 8.0)).all()
        assert rows == [(hole["D1"], 8.0)]
        assert conn.execute(sa.select(import_log.c.job_id)).scalars().all() == [None, None]


def test_passwords_are_masked_in_internal_errors() -> None:
    from bf_core.api.errors import internal_body

    body = internal_body(RuntimeError("insert failed: {'secret': 'Пароль123', 'id': 5} password=qwerty"))
    assert "Пароль123" not in body["detail"] and "qwerty" not in body["detail"] and "***" in body["detail"]
    assert "Пароль123" not in body["trace"]


def test_odd_control_characters_inside_a_cell_do_not_split_the_line(tmp_path: Path) -> None:
    from bf_core.formats.registry import read_table

    p = tmp_path / "t.csv"
    tail = "".join(f"D{i};обычная строка;{i}\r\n" for i in range(3, 60))  # control characters stay under 1 %
    p.write_bytes(("HOLE;NOTE;X\r\nD1;a\x0bb\x1cc\u2028d\x85e;1\r\nD2;e;2\r\n" + tail).encode())
    t = read_table(p)
    assert t.rows == 59 and t.column("NOTE").to_pylist()[:2] == ["a\x0bb\x1cc\u2028d\x85e", "e"]
    assert t.column("X").to_pylist()[:2] == [1.0, 2.0]
