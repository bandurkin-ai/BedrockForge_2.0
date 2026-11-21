"""QAQC reports (ADR 0020 §7; подшаг 14.13): Т8.4-8 — the summary of a report by a filter and of a report over the
records taken by that filter beforehand are one canonical JSON byte for byte; a report is computed after its data
are deleted; «устарел» after an edit of the settings, of the reference, a replacement of the data; «Перестроить»;
a cancelled job leaves no object (and a cancelled rebuild changes nothing); PNG > 2 МБ → 413, not a PNG → 415,
the 201st picture is refused; the `.bfggis` cycle keeps the report with its files (10.5 п. 5)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.jobs.context import JobCancelled, JobContext
from bf_core.jobs.store import drop_partial
from bf_core.projects.service import projects_of
from bf_core.qaqc import config as cf
from bf_core.qaqc import jobs as qj
from bf_core.qaqc import report as rp
from bf_core.qaqc import service as sv
from bf_core.qaqc.filters import map_types, parse_filter, row_mask
from bf_core.storage.blobs import BlobStore
from tests.test_archive import export, wait_job
from tests.test_import_job import API
from tests.test_qaqc_import_api import csv_file, import_csv, qaqc_core  # noqa: F401 - the fixture

Q = f"{API}/qaqc"
FILTER = {"v": 1, "labs": ["LAB1", "lab3"], "date_from": "2024-03-01"}


def png(w: int = 40, h: int = 20) -> bytes:
    """A real PNG of w × h red pixels (the core reads its IHDR)."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\0" + b"\xff\x00\x00" * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG = png()


@pytest.fixture()
def qc(qaqc_core, pclient):  # type: ignore[no-untyped-def]  # noqa: F811
    assert pclient.post(f"{API}/projects", json={"name": "Отчёты"}).status_code == 200
    return pclient


def project(c: Any) -> Any:
    return projects_of(c.core).require_current()


def settings(c: Any) -> None:
    from tools.gen.qaqc import generate

    gen = generate(10, seed=1)
    assert c.put(f"{Q}/crm", json={"rows": gen.crm, "expected_version": 0}).status_code == 200
    cfg = c.get(f"{Q}/config").json()
    assert c.put(f"{Q}/config", json={**cfg, "dl": gen.dl, "expected_version": 0}).status_code == 200


def build(c: Any, data_id: int, **body: Any) -> dict[str, Any]:
    res = c.post(f"{Q}/reports", json={"data_id": data_id, **body})
    assert res.status_code == 202, res.text
    job = wait_job(c, res.json()["source"], res.json()["job_id"], timeout=120)
    assert job["status"] == "done", job
    return c.get(f"{Q}/reports/{res.json()['report_id']}").json()  # type: ignore[no-any-return]


def data(c: Any, tmp: Path, control: int = 800, seed: int = 12345, name: str = "Контроль") -> int:
    return import_csv(c, csv_file(tmp, control, name=f"{name}.csv", seed=seed), name)["data_id"]  # type: ignore[no-any-return]


