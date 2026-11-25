"""The inspector of a block (ТЗ 7.35 Д-30; Э18, 18.Д3; e18-api.md §7): `POST /estimate/inspect`.

* ТД30-1 — 1000 random blocks of a model of the T8.8-7 kind (a turned grid, a variogram of three structures with their
  own axes, an ellipsoid with three angles, three passes with the limits of a hole and of an octant, a soft boundary):
  the recalculation of the inspector equals bit for bit the fields `kernel.run` wrote (the job of 18.12 is written in
  `estinspect.write_fields`); a block out of the domain is STATUS 1 as it is stored;
* the answer of a block (the table of the candidates, the segments, the snapshot, the stored values), a block found by
  its indices, its point or its row, the weights give the estimate;
* the speed (≤ 200 ms with a warm cache), the cache, the flag «параметры или входы изменились» after an edit of the
  table of the samples, the refusals in Russian.
ТД30-2 and the sub-blocks are in `test_estimate_inspect_sets.py`, the route and the fixture of the window — in
`test_estimate_inspect_route.py`."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate import inspect_spot as isp
from bf_core.geostat.estimate import search as se
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate.spec import parse_inspect
from tests import estinspect as ei
from tests.estinspect import Ctx, prepared, stored_of

LIMIT_S = 0.2


@pytest.fixture(scope="module")
def ctx(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Ctx]:
    yield from ei.ctx_of(tmp_path_factory.mktemp("inspect"))


@pytest.fixture()
def fresh_ctx(tmp_path: Path) -> Iterator[Ctx]:
    yield from ei.ctx_of(tmp_path)


def refused(fn: Any, status: int, code: str, param: str | None = None) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    err = e.value
    assert (err.status, err.code) == (status, code), (err.code, err.message)
    assert err.message and err.hint and any("а" <= ch.lower() <= "я" for ch in err.message)  # Russian, with a hint
    if param is not None:
        assert err.extra.get("param") == param
    return err


# --- ТД30-1 ---------------------------------------------------------------------------------------------------------


def test_td30_1_one_thousand_blocks_equal_the_fields_the_job_wrote(ctx: Ctx) -> None:
    model, row, entry, fresh = prepared(ctx)
    n = model.rows
    rng = np.random.default_rng(30)
    pick = set(rng.choice(n, 1000, replace=False).tolist())
    status = ctx.cols["AU_OK_STATUS"]
    pick |= set(np.flatnonzero(status == 2).tolist())  # the rare ones are all there
    seen: dict[str, Any] = {"status": [0, 0, 0, 0], "pass": {}}
    t0 = time.perf_counter()
    for r in sorted(pick):
        spot = isp.locate(model, {"row": r})
        got = ins.one_row(model, spot, row, entry, fresh, None, chosen=True)
        assert got is not None
        s = got["status"]
        seen["status"][s] += 1
        assert s == int(stored_of(ctx, "AU_OK_STATUS", r)), r
        if s == 1:  # out of the domain: STATUS 1 as stored, the rest empty, nothing to compare
            assert got["same"] is None and not got["active"] and got["candidates"] == [] and got["pass"] == 0
            assert got["warning"] is None
            continue
        assert got["same"] is True and got["changed"] is False and got["warning"] is None, (r, got["fields"])
        for f in got["fields"]:
            mine = ei.cast(np.array([np.nan if f["calc"] is None else f["calc"]]), f["dtype"])[0]
            theirs = stored_of(ctx, f["name"], r)
            assert (math.isnan(mine) and math.isnan(theirs)) or mine == theirs, (r, f)
            assert f["same"] is True and f["exists"]
        if s == 0:
            seen["pass"][got["pass"]] = seen["pass"].get(got["pass"], 0) + 1
    elapsed = time.perf_counter() - t0
    # a real mixture, not a vacuous test: blocks out of the domain, with too few samples, all three passes
    assert seen["status"][1] > 100 and seen["status"][2] >= 3 and seen["status"][0] > 600, seen
    assert sorted(seen["pass"]) == [1, 2, 3] and min(seen["pass"].values()) >= 20, seen
    print(f"\n[inspect] {len(pick)} blocks, {1000 * elapsed / len(pick):.1f} ms per block (one_row incl. reads)")


def test_the_weights_of_the_selected_samples_give_the_estimate(ctx: Ctx) -> None:
    model, row, entry, fresh = prepared(ctx)
    status = ctx.cols["AU_OK_STATUS"]
    rows = np.flatnonzero(status == 0)[::13][:150]
    for r in rows:
        got = ins.one_row(model, isp.locate(model, {"row": int(r)}), row, entry, fresh, None, chosen=True)
        assert got is not None
        sel = [c for c in got["candidates"] if c["reason"] in ("selected", "hy_capped")]
        assert len(sel) == got["values"]["NS"] == got["counts"]["selected"]
        lam = np.array([c["lambda"] for c in sel])
        z = np.array([c["z"] for c in sel])
        assert abs(lam.sum() - 1.0) < 1e-9  # OK: the weights sum to 1
        assert abs(float(lam @ z) - got["values"]["EST"]) <= 1e-9 * max(1.0, abs(got["values"]["EST"]))
        assert (lam < 0).any() or True  # negative weights are allowed (their own colour in the window)
        assert got["values"]["NH"] == len({c["hole"] for c in sel})
        assert all(c["lambda"] is None for c in got["candidates"] if c not in sel)


def test_a_block_is_found_by_ijk_xyz_and_row_alike(ctx: Ctx) -> None:
    model = ctx.model()
    for r in (0, 777, 2559, 1234):
        a = ctx.run(row=r)
        ijk = a["block"]["ijk"]
        b = ctx.run(block=ijk)
        c = ctx.run(xyz=a["block"]["centre"])
        assert a["block"]["row"] == b["block"]["row"] == c["block"]["row"] == r
        assert a["results"] == b["results"] == c["results"]
        np.testing.assert_allclose(a["block"]["centre"], geometry.centres(model.grid, np.array([a["block"]["p"]]))[0],
                                   rtol=0, atol=1e-9)  # fmt: skip
        assert a["block"]["size"] == [5.0, 5.0, 2.5]


# --- the answer ------------------------------------------------------------------------------------------------------


def test_the_answer_has_what_the_window_needs_and_is_plain_json(ctx: Ctx) -> None:
    status = ctx.cols["AU_OK_STATUS"]
    r = int(np.flatnonzero(status == 0)[300])
    out = ctx.run(row=r)
    json.dumps(out, allow_nan=False)  # no NaN: empty is null
    assert out["model_id"] == ctx.w.big and out["warnings"] == [] and len(out["results"]) == 1
    res = out["results"][0]
    assert (res["estimation_id"], res["name"], res["method"], res["prefix"], res["sub"]) == (
        ctx.eid, "AU · OK · руда", "ok", "AU_OK_", "parent")  # fmt: skip
    assert res["block"] == {"centre": out["block"]["centre"], "size": [5.0, 5.0, 2.5], "rows": 1}
    assert res["domain"] == {"field": "DOM", "label": "руда", "row_label": "руда"} and res["active"] is True
    assert res["status"] == 0 and res["status_label"] == "оценён" and res["pass"] in (1, 2, 3)
    assert {"EST", "KV", "SLOPE", "KE", "NS", "NH", "AD", "PASS", "STATUS"} <= set(res["values"])
    assert res["same"] is True and res["changed"] is False and res["warning"] is None and res["missing_fields"] == []
    assert res["lineage"]["state"] in ("current", "pinned", "stale")
    assert res["source"] == {"set_id": ctx.w.big_set, "table": str(ctx.w.big_table), "field": "AU"}
    assert res["soft_dist"] == 8.0 and res["disc"] == [3, 3, 2]
    ell = res["ellipsoid"]
    assert (
        ell["pass"] == res["pass"]
        and ell["ang1"] == 35.0
        and ell["r1"] == pytest.approx(18.0 * [1, 1.6, 2.4][ell["pass"] - 1])
    )
    assert len(ell["centre_xyz"]) == 3 and res["passes"][-1]["ok"] is True
    assert [p["pass"] for p in res["passes"]] == list(range(1, res["pass"] + 1))
    cands = res["candidates"]
    assert 0 < len(cands) <= 1000 and res["counts"]["candidates"] >= len(cands) == res["counts"]["shown"]
    keys = {"i", "row_id", "hole", "from", "to", "xyz", "h", "dist", "octant", "z_raw", "z", "lambda", "reason",
            "reason_label", "merged", "domain"}  # fmt: skip
    assert all(set(c) == keys for c in cands)
    assert [c["h"] for c in cands] == sorted(c["h"] for c in cands)  # by h_s
    assert {c["reason"] for c in cands} <= set(se.REASONS) and {c["domain"] for c in cands} <= {
        "руда", "мягкая граница", "сульфид", "окисл"}  # fmt: skip
    # the segments: the centre → the selected samples, with their weights
    segs = res["segments"]
    sel = [c for c in cands if c["reason"] in ("selected", "hy_capped")]
    assert [s["i"] for s in segs] == [c["i"] for c in sel] and all(s["from"] == res["block"]["centre"] for s in segs)
    assert [s["to"] for s in segs] == [c["xyz"] for c in sel] and [s["lambda"] for s in segs] == [
        c["lambda"] for c in sel
    ]
    # the snapshot: the parameters, the samples, the weights
    snap = res["snapshot"]
    json.dumps(snap, allow_nan=False)
    assert snap["kind"] == "estimate.inspect" and snap["estimation"]["params"]["method"]["kind"] == "ok"
    assert snap["estimation"]["params"]["variogram"]["snapshot"]["structures"][0]["type"] == "spherical"
    assert [s["lambda"] for s in snap["samples"]] == [c["lambda"] for c in sel]
    assert snap["result"]["values"] == res["values"] and snap["block"]["ijk"] == out["block"]["ijk"]


def test_a_block_out_of_the_domain_is_not_listed_without_params_and_is_status_1_with_them(ctx: Ctx) -> None:
    status = ctx.cols["AU_OK_STATUS"]
    r = int(np.flatnonzero(status == 1)[5])
    out = ctx.run(row=r)
    assert out["results"] == [] and out["warnings"] == ["Блок вне доменов всех строк оценки этой модели."]
    chosen = ctx.run(row=r, params_id=ctx.eid)["results"][0]
    assert chosen["status"] == 1 and chosen["status_label"] == "вне домена" and chosen["active"] is False
    assert chosen["domain"]["row_label"] == "порода" and chosen["same"] is None and chosen["changed"] is False
    assert chosen["values"]["EST"] is None and chosen["pass"] == 0 and chosen["candidates"] == []  # empty, not 0
    assert chosen["ellipsoid"]["pass"] == 1
    assert [f["same"] for f in chosen["fields"]] == [None] * len(chosen["fields"])


def test_a_block_with_too_few_samples_is_status_2_and_still_shows_its_candidates(ctx: Ctx) -> None:
    r = int(np.flatnonzero(ctx.cols["AU_OK_STATUS"] == 2)[0])
    res = ctx.run(row=r)["results"][0]
    assert res["status"] == 2 and res["status_label"] == "мало проб" and res["same"] is True
    assert res["values"]["EST"] is None and res["pass"] == 0 and res["passes"][-1]["reason"] == "few"
    assert res["counts"]["selected"] == 0 and res["segments"] == []
    assert all(c["lambda"] is None for c in res["candidates"])


# --- the speed, the cache, the flag «изменились» -------------------------------------------------------------------


def test_a_block_is_inspected_in_200_ms_with_a_warm_cache_and_the_cache_is_kept(ctx: Ctx) -> None:
    status = ctx.cols["AU_OK_STATUS"]
    rows = [int(r) for r in np.flatnonzero(status == 0)[::97][:25]]
    ctx.run(row=rows[0])  # the row is compiled (the cold path: the samples are read, the kernel is built)
    n_cache = len(ins._CACHE)  # noqa: SLF001
    times = []
    for r in rows:
        t0 = time.perf_counter()
        out = ctx.run(row=r)
        times.append(time.perf_counter() - t0)
        assert out["results"][0]["same"] is True
    assert len(ins._CACHE) == n_cache  # noqa: SLF001
    print(f"\n[inspect] run(): median {1000 * float(np.median(times)):.1f} ms, max {1000 * max(times):.1f} ms")
    assert max(times) <= LIMIT_S, times


def test_a_cold_row_is_slower_and_a_new_version_of_the_row_is_a_new_entry(fresh_ctx: Ctx) -> None:
    c = fresh_ctx
    assert len(ins._CACHE) == 0  # noqa: SLF001
    t0 = time.perf_counter()
    c.run(row=100, params_id=c.eid)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    c.run(row=101, params_id=c.eid)
    warm = time.perf_counter() - t0
    assert len(ins._CACHE) == 1 and warm < cold  # noqa: SLF001
    with dialect.write_tx(c.project.engine) as conn:
        st.update(conn, c.w.store, c.project, c.eid, {"disc": [2, 2, 2]}, 1)
    c.run(row=101, params_id=c.eid)
    assert len(ins._CACHE) == 2  # noqa: SLF001  (the old one ages out of the cache by itself)


def test_an_edited_table_of_the_samples_raises_the_flag_parameters_or_inputs_changed(fresh_ctx: Ctx) -> None:
    c = fresh_ctx
    status = c.cols["AU_OK_STATUS"]
    r = int(np.flatnonzero(status == 0)[200])
    before = c.run(row=r)["results"][0]
    assert before["changed"] is False and before["warning"] is None and before["same"] is True
    # an edit of a sample the block uses: the recalculation no longer equals the stored fields
    used = next(x for x in before["candidates"] if x["reason"] == "selected" and x["lambda"] is not None)
    ei.edit_sample(c.w, 999.0, row_id=used["row_id"])
    after = c.run(row=r)["results"][0]
    assert after["changed"] is True and after["warning"] == "Параметры или входы изменились"
    assert after["same"] is False and "AU_OK_EST" in after["warning_detail"]
    assert after["stale_reason"] == "изменена таблица «Пробы инспектора»"
    assert any(f["same"] is False for f in after["fields"]) and after["values"]["EST"] != before["values"]["EST"]
    assert next(x for x in after["candidates"] if x["row_id"] == used["row_id"])["z_raw"] == 999.0
    assert after["lineage"]["state"] == "stale"
    # the stored values are what they were: the field of the model is not touched by the inspector
    assert next(f for f in after["fields"] if f["key"] == "EST")["stored"] == before["values"]["EST"]


def test_an_edit_that_does_not_reach_the_block_still_says_the_inputs_changed(fresh_ctx: Ctx) -> None:
    c = fresh_ctx
    r = int(np.flatnonzero(c.cols["AU_OK_STATUS"] == 0)[10])
    before = c.run(row=r)["results"][0]
    used = {x["row_id"] for x in before["candidates"]}
    far = next(rid for rid in ei.table_rows(c.w) if rid not in used)  # a row of the table the block does not look at
    ei.edit_sample(c.w, 0.5, row_id=far)
    after = c.run(row=r)["results"][0]
    assert after["same"] is True  # the block did not see the sample: the recalculation is the stored one
    assert after["changed"] is True and after["warning"] == "Параметры или входы изменились"  # but the table moved
    assert after["warning_detail"] == "изменена таблица «Пробы инспектора»"


def test_a_row_whose_source_is_gone_is_refused_when_chosen_and_skipped_otherwise(fresh_ctx: Ctx) -> None:
    c = fresh_ctx
    with dialect.write_tx(c.project.engine) as conn:
        from bf_core.db.schema import dh_tables

        conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == c.w.big_table))
    r = int(np.flatnonzero(c.cols["AU_OK_STATUS"] == 0)[3])
    with pytest.raises(ApiError) as e:
        c.run(row=r, params_id=c.eid)
    assert (e.value.status, e.value.code) == (409, "source_lost") and e.value.message and e.value.hint
    out = c.run(row=r)
    assert out["results"] == [] and out["warnings"] and "источник удалён" in out["warnings"][0]


def test_the_refusals_are_in_russian_with_a_hint(ctx: Ctx) -> None:
    run = ctx.run
    refused(lambda: ins.run(ctx.project, parse_inspect({"model_id": 999_999, "row": 0})), 404, "block_model_not_found")
    refused(lambda: run(row=0, params_id=999_999), 404, "estimation_not_found")
    refused(lambda: run(row=10_000_000), 422, "bad_param", "row")
    refused(lambda: run(block=[20, 0, 0]), 422, "bad_param", "block.0")
    refused(lambda: run(block=[0, 16, 0]), 422, "bad_param", "block.1")
    refused(lambda: run(block=[0, 0, 8]), 422, "bad_param", "block.2")
    refused(lambda: run(xyz=[0.0, 0.0, 0.0]), 422, "bad_param", "xyz")  # outside the model
    refused(lambda: parse_inspect({"model_id": 1, "row": 0, "block": [0, 0, 0]}), 422, "bad_param", "block")
    refused(lambda: parse_inspect({"model_id": 1}), 422, "bad_param", "block")
    refused(lambda: parse_inspect({"model_id": 1, "block": [1, 2]}), 422, "bad_param", "block")
    refused(lambda: parse_inspect({"model_id": 1, "block": [1, -2, 3]}), 422, "bad_param", "block.1")
    refused(lambda: parse_inspect([1]), 422, "bad_param")
    # parameters of another model: 422; a model with no fields of the estimation: 409
    other = ctx.w.model
    refused(lambda: ins.run(ctx.project, parse_inspect({"model_id": other, "row": 0, "params_id": ctx.eid})), 422,
            "bad_param", "params_id")  # fmt: skip
    refused(lambda: ins.run(ctx.project, parse_inspect({"model_id": other, "row": 0})), 409, "not_estimated",
            "model_id")  # fmt: skip


def test_a_sparse_model_has_holes_in_the_blocks_it_lists(tmp_path: Path) -> None:
    """A block that the sparse model does not have is 422 `bad_param {param: block}`, not a block of nothing."""
    from types import SimpleNamespace

    from bf_core.db import migrate
    from tests.dbutil import make_engine
    from tests.test_blocks_fields_store import make_model

    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    project = SimpleNamespace(engine=engine, uid="sparse", folder=tmp_path)
    from bf_core.storage.blobs import BlobStore

    store = BlobStore(tmp_path)
    mid = make_model(project, store, n=40)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, mid)
    present = set(int(v) for v in model.p())
    gone = next(p for p in range(480) if p not in present)
    i, j, k = (int(a[0]) for a in geometry.row_ijk(model.grid, np.array([gone], dtype=np.uint64)))
    refused(lambda: isp.locate(model, {"ijk": [i, j, k]}), 422, "bad_param", "block")
    engine.dispose()


# --- the reading of rows of a model (the cache of the decoded row groups) -------------------------------------------


def test_the_rows_read_through_the_cache_are_those_of_the_model(ctx: Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model of the world has row groups of 100 rows (350 rows): a range inside a group goes through the cache,
    a range across a boundary — straight; the same values, a second read costs no file, the budget is kept."""
    with dialect.read_tx(ctx.project.engine) as conn:
        model = Model.open(conn, ctx.w.store, ctx.w.model)
    assert [isp.group_span(model, r) for r in (0, 99, 100, 349)] == [(0, 100), (0, 100), (100, 200), (300, 350)]
    isp.forget()
    names = ["AU", "CU", "N", "DOM"]
    for a, b in [(5, 6), (10, 60), (99, 100), (95, 105), (0, 350), (301, 350), (150, 151)]:
        got = isp.read_fields(model, names, a, b)
        want = model.columns(names, a, b)
        assert got.keys() == want.keys()
        for n in names:
            np.testing.assert_array_equal(got[n], want[n])  # NaN equals NaN, an empty category is −1
        p, s, w = isp.read_cells(model, a, b)
        np.testing.assert_array_equal(p, model.p(a, b))
        assert s is None and w is None
    assert len(isp._COLUMNS) > 0  # noqa: SLF001
    reads: list[Any] = []
    real = model.reader.fill
    monkeypatch.setattr(model.reader, "fill", lambda *a, **k: reads.append(a) or real(*a, **k))
    isp.read_fields(model, names, 10, 60)  # kept: no file is read
    assert reads == []
    got = isp.read_fields(model, names, 10, 60)
    got["AU"][:] = -1.0  # what is handed out is a copy: the cache is not spoilt
    assert not (isp.read_fields(model, names, 10, 60)["AU"] == -1.0).all()
    monkeypatch.setattr(isp, "COLUMNS_BYTES", 1500)  # room for a few columns of a group only
    for a in (5, 105, 205, 305):
        want = model.columns(names, a, a + 3)
        for n, v in isp.read_fields(model, names, a, a + 3).items():
            np.testing.assert_array_equal(v, want[n])
    assert isp._BYTES[0] == sum(int(v.nbytes) for v in isp._COLUMNS.values())  # noqa: SLF001
    assert isp._BYTES[0] <= 1500 + 4 * 800  # noqa: SLF001  (the group just read stays: at most the names of it)
    isp.forget()
    assert len(isp._COLUMNS) == 0 and isp._BYTES[0] == 0  # noqa: SLF001
