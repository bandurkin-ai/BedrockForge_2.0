"""A whole project to a .bfggis file and back (app/archive/)."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.archive import container, versions
from app.archive.container import BadPassword, Corrupted, TooNew
from app.archive.export import export_project
from app.archive.importer import import_project, remap_refs
from app.archive.spec import SKIPPED, TABLES
from app.db import Base
from app.models import Chart, DataEdit, Dataset, IntervalRow, IntervalTable, Project, Raster, Style, Wireframe


# ---- guards


def test_every_table_is_archived_or_skipped():
    listed = {t.name for t in TABLES} | SKIPPED
    missing = set(Base.metadata.tables) - listed
    assert not missing, f"tables neither in archive/spec.py TABLES nor SKIPPED: {missing}"


def test_every_migration_declares_its_archive_upgrade():
    revs = versions.revisions()
    later = revs[revs.index(versions.FIRST_REVISION):]
    assert set(later) <= set(versions.UPGRADES), (
        f"add the revisions {sorted(set(later) - set(versions.UPGRADES))} to app/archive/versions.py UPGRADES")


def test_remap_refs():
    maps = {"dataset": {5: 50}, "interval_table": {7: 70}, "style": {3: 30}}
    settings = {"x": {"setId": 5, "table": "7"}, "tracks": [{"table": "assay", "style": {"id": 3, "linked": True}},
                                                            {"palette": {"id": 99, "linked": True}}]}
    out = remap_refs(settings, maps)
    assert out["x"] == {"setId": 50, "table": "70"}
    assert out["tracks"][0] == {"table": "assay", "style": {"id": 30, "linked": True}}
    assert out["tracks"][1]["palette"] == {"id": None, "linked": False}
    # Swath plot series: a block model and the samples of a set, with their table.
    swath = {"series": [{"kind": "blocks", "modelId": 5, "field": "a1"}, {"kind": "samples", "setId": 5, "table": "7", "withinSeries": "s1"}]}
    assert remap_refs(swath, maps)["series"] == [{"kind": "blocks", "modelId": 50, "field": "a1"},
                                                 {"kind": "samples", "setId": 50, "table": "70", "withinSeries": "s1"}]


# ---- container


def _write(path: Path, password=None, entries=(("manifest.json", b"{}"),)):
    with container.ArchiveWriter(path, {"summary": {"name": "x"}}, password) as w:
        for name, data in entries:
            w.add_bytes(name, data)


def _names(path: Path, password=None) -> list[str]:
    with container.open_archive(path, password) as (_, tar):
        return [m.name for m in tar]


def test_container_password_and_damage(tmp_path):
    big = os.urandom(9 << 20)  # incompressible: three encrypted chunks
    p = tmp_path / "a.bfggis"
    _write(p, "секрет", [("manifest.json", b"{}"), ("blob", big)])
    with p.open("rb") as f:
        header, _ = container.read_header(f)
    assert header["encrypted"] and "summary" not in header
    assert _names(p, "секрет") == ["manifest.json", "blob"]
    with pytest.raises(BadPassword):
        _names(p, "не тот")
    with pytest.raises(BadPassword):
        _names(p)

    cut = tmp_path / "cut.bfggis"
    cut.write_bytes(p.read_bytes()[: -1000])
    with pytest.raises(Corrupted):
        _names(cut, "секрет")
    flipped = bytearray(p.read_bytes())
    flipped[-5000] ^= 1
    bad = tmp_path / "bad.bfggis"
    bad.write_bytes(bytes(flipped))
    with pytest.raises(Corrupted):
        _names(bad, "секрет")

    plain = tmp_path / "plain.bfggis"
    _write(plain)
    assert _names(plain) == ["manifest.json"]
    future = bytearray(plain.read_bytes())
    future[8:10] = struct.pack("<H", container.FORMAT_VERSION + 1)
    (tmp_path / "f.bfggis").write_bytes(bytes(future))
    with pytest.raises(TooNew):
        _names(tmp_path / "f.bfggis")
    (tmp_path / "x.bfggis").write_bytes(b"PK\x03\x04 not ours")
    with pytest.raises(container.NotAnArchive):
        _names(tmp_path / "x.bfggis")


# ---- the whole project


@pytest.fixture
def rich_project(db_client, db_engine):
    """The demo project plus what the demo lacks: interval table, style, chart, edits, DTM link."""
    pid = db_client.post("/api/projects/demo").json()["id"]
    with Session(db_engine) as s:
        sid = s.scalars(select(Dataset.id).where(Dataset.project_id == pid, Dataset.kind == "drillholes")).first()
        table = IntervalTable(set_id=sid, name="Литология", columns=[{"name": "LITH", "kind": "text"}], row_count=2)
        s.add(table)
        s.flush()
        s.add_all([IntervalRow(table_id=table.id, hole_id="DH-001", from_m=0, to_m=5, values={"LITH": "Гранит"}),
                   IntervalRow(table_id=table.id, hole_id="DH-001", from_m=5, to_m=9, values={"LITH": "Сланец"})])
        style = Style(project_id=pid, name="Литология", level="palette", data_kind="text", body={"classes": []})
        s.add(style)
        s.flush()
        s.add(Chart(dataset_id=sid, kind="downhole", name="Колонка", settings={
            "tracks": [{"table": str(table.id), "field": "LITH", "style": {"id": style.id, "linked": True}}],
            "x": {"setId": sid}}))
        raster = s.scalars(select(Raster).join(Dataset).where(Dataset.project_id == pid)).first()
        wf = s.scalars(select(Wireframe).join(Dataset).where(Dataset.project_id == pid)).first()
        wf.dtm = {"raster_id": raster.id, "opacity": 1}
        # A swath plot naming the set and another object of the project (as if it were a block model).
        s.add(Dataset(project_id=pid, kind="swath", name="Swath plot 1", meta={"settings": {"direction": "x", "series": [
            {"kind": "blocks", "modelId": raster.dataset_id, "field": "a1"}, {"kind": "samples", "setId": sid, "table": str(table.id)}]}}))
        s.commit()
        row = s.scalars(select(IntervalRow.id).where(IntervalRow.table_id == table.id).order_by(IntervalRow.id)).first()
        tid = table.id
    # Edits through the API, as the table editor makes them.
    r = db_client.patch(f"/api/sets/{sid}/datatables/{tid}/rows/{row}", json={"values": {"LITH": "Диорит"}})
    assert r.status_code == 200, r.text
    collar = db_client.get(f"/api/sets/{sid}/datatables/collar?limit=1").json()["rows"][0]
    assert db_client.patch(f"/api/sets/{sid}/datatables/collar/rows/{collar['_id']}",
                           json={"values": {"z": 999.5}}).status_code == 200
    return pid


def _counts(session: Session, project_id: int) -> dict:
    from app.archive.spec import scope_sql

    return {t.name: session.execute(text(f"SELECT count(*) FROM {t.name} WHERE {scope_sql(t.name, project_id)}")).scalar()
            for t in TABLES}


def _blob_set(session: Session, project_id: int) -> set[str]:
    from app.archive.export import project_blobs

    return {sha for sha, _, _ in project_blobs(session, project_id)}


def test_round_trip(rich_project, db_engine, db_client, tmp_path):
    pid = rich_project
    out = tmp_path / "p.bfggis"
    factory = lambda: Session(db_engine)  # noqa: E731
    result = export_project(factory, pid, out, browser={"workspace": {"hidden": ["x"]}})
    assert result["files"] > 0 and out.stat().st_size == result["bytes"]

    with Session(db_engine) as s:
        before = _counts(s, pid)
        shas = _blob_set(s, pid)
        res = import_project(s, out)
        s.commit()
    new = res["project_id"]
    assert new != pid and res["name"] == "Демо: участок Северный (2)"
    assert res["browser"] == {"workspace": {"hidden": ["x"]}}
    with Session(db_engine) as s:
        assert _counts(s, new) == before
        assert _blob_set(s, new) == shas
        maps = res["maps"]
        sid_new = maps["dataset"][str(s.scalars(select(Dataset.id).where(Dataset.project_id == pid, Dataset.kind == "drillholes")).first())]
        chart = s.scalars(select(Chart).where(Chart.dataset_id == sid_new)).one()
        table = s.scalars(select(IntervalTable).where(IntervalTable.set_id == sid_new)).one()
        style = s.scalars(select(Style).where(Style.project_id == new)).one()
        assert chart.settings["tracks"][0]["table"] == str(table.id)
        assert chart.settings["tracks"][0]["style"] == {"id": style.id, "linked": True}
        assert chart.settings["x"]["setId"] == sid_new
        swath = s.scalars(select(Dataset).where(Dataset.project_id == new, Dataset.kind == "swath")).one()
        model_old = s.scalars(select(Raster.dataset_id).join(Dataset).where(Dataset.project_id == pid)).first()
        assert swath.meta["settings"]["series"] == [
            {"kind": "blocks", "modelId": maps["dataset"][str(model_old)], "field": "a1"},
            {"kind": "samples", "setId": sid_new, "table": str(table.id)}]
        wf = s.scalars(select(Wireframe).join(Dataset).where(Dataset.project_id == new, Wireframe.dtm.is_not(None))).one()
        raster_ids = set(s.scalars(select(Raster.id).join(Dataset).where(Dataset.project_id == new)))
        assert wf.dtm["raster_id"] in raster_ids
        edits = s.scalars(select(DataEdit).where(DataEdit.set_id == sid_new).order_by(DataEdit.id)).all()
        assert {e.table_key for e in edits} == {str(table.id), "collar"}
        lith_edit = next(e for e in edits if e.table_key == str(table.id))
        assert s.get(IntervalRow, lith_edit.row_id).values["LITH"] == "Диорит"
    # Undo works in the copy and leaves the original alone.
    assert db_client.post(f"/api/edits/{lith_edit.id}/undo").status_code == 200
    with Session(db_engine) as s:
        assert s.get(IntervalRow, lith_edit.row_id).values["LITH"] == "Гранит"
        orig = s.scalars(select(IntervalRow).join(IntervalTable).join(Dataset, Dataset.id == IntervalTable.set_id)
                         .where(Dataset.project_id == pid).order_by(IntervalRow.id)).first()
        assert orig.values["LITH"] == "Диорит"
    stats_old = db_client.get(f"/api/projects/{pid}/stats").json()
    stats_new = db_client.get(f"/api/projects/{new}/stats").json()
    for k in ("datasets", "holes", "surveys", "assays", "interval_rows", "wireframes", "charts"):
        assert stats_old[k] == stats_new[k], k


def test_conflict_modes_and_password(rich_project, db_engine, tmp_path):
    pid = rich_project
    out = tmp_path / "p.bfggis"
    export_project(lambda: Session(db_engine), pid, out, password="пароль")
    with Session(db_engine) as s:
        with pytest.raises(BadPassword):
            import_project(s, out, password="нет")
        s.rollback()
        res = import_project(s, out, password="пароль", mode="rename", name="Перенос")
        s.commit()
        assert res["name"] == "Перенос"
        from app.archive.importer import ImportFailed

        with pytest.raises(ImportFailed):
            import_project(s, out, password="пароль", mode="rename", name="Перенос")
        s.rollback()
        before = _counts(s, pid)
        res = import_project(s, out, password="пароль", mode="replace", name="Демо: участок Северный")
        s.commit()
        assert res["replaced_project_id"] == pid and res["name"] == "Демо: участок Северный"
        assert s.get(Project, pid) is None
        assert _counts(s, res["project_id"]) == before
        names = set(s.scalars(select(Project.name)))
        assert names == {"Демо: участок Северный", "Перенос"}


def test_failed_replace_keeps_the_old_project(rich_project, db_engine, tmp_path):
    out = tmp_path / "p.bfggis"
    export_project(lambda: Session(db_engine), rich_project, out)
    cut = tmp_path / "cut.bfggis"
    cut.write_bytes(out.read_bytes()[: out.stat().st_size // 2])
    with Session(db_engine) as s:
        with pytest.raises(Corrupted):
            import_project(s, cut, mode="replace", name="Демо: участок Северный")
        s.rollback()
        assert s.get(Project, rich_project) is not None
        assert s.scalar(select(text("count(*)")).select_from(Project)) == 1


def test_newer_schema_is_refused(rich_project, db_engine, tmp_path, monkeypatch):
    out = tmp_path / "p.bfggis"
    with Session(db_engine) as s:
        s.execute(text("UPDATE alembic_version SET version_num = '9999'"))
        s.commit()
    try:
        export_project(lambda: Session(db_engine), rich_project, out)
    finally:
        with Session(db_engine) as s:
            s.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": versions.head()})
            s.commit()
    with Session(db_engine) as s, pytest.raises(TooNew):
        import_project(s, out)


def test_older_schema_runs_the_upgrades(rich_project, db_engine, tmp_path, monkeypatch):
    """An archive of an older revision: the upgrade statements of later revisions run on staging."""
    out = tmp_path / "p.bfggis"
    export_project(lambda: Session(db_engine), rich_project, out)
    real = versions.revisions()
    monkeypatch.setattr(versions, "revisions", lambda: (*real, "9001"))
    monkeypatch.setitem(versions.UPGRADES, "9001", ["UPDATE stg_project SET description = 'обновлено'"])
    with Session(db_engine) as s:
        res = import_project(s, out)
        s.commit()
        assert s.get(Project, res["project_id"]).description == "обновлено"


def test_api_flow(rich_project, db_client, run_jobs):
    pid = rich_project
    summary = db_client.get(f"/api/projects/{pid}/archive/summary").json()
    assert summary["tables"]["collar"] == 48 and summary["files"] > 0
    job = db_client.post(f"/api/projects/{pid}/archive", json={"filename": "Перенос", "password": "s3"}).json()
    assert "password" not in str(db_client.get(f"/api/jobs/{job['id']}").json()["params"])
    run_jobs()
    done = db_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    assert done["result"]["filename"] == "Перенос.bfggis" and done["result"]["encrypted"]
    r = db_client.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200 and r.content.startswith(container.MAGIC)
    part = db_client.get(f"/api/jobs/{job['id']}/download", headers={"Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == container.MAGIC
    data = r.content

    up = db_client.post("/api/uploads", json={"filename": "Перенос.bfggis", "size": len(data)}).json()
    assert up["project_id"] is None
    assert db_client.put(f"/api/uploads/{up['id']}?offset=0", content=data).json()["status"] == "complete"
    locked = db_client.post("/api/archives/inspect", json={"upload_id": up["id"]}).json()
    assert locked["encrypted"] and not locked["password_ok"]
    wrong = db_client.post("/api/archives/inspect", json={"upload_id": up["id"], "password": "x"}).json()
    assert not wrong["password_ok"] and wrong["error"] == "Неверный пароль"
    info = db_client.post("/api/archives/inspect", json={"upload_id": up["id"], "password": "s3"}).json()
    assert info["password_ok"] and info["name_taken"] and info["free_name"] == "Демо: участок Северный (2)"
    assert info["tables"]["collar"] == 48
    job = db_client.post("/api/archives/import", json={"upload_id": up["id"], "password": "s3", "mode": "copy"}).json()
    assert "password" not in str(db_client.get(f"/api/jobs/{job['id']}").json()["params"])
    run_jobs()
    done = db_client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done", done
    new = done["result"]["project_id"]
    assert done["project_id"] == new
    assert db_client.get(f"/api/projects/{new}/stats").json()["holes"] == 48
    assert db_client.get(f"/api/uploads/{up['id']}").status_code == 404
    assert db_client.post("/api/uploads", json={"filename": "x.zip", "size": 1}).status_code == 422


def test_large_tables_stream(db_client, db_engine, tmp_path):
    """200 000 interval rows go through COPY and the staging tables without trouble."""
    pid = db_client.post("/api/projects", json={"name": "Большой"}).json()["id"]
    sid = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()["id"]
    with Session(db_engine) as s:
        t = IntervalTable(set_id=sid, name="Пробы", columns=[{"name": "AU", "kind": "number"}])
        s.add(t)
        s.flush()
        s.execute(text("INSERT INTO interval_row (table_id, hole_id, from_m, to_m, \"values\") "
                       "SELECT :t, 'H' || (g / 1000), g, g + 1, jsonb_build_object('AU', g * 0.001) "
                       "FROM generate_series(1, 200000) g"), {"t": t.id})
        s.commit()
    out = tmp_path / "big.bfggis"
    export_project(lambda: Session(db_engine), pid, out)
    with Session(db_engine) as s:
        res = import_project(s, out)
        s.commit()
        assert res["counts"]["interval_row"] == 200000
        total = s.execute(text("SELECT sum((\"values\"->>'AU')::float) FROM interval_row r JOIN interval_table t "
                               "ON t.id = r.table_id JOIN dataset d ON d.id = t.set_id WHERE d.project_id = :p"),
                          {"p": res["project_id"]}).scalar()
        assert total == pytest.approx(sum(g * 0.001 for g in range(1, 200001)))
