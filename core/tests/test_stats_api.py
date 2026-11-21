"""The API of the charts of ТЗ 7.25–7.27 (ADR 0019 §10, подшаг 13.15): `POST /stats/histogram`, `/stats/qq` and
`/stats/chart/{id}` answer what the kernels give on the same rows of the demo set; a saved histogram is
recomputed after an edit of a sample (Т7.28-1); the refusals carry their code and `param`; at most two heavy
calculations at a time; nothing heavy is imported with the routes."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, source
from bf_core.stats.histogram import HistogramSpec, histogram_chart
from bf_core.stats.qq import qq_chart
from bf_core.stats.select import SampleFilter, select_sample
from tests.test_import_job import API

S = f"{API}/stats"


@pytest.fixture()
def charts_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: the stats router has these routes (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    if "/stats/histogram" not in paths:
        monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
        stats_router.router.include_router(routes_charts.router)


@pytest.fixture()
def demo(charts_routes, pclient):  # type: ignore[no-untyped-def]
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return c, set_id


def kernel_side(c: Any, set_id: int, field: str, flt: SampleFilter, holes: list[str] | None = None) -> Any:
    """The selection the kernels take for a field of the sample table, read straight from the source."""
    project = projects_of(c.core).require_current()
    cols = source.read(project, set_id, None, [field, "length"])
    values, length = cols.number(field), cols.number("length")
    if holes:
        keep = [cols.holes[h] in holes for h in cols.hole_of()]
        values, length = values[keep], length[keep]
    return select_sample(values, flt, length=length)


def hist_body(set_id: int, **extra: Any) -> dict[str, Any]:
    view = {"y": "percent", "cumulative": True, "marks": {"mean": True}, "color": "#1f6bff"}
    return {"v": 1, "set_id": set_id, "table": None, "field": "AU", "weight": "length", "top_cut": 2.0,
            "bin_width": None, "bins": 20, "start": None, "end": None, "log": False, "view": view, **extra}  # fmt: skip


def test_histogram_is_the_kernel(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    r = c.post(f"{S}/histogram", json=hist_body(set_id))
    assert r.status_code == 200, r.text
    got = r.json()
    flt = SampleFilter(weight="length", top_cut=2.0)
    want = routes_charts._safe(histogram_chart(kernel_side(c, set_id, "AU", flt), HistogramSpec(bins=20)))
    assert got == {"field": "AU", "set_id": set_id, **want}
    assert set(got) == {"field", "set_id", "log", "weighted", "counts", "stats", "stats_unweighted", "bins",
                        "conventions"}  # fmt: skip
    assert set(got["bins"]) == {"edges", "counts", "weights", "percent", "cumulative_percent", "width", "below",
                                "above", "nonpositive"}  # fmt: skip
    assert got["weighted"] is True and got["stats_unweighted"] is not None
    assert got["counts"]["missing"] > 0 and got["counts"]["capped"] > 0  # the demo has an empty grade
    # the field by any case, the default field (the first component), the table by its name; no weight
    assert c.post(f"{S}/histogram", json=hist_body(set_id, field="au")).json()["field"] == "AU"
    plain = c.post(f"{S}/histogram", json=hist_body(set_id, field=None, table="Пробы", weight="none")).json()
    assert plain["field"] == "AU" and plain["weighted"] is False and plain["stats_unweighted"] is None


def test_histogram_of_some_holes_and_log(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    holes = ["DH-001", "dh-002", "нет такой"]
    r = c.post(f"{S}/histogram", json=hist_body(set_id, holes=holes, log=True, bins=None, top_cut=None))
    assert r.status_code == 200, r.text
    sel = kernel_side(c, set_id, "AU", SampleFilter(), ["DH-001", "DH-002"])
    assert r.json() == {"field": "AU", "set_id": set_id,
                        **routes_charts._safe(histogram_chart(sel, HistogramSpec(log=True)))}  # fmt: skip
    assert 0 < r.json()["counts"]["rows"] < 200


def test_qq_against_a_distribution_and_two_samples(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    body = {"v": 1, "set_id": set_id, "table": None, "field": "AU", "distribution": "lognormal", "y_set_id": None,
            "y_table": None, "y_field": None, "holes": [], "y_holes": [], "quantiles": 99, "log": True}  # fmt: skip
    r = c.post(f"{S}/qq", json=body)
    assert r.status_code == 200, r.text
    x = kernel_side(c, set_id, "AU", SampleFilter())
    want = routes_charts._safe(qq_chart(x, distribution="lognormal"))
    assert r.json() == {"field": "AU", "set_id": set_id, "y_field": None, "y_set_id": None, **want}
    assert r.json()["mode"] == "theory" and r.json()["y"] is None

    two = {**body, "distribution": "sample", "y_field": "CU", "quantiles": 19, "y_holes": ["DH-003"]}
    r = c.post(f"{S}/qq", json=two)
    assert r.status_code == 200, r.text
    y = kernel_side(c, set_id, "CU", SampleFilter(), ["DH-003"])
    want = routes_charts._safe(qq_chart(x, distribution="sample", y=y, quantiles=19))
    assert r.json() == {"field": "AU", "set_id": set_id, "y_field": "CU", "y_set_id": set_id, **want}
    assert len(r.json()["points"]) == 19 and r.json()["y"]["counts"]["rows"] < x.counts["rows"]
    same = c.post(f"{S}/qq", json={**two, "y_field": "AU", "y_holes": []}).json()
    assert same["ks"] == 0.0 and all(p[0] == p[1] for p in same["points"])  # Т7.26-2 through the API

    r = c.post(f"{S}/qq", json={**body, "distribution": "sample"})
    assert r.status_code == 422 and r.json()["code"] == "bad_param" and r.json()["param"] == "y_field"
    r = c.post(f"{S}/qq", json={**two, "quantiles": 2})
    assert r.status_code == 422 and r.json()["param"] == "quantiles"
    r = c.post(f"{S}/qq", json={**body, "distribution": "weibull"})
    assert r.status_code == 422 and r.json()["param"] == "distribution"


def test_saved_histogram_follows_an_edit(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.28-1: after an edit of a sample the saved histogram opened again is recomputed."""
    c, set_id = demo
    settings = hist_body(set_id, top_cut=None)
    r = c.post(f"{API}/charts", json={"set_id": set_id, "kind": "histogram", "name": "Гистограмма · AU",
                                      "settings": settings, "auto_name": True})  # fmt: skip
    assert r.status_code == 200, r.text
    chart = r.json()
    first = c.post(f"{S}/chart/{chart['id']}")
    assert first.status_code == 200, first.text
    assert first.json()["chart"] == {"id": chart["id"], "kind": "histogram", "name": chart["name"], "version": 1}
    same = c.post(f"{S}/histogram", json=settings).json()
    assert {k: v for k, v in first.json().items() if k != "chart"} == same

    tabs = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    key = next(t["table"] for t in tabs if t["name"] == "Пробы")
    rows = c.get(f"{API}/drillholes/sets/{set_id}/tables/{key}?limit=5").json()
    au = next(col["key"] for col in rows["columns"] if col["name"] == "AU")
    row = rows["rows"][0]
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{key}/rows/{row['id']}", json={"column": au, "value": 500.0})
    assert r.status_code == 200, r.text
    again = c.post(f"{S}/chart/{chart['id']}").json()
    assert again["stats"]["max"] == 500.0 and again["stats"]["mean"] > first.json()["stats"]["mean"]
    want = histogram_chart(kernel_side(c, set_id, "AU", SampleFilter()), HistogramSpec(bins=20))
    assert again["stats"] == routes_charts._safe(want["stats"])

    r = c.post(f"{S}/chart/{10**9}")
    assert r.status_code == 404 and r.json()["code"] == "chart_not_found" and r.json()["hint"]


