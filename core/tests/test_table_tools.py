"""Tools of the «Таблица» panel (ТЗ 7.34 п. 22, 31–36): replace, computed fields, columns, append, merge by
key, code lists — each undone at once from the journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_import_job import API
from tests.test_tables import _key, _page, dset  # noqa: F401 - the fixture

pytestmark = pytest.mark.windows


def _undo_last(c, set_id: int) -> None:  # type: ignore[no-untyped-def]
    edit = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"][0]
    r = c.post(f"{API}/drillholes/edits/{edit['id']}/undo")
    assert r.status_code == 200, r.text


def test_find_and_replace_is_one_undo(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables/{assay}"
    lith = _key(_page(c, set_id, assay), "Lith")
    r = c.post(f"{base}/replace", json={"column": lith, "find": "гранит", "replace": "Гранит-порфир", "whole": True})
    assert r.status_code == 200 and r.json()["replaced"] == 2, r.text
    values = sorted(str(row[lith]) for row in _page(c, set_id, assay)["rows"])
    assert values == ["None", "Гранит-порфир", "Гранит-порфир", "диорит"]
    edits = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"]
    assert len({e["group"] for e in edits[:2]}) == 1
    _undo_last(c, set_id)
    assert sorted(str(row[lith]) for row in _page(c, set_id, assay)["rows"]) == ["None", "Гранит", "гранит", "диорит"]
    r = c.post(f"{base}/replace", json={"column": lith, "find": "кварц", "replace": "x"})
    assert r.status_code == 404 and r.json()["code"] == "nothing_found"


def test_computed_field_rename_delete_and_undo(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables/{assay}"
    r = c.post(f"{base}/columns", json={"name": "Метропроцент", "expression": "(To - From) * Au_ppm"})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    rows = {(row["hole"], row["from"]): row[key] for row in _page(c, set_id, assay)["rows"]}
    assert rows[("D1", 0.0)] == 1.5 and rows[("D2", 0.0)] == 0.4 and rows[("D1", 1.0)] is None
    r = c.post(f"{base}/columns", json={"name": "метропроцент", "kind": "number"})
    assert r.status_code == 409 and r.json()["code"] == "column_exists"
    assert c.patch(f"{base}/columns/{key}", json={"name": "МП"}).status_code == 200
    assert _key(_page(c, set_id, assay), "МП") == key
    _undo_last(c, set_id)
    assert _key(_page(c, set_id, assay), "Метропроцент") == key
    assert c.delete(f"{base}/columns/{key}").status_code == 200
    assert key not in {col["key"] for col in _page(c, set_id, assay)["columns"]}
    _undo_last(c, set_id)  # the column comes back with its values (the slot was only hidden)
    rows = {(row["hole"], row["from"]): row[key] for row in _page(c, set_id, assay)["rows"]}
    assert rows[("D1", 0.0)] == 1.5
    r = c.post(f"{base}/columns", json={"name": "Плохо", "expression": "Au_ppm +"})
    assert r.status_code == 422 and r.json()["code"] == "bad_expression"
    r = c.post(f"{API}/drillholes/sets/{set_id}/tables/collar/columns", json={"name": "Длина2", "expression": "td * 2"})
    assert r.status_code == 200, r.text
    rows = {row["hole"]: row["a:Длина2"] for row in _page(c, set_id, "collar")["rows"]}
    assert rows == {"D1": 100.0, "D2": 80.0, "D3": 60.0}


def test_append_merge_and_codes(dset, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables"
    au = _key(_page(c, set_id, assay), "Au_ppm")
    # a second interval table «Литология» built with a computed-free copy: append the assay table to it
    r = c.post(f"{base}/{assay}/columns", json={"name": "LITH2", "kind": "text", "expression": "upper(Lith)"})
    assert r.status_code == 200
    lith2 = r.json()["key"]
    # merge by key from collars is not allowed (no from/to); merge from itself into a fresh copy instead
    r = c.post(f"{base}/{assay}/append", json={"source": assay})
    assert r.status_code == 422 and r.json()["code"] == "not_allowed"

    # code list: Lith must be one of the codes
    r = c.put(f"{API}/drillholes/codes", json={"name": "Породы", "items": [{"code": "гранит", "title": "Гранит"}]})
    assert r.status_code == 200 and r.json()["lists"][0]["items"] == [{"code": "гранит", "title": "Гранит"}]
    lith = _key(_page(c, set_id, assay), "Lith")
    r = c.get(f"{base}/{assay}/codes/check", params={"column": lith})
    assert r.status_code == 409 and r.json()["code"] == "codes_not_linked"
    assert c.put(f"{base}/{assay}/codes", json={"column": lith, "codes": "Породы"}).status_code == 200
    chk = c.get(f"{base}/{assay}/codes/check", params={"column": lith}).json()
    assert chk["bad"] == [{"value": "диорит", "count": 1}] and chk["rows"] == 1
    shown = _page(c, set_id, assay, where=chk["where"])
    assert [row[lith] for row in shown["rows"]] == ["диорит"]
    assert {row[lith2] for row in _page(c, set_id, assay)["rows"]} == {"ГРАНИТ", "ДИОРИТ", None}
    assert au


def test_append_and_merge_by_key_with_undo(dset, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.test_import_job import _check, wait_job

    c, set_id, tabs = dset
    lith = tmp_path / "lith.csv"
    lith.write_text("Hole_ID,From,To,ROCK,RQD\nD1,0,1,гранит,80\nD1,1,2,диорит,55\nD3,0,1,сланец,\n", encoding="utf-8")
    chk = _check(c, lith, kind="interval", attributes=["ROCK", "RQD"])
    spec = {
        "path": str(lith),
        "kind": "interval",
        "mapping": chk["mapping"],
        "attributes": ["ROCK", "RQD"],
        "table_name": "Литология",
    }
    r = c.post(f"{API}/drillholes/import", json={"set_id": set_id, "files": [spec]}).json()
    assert wait_job(c, r["source"], r["id"])["status"] == "done"
    tables = {t["name"]: t for t in c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]}
    assay, litho = tables["Пробы (Assay)"]["table"], tables["Литология"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables"

    rock = _key(_page(c, set_id, litho), "ROCK")
    r = c.post(f"{base}/{assay}/merge", json={"source": litho, "key": ["hole", "from", "to"], "columns": [rock]})
    assert r.status_code == 200 and r.json()["matched"] == 3, r.text
    new = r.json()["columns"][0]["key"]
    rows = {(row["hole"], row["from"]): row[new] for row in _page(c, set_id, assay)["rows"]}
    assert rows == {("D1", 0.0): "гранит", ("D1", 1.0): "диорит", ("D2", 0.0): None, ("D3", 0.0): "сланец"}
    _undo_last(c, set_id)
    assert new not in {col["key"] for col in _page(c, set_id, assay)["columns"]}

    r = c.post(f"{base}/{litho}/append", json={"source": assay})
    assert r.status_code == 200 and r.json()["rows"] == 4, r.text
    p = _page(c, set_id, litho)
    assert p["total"] == 7 and "Au_ppm" in {col["name"] for col in p["columns"]}
    assert {t["name"]: t["rows"] for t in c.get(f"{base}").json()["tables"]}["Литология"] == 7
    _undo_last(c, set_id)
    assert _page(c, set_id, litho)["total"] == 3
    assert {t["name"]: t["rows"] for t in c.get(f"{base}").json()["tables"]}["Литология"] == 3


def test_export_csv_xlsx_and_dat_round_trip(dset, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import openpyxl

    from bf_core.formats.micromine import write_dat
    from bf_core.formats.micromine_header import Field, read_header
    from bf_core.formats.registry import read_table
    from tests.test_import_job import _check, wait_job

    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    base = f"{API}/drillholes/sets/{set_id}/tables/{assay}"

    def export(path, fmt, **extra):  # type: ignore[no-untyped-def]
        r = c.post(
            f"{base.rsplit('/', 1)[0]}/{extra.pop('table', assay)}/export",
            json={"path": str(path), "format": fmt, **extra},
        )
        assert r.status_code == 200, r.text
        job = wait_job(c, r.json()["source"], r.json()["id"])
        assert job["status"] == "done", job
        return job["result"]

    res = export(tmp_path / "пробы", "csv", where="Au_ppm > 0,5")
    text = Path(res["path"]).read_text(encoding="utf-8-sig").splitlines()
    assert res["rows"] == 2 and text[0].split(";")[:4] == ["Hole_ID", "From", "To", "Au_ppm"]  # names as imported
    assert text[1].split(";")[:4] == ["D1", "0", "1", "1,5"]
    res = export(tmp_path / "пробы.xlsx", "xlsx", sort="from", desc=True)
    ws = openpyxl.load_workbook(res["path"]).active
    assert ws.max_row == 5 and ws.cell(1, 1).value == "Hole_ID"
    assert not list(tmp_path.glob("*.part"))

    # DAT imported → exported: the same fields (types, widths, decimals) and values
    src = tmp_path / "assay.dat"
    csv_in = tmp_path / "in.csv"
    csv_in.write_text("HOLE,FROM,TO,AU,SAMPLE\nK1,0,1.25,0.5,A1\nK1,1.25,2,7,A2\n", encoding="utf-8")
    fields = [
        Field("HOLE", "C", 6, 0),
        Field("FROM", "N", 9, 2),
        Field("TO", "N", 9, 2),
        Field("AU", "R", 8, 3),
        Field("SAMPLE", "C", 4, 0),
    ]
    write_dat(src, read_table(csv_in).columns, fields=fields)
    chk = _check(c, src, kind="assay")
    spec = {
        "path": str(src),
        "kind": "assay",
        "mapping": chk["mapping"],
        "components": [{"column": "AU", "unit": "g/t"}],
        "table_name": "DAT",
    }
    r = c.post(f"{API}/drillholes/import", json={"set_name": "ДАТ", "files": [spec]}).json()
    assert wait_job(c, r["source"], r["id"])["status"] == "done"
    dat_set = next(s for s in c.get(f"{API}/drillholes/sets").json()["sets"] if s["name"] == "ДАТ")
    dat_table = next(
        t for t in c.get(f"{API}/drillholes/sets/{dat_set['id']}/tables").json()["tables"] if t["kind"] == "assay"
    )
    out = tmp_path / "back.dat"
    r = c.post(
        f"{API}/drillholes/sets/{dat_set['id']}/tables/{dat_table['table']}/export",
        json={"path": str(out), "format": "dat"},
    )
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    a, b = read_header(src), read_header(out)
    assert [(f.name, f.kind, f.width, f.decimals) for f in a.fields] == [
        (f.name, f.kind, f.width, f.decimals) for f in b.fields
    ]
    ta, tb = read_table(src), read_table(out)
    assert [col.to_pylist() for col in ta.columns] == [col.to_pylist() for col in tb.columns]
