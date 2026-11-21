"""The selection of samples (ТЗ 8.3, № 237; ADR 0019 §8, подшаг 13.19): `POST /stats/selection` (the points of a
chart, a lasso of a scatter density, a range of a box plot or a log-probability plot, the filter of the table),
`GET /stats/selection/{token}`, `/segments` (BFA1), `GET /drillholes/sets/{id}/tables/{table}?selection=`, 409
`selection_stale` after an edit, more than 200 000 rows — 422."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.drillholes.trajectory import interval_xyz, trajectories
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_more, routes_selection, selection, source
from bf_core.stats.scatter import pairs, rows_in_polygon
from bf_core.viz import binary
from tests.test_import_job import API

S = f"{API}/stats"
LASSO = [[0.2, 0.0], [3.0, 0.05], [2.5, 0.6], [0.4, 0.4]]  # AU (г/т) × CU (%) of the demo set


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """The demo set with the routes of the charts and of the selection (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    for path, module in (("/stats/histogram", routes_charts), ("/stats/scatter", routes_more),
                         ("/stats/selection", routes_selection)):  # fmt: skip
        if path not in paths:
            stats_router.router.include_router(module.router)
    selection.STORE.clear()
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return c, set_id, projects_of(c.core).require_current()


def scatter_body(set_id: int, **extra: Any) -> dict[str, Any]:
    return {"kind": "scatter", "v": 1, "set_id": set_id, "table": None, "x": "AU", "y": "CU", "weight": "none",
            **extra}  # fmt: skip


def in_lasso(project: Any, set_id: int, polygon: list[list[float]], **log: bool) -> np.ndarray:
    cols = source.read(project, set_id, None, ["AU", "CU"])
    p = pairs(cols.number("AU"), cols.number("CU"), **log)
    return np.sort(cols.row_ids[rows_in_polygon(p, polygon)])


def table_rows(c: Any, set_id: int, table_id: int, **params: Any) -> dict[str, Any]:
    r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{table_id}", params={"limit": 1000, **params})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_lasso_selects_exactly_the_rows_and_as_many_segments(demo) -> None:  # type: ignore[no-untyped-def]
    """Т8.3-3: the lasso of a density → exactly these rows → as many segments, at their places on the holes."""
    c, set_id, project = demo
    want = in_lasso(project, set_id, LASSO)
    assert 20 < want.size < 4000
    r = c.post(f"{S}/selection", json={"chart": scatter_body(set_id), "polygon": LASSO})
    assert r.status_code == 200, r.text
    sel = r.json()
    assert sel["n"] == want.size and sel["source"] == "scatter" and sel["unknown"] == 0
    assert sel["set_id"] == set_id and sel["table_name"] == "Пробы" and sel["rev"] == 1
    got = c.get(f"{S}/selection/{sel['token']}").json()
    assert got["rows"] == want.tolist()

    r = c.get(f"{S}/selection/{sel['token']}/segments")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    head, arrays = binary.decode(r.content)
    assert head["kind"] == "selection_segments" and head["n"] == want.size
    assert head["segments"] == want.size and head["no_position"] == 0  # every hole of the demo has a path
    assert sorted(arrays["row"].tolist()) == want.tolist()
    # the ends on the paths of the holes (float32 from a whole-metre origin)
    origin = np.array(head["origin"])
    assert np.array_equal(origin, np.round(origin))
    st = trajectories(project, set_id)
    a, b = interval_xyz(st, arrays["hole"].astype(np.int64), arrays["from"], arrays["to"])
    assert np.allclose(arrays["a"].reshape(-1, 3) + origin, a, atol=1e-3)
    assert np.allclose(arrays["b"].reshape(-1, 3) + origin, b, atol=1e-3)
    assert np.all(arrays["to"] > arrays["from"])

    # the same lasso on log axes is drawn in log10: the points inside it are another set
    ring = [[0.2, 0.01], [3.0, 0.05], [2.5, 0.6], [0.4, 0.4]]
    log = c.post(f"{S}/selection", json={"chart": scatter_body(set_id, log_x=True, log_y=True), "polygon": ring})
    assert log.status_code == 200, log.text
    want_log = in_lasso(project, set_id, ring, log_x=True, log_y=True)
    assert log.json()["n"] == want_log.size and not np.array_equal(want_log, in_lasso(project, set_id, ring))

    rect = c.post(f"{S}/selection", json={"chart": scatter_body(set_id), "rect": [3.0, 0.2, 0.6, 0.0]}).json()
    cols = source.read(project, set_id, None, ["AU", "CU"])
    au, cu = cols.number("AU"), cols.number("CU")
    assert rect["n"] == int(((au >= 0.2) & (au <= 3.0) & (cu >= 0.0) & (cu <= 0.6)).sum())


def test_points_of_a_chart_are_the_rows_given(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    pts = c.post(f"{S}/scatter", json={k: v for k, v in scatter_body(set_id).items() if k != "kind"}).json()
    assert pts["mode"] == "points"
    chosen = pts["row"][::3]
    r = c.post(f"{S}/selection", json={"set_id": set_id, "table": None, "rows": [*chosen, chosen[0], 10**11]})
    assert r.status_code == 200, r.text
    sel = r.json()
    assert sel["n"] == len(chosen) and sel["unknown"] == 1 and sel["source"] == "rows"  # repeats count once
    assert c.get(f"{S}/selection/{sel['token']}").json()["rows"] == sorted(chosen)
    head, arrays = binary.decode(c.get(f"{S}/selection/{sel['token']}/segments").content)
    assert head["segments"] == len(chosen) and sorted(arrays["row"].tolist()) == sorted(chosen)


def test_table_filter_by_token(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    want = in_lasso(project, set_id, LASSO)
    sel = c.post(f"{S}/selection", json={"chart": scatter_body(set_id), "polygon": LASSO}).json()
    page = table_rows(c, set_id, sel["table_id"], selection=sel["token"])
    assert page["total"] == want.size and sorted(r["id"] for r in page["rows"]) == want.tolist()
    assert page["selection"] == {"token": sel["token"], "n": want.size}
    au = next(col["key"] for col in page["columns"] if col["name"] == "AU")
    by_au = table_rows(c, set_id, sel["table_id"], selection=sel["token"], sort=au, desc=True, where="AU > 1")
    values = [r[au] for r in by_au["rows"]]
    assert values == sorted(values, reverse=True) and all(v > 1 for v in values) and 0 < by_au["total"] < want.size
    second = table_rows(c, set_id, sel["table_id"], selection=sel["token"], offset=5, limit=3)
    assert [r["id"] for r in second["rows"]] == [r["id"] for r in page["rows"]][5:8]

    # «и наоборот»: the rows of the filter of the table
    hole = page["rows"][0]["hole_id"]
    back = c.post(f"{S}/selection", json={"set_id": set_id, "table": sel["table_id"],
                                          "filter": {"where": "AU > 1", "hole": hole}})  # fmt: skip
    assert back.status_code == 200, back.text
    shown = table_rows(c, set_id, sel["table_id"], where="AU > 1", hole=hole)
    assert back.json()["n"] == shown["total"] > 0 and back.json()["source"] == "table"
    assert c.get(f"{S}/selection/{back.json()['token']}").json()["rows"] == sorted(r["id"] for r in shown["rows"])
    bad = c.post(f"{S}/selection", json={"set_id": set_id, "table": None, "filter": {"where": "AU >"}})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_expression"

    gone = c.get(f"{API}/drillholes/sets/{set_id}/tables/{sel['table_id']}", params={"selection": "f" * 24})
    assert gone.status_code == 404 and gone.json()["code"] == "selection_not_found"
    other = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar", params={"selection": sel["token"]})
    assert other.status_code == 422 and other.json()["param"] == "selection"


def test_an_edit_makes_the_selection_stale(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _project = demo
    sel = c.post(f"{S}/selection", json={"chart": scatter_body(set_id), "polygon": LASSO}).json()
    page = table_rows(c, set_id, sel["table_id"], selection=sel["token"])
    au = next(col["key"] for col in page["columns"] if col["name"] == "AU")
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{sel['table_id']}/rows/{page['rows'][0]['id']}",
                json={"column": au, "value": 99.5})  # fmt: skip
    assert r.status_code == 200, r.text
    for url in (f"{S}/selection/{sel['token']}", f"{S}/selection/{sel['token']}/segments"):
        r = c.get(url)
        assert r.status_code == 409 and r.json()["code"] == "selection_stale", r.text
        assert "Пробы" in r.json()["message"] and r.json()["hint"] and r.json()["token"] == sel["token"]
    r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{sel['table_id']}", params={"selection": sel["token"]})
    assert r.status_code == 409 and r.json()["code"] == "selection_stale"
    assert c.delete(f"{S}/selection/{sel['token']}").json() == {"deleted": True}
    assert c.get(f"{S}/selection/{sel['token']}").json()["code"] == "selection_not_found"


def test_box_and_probplot_ranges(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    box = {"kind": "box", "v": 1, "set_id": set_id, "table": None, "field": "AU", "weight": "length", "log": True}
    r = c.post(f"{S}/selection", json={"chart": box, "range": [2.0, 0.5], "groups": None})
    assert r.status_code == 200, r.text
    au = source.read(project, set_id, None, ["AU"]).number("AU")
    assert r.json()["n"] == int(((au >= 0.5) & (au <= 2.0)).sum()) and r.json()["source"] == "box"
    none = c.post(f"{S}/selection", json={"chart": box, "range": [0.5, 2.0], "groups": ["гранит"]}).json()
    assert none["n"] == 0  # a chart without domains has the one group null
    whole = c.post(f"{S}/selection", json={"chart": box, "range": [0.5, 2.0], "groups": [None]}).json()
    assert whole["n"] == r.json()["n"]
    prob = {**box, "kind": "probplot"}
    prob.pop("log")
    p = c.post(f"{S}/selection", json={"chart": prob, "range": [-1.0, 0.5]}).json()
    assert p["n"] == int(((au > 0.0) & (au <= 0.5)).sum())  # ≤ 0 are not drawn on a log-probability plot


def test_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _project = demo
    r = c.post(f"{S}/selection", json={"set_id": set_id, "rows": list(range(1, 200_002))})
    assert r.status_code == 422 and r.json()["code"] == "too_many_rows" and r.json()["param"] == "rows"
    assert "200 000" in r.json()["message"] and r.json()["hint"]
    for body, param in (
        ({"set_id": set_id}, "rows"),
        ({"set_id": set_id, "rows": [1], "filter": {}}, "rows"),
        ({"set_id": set_id, "rows": ["1"]}, "rows"),
        ({"chart": {"kind": "qq", "v": 1, "set_id": set_id}, "range": [0, 1]}, "chart"),
        ({"chart": scatter_body(set_id)}, "polygon"),
        ({"chart": scatter_body(set_id), "polygon": [[0, 0], [1, 1]]}, "polygon"),
        ({"chart": scatter_body(set_id), "rect": [0, 1, 0]}, "rect"),
        ({"chart": {**scatter_body(set_id), "kind": "box", "field": "AU"}, "range": [0, "a"]}, "range"),
        ({"chart": {**scatter_body(set_id), "x": "НЕТ"}, "rect": [0, 1, 0, 1]}, "x"),
    ):
        r = c.post(f"{S}/selection", json=body)
        assert r.status_code == 422 and r.json()["param"] == param, (body, r.text)
        assert r.json()["message"] and r.json()["hint"]
    r = c.get(f"{S}/selection/{'0' * 24}/segments", params={"method": "nope"})
    assert r.status_code == 422 and r.json()["code"] == "bad_method"


def test_200_thousand_rows_to_segments_within_a_second(demo) -> None:  # type: ignore[no-untyped-def]
    """The time of 13.19: 200 000 rows of the holes of the demo → the selection of the filter and the segments
    (positions on the paths, float32) ≤ 1 s; 200 001 — 422 `too_many_rows` {filter}."""
    c, set_id, project = demo
    holes = source.read(project, set_id, None, []).holes
    n = 200_000
    per = n // len(holes) + 1
    k = np.arange(n)
    big = dh.TableData(
        name="Мелкие", kind="interval", holes=[holes[i // per] for i in range(n)],
        from_m=(k % per) * 0.05, to_m=(k % per) * 0.05 + 0.05,
        columns=[dh.ColumnSpec("V", "number", None, "component")], values=[(k % 97).astype(float).tolist()],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, big, job_id=None, tick=lambda _p, _m: None)
    t = next(t for t in c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"] if t["name"] == "Мелкие")
    table_rows(c, set_id, int(t["table"]))  # the snapshot of the table, as the panel has it open
    c.get(f"{API}/viz/sets/{set_id}/holes")  # the trajectories, as 3D has them
    t0 = time.perf_counter()
    r = c.post(f"{S}/selection", json={"set_id": set_id, "table": "Мелкие", "filter": {"where": None}})
    assert r.status_code == 200, r.text
    seg = c.get(f"{S}/selection/{r.json()['token']}/segments")
    spent = time.perf_counter() - t0
    head, _arrays = binary.decode(seg.content)
    assert r.json()["n"] == n and head["segments"] == n
    print(f"\n200 000 строк → выделение и отрезки: {spent:.2f} с, {len(seg.content) / 1e6:.1f} МБ")
    assert spent <= 1.0
    lasso = [[-1.0, -1.0], [200.0, -1.0], [200.0, 200.0], [-1.0, 200.0]]
    body = {"chart": {"kind": "scatter", "v": 1, "set_id": set_id, "table": "Мелкие", "x": "V", "y": "length",
                      "weight": "none"}, "polygon": lasso}  # fmt: skip
    assert c.post(f"{S}/selection", json=body).json()["n"] == n
    base = f"{API}/drillholes/sets/{set_id}/tables/{t['table']}"
    v = next(col["key"] for col in table_rows(c, set_id, int(t["table"]), limit=1)["columns"] if col["name"] == "V")
    r = c.post(f"{base}/rows", json={"values": {"hole": holes[0], "from": 999.0, "to": 999.5, v: 3.0}})
    assert r.status_code == 200, r.text
    r = c.post(f"{S}/selection", json={"set_id": set_id, "table": "Мелкие", "filter": {}})
    assert r.status_code == 422 and r.json()["code"] == "too_many_rows" and r.json()["param"] == "filter"
    assert c.post(f"{S}/selection", json=body).json()["code"] == "too_many_rows"
