"""The API of the charts of ТЗ 8.3 (ADR 0019 §10, подшаг 13.16): `POST /stats/scatter`, `/box`, `/probplot`,
`/domains`, `/ternary` answer what the kernels give on the same rows, laid out as the windows read them
(`ui/src/modules/charts/apiMore.ts`): a domain of the same and of another table, Y of another table by the sample
key, a colour field, 200 001 points → the density, the saved charts, the refusals."""

from __future__ import annotations

import math
import subprocess
import sys
from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_more, source
from bf_core.stats.select import SampleFilter, select_sample
from tests.test_import_job import API

S = f"{API}/stats"
ROCKS = ("гранит", "диорит", "сланец")
ZONES = ("окисл", "перех", "перв")


@pytest.fixture()
def more_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: the stats router has these routes (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    if "/stats/histogram" not in paths:
        stats_router.router.include_router(routes_charts.router)
    if "/stats/scatter" not in paths:
        stats_router.router.include_router(routes_more.router)


def noop(_p: float | None, _m: str | None) -> None:
    pass


@pytest.fixture()
def rich(more_routes, pclient):  # type: ignore[no-untyped-def]
    """The demo set and two more interval tables beside «Пробы» (a set has one assay table): «Химия» (the keys of
    the samples but every seventh; PB, ZN, FE, ROCK, DECL_W) and «Литология» (three intervals of ZONE per hole)."""
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    project = projects_of(c.core).require_current()
    cols = source.read(project, set_id, None, ["AU"])
    keep = np.arange(cols.n) % 7 != 3
    holes = [cols.holes[h] for h in cols.hole_of()]
    rng = np.random.default_rng(5)
    n = int(keep.sum())
    pb = rng.lognormal(0.0, 1.0, n)
    pb[::11] = np.nan
    zn = rng.lognormal(1.0, 0.5, n)
    zn[::13] = -0.05
    rock = [None if i % 9 == 0 else ROCKS[i % 3] for i in range(n)]
    chem = dh.TableData(
        name="Химия", kind="interval", holes=[h for h, k in zip(holes, keep, strict=True) if k],
        from_m=cols.from_m[keep], to_m=cols.to_m[keep],
        columns=[dh.ColumnSpec("PB", "number", "%", "component"), dh.ColumnSpec("ZN", "number", "%", "component"),
                 dh.ColumnSpec("FE", "number", "%", "component"), dh.ColumnSpec("ROCK", "text"),
                 dh.ColumnSpec("DECL_W", "number")],
        values=[pb.tolist(), zn.tolist(), rng.uniform(1.0, 30.0, n).tolist(), rock, rng.uniform(0.2, 2.0, n).tolist()],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, chem, job_id=None, tick=noop)
    lh, lf, lt, lz = [], [], [], []
    for k, name in enumerate(cols.holes):
        a, b = cols.starts[k], cols.starts[k + 1]
        lo, hi = float(cols.from_m[a]), float(cols.to_m[b - 1])
        cuts = [lo, lo + (hi - lo) / 3.0, lo + 2.0 * (hi - lo) / 3.0, hi]
        for j in range(3):
            lh.append(name), lf.append(cuts[j]), lt.append(cuts[j + 1]), lz.append(ZONES[(k + j) % 3])
    litho = dh.TableData(name="Литология", kind="interval", holes=lh, from_m=lf, to_m=lt,
                         columns=[dh.ColumnSpec("ZONE", "text")], values=[lz])  # fmt: skip
    dh.write_table(project.engine, set_id, litho, job_id=None, tick=noop)
    return c, set_id, project


def safe(x: Any) -> Any:
    return routes_charts._safe(x)


def by_domain_body(set_id: int, **extra: Any) -> dict[str, Any]:
    return {"v": 1, "set_id": set_id, "table": "Химия", "field": "PB", "domain": "ROCK", "domain_table": None,
            "weight": "length", "weight_field": None, "negatives": "keep", "exclude_zero": False, "bottom_cut": None,
            "bottom_mode": "exclude", "top_cut": None, "top_mode": "cap", "view": {"color": "#1f6bff"},
            **extra}  # fmt: skip


def chem_side(project: Any, set_id: int, field: str, flt: SampleFilter, *more: str) -> tuple[Any, Any]:
    cols = source.read(project, set_id, "Химия", [field, "length", *more])
    return cols, select_sample(**cols.select_args(field), flt=flt)


def test_box_same_table_domain_is_the_kernel(rich) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.boxplot import box_chart

    c, set_id, project = rich
    r = c.post(f"{S}/box", json=by_domain_body(set_id, log=True))
    assert r.status_code == 200, r.text
    got = r.json()
    cols, sel = chem_side(project, set_id, "PB", SampleFilter(), "ROCK")
    codes, labels = cols.text("ROCK")
    want = safe(box_chart(sel, codes, labels, log=True))
    assert got["set_id"] == set_id and got["field"] == "PB" and got["domain"] == "ROCK"
    assert got["domain_table"] is None and got["domain_outside"] == 0
    assert got["log"] is True and got["weighted"] is True and got["counts"] == want["counts"]
    assert [g["label"] for g in got["groups"]] == [*ROCKS, None]  # the rows without a domain — last
    assert got["groups"][-1]["role"] == "none"
    for g, b in zip(got["groups"], want["boxes"], strict=True):
        assert g["mean"] == b["arithmetic_mean"] and g["geometric_mean"] == b["mean"]
        assert (g["n"], g["q1"], g["median"], g["q3"], g["outliers"]) == (b["count"], b["q1"], b["median"], b["q3"],
                                                                         b["outliers"])  # fmt: skip
        assert g["outliers_total"] == b["outliers_low"] + b["outliers_high"]
    assert got["outliers_total"] == want["outliers"]["total"] and got["outliers_shown"] == want["outliers"]["shown"]
    assert sum(g["n"] for g in got["groups"]) == got["counts"]["used"]
    plain = c.post(f"{S}/box", json=by_domain_body(set_id, domain=None, log=False)).json()
    assert [g["label"] for g in plain["groups"]] == [None] and plain["groups"][0]["role"] == "all"
    assert plain["groups"][0]["geometric_mean"] is None


def test_domain_of_another_table(rich) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.domain_stats import domain_stats

    c, set_id, project = rich
    body = by_domain_body(set_id, table="Пробы", field="AU", domain="zone", domain_table="Литология")
    r = c.post(f"{S}/domains", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    cols = source.read(project, set_id, "Пробы", ["AU", "length"])
    sel = select_sample(**cols.select_args("AU"), flt=SampleFilter())
    dom = source.domain_at(cols, source.read(project, set_id, "Литология", ["ZONE"]), "ZONE")
    want = safe(domain_stats(sel, dom.codes, dom.labels, length=cols.number("length")))
    assert got["domain"] == "ZONE" and got["domain_table"] == "Литология" and got["domain_outside"] == dom.outside
    assert got["counts"] == want["counts"] and got["decl_field"] is None and got["decl_hint"] == want["notes"][0]
    assert [row["label"] for row in got["rows"]] == [x["domain"] for x in want["rows"]]
    for row, x in zip([*got["rows"], got["total"]], [*want["rows"], want["total"]], strict=True):
        assert row["n"] == x["count"] and row["decl_mean"] is None
        assert {k: row[k] for k in ("length", "mean", "median", "std", "cv", "min", "max", "p10", "p50", "p90")} == {
            k: x[k] for k in ("length", "mean", "median", "std", "cv", "min", "max", "p10", "p50", "p90")
        }
    assert got["total"]["n"] == sum(row["n"] for row in got["rows"]) == got["counts"]["used"]  # Σ n = итог
    assert set(got["rows"][0]) >= {"label", "n", "length", "mean", "decl_mean", "median", "std", "cv", "min", "max",
                                   "p10", "p50", "p90"}  # fmt: skip

    # kept beside the cached columns; the other table replaced — a miss
    assert c.post(f"{S}/domains", json=body).json() == got
    lc = source.read(project, set_id, "Литология", ["ZONE"])
    one = dh.TableData(name="Литология", kind="interval", holes=[lc.holes[h] for h in lc.hole_of()],
                       from_m=lc.from_m, to_m=lc.to_m, columns=[dh.ColumnSpec("ZONE", "text")],
                       values=[["перв"] * lc.n])  # fmt: skip
    dh.write_table(project.engine, set_id, one, job_id=None, tick=noop)
    again = c.post(f"{S}/domains", json=body).json()
    assert [row["label"] for row in again["rows"]] == ["перв"] + ([None] if again["counts"]["no_domain"] else [])

    # the field DECL_W of the table gives the declustered means
    r = c.post(f"{S}/domains", json=by_domain_body(set_id, weight="none"))
    assert r.status_code == 200, r.text
    assert r.json()["decl_field"] == "DECL_W" and r.json()["decl_hint"] is None
    assert r.json()["total"]["decl_mean"] is not None and r.json()["weighted"] is False


def test_probplot_is_the_kernel(rich) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.probplot import probplot_chart

    c, set_id, project = rich
    body = by_domain_body(set_id, field="ZN", negatives="exclude")
    r = c.post(f"{S}/probplot", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    cols, sel = chem_side(project, set_id, "ZN", SampleFilter(negatives="exclude"), "ROCK")
    codes, labels = cols.text("ROCK")
    want = safe(probplot_chart(sel, codes, labels))
    assert got["counts"] == want["counts"] and got["weighted"] is True and got["counts"]["negative"] > 0
    assert len(got["series"]) == len(want["lines"])
    for s, line in zip(got["series"], want["lines"], strict=True):
        assert s["label"] == line["domain"] and s["count"] == line["count"]
        assert s["points"] == [[p[0], p[2]] for p in line["points"]]
        assert s["stats"]["median"] is not None and s["stats"]["mean"] > 0 and s["stats"]["p90"] >= s["stats"]["median"]


def test_scatter_points_y_of_another_table_and_colour(rich) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.scatter import scatter_chart

    c, set_id, project = rich
    body = {"v": 1, "set_id": set_id, "table": "Пробы", "x": "AU", "y": "zn", "y_table": "Химия", "color": None,
            "log_x": False, "log_y": True, "weight": "length", "weight_field": None, "negatives": "half_abs",
            "exclude_zero": True, "regression": "ols", "view": {"diagonal": True, "color": "#1f6bff"}}  # fmt: skip
    r = c.post(f"{S}/scatter", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    cols = source.read(project, set_id, "Пробы", ["AU", "length"])
    pr = source.paired(cols, source.read(project, set_id, "Химия", ["ZN"]), "ZN")
    x, y = cols.number("AU"), pr.values
    ok = np.isfinite(x) & np.isfinite(y)
    xx, yy = np.where(x < 0, -x / 2, x), np.where(y < 0, -y / 2, y)
    ok &= (xx != 0) & (yy != 0)
    want = safe(scatter_chart(np.where(ok, xx, np.nan), np.where(ok, yy, np.nan), cols.number("length"),
                              log_y=True, row_ids=cols.row_ids))  # fmt: skip
    k = got["counts"]
    assert k["unmatched"] > 0 and k["negative"] > 0 and k["missing_x"] > 0
    assert k["rows"] == cols.n and k["used"] == want["counts"]["used"] == got["n"]
    assert k["nonpositive_log"] == want["counts"]["nonpositive"]
    assert k["missing_x"] + k["missing_y"] + k["unmatched"] + k["bad_weight"] + k["zero_excluded"] + \
        k["nonpositive_log"] + k["used"] == k["rows"]  # fmt: skip
    assert got["mode"] == "points" and got["x"] == want["points"]["x"] and got["y"] == want["points"]["y"]
    assert got["row"] == want["points"]["row"] and got["c"] is None and got["color"] is None
    st = want["stats"]
    assert got["fits"] == {"ols": st["ols"], "rma": st["rma"], "weighted": {k: st["weighted"][k] for k in "ab"}}
    assert got["pearson"] == st["pearson"] and got["spearman"] == st["spearman"]
    assert got["x_stats"] == {**st["x"], "min": want["range"]["x"][0], "max": want["range"]["x"][1]}
    assert (got["x_field"], got["y_field"], got["y_set_id"], got["log_y"]) == ("AU", "ZN", set_id, True)

    # the colour of a text field: the index into labels, null — empty; of a number — min, max
    body = {**body, "table": "Химия", "x": "PB", "y": None, "y_table": None, "color": "rock", "log_y": False}
    got = c.post(f"{S}/scatter", json=body).json()
    assert got["y_field"] == "ZN" and got["counts"]["unmatched"] == 0 and got["color_field"] == "ROCK"
    assert got["color"] == {"kind": "text", "labels": list(sorted(ROCKS))}
    assert None in got["c"] and set(got["c"]) - {None} <= {0, 1, 2}
    got = c.post(f"{S}/scatter", json={**body, "color": "FE", "weight": "none"}).json()
    assert got["color"]["kind"] == "number" and got["color"]["min"] == min(got["c"]) and got["fits"]["weighted"] is None


def test_scatter_of_200001_points_is_a_density(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = rich
    n = 200_001
    big = dh.TableData(
        name="Много", kind="interval", holes=[f"M-{i // 1000:03d}" for i in range(n)],
        from_m=np.arange(n, dtype=np.float64) % 1000, to_m=np.arange(n, dtype=np.float64) % 1000 + 1.0,
        columns=[dh.ColumnSpec("X", "number", None, "component"), dh.ColumnSpec("Y", "number", None, "component")],
        values=[np.arange(n, dtype=np.float64).tolist(), (2.0 * np.arange(n) + 1.0).tolist()],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, big, job_id=None, tick=noop)
    body = {"v": 1, "set_id": set_id, "table": "Много", "x": None, "y": None, "weight": "none"}
    r = c.post(f"{S}/scatter", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["mode"] == "density" and "x" not in got and got["c"] is None
    d = got["density"]
    assert (d["nx"], d["ny"], d["x"], d["y"]) == (100, 100, [0.0, 200_000.0], [1.0, 400_001.0])
    assert len(d["counts"]) == 100 * 100 and sum(d["counts"]) == n and d["max"] == max(d["counts"])
    # the diagonal from the bottom left: counts[iy · nx + ix]
    assert d["counts"][0] > 0 and d["counts"][99 * 100 + 99] > 0 and d["counts"][99] == 0
    assert math.isclose(got["fits"]["ols"]["b"], 2.0, rel_tol=1e-12) and math.isclose(got["fits"]["ols"]["a"], 1.0)
    one_less = c.post(f"{S}/scatter", json={**body, "holes": [f"M-{i:03d}" for i in range(200)]}).json()
    assert one_less["mode"] == "points" and len(one_less["x"]) == 200_000

    t = c.post(f"{S}/ternary", json={"v": 1, "set_id": set_id, "table": "Много", "a": "X", "b": "Y", "c": "length",
                                     "color": None})  # fmt: skip
    assert t.status_code == 200, t.text
    tern = t.json()
    assert tern["total"] == n and len(tern["a"]) == 200_000  # evenly thinned
    assert tern["counts"]["used"] == tern["total"]


def test_ternary_is_the_kernel(rich) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.ternary import shares

    c, set_id, project = rich
    body = {"v": 1, "set_id": set_id, "table": "Химия", "a": None, "b": None, "c": None, "color": "ROCK",
            "view": {"color": "#000"}}  # fmt: skip
    r = c.post(f"{S}/ternary", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    cols = source.read(project, set_id, "Химия", ["PB", "ZN", "FE", "ROCK"])
    s = shares(cols.number("PB"), cols.number("ZN"), cols.number("FE"))
    assert got["fields"] == ["PB", "ZN", "FE"] and got["color_field"] == "ROCK"
    assert got["a"] == s.a.tolist() and got["c"] == s.c.tolist() and got["row"] == cols.row_ids[s.rows].tolist()
    k = s.counts
    assert got["counts"] == {"rows": k["rows"], "missing": k["missing"], "used": k["used"],
                             "nonpositive_sum": k["negative"] + k["zero_sum"], "negative": k["negative"],
                             "zero_sum": k["zero_sum"]}  # fmt: skip
    assert got["total"] == len(got["a"]) == k["used"] and got["counts"]["negative"] > 0
    codes = cols.text("ROCK")[0][s.rows]
    assert got["color_values"] == [None if v < 0 else int(v) for v in codes]
    assert got["color"] == {"kind": "text", "labels": sorted(ROCKS)}


def test_saved_charts_of_8_3(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _ = rich
    bodies = {
        "scatter": {"v": 1, "set_id": set_id, "table": "Химия", "x": "PB", "y": "ZN", "weight": "none"},
        "box": by_domain_body(set_id, log=False),
        "probplot": by_domain_body(set_id),
        "domains": by_domain_body(set_id, domain="ZONE", domain_table="Литология"),
        "ternary": {"v": 1, "set_id": set_id, "table": "Химия", "a": "PB", "b": "ZN", "c": "FE"},
    }
    for kind, settings in bodies.items():
        r = c.post(f"{API}/charts", json={"set_id": set_id, "kind": kind, "name": kind, "settings": settings,
                                          "auto_name": False})  # fmt: skip
        assert r.status_code == 200, r.text
        saved = c.post(f"{S}/chart/{r.json()['id']}")
        assert saved.status_code == 200, saved.text
        assert saved.json()["chart"]["kind"] == kind
        direct = c.post(f"{S}/{kind}", json=settings).json()
        assert {k: v for k, v in saved.json().items() if k != "chart"} == direct


def test_refusals(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _ = rich

    def fail(kind: str, body: dict[str, Any], status: int = 422) -> dict[str, Any]:
        r = c.post(f"{S}/{kind}", json=body)
        assert r.status_code == status, r.text
        out: dict[str, Any] = r.json()
        assert out["message"] and out["hint"]
        return out

    box = by_domain_body(set_id)
    assert fail("box", {**box, "log": "yes"})["param"] == "log"
    out = fail("box", {**box, "domain": "PB"})
    assert out["code"] == "field_not_text" and out["param"] == "domain"
    out = fail("box", {**box, "domain": "ZONE"})
    assert out["code"] == "field_not_found" and out["param"] == "domain"
    assert fail("domains", {**box, "domain_table": "Нет"}, 404)["code"] == "table_not_found"
    assert fail("probplot", {**box, "field": "ROCK"})["code"] == "field_not_number"
    out = fail("probplot", {**box, "bottom_cut": 1e9})
    assert out["code"] == "no_values"
    out = fail("probplot", {**box, "field": "ZN", "negatives": "exclude", "top_cut": 0.0, "top_mode": "cap",
                            "bottom_cut": -1.0})  # fmt: skip
    assert out["code"] == "no_positive"
    out = fail("box", {**box, "field": "ZN", "log": True, "top_cut": 0.0, "bottom_cut": -1.0})
    assert out["code"] == "no_positive" and out["param"] == "log"
    sc = {"v": 1, "set_id": set_id, "table": "Химия", "x": "PB", "y": "ZN"}
    assert fail("scatter", {**sc, "regression": "loess"})["param"] == "regression"
    assert fail("scatter", {**sc, "log_x": 1})["param"] == "log_x"
    assert fail("scatter", {**sc, "color": "НЕТ"})["param"] == "color"
    assert fail("scatter", {**sc, "y": "ROCK"})["param"] == "y"
    assert fail("scatter", {**sc, "y_set_id": 10**9}, 404)["code"] == "set_not_found"
    assert fail("scatter", {**sc, "y_set_id": "1"})["param"] == "y_set_id"
    assert fail("scatter", {**sc, "holes": ["нет"]})["code"] == "no_values"
    assert fail("scatter", {**sc, "color": "Номер пробы", "table": "Пробы", "x": "AU", "y": "CU"})["code"] == \
        "too_many_domains"  # fmt: skip
    tr = {"v": 1, "set_id": set_id, "table": "Пробы", "a": None, "b": None, "c": None}
    assert fail("ternary", tr)["param"] == "c"  # «Пробы» has two numeric fields but `length`
    assert fail("ternary", {**tr, "c": "length", "holes": ["нет"]})["code"] == "no_values"
    assert fail("ternary", {**tr, "set_id": None})["param"] == "set_id"


def test_routes_import_nothing_heavy() -> None:
    code = (
        "import sys; import bf_core.stats.routes_more;"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with the routes: {r.stdout.strip()}"
