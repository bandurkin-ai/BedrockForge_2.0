"""Saved charts of Э13 (ТЗ 7.28, ADR 0019 §2): the kinds and the check of their settings, CRUD, names that
follow the settings (Т7.28-2) and « (2)», swath plots of the project, the view key `swaths` on the fixtures
shared with the interface, the ids inside the settings renumbered by a `.bfggis` (10.5 п. 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.archive import importer
from bf_core.archive.chart_remap import remap_charts
from bf_core.archive.export import export_project
from bf_core.charts import kinds
from bf_core.charts import service as charts
from bf_core.db import dialect, migrate
from bf_core.drillholes import store as dh
from bf_core.projects.project import DB_FILE
from bf_core.projects.service import projects_of
from bf_core.viewstate.keys import BadValue, drop_missing, ids_in, map_ids, normalize
from tests.dbutil import make_engine
from tests.test_import_job import API

SWATHS = json.loads((Path(__file__).parent / "data" / "view" / "swaths.json").read_text(encoding="utf-8"))
NEW_KINDS = [k for k in kinds.SET_KINDS if k != kinds.STRIPLOG]


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


def post(c: Any, **body: Any) -> Any:
    return c.post(f"{API}/charts", json=body)


# --- the check of the settings ---------------------------------------------------------------------------


def test_check_version_size_lists_and_ids() -> None:
    kinds.check("histogram", {"v": 1, "field": "AU"})
    for bad, param in (
        ({"field": "AU"}, "v"),
        ({"v": True}, "v"),
        ({"v": 2}, "v"),
        ({"v": 1, "pad": "x" * (64 * 1024)}, "settings"),
        ({"v": 1, "y_set_id": 0}, "y_set_id"),
        ({"v": 1, "y_set_id": "3"}, "y_set_id"),
        ({"v": 1, "wireframe_id": True}, "wireframe_id"),
        ({"v": 1, "table": ""}, "table"),
        ({"v": 1, "fields": ["AU"] * 101}, "fields"),
        ({"v": 1, "fields": ["AU", 3]}, "fields"),
        ({"v": 1, "holes": ["DH1"] * 10_001}, "holes"),
    ):
        with pytest.raises(kinds.KindError) as e:
            kinds.check("qq", bad)
        assert e.value.param == param and e.value.code == "bad_chart", bad
    kinds.check("qq", {"v": 1, "y_set_id": None, "fields": ["AU"] * 100, "holes": ["D1"] * 10_000})
    with pytest.raises(kinds.KindError, match="Неизвестный вид"):
        kinds.check("ward", {"v": 1})
    with pytest.raises(kinds.KindError) as e:
        kinds.check("swath", {"v": 1, "series": [{"kind": "samples", "set_id": 1}] * 13})
    assert e.value.code == "too_many_series" and e.value.param == "series"
    with pytest.raises(kinds.KindError) as e:
        kinds.check("swath", {"v": 1, "series": [{"model_id": -1}]})
    assert e.value.param == "series[0].model_id"
    kinds.check("swath", {"v": 1, "series": [{"model_id": None, "set_id": 4}] * 12})
    # the display along holes of Э6 as it was: no version, 256 КБ
    kinds.check("striplog", {"tracks": [{"field": "AU"}], "pad": "x" * (200 * 1024)})
    with pytest.raises(kinds.KindError):
        kinds.check("striplog", {"tracks": [{}] * 33})


def test_map_ids_renumbers_and_forgets() -> None:
    settings = {"v": 1, "y_set_id": 3, "series": [{"set_id": 3, "field": "AU"}, {"model_id": 9}, {"model_id": None}]}
    out = kinds.map_ids(settings, {3: 30}.get)
    assert out == {
        "v": 1,
        "y_set_id": 30,
        "series": [{"set_id": 30, "field": "AU"}, {"model_id": None}, {"model_id": None}],
    }
    assert settings["y_set_id"] == 3  # the input is not changed
    same = {"v": 1, "field": "AU"}
    assert kinds.map_ids(same, {3: 30}.get) is same
    assert sorted(kinds.object_ids(settings)) == [3, 3, 9]


# --- CRUD ---------------------------------------------------------------------------------------------


def test_crud_of_every_kind_of_a_set(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    made = {}
    for kind in NEW_KINDS:
        r = post(c, set_id=set_id, kind=kind, name=kinds.TITLES[kind], settings={"v": 1, "field": "AU"}, auto_name=True)
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["kind"] == kind and got["set_id"] == set_id and got["auto_name"] is True and got["version"] == 1
        made[kind] = got
    for kind, chart in made.items():
        listed = c.get(f"{API}/charts", params={"set_id": set_id, "kind": kind}).json()["charts"]
        assert [x["id"] for x in listed] == [chart["id"]]
        r = c.put(f"{API}/charts/{chart['id']}", json={"settings": {"v": 1, "field": "AG"}, "expected_version": 1})
        assert r.status_code == 200 and r.json()["settings"]["field"] == "AG" and r.json()["version"] == 2
    r = c.put(f"{API}/charts/{made['qq']['id']}", json={"settings": {"v": 1}, "expected_version": 1})
    assert r.status_code == 409 and r.json()["code"] == "version_conflict" and r.json()["chart"]["version"] == 2
    r = post(c, set_id=set_id, kind="qq", name=" q-q ГРАФИК ", settings={"v": 1})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"
    r = post(c, set_id=set_id, kind="histogram", name="Без версии", settings={"field": "AU"})
    assert r.status_code == 422 and r.json()["code"] == "bad_chart" and r.json()["param"] == "v"
    r = post(c, set_id=set_id, kind="ward", name="Ward", settings={"v": 1})
    assert r.status_code == 422 and r.json()["param"] == "kind"
    assert post(c, set_id=None, kind="histogram", name="Без набора", settings={"v": 1}).status_code == 422
    assert post(c, set_id=10**9, kind="histogram", name="Чужой", settings={"v": 1}).status_code == 404
    for chart in made.values():
        assert c.delete(f"{API}/charts/{chart['id']}").status_code == 200
    assert c.get(f"{API}/charts", params={"set_id": set_id}).json()["charts"] == []


def test_auto_name_follows_the_settings_until_renamed(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.28-2: a hand-given name does not change with the settings; a taken auto name becomes « (2)»."""
    c, set_id = demo
    name = "Гистограмма · AU · по длине"
    one = post(c, set_id=set_id, kind="histogram", name=name, settings={"v": 1}, auto_name=True).json()
    two = post(c, set_id=set_id, kind="histogram", name=name, settings={"v": 1}, auto_name=True).json()
    assert one["name"] == name and two["name"] == f"{name} (2)"
    assert post(c, set_id=set_id, kind="histogram", name=name, settings={"v": 1}).status_code == 409
    r = c.put(
        f"{API}/charts/{two['id']}",
        json={"settings": {"v": 1, "field": "AG"}, "auto_name": "Гистограмма · AG · по длине", "expected_version": 1},
    ).json()
    assert r["name"] == "Гистограмма · AG · по длине" and r["auto_name"] is True
    r = c.put(f"{API}/charts/{two['id']}", json={"auto_name": name, "expected_version": 2}).json()
    assert r["name"] == f"{name} (2)"  # the first one still holds the name
    r = c.put(f"{API}/charts/{two['id']}", json={"name": "Моё золото", "expected_version": 3}).json()
    assert r["name"] == "Моё золото" and r["auto_name"] is False
    r = c.put(
        f"{API}/charts/{two['id']}",
        json={"settings": {"v": 1, "field": "CU"}, "auto_name": "Гистограмма · CU", "expected_version": 4},
    ).json()
    assert r["name"] == "Моё золото" and r["settings"]["field"] == "CU" and r["auto_name"] is False
    r = c.put(f"{API}/charts/{two['id']}", json={"name": name, "expected_version": 5})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"


