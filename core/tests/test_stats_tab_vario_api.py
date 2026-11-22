"""The API of the variograms and the local uncertainty of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11, решение 21;
подшаг 16.17): `POST /stats/tab/variogram`, `/variogram-model`, `/map`, `/local` answer what the kernels of 16.10
give on the points of the table (the middles of the intervals in plan; `local` — with the hole of every
interval); missing values are not points (never 0); more than 20 000 intervals — 422 `too_many_samples`."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_tab_vario, source
from bf_core.stats.local import tab_local
from bf_core.stats.tab import tab_points
from bf_core.stats.tab_vario import tab_map, tab_variogram, tab_variogram_model
from tests.test_import_job import API

T = f"{API}/stats/tab"
MODEL = {"nugget": 0.1, "structures": [{"it": 1, "cc": 0.9, "hmaj": 150.0, "hmin": 80.0, "azi": 30.0}]}


@pytest.fixture()
def demo(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """The demo set with the routes of 16.17 (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    if "/stats/tab/variogram" not in paths:
        stats_router.router.include_router(routes_tab_vario.router)
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return c, set_id, projects_of(c.core).require_current()


def points(project: Any, set_id: int, field: str = "AU", table: Any = None) -> tuple[Any, Any]:
    cols = source.read(project, set_id, table, [field])
    return tab_points(source.midpoints(project, cols), cols.number(field)), cols.hole_of()


def post(c: Any, path: str, **body: Any) -> dict[str, Any]:
    r = c.post(f"{T}/{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def refused(c: Any, path: str, body: dict[str, Any], status: int = 422) -> dict[str, Any]:
    r = c.post(f"{T}/{path}", json=body)
    assert r.status_code == status, r.text
    e = r.json()
    assert e["message"] and e["hint"], e
    return e  # type: ignore[no-any-return]


def test_variogram_is_the_kernel(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    p, _groups = points(project, set_id)
    got = post(c, "variogram", set_id=set_id, table=None, field=None, lag="25", nlag=8, directions="four",
               min_pairs=10, model=MODEL)  # fmt: skip
    want = routes_charts._safe(tab_variogram(p, lag=25.0, nlag=8, directions="four", min_pairs=10, model=MODEL))
    assert got["set_id"] == set_id and got["table"] == "Пробы" and got["field"] == "AU"
    for k in ("count", "sigma2", "lag", "nlag", "lag_tol", "lags", "directions", "model", "counts", "standardize"):
        assert got[k] == want[k], k
    assert [d["az"] for d in got["directions"]] == [0.0, 45.0, 90.0, 135.0]
    assert all(len(d["model_gamma"]) == 8 for d in got["directions"])
    assert got["count"] == p.n and got["counts"] == p.counts

    omni = post(c, "variogram", set_id=set_id, field="AU", lag="25,0", standardize=True)
    assert omni["lag"] == 25.0 and omni["nlag"] == 10 and omni["model"] is None
    (d,) = omni["directions"]
    assert d["omni"] is True and "model_gamma" not in d
    assert all(g is None or isinstance(g, float) for g in d["gamma"])  # an empty lag is null, never 0
    assert [g is None for g in d["gamma"]] == [n == 0 for n in d["n"]]
    part = post(c, "variogram", set_id=set_id, field="AU", lag=25, holes=["dh-001", "DH-002", "DH-003"])
    assert 0 < part["count"] < p.n


def test_model_curve_needs_no_data(demo) -> None:  # type: ignore[no-untyped-def]
    c, _set_id, _project = demo
    got = post(c, "variogram-model", nug=0.1, structures=MODEL["structures"], n=11, directions="four")
    want = routes_charts._safe(tab_variogram_model(0.1, MODEL["structures"], n=11, directions="four"))
    assert got == want and got["h_max"] == 225.0 and got["sill"] == pytest.approx(1.0)
    assert len(got["directions"]) == 4 and got["directions"][0]["gamma"][0] == pytest.approx(0.0)
    e = refused(c, "variogram-model", {"nugget": 0.0, "structures": []})
    assert e["code"] == "bad_param" and e["param"] == "h_max"


def test_map_is_the_kernel(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    p, _groups = points(project, set_id, "CU")
    got = post(c, "map", set_id=set_id, field="cu", cell=20, n="6", min_pairs=3)
    want = routes_charts._safe(tab_map(p, cell=20.0, n=6, min_pairs=3))
    assert got["field"] == "CU"
    for k in ("count", "sigma2", "cell", "nu", "nv", "u", "v", "gamma", "n", "counts"):
        assert got[k] == want[k], k
    assert len(got["u"]) == len(got["v"]) == 13 and len(got["gamma"]) == 13 and len(got["gamma"][0]) == 13
    flat = [(g, n) for gs, ns in zip(got["gamma"], got["n"], strict=True) for g, n in zip(gs, ns, strict=True)]
    assert all((g is None) == (n < 3) for g, n in flat)  # fewer pairs than the threshold — null


def test_local_takes_the_holes_of_the_intervals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    p, groups = points(project, set_id)
    body = {"grid": {"nx": 12, "ny": "10"}, "k": 6, "realizations": 20, "seed": 11}
    got = post(c, "local", set_id=set_id, field="AU", threshold="0,5", **body)
    want = routes_charts._safe(tab_local(p, groups, grid={"nx": 12, "ny": 10}, k=6, realizations=20, seed=11,
                                         threshold=0.5, threads=2))  # fmt: skip
    assert got["holes"] == len(set(np.asarray(groups)[p.rows].tolist())) > 3
    for k in ("count", "holes", "realizations", "k", "power", "radius", "seed", "threshold", "grid", "mean", "sd",
              "p10", "p90", "prob", "empty", "counts"):  # fmt: skip
        assert got[k] == want[k], k
    assert len(got["mean"]) == 10 and len(got["mean"][0]) == 12
    again = post(c, "local", set_id=set_id, field="AU", **body)
    assert again["mean"] == got["mean"] and again["prob"] is None
    free = post(c, "local", set_id=set_id, field="AU", grid={"nx": 5, "ny": 5}, realizations=5)
    assert isinstance(free["seed"], int)  # drawn and returned
    tight = post(c, "local", set_id=set_id, field="AU", grid={"nx": 5, "ny": 5}, realizations=5, radius=1)
    assert tight["empty"] > 0 and any(v is None for row in tight["mean"] for v in row)  # not reached — null


def test_missing_values_are_not_points(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = demo
    holes = source.read(project, set_id, None, []).holes
    n = 120
    k = np.arange(n)
    values: list[Any] = [None if i % 4 == 0 else float(i % 7 + 1) for i in range(n)]
    sparse = dh.TableData(
        name="Редкие", kind="interval", holes=[holes[i % 12] for i in range(n)],
        from_m=(k // 12) * 2.0, to_m=(k // 12) * 2.0 + 2.0,
        columns=[dh.ColumnSpec("V", "number", None, "component")], values=[values],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, sparse, job_id=None, tick=lambda _p, _m: None)
    for path, extra in (("variogram", {"lag": 20}), ("map", {"cell": 20, "n": 3, "min_pairs": 1}),
                        ("local", {"grid": {"nx": 4, "ny": 4}, "realizations": 5, "seed": 1})):  # fmt: skip
        got = post(c, path, set_id=set_id, table="Редкие", field="V", **extra)
        assert got["count"] == 90 and got["counts"]["missing"] == 30 and got["counts"]["rows"] == n, path


def test_twenty_thousand_and_one_intervals_are_refused(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.29-2: 422 `too_many_samples` {param: table} with the advice to composite."""
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
    for path in ("variogram", "map", "local"):
        e = refused(c, path, {"set_id": set_id, "table": "Мелкие", "field": "V"})
        assert e["code"] == "too_many_samples" and e["param"] == "table", path
        message = e["message"].replace(" ", " ")
        assert "20 001" in message and "20 000" in message
        assert "Композиты" in e["hint"] and "Вариография" in e["hint"]
    ok = post(c, "map", set_id=set_id, table="Мелкие", field="V", holes=holes[:10], n=2)
    assert ok["count"] == 10 * per


def test_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _project = demo
    for path, body, code, param in (
        ("variogram", {"lag": "много"}, "bad_param", "lag"),
        ("variogram", {"lag": 0}, "bad_param", "lag"),
        ("variogram", {"nlag": 201}, "bad_param", "nlag"),
        ("variogram", {"directions": "eight"}, "bad_param", "directions"),
        ("variogram", {"model": {"nugget": -1, "structures": []}}, "bad_param", "model.nugget"),
        ("variogram", {"model": [1]}, "bad_param", "model"),
        ("variogram", {"field": "НЕТ"}, "field_not_found", "field"),
        ("map", {"n": 101}, "bad_param", "n"),
        ("map", {"cell": "-5"}, "bad_param", "cell"),
        ("local", {"grid": {"nx": 1, "ny": 10}}, "bad_param", "grid.nx"),
        ("local", {"k": 65}, "bad_param", "k"),
        ("local", {"realizations": 4}, "bad_param", "realizations"),
        ("local", {"threshold": "порог"}, "bad_param", "threshold"),
    ):
        e = refused(c, path, {"set_id": set_id, **body})
        assert e["code"] == code and e.get("param") == param, (path, body, e)
    assert refused(c, "map", {"set_id": 999_999}, 404)["code"] == "set_not_found"
    assert refused(c, "variogram", {"set_id": set_id, "table": "Нет такой"}, 404)["code"] == "table_not_found"
    assert c.post(f"{T}/local", json=[1]).status_code == 422
