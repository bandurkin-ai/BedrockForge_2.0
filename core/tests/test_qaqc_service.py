"""QAQC storage (ADR 0020 §2–3; подшаг 14.9): data objects — create, read, rename, replace, delete, the limit of 50;
the settings and the CRM reference in `settings(scope="qaqc")` with versions (409); limits outside the ranges of
ТЗ (even group of Т-Х, σ 6, run 4) → 422 `bad_param {param}`; a CRM row with σ ≤ 0 → `bad_crm {row}`; the 5001st
row of CRM → 422; the `.bfggis` cycle keeps the data, the settings and the reference (10.5 п. 5). The 423 of a
read-only project — `test_qaqc_import_api.py` (it needs the router)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, objects
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.qaqc import config as cf
from bf_core.qaqc import normalize as nz
from bf_core.qaqc import records as r
from bf_core.qaqc import service as sv
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_archive import export, wait_job
from tests.test_import_job import API
from tests.test_qaqc_records import MAP, small, table


def made() -> nz.Normalized:
    return nz.normalize(table(**small()), MAP)


def put(conn: sa.Connection, store: BlobStore, name: str = "Контроль 2024") -> int:
    m = made()
    meta = sv.make_meta(m.records, m.counts, m.notes, {"kind": "file", "name": "q.csv", "sha256": "0" * 64}, MAP)
    return sv.create(conn, store, sv.stage(store, m.records), name=name, meta=meta)


@pytest.mark.db
def test_data_objects_on_the_database(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path)
    with dialect.write_tx(engine) as conn:
        a = put(conn, store)
        info = sv.info(conn, a)
        assert (info["name"], info["version"], info["rows"]) == ("Контроль 2024", 1, 6)
        assert info["elements"] == [{"name": "AU", "unit": "ppm"}] and info["labs"] == ["L1"]
        assert info["batches"] == 2 and info["date_from"] == "2024-01-02" and info["date_to"] == "2024-01-03"
        codes = {t["code"]: (t["type"], t["n"]) for t in info["type_codes"]}
        assert codes == {"BLANK": ("BLANK", 1), "CRM": ("CRM", 1), "DUP_F": ("DUP_F", 1), "DUP_P": ("DUP_P", 1),
                         "ORIG": ("ORIG", 2)}  # fmt: skip
        assert info["counts"]["no_parent"] == 1 and info["stale"] is False and len(info["sha256"]) == 64
        with pytest.raises(ApiError) as e:
            put(conn, store)
        assert (e.value.status, e.value.code) == (409, "name_taken")
        b = put(conn, store, "Другие")
        assert [d["id"] for d in sv.list_data(conn)] == [b, a]  # by name
        renamed = sv.rename(conn, a, "  Контроль   новый ", 1)
        assert (renamed["name"], renamed["version"]) == ("Контроль новый", 2)
        with pytest.raises(ApiError) as e:
            sv.rename(conn, a, "Ещё", 1)
        assert (e.value.status, e.value.code) == (409, "version_conflict") and e.value.extra["current"]["version"] == 2
        with pytest.raises(ApiError) as e:
            sv.rename(conn, a, "Другие", 2)
        assert e.value.code == "name_taken"
        old_sha = sv.sha_of(conn, a)
        m = nz.normalize(table(**small(AU_ppm=["2"] * 6)), MAP)
        meta = sv.make_meta(m.records, m.counts, m.notes, {"kind": "file", "name": "q2.csv", "sha256": "1" * 64}, MAP)
        uid = conn.execute(sa.select(objects.c.meta).where(objects.c.id == a)).scalar_one()["uid"]
        assert sv.replace(conn, store, a, sv.stage(store, m.records), meta) == 3
        assert sv.sha_of(conn, a) != old_sha
        assert conn.execute(sa.select(objects.c.meta).where(objects.c.id == a)).scalar_one()["uid"] == uid
        owners = conn.execute(sa.select(sa.func.count()).select_from(blob_owners)
                              .where(blob_owners.c.owner_id == a)).scalar_one()  # fmt: skip
        assert owners == 1
        gone = sv.delete(conn, b)
        assert gone["id"] == b and len(gone["shas"]) == 1
        with pytest.raises(ApiError) as e:
            sv.info(conn, b)
        assert (e.value.status, e.value.code) == (404, "qaqc_data_not_found")


@pytest.mark.db
def test_fifty_data_at_most(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path)
    now = dialect.utcnow()
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.insert(objects), [
            dict(kind=sv.KIND, name=f"Д{i}", name_key=dialect.name_key(f"Д{i}"), sort_key=0.0, state="ready",
                 version=1, meta={"v": 1}, created_at=now, updated_at=now) for i in range(sv.MAX_DATA)
        ])  # fmt: skip
        with pytest.raises(ApiError) as e:
            put(conn, store, "Пятьдесят первый")
        assert (e.value.status, e.value.code) == (422, "too_many_data") and e.value.hint


@pytest.mark.db
def test_config_and_crm_versions(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        c0 = cf.get_config(conn)
        assert c0["version"] == 0 and c0["types"]["STD"] == "CRM" and c0["types"]["BLK"] == "BLANK"
        assert c0["limits"]["sigma_warn"] == 2 and c0["limits"]["th_group"] == 11 and c0["empty_is_orig"] is True
        body = {"types": {" std ": "CRM", "Дубль": "DUP_F"}, "dl": [{"element": "AU", "lab": None, "value": 0.01,
                "unit": "ppm"}], "limits": {"run": 7}, "empty_is_orig": False}  # fmt: skip
        c1 = cf.put_config(conn, body, 0)
        assert c1["version"] == 1 and c1["types"] == {"STD": "CRM", "ДУБЛЬ": "DUP_F"}
        assert c1["limits"]["run"] == 7 and c1["limits"]["sigma_fail"] == 3 and c1["dl"][0]["value"] == 0.01
        assert cf.type_of(c1, " дубль") == "DUP_F" and cf.type_of(c1, "ORIG") is None and cf.type_of(c1, " ") is None
        with pytest.raises(ApiError) as e:
            cf.put_config(conn, body, 0)
        assert (e.value.status, e.value.code) == (409, "version_conflict") and e.value.extra["current"]["version"] == 1
        assert cf.put_config(conn, {}, 1)["version"] == 2
        k0 = cf.get_crm(conn)
        assert k0 == {"version": 0, "rows": []}
        rows = [{"crm_id": "OREAS 45", "element": "AU", "mean": 1.0, "sd": 0.05, "unit": "ppm", "source": "паспорт"}]
        k1 = cf.put_crm(conn, rows, 0)
        assert k1["version"] == 1 and k1["rows"] == rows
        with pytest.raises(ApiError) as e:
            cf.put_crm(conn, rows, 0)
        assert e.value.code == "version_conflict"


@pytest.mark.parametrize(
    ("limits", "param"),
    [({"th_group": 12}, "limits.th_group"), ({"sigma_fail": 6}, "limits.sigma_fail"), ({"run": 4}, "limits.run"),
     ({"run": 9.5}, "limits.run"), ({"k_blank": 0.5}, "limits.k_blank"), ({"k_dup": 51}, "limits.k_dup"),
     ({"sigma_warn": 3, "sigma_fail": 3}, "limits.sigma_warn"), ({"hard_share": 49}, "limits.hard_share"),
     ({"n_boot": 99}, "limits.n_boot"), ({"sigma_warn": "2"}, "limits.sigma_warn"), ({"nope": 1}, "limits.nope")],
)  # fmt: skip
def test_limits_outside_the_ranges(limits: dict[str, Any], param: str) -> None:
    with pytest.raises(ApiError) as e:
        cf.check_config({"limits": limits})
    assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", param)
    assert e.value.message and e.value.hint


def test_limits_on_the_bounds() -> None:
    edge = {"sigma_warn": 1, "sigma_fail": 5, "run": 20, "k_blank": 50, "k_dup": 0, "th_group": 51, "n_boot": 5000}
    assert cf.check_limits(edge)["th_group"] == 51 and cf.check_limits({"run": 5.0})["run"] == 5


@pytest.mark.parametrize(
    ("row", "field"),
    [({"sd": 0}, "sd"), ({"sd": -0.1}, "sd"), ({"mean": 0}, "mean"), ({"mean": None}, "mean"),
     ({"crm_id": " "}, "crm_id"), ({"element": None}, "element")],
)  # fmt: skip
def test_bad_crm_rows(row: dict[str, Any], field: str) -> None:
    good = {"crm_id": "A", "element": "AU", "mean": 1.0, "sd": 0.1}
    with pytest.raises(ApiError) as e:
        cf.check_crm([good, {**good, "crm_id": "B", **row}])
    assert (e.value.status, e.value.code, e.value.extra["row"]) == (422, "bad_crm", 1)
    assert e.value.extra["param"] == f"rows[1].{field}" and "строка 2" in e.value.message


def test_crm_repeated_and_too_many() -> None:
    good = {"crm_id": "A", "element": "AU", "mean": 1.0, "sd": 0.1}
    with pytest.raises(ApiError) as e:
        cf.check_crm([good, {**good, "crm_id": " a", "element": "au"}])
    assert e.value.code == "bad_crm" and e.value.extra["row"] == 1
    many = [{**good, "crm_id": f"C{i}"} for i in range(cf.MAX_CRM + 1)]
    with pytest.raises(ApiError) as e:
        cf.check_crm(many)
    assert (e.value.status, e.value.code) == (422, "too_many_rows")
    assert len(cf.check_crm(many[: cf.MAX_CRM])) == cf.MAX_CRM


def test_dl_and_types_errors() -> None:
    for body, param in (
        ({"dl": [{"element": "AU", "value": 0}]}, "dl[0].value"),
        ({"dl": [{"element": "AU", "value": 1}, {"element": "au", "lab": None, "value": 2}]}, "dl[1]"),
        ({"types": {"STD": "STANDARD"}}, "types.STD"),
        ({"types": {"x": "CRM", "X": "BLANK"}}, "types.X"),
        ({"empty_is_orig": "да"}, "empty_is_orig"),
    ):
        with pytest.raises(ApiError) as e:
            cf.check_config(body)
        assert (e.value.code, e.value.extra["param"]) == ("bad_param", param)


def test_crm_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "справочник CRM.csv"
    path.write_text("Standard;Element;Mean;SD;Unit;Source\nOREAS 45;Au;1,05;0,05;ppm;паспорт\n"
                    "OREAS 46;Au;2;0;ppm;\n", encoding="utf-8")  # fmt: skip
    got = cf.read_crm_file(path)
    assert got["rows"][0] == {"crm_id": "OREAS 45", "element": "Au", "unit": "ppm", "source": "паспорт",
                              "mean": 1.05, "sd": 0.05}  # fmt: skip
    assert got["problems"] == [{"row": 1, "message": "Справочник CRM, строка 2: σ должно быть больше нуля."}]
    bad = tmp_path / "bad.csv"
    bad.write_text("A;B\n1;2\n", encoding="utf-8")
    with pytest.raises(ApiError) as e:
        cf.read_crm_file(bad)
    assert e.value.code == "bad_crm_file" and e.value.extra["columns"] == ["A", "B"]


def test_table_source_is_found_by_names_after_renumbering(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    now = dialect.utcnow()
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        res = conn.execute(sa.insert(dh.dh_tables).values(set_id=set_id, name="Пробы", name_key=dialect.name_key(
            "Пробы"), kind="assay", rev=4, state="ready", created_at=now, updated_at=now))  # fmt: skip
        table_id = int(res.inserted_primary_key[0])  # type: ignore[index]
        src = {"kind": "table", "set_id": set_id, "table_id": table_id, "set_name": "Скважины", "table_name": "Пробы",
               "rev": 4}  # fmt: skip
        assert sv.source_state(conn, {"source": src})["stale"] is False
        assert sv.source_state(conn, {"source": {**src, "rev": 3}})["stale"] is True
        moved = sv.source_state(conn, {"source": {**src, "set_id": 999, "table_id": 999}})
        assert moved["stale"] is False and moved["table_id"] == table_id
        gone = sv.source_state(conn, {"source": {**src, "table_id": 999, "table_name": "Нет"}})
        assert gone == {"stale": True, "source_missing": True, "rev": None}


def test_bfggis_cycle_keeps_data_config_and_crm(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c = pclient
    assert c.post(f"{API}/projects", json={"name": "QAQC"}).status_code == 200
    p = projects_of(c.core).require_current()
    store = BlobStore(p.folder)
    rows = [{"crm_id": "CRM-01", "element": "AU", "mean": 1.0, "sd": 0.05, "unit": "ppm", "source": None}]
    with dialect.write_tx(p.engine) as conn:
        data_id = put(conn, store)
        cfg = cf.put_config(conn, {"types": {"STD": "CRM"}, "limits": {"run": 11}}, 0)
        crm = cf.put_crm(conn, rows, 0)
        before = sv.info(conn, data_id)
    recs = sv.open_records(p, data_id)
    au = recs.number(r.v("AU")).copy()
    job = export(c, tmp_path / "Контроль")
    path = Path(job["result"]["path"])
    res = c.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(c, res["source"], res["id"])
    assert job["status"] == "done", job
    assert c.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    q = projects_of(c.core).require_current()
    new_id = int(job["result"]["id_maps"]["objects"][str(data_id)])
    with dialect.read_tx(q.engine) as conn:
        after = sv.info(conn, new_id)
        assert cf.get_config(conn) == cfg and cf.get_crm(conn) == crm
    keep = ("name", "version", "rows", "elements", "fields", "labs", "batches", "date_from", "date_to", "type_codes",
            "source", "mapping", "sha256", "counts", "notes")  # fmt: skip
    assert {k: after[k] for k in keep} == {k: before[k] for k in keep}
    back = sv.open_records(q, new_id)
    assert np.array_equal(back.number(r.v("AU")), au, equal_nan=True)
    assert back.text(r.TYPE_CODE).values() == recs.text(r.TYPE_CODE).values()


def test_crm_file_too_long_is_refused_before_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """14.25: an estimate far past 5000 rows is refused before the file is read."""
    import bf_core.formats.registry as reg

    path = tmp_path / "big.csv"
    path.write_text("CRM_ID,ELEMENT,MEAN,SD\n" + "A,AU,1,0.1\n" * (2 * cf.MAX_CRM + 500), encoding="utf-8")
    monkeypatch.setattr(reg, "read_table", lambda *a, **k: pytest.fail("the whole file was read"))
    with pytest.raises(ApiError) as e:
        cf.read_crm_file(path)
    assert e.value.code == "too_many_rows"
