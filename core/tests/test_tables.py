"""The «Таблица» panel: pages, sorting, filters, Σ, edits with a journal and undo (ТЗ 7.10; 7.34 п. 21, 360)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bf_core.drillholes.expr import ExprError, evaluate_filter, evaluate_value
from tests.test_import_job import API, _check, wait_job

pytestmark = pytest.mark.windows


@pytest.fixture()
def dset(pclient, tmp_path: Path):  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Таблицы"})
    collar = tmp_path / "collar.csv"
    collar.write_text(
        "HoleID,X,Y,Z,Depth,STATUS\nD1,10,20,300,50,бур\nD2,,21,301,40,\nD3,5,22,302,30,план\n", encoding="utf-8"
    )
    assay = tmp_path / "assay.csv"
    assay.write_text(
        "Hole_ID,From,To,Au_ppm,Lith,SampleID\nD1,0,1,1.5,гранит,S1\nD1,1,2,,диорит,S2\nD2,0,2,0.2,Гранит,S3\nD3,0,1,3,,S4\n",
        encoding="utf-8",
    )
    files = []
    for path, extra in ((collar, {}), (assay, {"attributes": ["Lith"]})):
        chk = _check(pclient, path, **extra)
        files.append(
            {
                "path": str(path),
                "kind": chk["kind"],
                "mapping": chk["mapping"],
                "components": chk["components"],
                **extra,
            }
        )
    files[1]["components"] = [{"column": "Au_ppm", "unit": "ppm"}]
    r = pclient.post(f"{API}/drillholes/import", json={"set_name": "Н", "files": files}).json()
    job = wait_job(pclient, r["source"], r["id"])
    assert job["status"] == "done", job["error"]
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    tabs = pclient.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    return pclient, set_id, {t["kind"]: t for t in tabs}


def _page(c, set_id: int, table: str, **q):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{table}", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _key(page: dict, name: str) -> str:
    return next(c["key"] for c in page["columns"] if c["name"] == name)


def test_tabs_pages_sorting_with_missing_last(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assert [t["kind"] for t in tabs.values()] == ["collar", "survey", "assay"]
    p = _page(c, set_id, "collar", sort="x")
    assert [r["hole"] for r in p["rows"]] == ["D3", "D1", "D2"]  # D2 has no X: last
    p = _page(c, set_id, "collar", sort="x", desc=True)
    assert [r["hole"] for r in p["rows"]] == ["D1", "D3", "D2"]  # still last when descending
    assert p["total"] == 3 and _key(p, "status").startswith("a:")
    assay = tabs["assay"]["table"]
    p = _page(c, set_id, assay, sort=None, limit=2)
    assert p["total"] == 4 and len(p["rows"]) == 2
    lith = _key(p, "Lith")
    p = _page(c, set_id, assay, sort=lith)
    assert [r[lith] for r in p["rows"]] == ["гранит", "Гранит", "диорит", None]  # case-insensitive, stable
    hole = p["rows"][0]["hole_id"]
    p = _page(c, set_id, assay, hole=hole)
    assert {r["hole"] for r in p["rows"]} == {"D1"} and p["total"] == 2


def test_expression_filter_and_stats(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    p = _page(c, set_id, assay, where='Au_ppm > 0,5 и [Lith] = "ГРАНИТ"')
    assert [r["hole"] for r in p["rows"]] == ["D1"]
    p = _page(c, set_id, assay, where="isnull(Au_ppm) or (To - From) > 1")
    assert sorted(r["hole"] for r in p["rows"]) == ["D1", "D2"]
    r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{assay}", params={"where": "Au_ppm >"})
    assert r.status_code == 422 and r.json()["code"] == "bad_expression"
    r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{assay}", params={"where": "NOPE > 1"})
    assert r.status_code == 422 and "NOPE" in r.json()["message"]
    au = _key(p, "Au_ppm")
    s = c.get(f"{API}/drillholes/sets/{set_id}/tables/{assay}/stats", params={"column": au}).json()
    assert s["filled"] == 3 and s["empty"] == 1 and s["min"] == 0.2 and s["max"] == 3.0 and s["median"] == 1.5
    lith = _key(p, "Lith")
    s = c.get(f"{API}/drillholes/sets/{set_id}/tables/{assay}/stats", params={"column": lith}).json()
    assert s["unique"] == 3 and s["empty"] == 1


def test_edit_cell_decimal_comma_undo_and_conflict(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    assay = tabs["assay"]["table"]
    p = _page(c, set_id, assay, sort="from")
    au = _key(p, "Au_ppm")
    row = p["rows"][0]
    url = f"{API}/drillholes/sets/{set_id}/tables/{assay}/rows/{row['id']}"
    r = c.patch(url, json={"column": au, "value": "12,5", "rev": p["rev"]})
    assert r.status_code == 200 and r.json()["value"] == 12.5
    rev = r.json()["rev"]
    assert rev == p["rev"] + 1
    r = c.patch(url, json={"column": au, "value": "1", "rev": p["rev"]})  # a stale revision
    assert r.status_code == 409 and r.json()["code"] == "version_conflict"
    r = c.patch(url, json={"column": "to", "value": "-1", "rev": rev})
    assert r.status_code == 422 and r.json()["code"] == "to_before_from"
    r = c.patch(url, json={"column": au, "value": "abc", "rev": rev})
    assert r.status_code == 422 and r.json()["code"] == "not_a_number"
    assert _page(c, set_id, assay, sort="from")["rows"][0][au] == 12.5  # the snapshot was patched
    edits = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"]
    assert edits[0]["changes"] == {au: [1.5, 12.5]} and edits[0]["author"]
    r = c.post(f"{API}/drillholes/edits/{edits[0]['id']}/undo")
    assert r.status_code == 200, r.text
    assert _page(c, set_id, assay, sort="from")["rows"][0][au] == 1.5
    assert c.post(f"{API}/drillholes/edits/{edits[0]['id']}/undo").json()["code"] == "already_undone"


def test_rows_add_delete_undo_and_checks(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    base = f"{API}/drillholes/sets/{set_id}/tables"
    r = c.post(f"{base}/collar/rows", json={"values": {"hole": "D1", "x": 1, "y": 2, "z": 3, "td": 4}})
    assert r.status_code == 409 and r.json()["code"] == "collar_exists"
    r = c.post(f"{base}/collar/rows", json={"values": {"hole": "D9", "x": "1,5", "y": 2, "z": 3}})
    assert r.status_code == 422 and r.json()["code"] == "required_field"  # TD is required
    r = c.post(f"{base}/collar/rows", json={"values": {"hole": "D9", "x": "1,5", "y": 2, "z": 3, "td": 10}})
    assert r.status_code == 200, r.text
    p = _page(c, set_id, "collar")
    assert p["total"] == 4 and any(row["hole"] == "D9" and row["x"] == 1.5 for row in p["rows"])
    d1 = next(row for row in p["rows"] if row["hole"] == "D2")
    r = c.patch(f"{base}/collar/rows/{d1['id']}", json={"column": "hole", "value": "D1"})
    assert r.status_code == 409 and r.json()["code"] == "hole_exists"

    assay = tabs["assay"]["table"]
    r = c.post(f"{base}/{assay}/rows", json={"values": {"hole": "D1", "from": 5, "to": 4}})
    assert r.status_code == 422 and r.json()["code"] == "to_before_from"
    p = _page(c, set_id, assay, sort="from")
    au, lith = _key(p, "Au_ppm"), _key(p, "Lith")
    victim = next(row for row in p["rows"] if row[lith] == "диорит")
    assert c.delete(f"{base}/{assay}/rows/{victim['id']}").status_code == 200
    assert _page(c, set_id, assay)["total"] == 3
    tabs_now = {t["table"]: t for t in c.get(f"{base}").json()["tables"]}
    assert tabs_now[assay]["rows"] == 3
    edit = c.get(f"{API}/drillholes/sets/{set_id}/edits").json()["edits"][0]
    assert edit["action"] == "delete" and edit["hole"] == "D1"
    assert c.post(f"{API}/drillholes/edits/{edit['id']}/undo").status_code == 200
    p = _page(c, set_id, assay)
    back = [row for row in p["rows"] if row[lith] == "диорит"]
    assert p["total"] == 4 and len(back) == 1 and back[0]["id"] != victim["id"]  # a new row
    assert back[0]["from"] == 1.0 and back[0]["to"] == 2.0 and back[0][au] is None


def test_expressions_unit() -> None:
    cols = {
        "AU": ("number", np.array([1.0, np.nan, 3.0])),
        "Литология": ("text", np.array(["гранит", None, "Диорит"], dtype=object)),
    }

    def res(name: str):  # type: ignore[no-untyped-def]
        for k, v in cols.items():
            if k.casefold() == name.casefold():
                return v
        raise KeyError(name)

    assert evaluate_filter("au >= 1", res, 3).tolist() == [True, False, True]
    assert evaluate_filter("не isnull(AU)", res, 3).tolist() == [True, False, True]
    assert evaluate_filter('[литология] <> "гранит"', res, 3).tolist() == [False, False, True]
    assert evaluate_filter("between(AU; 0,5; 2)", res, 3).tolist() == [True, False, False]
    kind, v = evaluate_value("AU * 2 + 1", res, 3)
    assert kind == "number" and v[0] == 3.0 and np.isnan(v[1])
    kind, v = evaluate_value('upper(Литология) + "!"', res, 3)
    assert kind == "text" and v.tolist() == ["ГРАНИТ!", None, "ДИОРИТ!"]
    for bad in ("__import__('os')", "AU.real", "(lambda: 1)()", "AU[0]", '"a" < "b"', "AU +"):
        with pytest.raises(ExprError):
            evaluate_filter(bad, res, 3)


def test_sets_create_rename_delete(dset) -> None:  # type: ignore[no-untyped-def]
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import blob_owners, dh_holes, import_log
    from bf_core.projects.service import projects_of

    c, set_id, _tabs = dset
    r = c.post(f"{API}/drillholes/sets", json={"name": "  Пустой  набор "})
    assert r.status_code == 200 and r.json()["name"] == "Пустой набор"
    empty = r.json()["id"]
    assert c.post(f"{API}/drillholes/sets", json={"name": "пустой набор"}).status_code == 409
    tabs = c.get(f"{API}/drillholes/sets/{empty}/tables").json()
    assert [t["rows"] for t in tabs["tables"]] == [0, 0] and tabs["set"]["holes"] == 0
    r = c.post(
        f"{API}/drillholes/sets/{empty}/tables/collar/rows",
        json={"values": {"hole": "N1", "x": 1, "y": 2, "z": 3, "td": 5}},
    )
    assert r.status_code == 200, r.text
    assert c.patch(f"{API}/drillholes/sets/{empty}", json={"name": "Н"}).json()["code"] == "name_taken"
    assert c.patch(f"{API}/drillholes/sets/{empty}", json={"name": "Новый"}).status_code == 200
    p = projects_of(c.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(blob_owners).where(blob_owners.c.owner_kind == "import")
            ).scalar_one()
            == 2
        )
    assert c.delete(f"{API}/drillholes/sets/{set_id}").status_code == 200
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(import_log)).scalar_one() == 0
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(blob_owners).where(blob_owners.c.owner_kind == "import")
            ).scalar_one()
            == 0
        )
        assert conn.execute(sa.select(dh_holes.c.name)).scalars().all() == ["N1"]
    assert [s["name"] for s in c.get(f"{API}/drillholes/sets").json()["sets"]] == ["Новый"]
    assert c.get(f"{API}/drillholes/sets/{set_id}/tables").status_code == 404


def test_trajectories_coordinates_and_hole_card(dset) -> None:  # type: ignore[no-untyped-def]
    c, set_id, tabs = dset
    r = c.get(f"{API}/drillholes/sets/{set_id}/trajectories").json()
    assert r["method"] == "minimum_curvature" and r["skipped"] == ["D2"]  # D2 has no X
    d1 = next(h for h in r["holes"] if h["name"] == "D1")
    assert d1["stations"][0] == [0.0, 10.0, 20.0, 300.0] and d1["stations"][-1][3] == pytest.approx(
        250.0
    )  # vertical, TD 50
    assay = tabs["assay"]["table"]
    rows = _page(c, set_id, assay, sort="from", coords="true")["rows"]
    first = next(row for row in rows if row["hole"] == "D1" and row["from"] == 1.0)
    assert first["xyz_from"] == pytest.approx([10.0, 20.0, 299.0]) and first["xyz_to"] == pytest.approx(
        [10.0, 20.0, 298.0]
    )
    assert next(row for row in rows if row["hole"] == "D2")["xyz_from"] is None
    card = c.get(f"{API}/drillholes/sets/{set_id}/holes/{first['hole_id']}").json()
    assert card["name"] == "D1" and card["collar"]["z"] == 300.0 and card["trajectory"]["length"] == 50.0
    assert list(card["tables"].values())[0]["rows"] == 2
    # an edit of Z moves the trajectory (the set version is part of the cache key)
    collar = next(row for row in _page(c, set_id, "collar")["rows"] if row["hole"] == "D1")
    c.patch(f"{API}/drillholes/sets/{set_id}/tables/collar/rows/{collar['id']}", json={"column": "z", "value": "400"})
    d1 = next(h for h in c.get(f"{API}/drillholes/sets/{set_id}/trajectories").json()["holes"] if h["name"] == "D1")
    assert d1["stations"][0][3] == 400.0
    assert c.get(f"{API}/drillholes/sets/{set_id}/trajectories", params={"method": "x"}).status_code == 422


def test_hole_types() -> None:
    from bf_core.drillholes.router import hole_type

    assert hole_type({"status": "Проектная"})["key"] == "design"
    assert hole_type({"TYPE": "канава"})["key"] == "trench"
    assert hole_type({"Тип": "Траншея"})["key"] == "trench"
    assert hole_type(None)["key"] == "hole" and hole_type({"status": "бур"})["key"] == "hole"