def test_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo

    def fail(body: dict[str, Any], status: int = 422) -> dict[str, Any]:
        r = c.post(f"{S}/histogram", json=body)
        assert r.status_code == status, r.text
        out: dict[str, Any] = r.json()
        assert out["message"] and out["hint"]
        return out

    assert fail(hist_body(set_id, bins=0))["param"] == "bins"
    assert fail(hist_body(set_id, weight="tonnage"))["param"] == "weight"
    assert fail(hist_body(set_id, top_cut="5"))["param"] == "top_cut"
    assert fail(hist_body(set_id, weight="field"))["param"] == "weight_field"
    out = fail(hist_body(set_id, field="ZN"))
    assert out["code"] == "field_not_found" and out["param"] == "field"
    out = fail(hist_body(set_id, weight="field", weight_field="DENSITY"))
    assert out["code"] == "field_not_found" and out["param"] == "weight_field"
    out = fail(hist_body(set_id, field="Номер пробы"))
    assert out["code"] == "field_not_number" and out["param"] == "field"
    out = fail(hist_body(set_id, bottom_cut=1e9, top_cut=None))
    assert out["code"] == "no_values" and "ниже нижней отсечки" in out["hint"]
    out = fail(hist_body(set_id, bins=None, bin_width=1e-6))
    assert out["code"] == "too_many_bins" and out["param"] == "bin_width"
    assert fail({**hist_body(set_id), "set_id": None})["param"] == "set_id"
    assert fail(hist_body(set_id, v=2))["param"] == "v"
    assert fail(hist_body(set_id, holes="DH-001"))["param"] == "holes"
    assert fail(hist_body(10**9), 404)["code"] == "set_not_found"
    assert fail(hist_body(set_id, table="Литология"), 404)["code"] == "table_not_found"


