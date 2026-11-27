"""The review of the milestone В5, group 2 (Э18, Э18б): what a stored row of the plan must stand besides the job.

* A row whose search is `from: trend` reads back with the search not resolved (the ellipsoid is that of the trend as
  it is now): the cross-validation, the global check, KNA, the preview and the inspector of a block take it from the
  trend, as the job does, and are not refused with «Эллипсоид поиска не определён».
* The rows of one prefix share the fields (№ 77): the inspector of a block finds the row of EVERY domain, not the
  first row of the field alone.
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
