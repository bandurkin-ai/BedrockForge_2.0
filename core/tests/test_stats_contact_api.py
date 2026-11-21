"""The API of the contact analysis (ТЗ 8.3; ADR 0019 §4, §10; подшаг 13.17): `POST /stats/contact` answers what
`stats.contact.contact_chart` gives on the rows of the table, laid out as the window reads it
(`ui/src/modules/charts/apiMore.ts`): a domain of the same table and of another one (by the middle of a sample),
the saved chart, the contact «до каркаса» (13.8: a closed body of the demo against the kernel, a small cube and an
inclined hole counted by hand), an open wireframe — 422 with the advice, the refusals."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_surveys, objects
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import routes_charts, routes_contact, source
from bf_core.stats.contact import contact_chart, contact_chart_wireframe
from bf_core.stats.select import SampleFilter
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import exact
from bf_core.wireframes import store as ws
from tests.meshutil import UTM, cube
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


def _routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
    if "/stats/contact" not in paths:
        stats_router.router.include_router(routes_contact.router)


def test_t8_3_to_a_closed_body_of_the_demo_is_the_kernel(rich) -> None:  # type: ignore[no-untyped-def]
    """«До каркаса»: the crossings of the paths of the holes with «Рудное тело 1» — what the kernel of 13.8 gives
    on them; the domain and A, B are not used; the saved chart computes the same; a part of the holes."""
    from bf_core.composites import coords
    from bf_core.composites import source as dh_source
    from bf_core.geo import intersect as gi

    c, set_id, project = rich
    wf = {w["name"]: w["id"] for w in c.get(f"{API}/wireframes").json()["wireframes"]}
    closed = wf["Рудное тело 1"]
    got = post(c, body(set_id, wireframe_id=closed, domain=None, a=None, b=None, distance=10, step=2))
    cols = source.read(project, set_id, "Химия", ["PB", "length"])
    st = dh_source.trajectories(project, set_id)
    dense = coords.hole_index(st.hole_ids, cols.hole_db_ids)[cols.hole_of()]
    with dialect.read_tx(project.engine) as conn:
        state, stats = exact.set_targets(conn, [closed])[0]
    target = exact.geometry(project, state).target(stats)
    hits = gi.hole_crossings(st.traj, [target], holes=np.unique(dense[dense >= 0]))
    want = routes_charts._safe(contact_chart_wireframe(dense, cols.from_m, cols.to_m, hits, flt=SampleFilter(),
                                                       distance=10, step=2, **cols.select_args("PB")))  # fmt: skip
    assert (got["wireframe_id"], got["domain"], got["domain_table"], got["domain_outside"]) == (closed, None, None, 0)
    for k in ("distance", "step", "weighted", "counts", "contacts", "holes", "edges", "mean", "n", "a", "b",
              "conventions"):  # fmt: skip
        assert got[k] == want[k], k
    assert got["contacts"] > 0 and got["holes"] > 0 and sum(got["n"]) > 0
    assert (got["a"]["label"], got["b"]["label"]) == ("снаружи", "внутри") and "каркас" in got["conventions"]
    # the domain and A, B given are not used
    assert post(c, body(set_id, wireframe_id=closed, distance=10, step=2))["n"] == got["n"]
    r = c.post(f"{API}/charts", json={"set_id": set_id, "kind": "contact", "name": "До каркаса",
                                      "settings": body(set_id, wireframe_id=closed)})  # fmt: skip
    assert r.status_code == 200, r.text
    saved = c.post(f"{S}/chart/{r.json()['id']}").json()
    assert saved["n"] == got["n"] and saved["wireframe_id"] == closed
    crossed = sorted({cols.holes[h] for h in cols.hole_of()[np.isin(dense, hits.hole)]})
    some = post(c, body(set_id, wireframe_id=closed, distance=10, step=2, holes=crossed[:1]))
    assert some["holes"] == 1 and 0 < some["contacts"] <= got["contacts"]


SIN60 = math.sqrt(3.0) / 2.0


@pytest.fixture()
def tilted(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """A cube of 10 m (closed, normals outward) and one straight hole dipping 60° to the east through its top and
    bottom faces (along the hole: entry at 20/√3 ≈ 11.547 m, X 2.77; exit at 40/√3 ≈ 23.094 m, X 8.55), samples
    of 1 m to 40 m: PB 3 inside the cube and 1 outside it by the middle of the sample; a hole beside the cube."""
    _routes(monkeypatch)
    r = pclient.post(f"{API}/projects", json={"name": "Контакт до каркаса"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    lo = UTM.copy()
    v, t = cube(2, 10.0, offset=lo)
    now = dialect.utcnow()
    collars = {"Н-1": lo + [-3.0, 5.0, 20.0], "Н-2": lo + [40.0, 5.0, 20.0]}
    with dialect.write_tx(project.engine) as conn:
        cube_id = ws.put_wireframe(conn, BlobStore(project.folder), v, t, name="Куб")
        set_id = conn.execute(sa.insert(objects).values(
            kind="drillhole_set", name="Наклонные", name_key="наклонные", sort_key=0.0, state="ready", version=1,
            meta={}, created_at=now, updated_at=now)).inserted_primary_key[0]  # fmt: skip
        for name, (x, y, z) in collars.items():
            hid = conn.execute(sa.insert(dh_holes).values(set_id=set_id, name=name, x=x, y=y, z=z, td=40.0,
                                                          has_collar=True)).inserted_primary_key[0]  # fmt: skip
            conn.execute(sa.insert(dh_surveys).values(set_id=set_id, hole_id=hid, depth=0.0, azimuth=90.0, dip=-60.0))
    exact.forget()
    frm = np.arange(40.0)
    mid = frm + 0.5
    x = collars["Н-1"][0] + 0.5 * mid - lo[0]
    z = collars["Н-1"][2] - SIN60 * mid - lo[2]
    inside = (x > 0.0) & (x < 10.0) & (z > 0.0) & (z < 10.0)
    pb = np.concatenate([np.where(inside, 3.0, 1.0), np.full(40, 1.0)])
    table = dh.TableData(name="Пробы", kind="interval", holes=["Н-1"] * 40 + ["Н-2"] * 40,
                         from_m=np.concatenate([frm, frm]), to_m=np.concatenate([frm, frm]) + 1.0,
                         columns=[dh.ColumnSpec("PB", "number", "%", "component")], values=[pb.tolist()])  # fmt: skip
    dh.write_table(project.engine, set_id, table, job_id=None, tick=lambda _p, _m: None)
    return pclient, int(set_id), cube_id, inside


def test_t8_3_a_cube_and_an_inclined_hole_through_the_api(tilted) -> None:  # type: ignore[no-untyped-def]
    """Т8.3 «до каркаса» через API: 1 outside the cube and 3 inside — the means of the steps are 1 and 3
    exactly; 5 samples on each side of each face within 5 m: 2 a step; the hole beside the cube — no contact."""
    c, set_id, cube_id, inside = tilted
    assert int(inside.sum()) == 11  # the middles 12.5 … 22.5 m: 11.5 < 11.547 is above the top face
    got = post(c, {"v": 1, "set_id": set_id, "table": "Пробы", "field": "PB", "domain": None, "domain_table": None,
                   "a": None, "b": None, "distance": 5, "step": 1, "wireframe_id": cube_id,
                   "weight": "length"})  # fmt: skip
    assert got["wireframe_id"] == cube_id and (got["contacts"], got["holes"]) == (2, 1)
    assert got["edges"] == [float(k) for k in range(-5, 6)]
    assert got["mean"] == [1.0] * 5 + [3.0] * 5 and got["n"] == [2] * 10
    assert got["a"] == {"label": "снаружи", "n": 10, "mean": 1.0}
    assert got["b"] == {"label": "внутри", "n": 10, "mean": 3.0}
    assert got["counts"]["rows"] == 80 and got["counts"]["in_steps"] == 20 and got["counts"]["no_path"] == 0
    # the steps of 2 m to 10 m: the middle 17.5 is 5.95 m from the top face and 5.59 m from the bottom one
    wide = post(c, {"v": 1, "set_id": set_id, "table": "Пробы", "field": "PB", "distance": 10, "step": 2,
                    "wireframe_id": cube_id})  # fmt: skip
    assert wide["n"] == [4] * 5 + [4, 4, 3, 0, 0] and wide["mean"][5:] == [3.0, 3.0, 3.0, None, None]
    # only the hole beside the cube: no contact is an answer
    none = post(c, {"v": 1, "set_id": set_id, "table": "Пробы", "field": "PB", "wireframe_id": cube_id,
                    "holes": ["Н-2"]})  # fmt: skip
    assert none["contacts"] == 0 and sum(none["n"]) == 0 and none["a"]["n"] == 0
