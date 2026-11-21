"""The API of the contact analysis (ТЗ 8.3; ADR 0019 §4, §10; подшаг 13.17): `POST /stats/contact` answers what
`stats.contact.contact_chart` gives on the rows of the table, laid out as the window reads it
(`ui/src/modules/charts/apiMore.ts`): a domain of the same table and of another one (by the middle of a sample),
the saved chart, an open wireframe — 422 with the advice, the refusals."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_contact, source
from bf_core.stats.contact import contact_chart
from bf_core.stats.select import SampleFilter
from tests.test_import_job import API

S = f"{API}/stats"
ZONES = ("окисл", "перех", "перв")
ROCKS = ("гранит", "диорит")


@pytest.fixture()
def rich(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """The demo set, «Химия» (the keys of the samples; PB, ROCK — runs of 5 samples) and «Литология» (three
    intervals of ZONE a hole); the routes of the charts and of the contact (until `stats/router.py` has them)."""
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    if "/stats/histogram" not in paths:
        stats_router.router.include_router(routes_charts.router)
    if "/stats/contact" not in paths:
        stats_router.router.include_router(routes_contact.router)
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    set_id = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    project = projects_of(c.core).require_current()
    cols = source.read(project, set_id, None, ["AU"])
    holes = [cols.holes[h] for h in cols.hole_of()]
    n = cols.n
    rng = np.random.default_rng(3)
    pb = rng.lognormal(0.0, 1.0, n)
    pb[::17] = np.nan
    rock = [None if i % 23 == 0 else ROCKS[(i // 5) % 2] for i in range(n)]
    chem = dh.TableData(
        name="Химия", kind="interval", holes=holes, from_m=cols.from_m, to_m=cols.to_m,
        columns=[dh.ColumnSpec("PB", "number", "%", "component"), dh.ColumnSpec("ROCK", "text")],
        values=[pb.tolist(), rock],
    )  # fmt: skip
    dh.write_table(project.engine, set_id, chem, job_id=None, tick=lambda _p, _m: None)
    lh, lf, lt, lz = [], [], [], []
    for k, name in enumerate(cols.holes):
        a, b = cols.starts[k], cols.starts[k + 1]
        lo, hi = float(cols.from_m[a]), float(cols.to_m[b - 1])
        cuts = [lo, lo + (hi - lo) / 3.0, lo + 2.0 * (hi - lo) / 3.0, hi]
        for j in range(3):
            lh.append(name), lf.append(cuts[j]), lt.append(cuts[j + 1]), lz.append(ZONES[(k + j) % 3])
    litho = dh.TableData(name="Литология", kind="interval", holes=lh, from_m=lf, to_m=lt,
                         columns=[dh.ColumnSpec("ZONE", "text")], values=[lz])  # fmt: skip
    dh.write_table(project.engine, set_id, litho, job_id=None, tick=lambda _p, _m: None)
    return c, set_id, project


def body(set_id: int, **extra: Any) -> dict[str, Any]:
    return {"v": 1, "set_id": set_id, "table": "Химия", "field": "PB", "domain": "ROCK", "domain_table": None,
            "a": "гранит", "b": "диорит", "distance": 10, "step": 2, "wireframe_id": None, "weight": "length",
            "view": {"color": "#1f6bff"}, **extra}  # fmt: skip


def post(c: Any, b: dict[str, Any]) -> dict[str, Any]:
    r = c.post(f"{S}/contact", json=b)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_same_table_domain_is_the_kernel(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = rich
    got = post(c, body(set_id))
    cols = source.read(project, set_id, "Химия", ["PB", "ROCK", "length"])
    codes, labels = cols.text("ROCK")
    args = cols.select_args("PB")
    want = routes_charts._safe(contact_chart(cols.hole_of(), cols.from_m, cols.to_m, codes, labels, "гранит",
                                             "диорит", args["values"], SampleFilter(), length=args["length"],
                                             distance=10, step=2))  # fmt: skip
    assert (got["set_id"], got["field"], got["domain"], got["domain_table"], got["wireframe_id"]) == (
        set_id, "PB", "ROCK", None, None)  # fmt: skip
    for k in ("distance", "step", "weighted", "counts", "contacts", "holes", "edges", "mean", "n", "a", "b"):
        assert got[k] == want[k], k
    assert got["contacts"] > 0 and len(got["edges"]) == 2 * 5 + 1 and len(got["mean"]) == len(got["n"]) == 10
    assert got["a"]["label"] == "гранит" and got["b"]["label"] == "диорит"
    assert all((m is None) == (k == 0) for m, k in zip(got["mean"], got["n"], strict=True))  # empty — null
    # B → A is the mirror
    back = post(c, body(set_id, a="диорит", b="гранит"))
    assert back["n"] == got["n"][::-1] and back["contacts"] == got["contacts"]
    # the saved chart computes the same
    r = c.post(f"{API}/charts", json={"set_id": set_id, "kind": "contact", "name": "Контакт",
                                      "settings": body(set_id)})  # fmt: skip
    assert r.status_code == 200, r.text
    saved = c.post(f"{S}/chart/{r.json()['id']}").json()
    assert saved["n"] == got["n"] and saved["chart"]["kind"] == "contact"


def test_domain_of_another_table(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, project = rich
    got = post(c, body(set_id, domain="ZONE", domain_table="Литология", a="окисл", b="перех", distance=40, step=5))
    assert got["domain"] == "ZONE" and got["domain_table"] == "Литология"
    cols = source.read(project, set_id, "Химия", ["PB", "length"])
    found = source.domain_at(cols, source.read(project, set_id, "Литология", ["ZONE"]), "ZONE")
    args = cols.select_args("PB")
    want = routes_charts._safe(contact_chart(cols.hole_of(), cols.from_m, cols.to_m, found.codes, found.labels,
                                             "окисл", "перех", args["values"], SampleFilter(), length=args["length"],
                                             distance=40, step=5))  # fmt: skip
    assert got["domain_outside"] == found.outside
    for k in ("counts", "contacts", "holes", "mean", "n", "a", "b"):
        assert got[k] == want[k], k
    assert got["contacts"] > 0
    # part of the holes
    some = post(c, body(set_id, domain="ZONE", domain_table="Литология", a="окисл", b="перех", distance=40, step=5,
                        holes=cols.holes[:5]))  # fmt: skip
    assert 0 < some["holes"] <= 5 and some["counts"]["rows"] < got["counts"]["rows"]


def test_wireframes_and_refusals(rich) -> None:  # type: ignore[no-untyped-def]
    c, set_id, _project = rich
    wf = {w["name"]: w["id"] for w in c.get(f"{API}/wireframes").json()["wireframes"]}
    r = c.post(f"{S}/contact", json=body(set_id, wireframe_id=wf["Разлом"]))
    assert r.status_code == 422 and r.json()["code"] == "wireframe_open", r.text
    assert r.json()["param"] == "wireframe_id" and "Исправить каркас" in r.json()["hint"]
    closed = wf["Рудное тело 1"]
    r = c.post(f"{S}/contact", json=body(set_id, wireframe_id=closed))
    assert r.status_code == 422 and r.json()["code"] == "contact_wireframe_unavailable", (wf, r.text)
    r = c.post(f"{S}/contact", json=body(set_id, wireframe_id=999_999))
    assert r.status_code == 404 and r.json()["code"] == "wireframe_not_found"
    for extra, code, param in (
        ({"a": "гранит", "b": "гранит"}, "bad_param", "b"),
        ({"a": "базальт"}, "bad_param", "a"),
        ({"a": None}, "bad_param", "a"),
        ({"domain": None}, "bad_param", "domain"),
        ({"domain": "PB"}, "field_not_text", "domain"),
        ({"field": "ROCK"}, "field_not_number", "field"),
        ({"step": 0}, "bad_param", "step"),
        ({"distance": 1000, "step": 1}, "bad_param", "step"),
        ({"domain_table": "Нет такой"}, "table_not_found", None),
    ):
        r = c.post(f"{S}/contact", json=body(set_id, **extra))
        assert r.status_code in (404, 422) and r.json()["code"] == code, (extra, r.text)
        assert r.json().get("param") == param, (extra, r.json())
        assert r.json()["message"] and r.json()["hint"]