def test_swath_of_the_project_and_deleting_a_set(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    series = [{"kind": "samples", "set_id": set_id, "table": "assay", "field": "AU"}]
    r = post(c, set_id=None, kind="swath", name="Swath plot · X · 25 м", settings={"v": 1, "series": series},
             auto_name=True)  # fmt: skip
    assert r.status_code == 200, r.text
    swath = r.json()
    assert swath["set_id"] is None and swath["kind"] == "swath"
    again = post(c, kind="swath", name="Swath plot · X · 25 м", settings={"v": 1}, auto_name=True).json()
    assert again["name"] == "Swath plot · X · 25 м (2)"
    r = post(c, set_id=set_id, kind="swath", name="В наборе", settings={"v": 1})
    assert r.status_code == 422 and r.json()["param"] == "set_id"
    r = post(c, kind="swath", name="Много рядов", settings={"v": 1, "series": series * 13})
    assert r.status_code == 422 and r.json()["code"] == "too_many_series" and r.json()["param"] == "series"
    r = c.put(f"{API}/charts/{swath['id']}", json={"settings": {"v": 1, "series": series * 13}, "expected_version": 1})
    assert r.status_code == 422 and r.json()["code"] == "too_many_series"
    shown = post(c, set_id=set_id, name="Золото", settings={"tracks": []}).json()  # a striplog of Э6
    hist = post(c, set_id=set_id, kind="histogram", name="Гистограмма", settings={"v": 1}).json()
    assert [x["id"] for x in c.get(f"{API}/charts", params={"kind": "striplog"}).json()["charts"]] == [shown["id"]]
    in_set = {x["id"] for x in c.get(f"{API}/charts", params={"set_id": set_id}).json()["charts"]}
    assert in_set == {shown["id"], hist["id"]}
    assert {x["id"] for x in c.get(f"{API}/charts", params={"kind": "swath"}).json()["charts"]} == {
        swath["id"],
        again["id"],
    }
    # deleting the set takes its charts; the swath stays, its series still names the set (the window marks it)
    assert c.delete(f"{API}/drillholes/sets/{set_id}").status_code == 200
    left = c.get(f"{API}/charts").json()["charts"]
    assert {x["id"] for x in left} == {swath["id"], again["id"]}
    assert c.get(f"{API}/charts/{swath['id']}").json()["settings"]["series"][0]["set_id"] == set_id
    assert c.get(f"{API}/charts/{hist['id']}").status_code == 404


# --- on both databases ----------------------------------------------------------------------------------


@pytest.mark.db
def test_service_on_the_database(db_url: str, tmp_path: Path) -> None:
    """Charts of the project (parent NULL), names per parent, the limit of 200 swath plots, the cascade."""
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        s1 = dh.create_set(conn, "Набор 1")
        s2 = dh.create_set(conn, "Набор 2")
        a = charts.create(conn, s1, "Q-Q", "qq", {"v": 1, "y_set_id": s2}, None, auto_name=True)
        b = charts.create(conn, s2, "Q-Q", "qq", {"v": 1}, None, auto_name=True)
        assert a["name"] == b["name"] == "Q-Q"  # names are per set
        for _ in range(kinds.MAX_SWATHS):
            charts.create(conn, None, "Swath plot", "swath", {"v": 1, "series": [{"set_id": s1}]}, None,
                          auto_name=True)  # fmt: skip
        with pytest.raises(charts.ChartError) as e:
            charts.create(conn, None, "Ещё", "swath", {"v": 1}, None)
        assert e.value.code == "too_many_charts"
        names = [x["name"] for x in charts.list_charts(conn, kind="swath")]
        assert len(names) == 200 and "Swath plot" in names and "Swath plot (200)" in names
        assert charts.list_charts(conn, s1) == [charts.get(conn, a["id"])]
        assert charts.list_charts(conn, kind="striplog") == []
    with dialect.write_tx(engine) as conn:
        from bf_core.db.schema import objects

        conn.execute(sa.delete(objects).where(objects.c.id == s1))
    with dialect.read_tx(engine) as conn:
        assert [x["id"] for x in charts.list_charts(conn, kind="qq")] == [b["id"]]
        assert len(charts.list_charts(conn, kind="swath")) == 200
    engine.dispose()


# --- the view key `swaths` -------------------------------------------------------------------------------


def test_swaths_key_on_the_shared_fixtures() -> None:
    for case in SWATHS["cases"]:
        assert normalize("swaths", case["in"], strict=False) == case["out"], case
    for bad in SWATHS["strict_bad"]:
        with pytest.raises(BadValue):
            normalize("swaths", bad, strict=True)
    n, first = SWATHS["long"]["count"], SWATHS["long"]["kept_from"]
    out = normalize("swaths", [{"swath": i} for i in range(1, n + 1)], strict=True)
    assert [d["swath"] for d in out] == list(range(first, n + 1))
    value = [{"swath": 5, "visible": True}, {"swath": 7, "visible": False}]
    assert list(ids_in("swaths", value)) == [5, 7]
    assert drop_missing("swaths", value, lambda i: i == 7) == [{"swath": 7, "visible": False}]
    assert map_ids("swaths", value, {5: 50}.get) == [{"swath": 50, "visible": True}]


def test_swaths_key_through_the_api_forgets_deleted(demo) -> None:  # type: ignore[no-untyped-def]
    c, _ = demo
    swath = post(c, kind="swath", name="Swath", settings={"v": 1}).json()
    r = c.put(f"{API}/view/swaths", json={"value": [{"swath": swath["id"]}, {"swath": 10**9}], "expected_version": 0})
    assert r.status_code == 200, r.text
    got = c.get(f"{API}/view").json()["values"]
    assert got["swaths"]["value"] == [{"swath": swath["id"], "visible": True}]


# --- .bfggis ----------------------------------------------------------------------------------------------


@pytest.mark.db
def test_remap_charts_writes_the_new_parent_as_the_own_set(db_url: str, tmp_path: Path) -> None:
    """13.36: the window takes `settings.set_id`; after an import it is the new number of the parent, not the
    old one of the archive (which may now be another set); a swath plot gets no `set_id` of its own."""
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        s1 = dh.create_set(conn, "Набор 1")
        s2 = dh.create_set(conn, "Набор 2")
        old = s2 + 100  # the number of the set in the archive
        a = charts.create(conn, s2, "Q-Q", "qq", {"v": 1, "set_id": s1, "y_set_id": old}, None)
        b = charts.create(conn, s2, "Гистограмма", "histogram", {"v": 1}, None)
        w = charts.create(conn, None, "Swath", "swath", {"v": 1, "series": [{"set_id": old}]}, None)
        assert remap_charts(conn, {old: s2, s1: s1}) == 3
        assert charts.get(conn, a["id"])["settings"] == {"v": 1, "set_id": s2, "y_set_id": s2}
        assert charts.get(conn, b["id"])["settings"] == {"v": 1, "set_id": s2}
        assert charts.get(conn, w["id"])["settings"] == {"v": 1, "series": [{"set_id": s2}]}
        assert remap_charts(conn, {s2: s2}) == 0  # nothing left to change
    engine.dispose()


def test_bfggis_cycle_renumbers_the_ids_inside_the_settings(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """10.5 п. 5: charts and swath plots come along; ids of other objects inside their settings follow the new
    numbers, an object the archive does not have becomes null; the view key `swaths` is renumbered too; the own
    `set_id` of a chart of a set becomes its new parent (the window reads it, 13.36)."""
    c, set_id = demo
    gone = post(c, set_id=set_id, kind="histogram", name="Временный", settings={"v": 1}).json()["id"]
    qq = post(c, set_id=set_id, kind="qq", name="Q-Q", settings={"v": 1, "set_id": set_id, "y_set_id": set_id}).json()
    assert c.delete(f"{API}/charts/{gone}").status_code == 200  # a gap: the ids after it change on import
    series = [{"kind": "samples", "set_id": set_id, "field": "AU"}, {"kind": "blocks", "model_id": gone}]
    swath = post(c, kind="swath", name="Swath", settings={"v": 1, "series": series}).json()
    view = c.put(
        f"{API}/view/swaths", json={"value": [{"swath": swath["id"], "visible": False}], "expected_version": 0}
    )
    assert view.status_code == 200
    project = projects_of(c.core).require_current()
    archive = tmp_path / "cycle.bfggis"
    export_project(project.engine, project.folder, archive)
    result = importer.import_project(archive, tmp_path / "imported", name="Копия")
    ids = {int(k): v for k, v in result["id_maps"]["objects"].items()}
    assert ids[qq["id"]] == gone and ids[swath["id"]] != swath["id"]  # the old number `gone` is now the Q-Q
    engine = dialect.create_engine(dialect.sqlite_url(str(Path(result["path"]) / DB_FILE)))
    try:
        with dialect.read_tx(engine) as conn:
            got = {x["kind"]: x for x in charts.list_charts(conn)}
            assert got["qq"]["id"] == ids[qq["id"]] and got["qq"]["settings"]["y_set_id"] == ids[set_id]
            assert got["qq"]["set_id"] == ids[set_id] and got["qq"]["settings"]["set_id"] == ids[set_id]
            s = got["swath"]
            assert s["id"] == ids[swath["id"]] and s["set_id"] is None
            assert s["settings"]["series"] == [
                {"kind": "samples", "set_id": ids[set_id], "field": "AU"},
                {"kind": "blocks", "model_id": None},
            ]
            from bf_core.viewstate.service import read_all

            assert read_all(conn)["swaths"]["value"] == [{"swath": ids[swath["id"]], "visible": False}]
    finally:
        engine.dispose()
