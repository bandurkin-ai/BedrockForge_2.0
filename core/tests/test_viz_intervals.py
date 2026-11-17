"""Intervals of parts, field statistics and class counts for 3D displays (ADR 0007 §1): empty values stay
NaN (never 0), text comes as codes into the dictionary of the part, events have no «до», quantiles and
histograms by all rows, a value equal to a bound belongs to the upper class."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.viz import binary
from tests.test_import_job import API
from tests.test_viz_holes import manifest, open_demo

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    return open_demo(pclient)


def _all_rows(c, set_id: int, table: str) -> tuple[list[dict], dict[str, str]]:  # type: ignore[type-arg]
    """Every row of a table through the «Таблица» API (the reference) and column name → key."""
    rows: list[dict] = []  # type: ignore[type-arg]
    while True:
        page = c.get(
            f"{API}/drillholes/sets/{set_id}/tables/{table}", params={"offset": len(rows), "limit": 1000}
        ).json()
        rows += page["rows"]
        if len(rows) >= page["total"]:
            return rows, {col["name"]: col["key"] for col in page["columns"]}


def _intervals(c, set_id: int, k: int = 0, **params):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/viz/sets/{set_id}/intervals/{k}", params=params)
    assert r.status_code == 200, r.text
    return r, *binary.decode(r.content)


def _num(v: object) -> float:
    return math.nan if v is None else float(v)  # type: ignore[arg-type]


def test_demo_intervals_nan_codes_and_order(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    head, holes = manifest(c, set_id)
    tables = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    assay = next(t for t in tables if t["name"] == "Пробы")
    ref, keys = _all_rows(c, set_id, assay["table"])
    r, ih, a = _intervals(c, set_id, table="пробы", fields="AU,CU,Номер пробы")
    assert r.headers["cache-control"] == "no-cache" and ih["kind"] == "intervals" and ih["rows"] == len(ref)
    assert set(a) == {"slot", "from", "to", "v:AU", "v:CU", "c:Номер пробы"}
    assert a["slot"].dtype == np.dtype("<u2") and a["v:AU"].dtype == np.dtype("<f8")
    assert a["c:Номер пробы"].dtype == np.dtype("<i4")
    assert [f["name"] for f in ih["fields"]] == ["AU", "CU", "Номер пробы"] and ih["fields"][0]["unit"] == "г/т"
    # the order: slot, then «от»
    order = np.lexsort((a["from"], a["slot"]))
    np.testing.assert_array_equal(order, np.arange(len(order)))
    # the same rows as the table, value for value: NaN stays NaN
    slot_of = {n: i for i, n in enumerate(head["names"])}
    cats = ih["cats"]["Номер пробы"]
    assert cats == sorted(cats) and len(set(cats)) == len(cats)
    expect = sorted(
        (slot_of[x["hole"]], x["from"], _num(x["to"]), _num(x[keys["AU"]]), _num(x[keys["CU"]]), x[keys["Номер пробы"]])
        for x in ref
    )
    got = sorted(
        (int(s), f, t, au, cu, None if k < 0 else cats[k])
        for s, f, t, au, cu, k in zip(
            *(a[n].tolist() for n in ("slot", "from", "to", "v:AU", "v:CU", "c:Номер пробы")), strict=True
        )
    )
    assert len(got) == len(expect)
    for g, e in zip(got, expect, strict=True):
        assert g[:3] == e[:3] and g[5] == e[5]
        for x, y in zip(g[3:5], e[3:5], strict=True):
            assert (math.isnan(x) and math.isnan(y)) or x == y
    assert np.isnan(a["v:AU"]).sum() == 1 and np.isnan(a["v:CU"]).sum() == 1  # DH-031 AU, DH-012 CU
    assert (a["v:AU"] == 0).sum() == 0 and (a["v:AU"] < 0).sum() == 1  # the negative grade is kept

    # an emptied sample number is −1; an edited depth reorders the rows; a new ETag
    tag = r.headers["etag"]
    row = next(x for x in ref if x["hole"] == "DH-001" and x["from"] == 4.0)
    base = f"{API}/drillholes/sets/{set_id}/tables/{assay['table']}/rows/{row['id']}"
    assert c.patch(base, json={"column": keys["Номер пробы"], "value": ""}).status_code == 200
    assert c.patch(base, json={"column": keys["AU"], "value": ""}).status_code == 200
    r2, ih2, a2 = _intervals(c, set_id, table="Пробы", fields="AU,Номер пробы")
    assert r2.headers["etag"] != tag
    hit = np.flatnonzero((a2["slot"] == slot_of["DH-001"]) & (a2["from"] == 4.0))
    assert len(hit) == 1 and a2["c:Номер пробы"][hit[0]] == -1 and math.isnan(a2["v:AU"][hit[0]])
    assert "" not in ih2["cats"]["Номер пробы"]
    assert c.patch(base, json={"column": "to", "value": "302"}).status_code == 200
    assert c.patch(base, json={"column": "from", "value": "300"}).status_code == 200
    _r3, _h3, a3 = _intervals(c, set_id, table="Пробы", fields="AU")
    mine = a3["from"][a3["slot"] == slot_of["DH-001"]]
    assert mine[-1] == 300.0 and np.all(np.diff(mine) >= 0)


def test_events_have_no_end_and_keep_nan(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.store import ColumnSpec, TableData, write_table

    c, set_id = demo
    project = c.core.services["projects"].require_current()
    data = TableData(
        "Структуры",
        "event",
        ["DH-001", "DH-001", "DH-002", "DH-047"],
        np.array([30.0, 5.0, 12.5, np.nan]),
        np.array([np.nan] * 4),
        [ColumnSpec("Тип", "text"), ColumnSpec("Угол", "number")],
        [["трещина", None, "жила", "жила"], [45.0, np.nan, 0.0, 10.0]],
    )
    write_table(project.engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    head, _holes = manifest(c, set_id)
    _r, ih, a = _intervals(c, set_id, table="структуры", fields="Угол,Тип")
    assert ih["table"]["kind"] == "event" and ih["rows"] == 3  # the row without a depth is left out
    assert np.isnan(a["to"]).all()
    s1, s2 = head["names"].index("DH-001"), head["names"].index("DH-002")
    assert a["slot"].tolist() == sorted([s1, s1, s2]) and a["from"][a["slot"] == s1].tolist() == [5.0, 30.0]
    by = {(int(s), f): i for i, (s, f) in enumerate(zip(a["slot"].tolist(), a["from"].tolist(), strict=True))}
    assert math.isnan(a["v:Угол"][by[(s1, 5.0)]]) and a["v:Угол"][by[(s2, 12.5)]] == 0.0  # 0 is a value
    assert ih["cats"]["Тип"] == ["жила", "трещина"]
    assert a["c:Тип"][by[(s1, 5.0)]] == -1 and a["c:Тип"][by[(s1, 30.0)]] == 1
    st = c.get(f"{API}/viz/sets/{set_id}/field-stats", params={"table": "Структуры", "field": "Тип"}).json()
    assert st["categories"] == [
        {"value": "жила", "count": 2, "length": None},
        {"value": "трещина", "count": 1, "length": None},
    ]
    cc = c.post(f"{API}/viz/sets/{set_id}/class-counts", json={"table": "Структуры", "field": "Угол", "bounds": [10]})
    assert cc.json() == {"counts": [1, 2], "lengths": None, "missing": 1, "rows": 4}


def test_field_stats_reference(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    tables = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    assay = next(t for t in tables if t["name"] == "Пробы")
    ref, keys = _all_rows(c, set_id, assay["table"])
    au = np.array([_num(x[keys["AU"]]) for x in ref])
    present = au[~np.isnan(au)]
    st = c.get(f"{API}/viz/sets/{set_id}/field-stats", params={"table": "пробы", "field": "au"}).json()
    assert st["kind"] == "number" and st["field"] == "AU" and st["unit"] == "г/т" and st["rows"] == len(ref)
    assert st["count"] == len(present) and st["missing"] == 1
    assert st["min"] == present.min() == -1.0 and st["max"] == present.max()
    for name, q in (("p02", 2), ("p50", 50), ("p98", 98), ("p99", 99)):
        assert st[name] == pytest.approx(np.percentile(present, q), rel=1e-12, abs=1e-12)
    lin = st["hist"]
    assert len(lin["counts"]) == 48 and sum(lin["counts"]) == len(present) and lin["lo"] == -1.0
    edges = np.linspace(lin["lo"], lin["hi"], 49)
    np.testing.assert_array_equal(np.histogram(present, bins=edges)[0], lin["counts"])
    log = st["hist_log"]
    assert len(log["counts"]) == 48 and sum(log["counts"]) == len(present) - 1 and st["nonpositive"] == 1
    assert log["lo"] == pytest.approx(math.log10(present[present > 0].min()))

    text = c.get(f"{API}/viz/sets/{set_id}/field-stats", params={"table": "Пробы", "field": "Номер пробы"}).json()
    assert text["kind"] == "text" and text["count"] == len(ref) and text["missing"] == 0
    assert text["unique"] == len({x[keys["Номер пробы"]] for x in ref}) > 1000 and text["truncated"] is True
    assert len(text["categories"]) == 1000
    top = text["categories"][0]
    assert top["value"] == "DH-041-009" and top["count"] == 2 and top["length"] == 4.0  # the repeated number
    assert [x["count"] for x in text["categories"]] == sorted((x["count"] for x in text["categories"]), reverse=True)


def test_class_counts_reference(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    tables = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    assay = next(t for t in tables if t["name"] == "Пробы")
    ref, keys = _all_rows(c, set_id, assay["table"])
    au = np.array([_num(x[keys["AU"]]) for x in ref])
    length = np.array([x["to"] - x["from"] for x in ref])
    bounds = [0.1, 0.5, 0.5, 1.0, float(np.nanmax(au))]  # a value equal to a bound goes up; an empty class
    r = c.post(f"{API}/viz/sets/{set_id}/class-counts", json={"table": "Пробы", "field": "AU", "bounds": bounds})
    assert r.status_code == 200, r.text
    out = r.json()
    edges = [-math.inf, *bounds, math.inf]
    want = [int(((au >= lo) & (au < hi)).sum()) for lo, hi in zip(edges, edges[1:], strict=False)]
    want_len = [float(length[(au >= lo) & (au < hi)].sum()) for lo, hi in zip(edges, edges[1:], strict=False)]
    assert out["counts"] == want and out["counts"][2] == 0 and out["counts"][-1] >= 1 and out["missing"] == 1
    assert out["lengths"] == pytest.approx(want_len)
    assert sum(out["counts"]) + out["missing"] == out["rows"] == len(ref)


def test_errors_and_304(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    url = f"{API}/viz/sets/{set_id}/intervals/0"
    r = c.get(url, params={"table": "Пробы", "fields": "AU"})
    hit = c.get(url, params={"table": "Пробы", "fields": "AU"}, headers={"If-None-Match": r.headers["etag"]})
    assert hit.status_code == 304 and hit.content == b""
    other = c.get(url, params={"table": "Пробы", "fields": "CU"}, headers={"If-None-Match": r.headers["etag"]})
    assert other.status_code == 200
    many = ",".join(["AU", "CU"] + [f"F{i}" for i in range(15)])
    r = c.get(url, params={"table": "Пробы", "fields": many})
    assert r.status_code == 422 and r.json()["code"] == "too_many_fields"
    r = c.get(url, params={"table": "Пробы", "fields": "AG"})
    assert r.status_code == 404 and r.json()["code"] == "field_not_found" and "«AG»" in r.json()["message"]
    r = c.get(url, params={"table": "Литология", "fields": "AU"})
    assert r.status_code == 404 and r.json()["code"] == "table_not_found"
    assert c.get(f"{API}/viz/sets/{set_id}/intervals/3", params={"table": "Пробы"}).json()["code"] == "part_not_found"
    body = {"table": "Пробы", "field": "AU", "bounds": [1, 0.5]}
    r = c.post(f"{API}/viz/sets/{set_id}/class-counts", json=body)
    assert r.status_code == 422 and r.json()["code"] == "bad_bounds"
    r = c.post(f"{API}/viz/sets/{set_id}/class-counts", json={**body, "field": "Номер пробы", "bounds": [1]})
    assert r.status_code == 422 and r.json()["code"] == "not_number"
    r = c.get(f"{API}/viz/sets/{set_id}/field-stats", params={"table": "Пробы", "field": "AG"})
    assert r.status_code == 404 and r.json()["code"] == "field_not_found"


def test_same_answer_from_the_parquet_snapshot(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A reopened project reads the snapshot column by column from its Parquet file: the same rows, NaN, codes."""
    from bf_core.drillholes import snapshot

    c, set_id = demo
    monkeypatch.setattr(snapshot, "PARQUET_FROM", 1)
    snapshot.snapshots.forget()
    params = {"table": "Пробы", "fields": "AU,Номер пробы,CU"}
    first = c.get(f"{API}/viz/sets/{set_id}/intervals/0", params=params).content
    project = c.core.services["projects"].require_current()
    assert list((project.folder / "cache" / "tables").glob("*.parquet"))
    snapshot.snapshots.forget()
    again = c.get(f"{API}/viz/sets/{set_id}/intervals/0", params=params).content
    assert again == first
    _h, a = binary.decode(again)
    assert np.isnan(a["v:AU"]).sum() == 1 and (a["c:Номер пробы"] >= 0).all()