def test_t848_filter_equals_the_records_taken_before(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    did = data(qc, tmp_path)
    a = build(qc, did, filter=FILTER, charts="none")
    assert a["state"] == "ready" and a["stale"] is None and a["elements"] == ["AU", "CU", "AG"]
    p = project(qc)
    with dialect.read_tx(p.engine) as conn:
        config = cf.get_config(conn)
    recs = sv.open_records(p, did)
    from bf_core.qaqc import records as r

    mask = row_mask(recs, parse_filter(FILTER), map_types(recs.text(r.TYPE_CODE), config))
    assert mask is not None and 0 < int(mask.sum()) < recs.n
    sub = recs.take(mask)
    store = BlobStore(p.folder)
    staged = sv.stage(store, sub)
    with dialect.write_tx(p.engine) as conn:
        meta = sv.make_meta(sub, {"rows": sub.n}, [], {"kind": "file", "name": "часть", "sha256": "0" * 64}, {})
        part = sv.create(conn, store, staged, name="Часть", meta=meta)
    b = build(qc, part, charts="none")
    sa_, sb = a["summary"], b["summary"]
    assert rp.canonical_summary(sa_) == rp.canonical_summary(sb)  # Т8.4-8, byte for byte
    au = sa_["elements"]["AU"]
    assert set(au) == {"summary", "standards", "blanks"} | {k for k in au if k.startswith(("duplicates", "external"))}
    assert "series" not in au["standards"] and au["standards"]["counts"]["filtered"] == int(mask.sum())
    assert a["inputs_sha256"] != b["inputs_sha256"] and len(a["data_sha256"]) == 64
    assert a["summary_size"] < 5 * 2**20


def test_report_lives_after_its_data_and_goes_stale(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    did = data(qc, tmp_path)
    rep = build(qc, did, elements=["au"], charts="all")
    rid = rep["id"]
    assert rep["elements"] == ["AU"] and rep["stale"] is None
    by_report = qc.post(f"{Q}/calc/standards", json={"report_id": rid, "element": "AU"}).json()
    assert by_report["groups"] == rep["summary"]["elements"]["AU"]["standards"]["groups"]
    cfg = qc.get(f"{Q}/config").json()
    res = qc.put(f"{Q}/config", json={**cfg, "limits": {"run": 10}, "expected_version": cfg["version"]})
    assert res.status_code == 200
    assert qc.get(f"{Q}/reports/{rid}").json()["stale"] == "config"
    after = qc.post(f"{Q}/calc/standards", json={"report_id": rid, "element": "AU"}).json()
    assert after["limits"]["run"] == 9  # the snapshot, not the current settings
    assert qc.put(f"{Q}/config", json={**cfg, "expected_version": cfg["version"] + 1}).status_code == 200
    assert qc.get(f"{Q}/reports/{rid}").json()["stale"] is None
    crm = qc.get(f"{Q}/crm").json()
    rows = [{**crm["rows"][0], "sd": crm["rows"][0]["sd"] * 2}, *crm["rows"][1:]]
    assert qc.put(f"{Q}/crm", json={"rows": rows, "expected_version": crm["version"]}).status_code == 200
    assert qc.get(f"{Q}/reports/{rid}").json()["stale"] == "config"
    assert qc.put(f"{Q}/crm", json={"rows": crm["rows"], "expected_version": crm["version"] + 1}).status_code == 200
    other = csv_file(tmp_path / "другие", 300, name="другие.csv", seed=7)
    m = qc.post(f"{Q}/data/inspect", json={"path": str(other)}).json()["mapping"]
    res = qc.post(f"{Q}/data", json={"name": "Контроль", "source": {"kind": "file", "path": str(other)}, "mapping": m,
                                     "replace_id": did})  # fmt: skip
    assert wait_job(qc, res.json()["source"], res.json()["job_id"], timeout=120)["status"] == "done"
    assert qc.get(f"{Q}/reports/{rid}").json()["stale"] == "data"
    assert qc.put(f"{Q}/reports/{rid}/images/img001", content=PNG).status_code == 200
    r2 = qc.post(f"{Q}/reports/{rid}/rebuild")
    assert r2.status_code == 202, r2.text
    job = wait_job(qc, r2.json()["source"], r2.json()["job_id"], timeout=120)
    assert job["status"] == "done" and job["result"]["rebuilt"] is True
    now = qc.get(f"{Q}/reports/{rid}").json()
    assert now["version"] == 2 and now["stale"] is None and now["images"] == 0  # the old charts went with the snapshot
    assert now["data_sha256"] != rep["data_sha256"]
    assert qc.delete(f"{Q}/data/{did}").status_code == 200
    gone = qc.get(f"{Q}/reports/{rid}").json()
    assert gone["stale"] == "source_deleted"
    still = qc.post(f"{Q}/calc/blanks", json={"report_id": rid, "element": "AU"})
    assert still.status_code == 200 and still.json()["groups"]  # the records of the report are its own
    no = qc.post(f"{Q}/reports/{rid}/rebuild")
    assert no.status_code == 409 and no.json()["code"] == "source_deleted" and no.json()["hint"]


class _Cancelling(JobContext):
    def __init__(self, job: dict[str, Any], at: str) -> None:
        super().__init__(job, lambda _m: None, threading.Event())
        self.at = at

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        if message and message.startswith(self.at):
            raise JobCancelled()


def test_cancelled_report_leaves_nothing(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    did = data(qc, tmp_path, 300)
    p = project(qc)
    store = BlobStore(p.folder)
    with dialect.write_tx(p.engine) as conn:
        inputs = rp.make_inputs(conn, did, None, None, "none")
        rid = rp.create(conn, store, None, inputs)
        rp.set_job(conn, rid, 999)
    ctx = _Cancelling({"id": 999, "params": {"report_id": rid}, "project_folder": str(p.folder)}, "Расчёт")
    with pytest.raises(JobCancelled):
        qj.run_report(ctx)
    ctx.close()
    with dialect.write_tx(p.engine) as conn:  # what the scheduler does after a cancel
        assert drop_partial(conn, 999) == 1
        assert conn.execute(sa.select(objects.c.id).where(objects.c.kind == rp.KIND)).first() is None
        assert conn.execute(sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == rid)).first() is None
    assert sv.open_records(p, did).n > 0  # the shared records of the data stay
    ready = build(qc, did, charts="none")
    with dialect.read_tx(p.engine) as conn:
        again = rp.make_inputs(conn, did, None, None, "all")
    ctx = _Cancelling({"id": 1000, "params": {"report_id": ready["id"], "inputs": again},
                       "project_folder": str(p.folder)}, "Сохранение")  # fmt: skip
    with pytest.raises(JobCancelled):
        qj.run_report(ctx)
    ctx.close()
    with dialect.write_tx(p.engine) as conn:
        assert drop_partial(conn, 1000) == 0
    same = qc.get(f"{Q}/reports/{ready['id']}").json()
    assert (same["version"], same["charts"], same["summary"]) == (1, "none", ready["summary"])


def test_charts_and_images(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    did = data(qc, tmp_path, 400)
    rid = build(qc, did, charts="all")["id"]
    ch = qc.get(f"{Q}/reports/{rid}/charts").json()
    assert ch["total"] == len(ch["charts"]) + ch["omitted"] and 0 < len(ch["charts"]) <= 200
    first = ch["charts"][0]
    assert first["key"] == "img001" and first["section"] == "standards" and first["title"]
    flagged = build(qc, did, charts="flagged", name="С нарушениями")
    fl = qc.get(f"{Q}/reports/{flagged['id']}/charts").json()
    assert fl["total"] < ch["total"]
    none = build(qc, did, charts="none", name="Без графиков")
    assert qc.get(f"{Q}/reports/{none['id']}/charts").json() == {"charts": [], "total": 0, "omitted": 0}
    ok = qc.put(f"{Q}/reports/{rid}/images/img001", content=PNG)
    assert ok.status_code == 200 and ok.json() == {"key": "img001", "images": 1}
    assert qc.put(f"{Q}/reports/{rid}/images/img001", content=PNG + b"1").json()["images"] == 1  # replaced
    big = qc.put(f"{Q}/reports/{rid}/images/img002", content=PNG + b"\0" * (2 * 2**20))
    assert big.status_code == 413 and big.json()["code"] == "image_too_large"
    huge = png(1, 1)[:16] + (9000).to_bytes(4, "big") + (10).to_bytes(4, "big") + png(1, 1)[24:]
    wide = qc.put(f"{Q}/reports/{rid}/images/img002", content=huge)
    assert wide.status_code == 415 and wide.json()["code"] == "image_size" and wide.json()["hint"]
    broken = qc.put(f"{Q}/reports/{rid}/images/img002", content=b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    assert broken.status_code == 415 and broken.json()["code"] == "not_png"
    jpeg = qc.put(f"{Q}/reports/{rid}/images/img002", content=b"\xff\xd8\xff\xe0" + b"\0" * 64)
    assert jpeg.status_code == 415 and jpeg.json()["code"] == "not_png"
    past = qc.put(f"{Q}/reports/{rid}/images/img201", content=PNG)
    assert past.status_code == 422 and past.json()["code"] == "too_many_images"
    unknown = qc.put(f"{Q}/reports/{rid}/images/img{len(ch['charts']) + 1:03d}", content=PNG)
    assert unknown.status_code in (404, 422)
    items = qc.get(f"{Q}/reports").json()["items"]
    assert [i["name"] for i in items] == sorted(i["name"] for i in items) and len(items) == 3
    ren = qc.put(f"{Q}/reports/{rid}", json={"name": "Итоговый", "expected_version": 1})
    assert ren.status_code == 200 and ren.json()["name"] == "Итоговый"
    taken = qc.put(f"{Q}/reports/{rid}", json={"name": "Без графиков", "expected_version": 2})
    assert taken.json()["code"] == "name_taken"
    assert qc.delete(f"{Q}/reports/{none['id']}").json() == {"id": none["id"], "deleted": True}
    assert qc.get(f"{Q}/reports/{none['id']}").json()["code"] == "qaqc_report_not_found"


def test_report_errors(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    did = data(qc, tmp_path, 100)
    for body, code in (({"data_id": did, "elements": ["ZN"]}, "field_not_found"),
                       ({"data_id": did, "charts": "some"}, "bad_param"),
                       ({"data_id": did, "filter": {"v": 1, "nope": 1}}, "bad_param"),
                       ({"data_id": "x"}, "bad_param")):  # fmt: skip
        res = qc.post(f"{Q}/reports", json=body)
        assert res.status_code == 422 and res.json()["code"] == code and res.json()["param"], body
    assert qc.post(f"{Q}/reports", json={"data_id": did + 9}).json()["code"] == "qaqc_data_not_found"
    res = qc.post(f"{Q}/calc/summary", json={"report_id": 77, "element": "AU"})
    assert res.status_code == 404 and res.json()["code"] == "qaqc_report_not_found"


def test_bfggis_cycle_keeps_the_report_and_its_files(qc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    settings(qc)
    did = data(qc, tmp_path, 300)
    rep = build(qc, did, charts="all")
    assert qc.put(f"{Q}/reports/{rep['id']}/images/img001", content=PNG).status_code == 200
    assert qc.delete(f"{Q}/data/{did}").status_code == 200  # the report keeps its own records
    job = export(qc, tmp_path / "С отчётом")
    res = qc.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    done = wait_job(qc, res["source"], res["id"])
    assert done["status"] == "done", done
    assert qc.post(f"{API}/projects/{done['result']['uid']}/open").status_code == 200
    new_id = int(done["result"]["id_maps"]["objects"][str(rep["id"])])
    after = qc.get(f"{Q}/reports/{new_id}").json()
    keep = ("name", "version", "state", "data_name", "data_sha256", "inputs_sha256", "elements", "filter", "charts",
            "summary", "summary_size")  # fmt: skip
    assert {k: after[k] for k in keep} == {k: rep[k] for k in keep}
    assert after["images"] == 1 and after["stale"] == "source_deleted"
    calc = qc.post(f"{Q}/calc/standards", json={"report_id": new_id, "element": "CU"}).json()
    assert calc["groups"] == rep["summary"]["elements"]["CU"]["standards"]["groups"]


def test_batch_status_by_any_element_and_no_orphan_building(qc, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """14.25: the summary holds the status of a batch by any element; a report whose job could not be started is
    not left `building`."""
    settings(qc)
    did = data(qc, tmp_path, 400)
    rep = build(qc, did, charts="none")
    line = rep["summary"]["batches_any"]
    per = [qc.post(f"{Q}/calc/batches", json={"report_id": rep["id"], "element": e}).json()["batches"]
           for e in rep["elements"]]  # fmt: skip
    status = rp.any_status(per)
    assert line == {"total": len(status), "failed": list(status.values()).count(2),
                    "not_assessed": list(status.values()).count(-1)}  # fmt: skip
    assert line["failed"] >= max(sum(b["status"] == 2 for b in rows) for rows in per)
    assert rp.any_status([[{"lab": "L", "batch": "B", "status": -1}], [{"lab": "L", "batch": "B", "status": 0}]]) == {
        ("L", "B"): 0
    }
    assert rp.any_status([[{"lab": "L", "batch": "B", "status": 0}], [{"lab": "L", "batch": "B", "status": 2}]]) == {
        ("L", "B"): 2
    }
    from bf_core.jobs.scheduler import Scheduler

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the queue is closed")

    monkeypatch.setattr(Scheduler, "submit", refuse)
    res = qc.post(f"{Q}/reports", json={"data_id": did, "charts": "none", "name": "Не начат"})
    assert res.status_code == 500 and res.json()["code"] == "internal"
    names = [i["name"] for i in qc.get(f"{Q}/reports").json()["items"]]
    assert "Не начат" not in names and len(names) == 1
