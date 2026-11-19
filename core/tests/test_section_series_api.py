"""Series of sections over HTTP (ТЗ 8.1; ADR 0016 §1, §3): CRUD, the number of a series, copy, 409 by the version,
limits → 422 with a hint, 423 read-only, the table of sections (Т8.1-1) with an ETag, `objects.changed`."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bf_core.projects.service import projects_of
from tests.test_import_job import API

pytestmark = pytest.mark.windows
URL = f"{API}/section-series"
T81_1 = {"type": "vertical", "p1": [1000, 5000, 0], "az": 90, "s0": 0, "s1": 100, "step": 25}


@pytest.fixture()
def series_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: its app has the router of the series (until `api/modules.py` lists it)."""
    import bf_core.api.app as app_module

    if "sections" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "sections"))


@pytest.fixture()
def sc(series_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Серии"}).status_code == 200
    return pclient


def error_of(r: Any, status: int, code: str) -> dict[str, Any]:
    body = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body


def events(c: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(name, json.loads(data)) for _id, name, data in c.core.events._recent]


def create(c: Any, params: dict[str, Any], **kw: Any) -> dict[str, Any]:
    r = c.post(URL, json={"params": params, **kw})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_t81_1_table_names_origins_and_etag(sc) -> None:  # type: ignore[no-untyped-def]
    s = create(sc, T81_1)
    assert s["number"] == 1 and s["name"] == "Серия 1" and s["count"] == 5 and s["version"] == 1
    p = s["params"]
    assert p["phi"] == 90.0 and p["towards"] == p["away"] == 25.0 and p["shadow"] == "prev" and p["p2"] is None
    assert p["overrides"] == {} and p["template"] is None and p["contour_layer"] is None and p["layout"] is None
    assert ("objects.changed", {"kind": "section_series", "id": s["id"]}) in events(sc)
    r = sc.get(f"{URL}/{s['id']}/table")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    t = r.json()
    assert t["version"] == 1 and t["count"] == 5
    assert [x["name"] for x in t["sections"]] == ["5000N", "5025N", "5050N", "5075N", "5100N"]
    assert [x["k"] for x in t["sections"]] == [0, 1, 2, 3, 4]
    assert [x["s"] for x in t["sections"]] == [0.0, 25.0, 50.0, 75.0, 100.0]
    assert [x["origin"] for x in t["sections"]] == [[1000.0, 5000.0 + 25 * k, 0.0] for k in range(5)]  # exactly
    assert all(x["towards"] == x["away"] == 25.0 for x in t["sections"])
    tag = r.headers["etag"]
    assert sc.get(f"{URL}/{s['id']}/table", headers={"If-None-Match": tag}).status_code == 304
    # own windows of a section are in the table; a change of the series is a new ETag
    r = sc.patch(f"{URL}/{s['id']}", json={"expected_version": 1, "params": {"overrides": {"2": {"away": 5}}}})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = sc.get(f"{URL}/{s['id']}/table", headers={"If-None-Match": tag})
    assert r.status_code == 200 and r.headers["etag"] != tag
    assert r.json()["sections"][2] | {"name": None} == {
        "k": 2, "name": None, "s": 50.0, "origin": [1000.0, 5050.0, 0.0], "towards": 25.0, "away": 5.0,
    }  # fmt: skip


def test_numbers_copy_delete_and_list(sc) -> None:  # type: ignore[no-untyped-def]
    a = create(sc, T81_1, name="Профили", folder=" Север / Блок 1 ")
    b = create(sc, {"type": "plan", "p1": [0, 0, 400], "s1": 100, "step": 10})
    assert (a["number"], b["number"]) == (1, 2) and a["folder"] == "Север/Блок 1"
    assert b["count"] == 11 and b["params"]["az"] == 90.0 and b["params"]["phi"] is None
    r = sc.post(f"{URL}/{a['id']}/copy")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["number"] == 3 and c["name"] == "Профили (копия)" and c["params"] == a["params"] and c["id"] != a["id"]
    assert sc.delete(f"{URL}/{b['id']}").json() == {"deleted": b["id"]}
    assert ("objects.changed", {"kind": "section_series", "id": b["id"], "deleted": True}) in events(sc)
    d = create(sc, T81_1)
    assert d["number"] == 4  # the largest + 1: a number is never given twice while the larger ones live
    listed = sc.get(URL).json()
    assert [s["number"] for s in listed["series"]] == [1, 3, 4] and listed["limit"] >= 1000
    assert sc.get(f"{URL}/{a['id']}").json() == listed["series"][0]
    error_of(sc.get(f"{URL}/{b['id']}"), 404, "series_not_found")
    error_of(sc.get(f"{URL}/{b['id']}/table"), 404, "series_not_found")
    error_of(sc.delete(f"{URL}/{b['id']}"), 404, "series_not_found")
    error_of(sc.post(f"{URL}/{b['id']}/copy", json={}), 404, "series_not_found")


def test_patch_version_conflict_and_merge(sc) -> None:  # type: ignore[no-untyped-def]
    s = create(sc, T81_1)
    url = f"{URL}/{s['id']}"
    r = sc.patch(url, json={"expected_version": 1, "name": "Новое", "params": {"p2": [1100, 5000]}})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["name"] == "Новое" and v2["number"] == 1
    assert v2["params"]["p2"] == [1100.0, 5000.0] and v2["params"]["az"] is None  # P2 replaces the azimuth
    assert v2["params"]["s1"] == 100.0 and v2["count"] == 5  # the fields not sent stay
    body = error_of(sc.patch(url, json={"expected_version": 1, "name": "Старое"}), 409, "version_conflict")
    assert body["version"] == 2 and body["series"] == v2
    assert sc.get(url).json()["name"] == "Новое"
    # a wrong change leaves the series as it was
    assert (
        error_of(sc.patch(url, json={"expected_version": 2, "params": {"step": 0}}), 422, "bad_series")["field"]
        == "step"
    )
    bad = error_of(sc.patch(url, json={"expected_version": 2, "params": {"template": "{k!r}"}}), 422, "bad_series")
    assert bad["field"] == "template"
    assert sc.get(url).json() == v2
    r = sc.patch(url, json={"expected_version": 2, "folder": None, "params": {"az": 0, "shadow": "both"}})
    assert r.json()["params"]["p2"] is None and r.json()["params"]["az"] == 0.0 and r.json()["version"] == 3


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"s1": 100_000, "step": 10}, "5 000"),  # K = 10 001
        ({"type": "spiral"}, "Тип серии"),
        ({"az": None, "p2": [1000, 5000]}, "совпадают"),
        ({"az": 90, "p2": [1, 2]}, "что-то одно"),
        ({"type": "inclined", "phi": 91}, "от 0 до 90"),
        ({"phi": 60}, "Наклонная"),
        ({"step": 0.001}, "0,01"),
        ({"towards": 20_000}, "10 000"),
        ({"s0": 50, "s1": 10}, "раньше начала"),
        ({"overrides": {"5": {"away": 1}}}, "разрезу 6"),
        ({"overrides": {"1": {"wide": 1}}}, "к себе"),
        ({"layout": {"sheet": "A3"}}, "Макет"),
        ({"contour_layer": 999}, "Слой контуров"),
        ({"extra": 1}, "неизвестное поле"),
        ({"p1": [1, "x", 0]}, "Точка P1"),
        ({"p1": [float("1e12"), 0, 0]}, "слишком большое"),
        ({"type": "polyline", "polyline": [[0, 0], [0, 0]]}, "совпадают"),
        ({"type": "polyline", "polyline": [[0, 0]] * 1001}, "1 000"),
        ({"template": "Р"}, "{k}"),  # every section the same name
        ({"template": "{X.__class__}"}, ""),
        ({"template": "{0}"}, ""),
        ({"template": "{k!r}"}, ""),
    ],
)
def test_bad_parameters_are_422_with_a_hint(sc, change, words) -> None:  # type: ignore[no-untyped-def]
    body = error_of(sc.post(URL, json={"params": {**T81_1, **change}}), 422, "bad_series")
    assert words in body["message"] + body["hint"]
    assert sc.get(URL).json()["series"] == []


