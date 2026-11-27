"""The review of the milestone В5, group 2 (Э18, Э18б): what a stored row of the plan must stand besides the job.

* A row whose search is `from: trend` reads back with the search not resolved (the ellipsoid is that of the trend as
  it is now): the cross-validation, the global check, KNA, the preview and the inspector of a block take it from the
  trend, as the job does, and are not refused with «Эллипсоид поиска не определён».
* The rows of one prefix share the fields (№ 77): the inspector of a block finds the row of EVERY domain, not the
  first row of the field alone; «Перестроить» of such a field runs the row that is «устарел».
* A parent of a model with sub-blocks that holds two domains (`sub: parent`): the inspector takes the row of the plan
  whose domain the sub-block under the pointer is in, and raises no flag «Параметры или входы изменились».
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat.estimate import checks, kna, xval
from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate import preview as pv
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate.spec import parse_inspect
from tests import estjob_util as u
from tests.estutil import W, get, make, w  # noqa: F401
from tests.test_estimate_job import cols, pair, run

# --- `from: trend` ---------------------------------------------------------------------------------------------------


def trend_row(w: W, **over: Any) -> int:  # noqa: F811
    return int(make(w, trend_id=w.trend, search={"from": "trend"}, **over)["id"])


def test_a_stored_trend_row_is_cross_validated_and_previewed(w: W) -> None:  # noqa: F811
    ident = trend_row(w, method={"kind": "nn"}, variogram=None, name="NN по тренду")
    meta = get(w, ident)["meta"]
    assert meta["search"]["from"] == "trend" and meta["search"]["ang1"] == 45.0
    assert not sb.load_spec(meta).resolved_search  # the stored form: the trend is asked again when the row is used
    out = xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"})
    assert out["n"] > 0 and sum(out["summary"]["by_status"]) == out["n"]
    # «устарел» of the table is by the hash of the row as stored: a fresh table is not stale
    with dialect.read_tx(w.project.engine) as conn:
        assert xval.read_page(conn, w.store, ident)["stale"] is False
    got = pv.preview(w.project, {"estimation_id": ident, "at": {"row": 0}})
    factor = get(w, ident)["meta"]["search"]["passes"][got["ellipsoid"]["pass"] - 1]["factor"]
    assert got["ellipsoid"]["ang1"] == 45.0 and got["ellipsoid"]["r1"] == 120.0 * factor


def test_the_checks_of_a_stored_trend_row_take_the_ellipsoid_of_the_trend(w: W) -> None:  # noqa: F811
    ident = trend_row(w, name="OK по тренду")  # OK with the variogram of the world, the search by the trend
    _id, _name, spec, _org = checks._row(w.project.engine, {"estimation_id": ident})  # noqa: SLF001
    assert spec.resolved_search and spec.search.angles == (45.0, 0.0, 0.0) and spec.search.radii is not None
    assert spec.search.radii[0] == 120.0
    stored, _o, eid, _n = kna._spec_of(w.project.engine, {"estimation_id": ident})  # noqa: SLF001
    assert eid == ident and stored.resolved_search and stored.search.angles == (45.0, 0.0, 0.0)
    # a one-off body of the KNA window comes as the canonical form of the route: `from: trend` again
    one_off, *_ = kna._spec_of(w.project.engine, {"estimation": spec.canonical()})  # noqa: SLF001
    assert one_off.resolved_search and one_off.search.angles == (45.0, 0.0, 0.0)


def test_the_inspector_compiles_a_stored_trend_row(w: W) -> None:  # noqa: F811
    ident = trend_row(w, method={"kind": "nn"}, variogram=None, name="NN по тренду")
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, w.model)
    entry = ins._build(w.project, w.store, model, dict(get(w, ident)["meta"]))  # noqa: SLF001
    assert entry.spec.search.angles == (45.0, 0.0, 0.0) and entry.rk.ell.radii[0] == 120.0


def test_a_manual_row_is_left_as_it_is(w: W) -> None:  # noqa: F811
    from bf_core.geostat.estimate.store_check import with_trend

    spec = sb.load_spec(get(w, int(make(w)["id"]))["meta"])  # the search by the variogram model
    with dialect.read_tx(w.project.engine) as conn:
        assert with_trend(conn, spec) is spec


# --- the rows of one prefix ------------------------------------------------------------------------------------------


def test_the_inspector_finds_the_row_of_every_domain_of_a_shared_prefix(tmp_path: Path) -> None:
    jw = u.world(tmp_path)
    try:
        a, b, plan = pair(jw)  # «руда» and «порода» write the fields AU_OK_*
        out = run(jw, plan_id=plan)
        assert out["merged"] == [{"prefix": "AU_OK_", "estimation_ids": [a, b]}]
        c = cols(jw, ["DOM", "AU_OK_STATUS"])
        for code, ident in ((0, a), (1, b)):
            rows = np.flatnonzero((c["DOM"] == code) & (c["AU_OK_STATUS"] == 0))
            assert rows.size
            got = ins.run(jw.project, parse_inspect({"model_id": jw.model, "row": int(rows[0])}))
            assert [(x["estimation_id"], x["status"], x["same"]) for x in got["results"]] == [(ident, 0, True)]
            assert got["warnings"] == []
    finally:
        ins.forget()
        jw.project.engine.dispose()


def test_the_rebuild_of_a_shared_field_runs_the_row_that_is_stale(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from bf_core import lineage
    from bf_core.geostat.estimate import job
    from bf_core.lineage import rebuild

    jw = u.world(tmp_path)
    try:
        a, b, plan = pair(jw)
        run(jw, plan_id=plan)
        node = lineage.Node("bm_field", jw.model, "au_ok_est")

        def state() -> str:
            with dialect.read_tx(jw.project.engine) as conn:
                return str(lineage.status(conn, [node])[node].state)

        assert state() == "current"
        u.put(jw, b, disc=[3, 3, 2])  # the second row of the prefix is edited: the fields are «устарел» by it
        assert state() == "stale"
        with dialect.read_tx(jw.project.engine) as conn:
            made = rebuild.make_plan(conn, node)
        assert (made.items[0].owner.kind, made.items[0].owner.id) == ("object:estimation", b)
        svc = SimpleNamespace(require_current=lambda writable=False: jw.project)
        step = rebuild.prepare_step(svc, made.items[0].owner, {})
        assert step.job_kind == "estimate.run" and step.params["estimation_ids"] == [b]
        job.run_estimate(jw.project.engine, jw.project.folder, step.params, threads=2)
        assert state() == "current"  # the job of the first row would have carried the old blocks of `b`: still stale
    finally:
        jw.project.engine.dispose()


# --- a parent of two domains -----------------------------------------------------------------------------------------


def test_the_inspector_takes_the_domain_of_the_sub_block_under_the_pointer(tmp_path: Path) -> None:
    jw = u.world(tmp_path)
    try:
        a = u.make(jw, model=jw.sub)  # `sub: parent` (the default): a sub-block gets the estimate of its parent
        b = u.make(jw, model=jw.sub, label="порода", zone="перв")
        run(jw, model=jw.sub, plan_id=u.plan(jw, [a, b], model=jw.sub))
        p = u.open_model(jw, jw.sub).p()
        c = cols(jw, ["DOM", "AU_OK_STATUS"], model=jw.sub)
        dom, status = c["DOM"], c["AU_OK_STATUS"]
        mixed = [int(q) for q in np.unique(p) if len(set(dom[p == q].tolist())) == 2]
        assert mixed  # the boundary of the domains cuts parents: what the sub-blocks are for
        seen = set()
        for r in np.flatnonzero(p == mixed[0]):
            assert status[r] == 0
            got = ins.run(jw.project, parse_inspect({"model_id": jw.sub, "row": int(r)}))
            want = a if dom[r] == 0 else b
            assert [(x["estimation_id"], x["active"], x["status"], x["same"], x["warning"])
                    for x in got["results"]] == [(want, True, 0, True, None)], int(r)  # fmt: skip
            seen.add(want)
            # the row of the other domain, asked for by its number: the sub-block is out of its domain, nothing differs
            other = ins.run(jw.project, parse_inspect({"model_id": jw.sub, "row": int(r),
                                                       "params_id": b if want == a else a}))  # fmt: skip
            assert [(x["active"], x["status"], x["warning"]) for x in other["results"]] == [(False, 1, None)]
        assert seen == {a, b}
    finally:
        ins.forget()
        jw.project.engine.dispose()