def test_at_most_two_heavy_at_a_time() -> None:
    now, most, lock = 0, 0, threading.Lock()

    def job() -> dict[str, Any]:
        nonlocal now, most
        with lock:
            now += 1
            most = max(most, now)
        time.sleep(0.05)
        with lock:
            now -= 1
        return {"x": float("nan")}

    out: list[dict[str, Any]] = []
    threads = [threading.Thread(target=lambda: out.append(routes_charts.run(job))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == routes_charts.HEAVY == 2 and out == [{"x": None}] * 6


def test_routes_import_nothing_heavy() -> None:
    code = (
        "import sys; import bf_core.stats.router, bf_core.stats.routes_charts;"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with the routes: {r.stdout.strip()}"


def dendro_body(set_id: int, **extra: Any) -> dict[str, Any]:
    return {"v": 1, "set_id": set_id, "table": None, "fields": [], "method": "pearson", "log": True,
            "weight": "length", "negatives": "half_abs", "exclude_zero": False, "missing": "pairwise",
            "linkage": "average", "distance": "1-r", "cut": None, "view": {"matrix": True}, **extra}  # fmt: skip


def test_dendrogram_is_the_kernel(demo) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    from bf_core.stats.correlation import CorrSpec
    from bf_core.stats.dendrogram import TreeSpec, dendrogram_chart

    c, set_id = demo
    r = c.post(f"{S}/dendrogram", json=dendro_body(set_id))
    assert r.status_code == 200, r.text
    got = r.json()
    project = projects_of(c.core).require_current()
    with source.dialect.read_tx(project.engine) as conn:
        infos = source.fields_of(conn, source.find_table(conn, set_id, None))
    comps = [f for f in infos if f.kind == "number" and f.role == "component"]
    assert len(comps) >= 2 and got["fields"] == [f.name for f in comps]
    cols = source.read(project, set_id, None, [f.name for f in comps] + ["length"])
    want = dendrogram_chart([f.name for f in comps], [cols.number(f.name) for f in comps], cols.number("length"),
                            CorrSpec(), TreeSpec(), units=[f.unit for f in comps])  # fmt: skip
    assert got == {"set_id": set_id, **routes_charts._safe(want)}
    assert {"linkage", "order", "leaves", "icoord", "dcoord", "correlation", "pairs", "spec", "counts",
            "field_counts", "conventions", "groups", "link_groups", "group_count"} <= set(got)  # fmt: skip

    # two fields by any case, Spearman, no weight, a threshold, some holes
    body = dendro_body(set_id, fields=["au", "CU"], method="spearman", weight="none", cut=0.5, log=False,
                       holes=["DH-001", "DH-002", "DH-003", "DH-004"])  # fmt: skip
    r = c.post(f"{S}/dendrogram", json=body)
    assert r.status_code == 200, r.text
    keep = np.array([cols.holes[h] in body["holes"] for h in cols.hole_of()])
    want = dendrogram_chart(["AU", "CU"], [cols.number("AU")[keep], cols.number("CU")[keep]], None,
                            CorrSpec(method="spearman", log=False), TreeSpec(cut=0.5),
                            units=[next(f.unit for f in infos if f.name == n) for n in ("AU", "CU")])  # fmt: skip
    assert r.json() == {"set_id": set_id, **routes_charts._safe(want)}
    assert r.json()["group_count"] in (1, 2) and r.json()["spec"]["weighted"] is False

    # the saved one
    r = c.post(f"{API}/charts", json={"set_id": set_id, "kind": "dendrogram", "name": "Дендрограмма",
                                      "settings": dendro_body(set_id), "auto_name": True})  # fmt: skip
    saved = c.post(f"{S}/chart/{r.json()['id']}").json()
    assert saved["chart"]["kind"] == "dendrogram" and saved["correlation"] == got["correlation"]


def test_dendrogram_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo

    def fail(status: int = 422, **extra: Any) -> dict[str, Any]:
        r = c.post(f"{S}/dendrogram", json=dendro_body(set_id, **extra))
        assert r.status_code == status, r.text
        out: dict[str, Any] = r.json()
        assert out["message"] and out["hint"]
        return out

    assert fail(fields=["AU"])["param"] == "fields"
    assert fail(fields=["AU", "au"])["param"] == "fields"  # one field named twice
    out = fail(fields=["AU", "ZN"])
    assert out["code"] == "field_not_found" and out["param"] == "fields"
    assert fail(fields=["AU", "Номер пробы"])["code"] == "field_not_number"
    assert fail(linkage="ward")["param"] == "linkage"
    assert fail(distance="euclid")["param"] == "distance"
    assert fail(cut=-1)["param"] == "cut"
    assert fail(method="kendall")["param"] == "method"
    assert fail(missing="all")["param"] == "missing"
    assert fail(log="yes")["param"] == "log"
    assert fail(weight="field")["param"] == "weight_field"
    assert fail(fields="AU")["param"] == "fields"
    out = fail(holes=["нет такой"])
    assert out["code"] == "too_few_values" and out["param"] == "fields"