def test_k_limit_is_5000_and_the_hint_gives_the_step(sc) -> None:  # type: ignore[no-untyped-def]
    assert create(sc, {**T81_1, "s1": 4999 * 25})["count"] == 5000
    body = error_of(sc.post(URL, json={"params": {**T81_1, "s1": 5000 * 25}}), 422, "bad_series")
    assert "5001" in body["message"] and "25,01" in body["hint"]


def test_polyline_inclined_and_contour_layer(sc) -> None:  # type: ignore[no-untyped-def]
    pl = create(sc, {"type": "polyline", "polyline": [[0, 0], [100, 0], [100, 100]], "s1": 500})
    assert pl["count"] == 1 and pl["params"]["s1"] == 0.0 and pl["params"]["corridor"] == 25.0
    assert pl["params"]["p1"] == [0.0, 0.0, 0.0] and pl["params"]["az"] is None
    inc = create(sc, {"type": "inclined", "p1": [5e5, 7e6, 300], "az": 45, "phi": 60, "s1": 50, "step": 10})
    assert inc["count"] == 6 and inc["params"]["phi"] == 60.0
    layer = sc.post(f"{API}/lines/layers", json={"name": "Контуры", "color": "#ff8800"})
    assert layer.status_code == 201, layer.text
    lid = layer.json()["id"]
    r = sc.patch(f"{URL}/{inc['id']}", json={"expected_version": 1, "params": {"contour_layer": lid}})
    assert r.status_code == 200 and r.json()["params"]["contour_layer"] == lid
    assert sc.delete(f"{API}/lines/files/{layer.json()['file_id']}").status_code == 200
    assert sc.get(f"{URL}/{inc['id']}").json()["params"]["contour_layer"] is None  # the layer is gone


def test_read_only_is_423(sc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s = create(sc, T81_1)
    project = projects_of(sc.core).require_current()
    monkeypatch.setattr(project, "read_only", True)
    error_of(sc.post(URL, json={"params": T81_1}), 423, "project_read_only")
    error_of(sc.patch(f"{URL}/{s['id']}", json={"expected_version": 1, "name": "X"}), 423, "project_read_only")
    error_of(sc.post(f"{URL}/{s['id']}/copy"), 423, "project_read_only")
    error_of(sc.delete(f"{URL}/{s['id']}"), 423, "project_read_only")
    assert sc.get(f"{URL}/{s['id']}/table").status_code == 200  # reading works
