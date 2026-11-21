"""Import and the data routes of QAQC (ADR 0020 §2, §10; подшаг 14.10): CSV and XLSX → data with the counts; the
table of the demo set → data, an edit of the table → «устарели», «Обновить»; no SAMPLE_ID, TYPE or elements →
422 with a hint; a cancelled job leaves no object and no file; the values of a field for the filter; the settings
and the reference through the API; a read-only project — 423; the job in its `spawn` worker from a path with
Cyrillic and spaces (`-m windows`)."""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.jobs.context import JobCancelled, JobContext
from bf_core.projects.service import projects_of
from bf_core.qaqc import jobs as qj
from bf_core.qaqc import records as r
from bf_core.qaqc import service as sv
from bf_core.storage.blobs import BlobStore
from tests.test_archive import wait_job
from tests.test_import_job import API

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.qaqc import generate, write_csv  # noqa: E402

Q = f"{API}/qaqc"


@pytest.fixture()
def qaqc_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: the router of QAQC and its job (until `api/modules.py`, `jobs/registry.py` list them)."""
    import bf_core.api.app as app_module

    if "qaqc" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "qaqc"))
    from bf_core.jobs import registry

    if "bf_core.qaqc.jobs" not in registry.HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.qaqc.jobs")


@pytest.fixture()
def qc(qaqc_core, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Контроль"}).status_code == 200
    return pclient


def csv_file(folder: Path, control: int = 600, name: str = "контроль.csv", **kw: Any) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    write_csv(path, generate(control, **kw))
    return path


def run_import(c: Any, body: dict[str, Any]) -> dict[str, Any]:
    res = c.post(f"{Q}/data", json=body)
    assert res.status_code == 202, res.text
    job = wait_job(c, res.json()["source"], res.json()["job_id"], timeout=120)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


def import_csv(c: Any, path: Path, name: str = "Контроль 2024") -> dict[str, Any]:
    info = c.post(f"{Q}/data/inspect", json={"kind": "file", "path": str(path)}).json()
    return run_import(c, {"name": name, "source": {"kind": "file", "path": str(path)}, "mapping": info["mapping"]})


@pytest.mark.windows
def test_csv_import_in_a_path_with_cyrillic(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = csv_file(tmp_path / "Данные QAQC" / "партия 1", name="контроль проб.csv")
    info = qc.post(f"{Q}/data/inspect", json={"kind": "file", "path": str(path)}).json()
    assert info["kind"] == "file" and info["info"]["format"] == "csv" and len(info["preview"]) == 50
    kinds = {c["name"]: c["kind"] for c in info["columns"]}
    assert kinds["AU_ppm"] == "number" and kinds["SAMPLE_ID"] == "text" and kinds["SEQ"] == "number"
    m = info["mapping"]
    assert (m["sample_id"], m["type"], m["parent_id"], m["lab"], m["batch"], m["date"]) == (
        "SAMPLE_ID", "TYPE", "PARENT_ID", "LAB", "BATCH", "DATE")  # fmt: skip
    assert [(e["name"], e["unit"]) for e in m["elements"]] == [("AU", "ppm"), ("CU", "%"), ("AG", "ppm")]
    res = run_import(qc, {"name": "Контроль 2024", "source": {"kind": "file", "path": str(path)}, "mapping": m})
    assert res["rows"] == 930 and res["replaced"] is False
    assert res["counts"]["no_parent"] == 0 and res["counts"]["dup_ids"] == 0 and res["counts"]["below_dl"] > 0
    assert res["events"] == [["objects.changed", {"kind": "qaqc_data", "id": res["data_id"]}]]
    data = qc.get(f"{Q}/data/{res['data_id']}").json()
    assert data["name"] == "Контроль 2024" and data["rows"] == 930 and data["labs"] == ["EXT", "LAB1", "LAB2", "LAB3"]
    assert data["source"]["kind"] == "file" and data["source"]["name"] == "контроль проб.csv"
    assert {t["code"]: t["type"] for t in data["type_codes"]} == {
        "BLANK": "BLANK", "CRM": "CRM", "DUP_C": "DUP_C", "DUP_F": "DUP_F", "DUP_P": "DUP_P", "ORIG": "ORIG",
        "UMP": "UMP"}  # fmt: skip
    assert data["stale"] is False and data["date_from"] <= data["date_to"]
    assert qc.get(f"{Q}/data").json()["items"][0]["id"] == res["data_id"]


def test_xlsx_import_flags_and_dates(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Проба", "Тип", "Исходная", "Лаб", "Партия", "Дата", "Au, г/т"])
    ws.append(["A1", "ORIG", None, "L1", "P1", datetime(2024, 3, 1), 1.25])
    ws.append(["A2", "std", None, "L1", "P1", "02.03.2024", "<0,01"])
    ws.append(["A3", "DUP_F", "a1", "L1", "P1", 45353, None])
    ws.append(["A4", "BLK", None, "L1", None, "вчера", ">50"])
    path = tmp_path / "контроль.xlsx"
    wb.save(path)
    info = qc.post(f"{Q}/data/inspect", json={"path": str(path)}).json()
    m = info["mapping"]
    assert (m["sample_id"], m["type"], m["parent_id"], m["lab"], m["batch"], m["date"]) == (
        "Проба", "Тип", "Исходная", "Лаб", "Партия", "Дата")  # fmt: skip
    assert m["elements"] == [{"column": "Au, г/т", "name": "Au", "unit": "г/т", "dl_column": None}]
    res = run_import(qc, {"source": {"kind": "file", "path": str(path)}, "mapping": m})
    assert res["name"] == "контроль" and res["rows"] == 4
    c = res["counts"]
    assert (c["below_dl"], c["above_dl"], c["bad_dates"], c["no_parent"]) == (1, 1, 1, 0)
    p = projects_of(qc.core).require_current()
    recs = sv.open_records(p, res["data_id"])
    assert recs.number(r.v("Au")).tolist()[:2] == [1.25, 0.005]
    assert recs.flags(r.q("Au")).tolist() == [0, 1, 0, 2]
    assert recs.number(r.p("Au"))[2] == 1.25
    day = 86_400_000
    ms = recs.date()
    assert ms[1] - ms[0] == day and ms[2] - ms[0] == day  # 45353 — 02.03.2024 by Excel
    data = qc.get(f"{Q}/data/{res['data_id']}").json()
    assert {t["code"]: t["type"] for t in data["type_codes"]} == {"BLK": "BLANK", "DUP_F": "DUP_F", "ORIG": "ORIG",
                                                                  "std": "CRM"}  # fmt: skip


def test_mapping_refused_with_a_hint(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = csv_file(tmp_path, 100)
    m = qc.post(f"{Q}/data/inspect", json={"path": str(path)}).json()["mapping"]
    src = {"kind": "file", "path": str(path)}
    for change, code in (({"sample_id": None}, "no_sample_id"), ({"type": None}, "no_type"),
                         ({"elements": []}, "no_elements"), ({"lab": "Нет такой"}, "field_not_found")):  # fmt: skip
        res = qc.post(f"{Q}/data", json={"name": "X", "source": src, "mapping": {**m, **change}})
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["code"] == code and body["message"] and body["hint"] and "param" in body
    res = qc.post(f"{Q}/data", json={"name": "X", "source": {"kind": "file", "path": str(tmp_path / "нет.csv")},
                                     "mapping": m})  # fmt: skip
    assert res.status_code == 404 and res.json()["code"] == "file_not_found"
    import_csv(qc, path, "Первые")
    res = qc.post(f"{Q}/data", json={"name": "первые", "source": src, "mapping": m})
    assert res.status_code == 409 and res.json()["code"] == "name_taken"
    assert qc.post(f"{Q}/data", json={"source": {"kind": "url"}}).json()["code"] == "bad_param"


def test_demo_table_snapshot_stale_and_refresh(qc) -> None:  # type: ignore[no-untyped-def]
    assert qc.post(f"{API}/projects/demo").status_code == 200
    info = qc.post(f"{Q}/data/inspect", json={"kind": "table", "set_id": 1, "table": "Пробы"}).json()
    names = [c["name"] for c in info["columns"]]
    assert {"Номер пробы", "AU", "CU", "HOLE", "FROM", "TO"} <= set(names) and info["rows"] == 4036
    m = info["mapping"]
    assert m["hole"] == "HOLE" and [(e["name"], e["unit"]) for e in m["elements"]] == [("AU", "г/т"), ("CU", "%")]
    m = {**m, "sample_id": "Номер пробы", "type": "HOLE"}  # the demo has no TYPE: hole names are codes «не распознано»
    res = run_import(qc, {"name": "Демо", "source": {"kind": "table", "set_id": 1, "table": "Пробы"}, "mapping": m})
    data = qc.get(f"{Q}/data/{res['data_id']}").json()
    assert data["rows"] == res["rows"] > 4000 and data["source"]["kind"] == "table" and data["stale"] is False
    assert all(t["type"] is None for t in data["type_codes"])
    p = projects_of(qc.core).require_current()
    with dialect.write_tx(p.engine) as conn:  # an edit of the table raises its rev
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == data["source"]["table_id"])
                     .values(rev=dh_tables.c.rev + 1))  # fmt: skip
    assert qc.get(f"{Q}/data/{res['data_id']}").json()["stale"] is True
    r2 = qc.post(f"{Q}/data/{res['data_id']}/refresh")
    assert r2.status_code == 202, r2.text
    job = wait_job(qc, r2.json()["source"], r2.json()["job_id"], timeout=120)
    assert job["status"] == "done" and job["result"]["replaced"] is True and job["result"]["data_id"] == res["data_id"]
    after = qc.get(f"{Q}/data/{res['data_id']}").json()
    assert after["stale"] is False and after["version"] == 2 and after["source"]["rev"] == data["source"]["rev"] + 1


class _Cancelling(JobContext):
    def __init__(self, job: dict[str, Any], at: str) -> None:
        super().__init__(job, lambda _m: None, threading.Event())
        self.at = at

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if message and message.startswith(self.at):
            raise JobCancelled()


@pytest.mark.parametrize("at", ["Нормализация", "Запись записей", "Сохранение"])
def test_cancelled_import_leaves_nothing(qc, tmp_path: Path, at: str) -> None:  # type: ignore[no-untyped-def]
    path = csv_file(tmp_path, 300)
    p = projects_of(qc.core).require_current()
    m = qc.post(f"{Q}/data/inspect", json={"path": str(path)}).json()["mapping"]
    params = qj.check_submit(p, {"name": "Отменённые", "source": {"kind": "file", "path": str(path)}, "mapping": m})
    ctx = _Cancelling({"id": 1, "params": params, "project_folder": str(p.folder)}, at)
    with pytest.raises(JobCancelled):
        qj.run_import(ctx)
    ctx.close()
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(objects.c.id).where(objects.c.kind == sv.KIND)).first() is None
    tmp = BlobStore(p.folder).tmp_dir
    assert not tmp.exists() or not any(tmp.iterdir())


def test_values_rename_delete_and_events(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    res = import_csv(qc, csv_file(tmp_path, 400))
    did = res["data_id"]
    v = qc.get(f"{Q}/data/{did}/values", params={"field": r.LAB}).json()
    assert [x["value"] for x in v["values"]] == ["EXT", "LAB1", "LAB2", "LAB3"] and v["more"] is False
    assert sum(x["n"] for x in v["values"]) == 620 and v["empty"] == 0
    v = qc.get(f"{Q}/data/{did}/values", params={"field": r.TYPE_CODE, "q": "dup", "limit": 2}).json()
    assert [x["value"] for x in v["values"]] == ["DUP_C", "DUP_F"] and v["more"] is True
    v = qc.get(f"{Q}/data/{did}/values", params={"field": r.SAMPLE_ID, "q": "s000000", "limit": 3}).json()
    assert [x["value"] for x in v["values"]] == ["S0000001", "S0000002", "S0000003"]
    assert qc.get(f"{Q}/data/{did}/values", params={"field": r.SEQ}).json()["values"][0] == {"value": 1.0, "n": 13}
    bad = qc.get(f"{Q}/data/{did}/values", params={"field": "нет"})
    assert bad.status_code == 422 and bad.json()["code"] == "field_not_found"
    ren = qc.put(f"{Q}/data/{did}", json={"name": "Новое имя", "expected_version": 1})
    assert ren.status_code == 200 and ren.json()["version"] == 2
    assert qc.put(f"{Q}/data/{did}", json={"name": "Ещё", "expected_version": 1}).json()["code"] == "version_conflict"
    assert qc.delete(f"{Q}/data/{did}").json() == {"id": did, "deleted": True}
    gone = qc.get(f"{Q}/data/{did}")
    assert gone.status_code == 404 and gone.json()["code"] == "qaqc_data_not_found"


def test_config_and_crm_routes(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cfg = qc.get(f"{Q}/config").json()
    assert cfg["version"] == 0 and cfg["limits"]["run"] == 9
    bad = qc.put(f"{Q}/config", json={"limits": {"th_group": 12}, "expected_version": 0})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_param" and bad.json()["param"] == "limits.th_group"
    ok = qc.put(f"{Q}/config", json={**cfg, "limits": {"run": 12}, "expected_version": 0})
    assert ok.status_code == 200 and ok.json()["version"] == 1 and ok.json()["limits"]["run"] == 12
    stale = qc.put(f"{Q}/config", json={"expected_version": 0})
    assert stale.status_code == 409 and stale.json()["current"]["version"] == 1
    rows = [{"crm_id": "CRM-01", "element": "AU", "mean": 1.0, "sd": 0}]
    bad = qc.put(f"{Q}/crm", json={"rows": rows, "expected_version": 0})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_crm" and bad.json()["row"] == 0
    rows[0]["sd"] = 0.05
    assert qc.put(f"{Q}/crm", json={"rows": rows, "expected_version": 0}).json()["version"] == 1
    path = tmp_path / "crm.csv"
    path.write_text("CRM_ID,ELEMENT,MEAN,SD\nA,AU,1.5,0.1\n", encoding="utf-8")
    got = qc.post(f"{Q}/crm/read", json={"path": str(path)}).json()
    assert got["rows"][0]["mean"] == 1.5 and got["problems"] == []
    assert qc.get(f"{Q}/crm").json()["rows"][0]["sd"] == 0.05  # read wrote nothing


def test_read_only_project_is_423(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = csv_file(tmp_path, 100)
    did = import_csv(qc, path)["data_id"]
    p = projects_of(qc.core).require_current()
    p.read_only = True
    try:
        m = qc.post(f"{Q}/data/inspect", json={"path": str(path)}).json()["mapping"]  # reading works
        assert qc.get(f"{Q}/data/{did}").status_code == 200 and qc.get(f"{Q}/config").status_code == 200
        for res in (
            qc.post(f"{Q}/data", json={"name": "Б", "source": {"kind": "file", "path": str(path)}, "mapping": m}),
            qc.put(f"{Q}/data/{did}", json={"name": "Б", "expected_version": 1}),
            qc.delete(f"{Q}/data/{did}"),
            qc.post(f"{Q}/data/{did}/refresh"),
            qc.put(f"{Q}/config", json={"expected_version": 0}),
            qc.put(f"{Q}/crm", json={"rows": [], "expected_version": 0}),
        ):
            assert res.status_code == 423 and res.json()["code"] == "project_read_only"
    finally:
        p.read_only = False
