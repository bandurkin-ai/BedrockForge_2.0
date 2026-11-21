"""The API of the tab «Статистика» (ТЗ 7.29; ADR 0019 §7, §10; подшаг 13.17): `POST /stats/tab/declus`, `/nscore`,
`/backtr`, `/bootstrap` answer what the kernels of `stats/tab.py` give on the points of the table (the middles
of the intervals in plan); the weights of declustering in the normal scores and the bootstrap; more than 20 000
intervals — 422 `too_many_samples` with the advice to composite (Т7.29-2)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_tab, source
from bf_core.stats.tab import tab_bootstrap, tab_declus, tab_nscore, tab_points
from tests.test_import_job import API

T = f"{API}/stats/tab"
CELLS = {"method": "cell", "cmin": 20.0, "cmax": 200.0, "ncell": 10, "noff": 4}


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """The demo set with the routes of the tab (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    if "/stats/tab/declus" not in paths:
        stats_router.router.include_router(routes_tab.router)
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return c, set_id, projects_of(c.core).require_current()


def points(project: Any, set_id: int, field: str = "AU") -> tuple[Any, Any]:
    cols = source.read(project, set_id, None, [field])
    return tab_points(source.midpoints(project, cols), cols.number(field)), cols.row_ids


def post(c: Any, path: str, **body: Any) -> dict[str, Any]:
    r = c.post(f"{T}/{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_declus_is_the_kernel(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    p, ids = points(project, set_id)
    got = post(c, "declus", set_id=set_id, table=None, field=None, declus=CELLS, weights=True)
    want = routes_charts._safe(tab_declus(p, CELLS, weights=True))
    assert got["set_id"] == set_id and got["table"] == "Пробы" and got["field"] == "AU"  # the first component
    assert got["rows"] == ids[want["rows"]].tolist() and got["weights"] == want["weights"]
    for k in ("method", "count", "naive_mean", "declustered_mean", "best_cell_size", "cell_sizes", "means", "counts"):
        assert got[k] == want[k], k
    assert got["counts"]["used"] == p.n and abs(sum(got["weights"]) - p.n) < 1e-6
    plain = post(c, "declus", set_id=set_id, field="cu", declus=None)
    assert plain["field"] == "CU" and plain["method"] == "cell" and "weights" not in plain and "rows" not in plain
    poly = post(c, "declus", set_id=set_id, field="AU", declus={"method": "polygon"})
    assert poly["method"] == "polygon" and poly["cell_sizes"] is None and len(poly["bounds"]) == 4
    one = post(c, "declus", set_id=set_id, field="AU", declus=CELLS, holes=["dh-001", "DH-002"])
    assert 0 < one["count"] < p.n


def test_nscore_and_bootstrap_take_the_weights_of_declustering(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    p, ids = points(project, set_id)
    plain = post(c, "nscore", set_id=set_id, field="AU")
    heavy = post(c, "nscore", set_id=set_id, field="AU", declus=CELLS)
    want = routes_charts._safe(tab_nscore(p, declus=CELLS))
    assert heavy["weighted"] is True and plain["weighted"] is False
    assert heavy["values"] == want["values"] and heavy["scores"] == want["scores"]
    assert heavy["sample_scores"] == want["sample_scores"] and heavy["table_rows"] == want["table"]
    assert heavy["rows"] == ids[p.rows].tolist() and len(heavy["sample_scores"]) == p.n
    assert heavy["scores"] != plain["scores"]  # the weights move the table

    back = post(c, "backtr", set_id=set_id, field="AU", declus=CELLS, scores=[-1.0, 0.0, 1.0])
    assert len(back["values"]) == 3 and back["values"] == sorted(back["values"])

    b0 = post(c, "bootstrap", set_id=set_id, field="AU", n_boot=200, seed=7)
    b1 = post(c, "bootstrap", set_id=set_id, field="AU", n_boot=200, seed=7, declus=CELLS)
    kernel = routes_charts._safe(tab_bootstrap(p, n_boot=200, seed=7, declus=CELLS, threads=2))
    assert b1["seed"] == 7 and b1["statistics"] == kernel["statistics"]
    assert b0["statistics"]["mean"]["observed_weighted"] is None
    assert b1["statistics"]["mean"]["observed_weighted"] is not None and b1["statistics"] != b0["statistics"]
    assert b1["counts"] == p.counts and b1["weighted"] is True and b0["weighted"] is False


def test_twenty_thousand_and_one_intervals_are_refused(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.29-2 through the API: 422 `too_many_samples` {param: table} with the advice to composite."""
    c, set_id, project = demo
    holes = source.read(project, set_id, None, []).holes
    n = 20_001
    per = n // len(holes) + 1
    k = np.arange(n)
    big = dh.TableData(
        name="Мелкие", kind="interval", holes=[holes[i // per] for i in range(n)],
        from_m=(k % per) * 0.5, to_m=(k % per) * 0.5 + 0.5,
        columns=[dh.ColumnSpec("V", "number", None, "component")], values=[(k % 97 + 1.0).tolist()],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, big, job_id=None, tick=lambda _p, _m: None)
    for path in ("declus", "nscore", "bootstrap"):
        r = c.post(f"{T}/{path}", json={"set_id": set_id, "table": "Мелкие", "field": "V"})
        assert r.status_code == 422, r.text
        e = r.json()
        assert e["code"] == "too_many_samples" and e["param"] == "table"
        message = e["message"].replace("\u00a0", " ")
        assert "20 001" in message and "20 000" in message and "Композиты" in e["hint"]
    # a part of the holes fits
    ok = post(c, "nscore", set_id=set_id, table="Мелкие", field="V", holes=holes[:10])
    assert ok["count"] == 10 * per


def test_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _project = demo
    for path, body, code, param in (
        ("declus", {"set_id": set_id, "field": "Номер пробы"}, "field_not_number", "field"),
        ("declus", {"set_id": set_id, "field": "НЕТ"}, "field_not_found", "field"),
        ("declus", {"set_id": set_id, "declus": {"method": "cell3d"}}, "bad_param", "method"),
        ("declus", {"set_id": set_id, "declus": {"what": 1}}, "bad_param", "declus"),
        ("declus", {"set_id": set_id, "weights": "да"}, "bad_param", "weights"),
        ("nscore", {"set_id": set_id, "holes": "DH-001"}, "bad_param", "holes"),
        ("backtr", {"set_id": set_id}, "bad_param", "scores"),
        ("bootstrap", {"set_id": set_id, "n_boot": 1}, "bad_param", "n_boot"),
        ("bootstrap", {"set_id": "1"}, "bad_param", "set_id"),
    ):
        r = c.post(f"{T}/{path}", json=body)
        assert r.status_code == 422, (path, body, r.text)
        e = r.json()
        assert (e["code"], e.get("param")) == (code, param), (path, body, e)
        assert e["message"] and e["hint"]
    r = c.post(f"{T}/nscore", json={"set_id": 999_999})
    assert r.status_code == 404 and r.json()["code"] == "set_not_found"
    r = c.post(f"{T}/nscore", json={"set_id": set_id, "holes": ["НЕТ ТАКОЙ"]})
    assert r.status_code == 422 and r.json()["code"] == "no_values"
