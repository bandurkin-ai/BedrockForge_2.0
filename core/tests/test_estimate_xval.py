"""The cross-validation (ТЗ 8.8.1; Т8.8-12; ADR 0024 §8; e18-api.md §8.1; Э18, 18.14): the table of a row — a sample or
a hole left out — is bit for bit the estimate of the row without it (`samples.take`); the figures of the summary
by hand; the NaN branches (empty, constant, no KV, KV = 0, STATUS ≠ 0); the same bits on any threads and portions;
the Parquet blob of the row (written whole, replaced, nothing on a cancel); the page of the answer; the job in a
worker (`BF_EXTRA_JOB_MODULES`)."""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate import xval
from bf_core.geostat.estimate.samples import load
from bf_core.jobs.context import JobCancelled
from tests.estutil import W, get, make, put, world
from tests.test_estimate_kernel import BODY_PASSES, TWO, holes_set, same, samples_of, spec_of

SPH = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 80, "a2": 80, "a3": 80}]}
RADII = {"radii": (160.0, 90.0, 45.0), "angles": (20.0, 5.0, 0.0), "passes": BODY_PASSES}


def row(kind: str, **over: Any) -> Any:
    return spec_of(kind, variogram=TWO if kind in ("sk", "ok") else None, **{**RADII, **over})


def at_xyz(t: xval.XvalTable, k: int) -> np.ndarray:
    return t.xyz[k : k + 1]


def without(rk_spec: Any, s: Any, rest: np.ndarray, point: np.ndarray) -> kn.ChunkResult:
    """The estimate in `point` of the row without the samples not in `rest`: the other side of Т8.8-12."""
    return kn.estimate(kn.compile_row(xval.xval_spec(rk_spec), s.take(rest)), point)


def equal_row(t: xval.XvalTable, k: int, want: kn.ChunkResult) -> None:
    assert t.status[k] == want.status[0]
    for have, key in ((t.est, "EST"), (t.kv, "KV"), (t.ns, "NS"), (t.pass_, "PASS")):
        got = want.values.get(key, np.full(1, math.nan))
        assert same(have[k : k + 1], got), (k, key, have[k], got)


# --- Т8.8-12: the table is the estimate without the sample ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["ok", "sk", "idw", "nn"])
def test_t8_8_12_a_sample_left_out_is_bit_for_bit_the_estimate_without_it(kind: str) -> None:
    s, rng = holes_set(5, n_holes=40, per=10)
    spec = row(kind)
    t = xval.cross_validate(spec, s, "sample")
    assert t.n == s.n and (t.i == np.arange(s.n)).all() and (t.status == 0).sum() > 300
    for k in rng.choice(s.n, 40 if kind == "ok" else 15, replace=False):
        rest = np.delete(np.arange(s.n), t.i[k])
        equal_row(t, int(k), without(spec, s, rest, at_xyz(t, int(k))))
    assert same(t.true, s.z) and (t.xyz == s.xyz).all() and same(t.error, t.est - t.true)


@pytest.mark.parametrize("kind", ["ok", "sk", "idw", "nn"])
def test_t8_8_12_a_hole_left_out_is_bit_for_bit_the_estimate_without_it(kind: str) -> None:
    s, rng = holes_set(6, n_holes=40, per=10)
    spec = row(kind)
    t = xval.cross_validate(spec, s, "hole")
    assert (t.status == 0).sum() > 300
    for h in rng.choice(len(s.holes), 12 if kind == "ok" else 4, replace=False):
        mine = np.flatnonzero(t.hole == h)
        assert mine.size == 10
        rest = np.flatnonzero(s.hole != h)
        for k in mine:
            equal_row(t, int(k), without(spec, s, rest, at_xyz(t, int(k))))
    # a hole is never its own neighbour: the sample of a hole sees the others (NS counts only them)
    other = cross_sample_neighbours(spec, s, t)
    assert other == 0


def cross_sample_neighbours(spec: Any, s: Any, t: xval.XvalTable) -> int:
    """How many selected samples of the cross-validation of a hole are of the same hole (must be none)."""
    rk = kn.compile_row(xval.xval_spec(spec), s)
    bad = 0
    for k in range(0, t.n, 37):
        c = s.local[t.i[k] : t.i[k] + 1]
        nb = rk.searcher.gather(c, int(t.pass_[k]) - 1 if t.status[k] == 0 else 0,
                                exclude=("hole", np.array([int(t.hole[k])])))  # fmt: skip
        n = int(nb.n[0])
        bad += int((s.hole[nb.idx[0, :n]] == t.hole[k]).sum())
    return bad


def test_ik_has_the_etype_as_the_estimate_and_no_kv() -> None:
    from tests.test_estimate_kernel_ik import ik_spec

    s, _ = holes_set(21, n_holes=40, per=10)
    spec = ik_spec(False)
    t = xval.cross_validate(spec, s, "hole")
    ok = t.status == 0
    assert ok.sum() > 200 and np.isfinite(t.est[ok]).all() and np.isnan(t.kv).all()
    lo, hi = float(s.z.min()), float(s.z.max())
    assert (t.est[ok] >= lo - 1e-9).all() and (t.est[ok] <= hi + 1e-9).all()
    k = int(np.flatnonzero(ok)[5])
    rk = kn.compile_row(xval.xval_spec(spec), s)
    one = kn.estimate(rk, s.local[t.i[k] : t.i[k] + 1], local=True, exclude=("hole", np.array([int(t.hole[k])])))
    assert same(t.est[k : k + 1], one.values["ETYPE"])
    got = t.summary()
    assert got["mean_ratio"] is None and got["n_ratio"] == 0 and got["n"] == int(ok.sum())
    assert np.isnan(t.z_error).all()


def test_the_same_bits_on_any_threads_and_portions() -> None:
    s, _ = holes_set(7, n_holes=30, per=10)
    spec = row("ok")
    base = xval.cross_validate(spec, s, "hole", threads=1, chunk=10_000)
    for threads, chunk in ((1, 7), (2, 64), (4, 33), (8, 100)):
        got = xval.cross_validate(spec, s, "hole", threads=threads, chunk=chunk)
        for name in ("est", "kv", "ns", "pass_", "status"):
            assert same(getattr(got, name), getattr(base, name)), (name, threads, chunk)
    assert base.summary() == xval.cross_validate(spec, s, "hole", threads=3, chunk=50).summary()


# --- the table: who is a target, STATUS ≠ 0 -----------------------------------------------------------------------


def far_set():
    """A cluster of 12 holes and one hole 5 km away: the far one has no neighbours when it is left out."""
    rng = np.random.default_rng(3)
    xyz, hole = [], []
    for h in range(13):
        x, y = (5000.0, 5000.0) if h == 12 else (rng.uniform(0, 200), rng.uniform(0, 200))
        for zz in np.linspace(0.0, 70.0, 8):
            xyz.append([x, y, zz])
            hole.append(h)
    z = rng.lognormal(0.0, 0.5, len(xyz))
    return samples_of(np.array(xyz), z, np.array(hole))


def test_rows_without_neighbours_are_in_the_table_and_out_of_the_summary() -> None:
    s = far_set()
    spec = spec_of("ok", variogram=SPH, r1=150.0, passes=[{"factor": 1, "n_min": 3, "n_max": 12}])
    t = xval.cross_validate(spec, s, "hole")
    far = t.hole == 12
    assert far.sum() == 8 and (t.status[far] == kn.ST_FEW).all() and (t.status[~far] == 0).all()
    assert np.isnan(t.est[far]).all() and np.isnan(t.kv[far]).all() and np.isnan(t.pass_[far]).all()
    assert np.isnan(t.error[far]).all() and np.isnan(t.z_error[far]).all()  # the gap is never a 0
    sm = t.summary()
    assert sm["n"] == int((~far).sum()) and sm["n_skipped"] == 0 and sm["by_status"] == [int((~far).sum()), 0, 8, 0]
    by_sample = xval.cross_validate(spec, s, "sample")  # alone left out, the far hole sees its own samples
    assert (by_sample.status[far] == 0).all()


def test_only_the_samples_of_the_domain_are_targets_and_a_hole_takes_out_all_its_samples() -> None:
    s, _ = holes_set(8, n_holes=12, per=10)
    dom = np.zeros(s.n, np.int16)
    dom[::4] = 1  # S: the soft boundary — a neighbour, never a target
    s = replace(s, dom=dom)
    spec = row("ok")
    t = xval.cross_validate(spec, s, "hole")
    assert t.n == int((dom == 0).sum()) and (dom[t.i] == 0).all() and (np.diff(t.i) > 0).all()
    assert same(t.true, s.z[t.i]) and (t.row_id == s.row_id[t.i]).all()
    sm = t.summary()
    assert sm["n"] + sm["by_status"][1] + sm["by_status"][2] + sm["by_status"][3] == t.n
    with pytest.raises(ApiError) as e:
        xval.cross_validate(spec, s, "everything")
    assert (e.value.code, e.value.extra["param"]) == ("bad_param", "by") and e.value.hint


def test_the_estimate_is_a_point_whatever_the_discretization_of_the_row() -> None:
    s, _ = holes_set(9, n_holes=20, per=8)
    point = xval.cross_validate(row("ok"), s, "sample")
    blocky = xval.cross_validate(row("ok", disc=(4, 4, 2)), s, "sample")
    for name in ("est", "kv", "ns", "pass_", "status"):
        assert same(getattr(point, name), getattr(blocky, name)), name
    assert xval.xval_spec(row("ok", disc=(4, 4, 2))).disc == (1, 1, 1)


# --- the summary ------------------------------------------------------------------------------------------------------


def test_the_summary_by_hand() -> None:
    true = np.array([1.0, 2.0, 3.0, 4.0])
    est = np.array([1.5, 1.5, 3.5, 5.0])
    kv = np.array([0.25, 0.5, 0.0, math.nan])
    got = xval.summary(true, est, kv)
    # E = [0.5, -0.5, 0.5, 1]; mean 0.375; Σ(E − mean)² / 4 = 1.1875 / 4; mae 2.5 / 4
    assert got["n"] == 4 and got["n_skipped"] == 0
    assert got["mean_error"] == pytest.approx(0.375, abs=1e-15)
    assert got["std_error"] == pytest.approx(math.sqrt(0.296875), abs=1e-15)
    assert got["mae"] == pytest.approx(0.625, abs=1e-15)
    # cov(true, est) = 1.5625, var(est) = 2.171875, var(true) = 1.25
    assert got["slope"] == pytest.approx(1.5625 / 2.171875, abs=1e-15)
    assert got["r"] == pytest.approx(1.5625 / math.sqrt(1.25 * 2.171875), abs=1e-15)
    assert got["r"] == pytest.approx(float(np.corrcoef(true, est)[0, 1]), abs=1e-14)
    assert got["slope"] == pytest.approx(float(np.polyfit(est, true, 1)[0]), abs=1e-14)
    # E²/KV over KV > 0: 0.25/0.25 = 1 and 0.25/0.5 = 0.5; the row with KV = 0 is not divided, NaN is not counted
    assert got["mean_ratio"] == pytest.approx(0.75, abs=1e-15)
    assert (got["n_ratio"], got["n_kv_zero"]) == (2, 1)
    json.dumps(got, allow_nan=False)  # no NaN in the answer: null


def test_the_summary_without_a_value_is_null_never_zero() -> None:
    with np.errstate(all="raise"), warnings.catch_warnings():
        warnings.simplefilter("error")
        empty = xval.summary(np.array([]), np.array([]), np.array([]))
        assert empty["n"] == 0 and all(empty[k] is None for k in ("mean_error", "std_error", "mae", "r", "slope",
                                                                  "mean_ratio"))  # fmt: skip
        gap = xval.summary(np.array([1.0, 2.0, math.nan, 4.0]), np.array([1.0, math.nan, 3.0, 5.0]), None)
        assert gap["n"] == 2 and gap["n_skipped"] == 2 and gap["mean_error"] == 0.5 and gap["mean_ratio"] is None
        one = xval.summary(np.array([2.0]), np.array([3.0]), np.array([1.0]))
        assert one["n"] == 1 and one["mean_error"] == 1.0 and one["std_error"] == 0.0 and one["r"] is None
        assert one["slope"] is None and one["mean_ratio"] == 1.0  # one point: the line is not defined
        flat = xval.summary(np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 2.0]), None)
        assert flat["r"] is None and flat["slope"] is None and flat["mae"] == pytest.approx(2 / 3)
        const_true = xval.summary(np.array([2.0, 2.0, 2.0]), np.array([1.0, 2.0, 3.0]), None)
        assert const_true["r"] is None and const_true["slope"] == 0.0  # the regression of a constant is flat
        zero = xval.summary(np.array([1.0, 2.0]), np.array([2.0, 1.0]), np.array([0.0, 0.0]))
        assert zero["mean_ratio"] is None and zero["n_kv_zero"] == 2 and zero["n_ratio"] == 0


def test_kv_zero_is_not_divided_in_the_table() -> None:
    s, _ = holes_set(10, n_holes=10, per=6)
    t = xval.cross_validate(row("ok"), s, "sample")
    k = int(np.flatnonzero(t.status == 0)[0])
    t.kv[k] = 0.0  # what the kernel writes for a rounding-noise system
    with np.errstate(all="raise"):
        z = t.z_error
        sm = t.summary()
    assert math.isnan(z[k]) and np.isfinite(np.delete(z, np.flatnonzero(~np.isfinite(z)))).all()
    assert sm["n_kv_zero"] == 1 and sm["n_ratio"] == sm["n"] - 1
    good = (t.status == 0) & (t.kv > 0)
    assert sm["mean_ratio"] == pytest.approx(float(np.mean(t.error[good] ** 2 / t.kv[good])), rel=1e-14)
    assert xval.cross_validate(row("idw"), s, "sample").summary()["mean_ratio"] is None  # IDW, NN — no KV


def test_the_histogram() -> None:
    h = xval.histogram(np.array([-1.0, 0.0, 0.5, 2.0, math.nan]), bins=3)
    assert h["edges"] == [-1.0, 0.0, 1.0, 2.0] and h["counts"] == [1, 2, 1]
    assert xval.histogram(np.array([math.nan])) == {"edges": [], "counts": []}
    one = xval.histogram(np.array([1.0, 1.0]), bins=4)
    assert one["edges"][0] == 0.5 and one["edges"][-1] == 1.5 and sum(one["counts"]) == 2


# --- the project: the blob, the page, the cancel ------------------------------------------------------------------


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def owners(w: W, ident: int) -> list[str]:
    with dialect.read_tx(w.project.engine) as conn:
        q = sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_id == ident, blob_owners.c.role == sb.XVAL)
        return [str(r[0]) for r in conn.execute(q)]


def page(w: W, ident: int, number: int = 1, size: int = 500) -> dict[str, Any]:
    with dialect.read_tx(w.project.engine) as conn:
        return xval.read_page(conn, w.store, ident, number, size)


def test_the_run_writes_the_table_and_the_page_answers(w: W) -> None:
    ident = make(w)["id"]
    with pytest.raises(ApiError) as e:
        page(w, ident)
    assert (e.value.status, e.value.code) == (404, "xval_not_found") and e.value.hint
    out = xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"})
    assert out["estimation_id"] == ident and out["by"] == "sample" and out["n"] == 5
    assert out["events"] == [["geostat.changed", {"kind": "estimation", "id": ident}]]
    assert out["summary"]["n"] == 5 and out["summary"]["by_status"] == [5, 0, 0, 0]
    got = page(w, ident)
    assert set(got) == {"estimation_id", "by", "at", "stale", "total", "page", "pages", "size", "rows", "summary",
                        "histogram"}  # fmt: skip
    head = (got["by"], got["total"], got["page"], got["pages"], got["size"], got["stale"])
    assert head == ("sample", 5, 1, 1, 500, False)
    assert got["summary"] == out["summary"] and sum(got["histogram"]["counts"]) == 5
    assert len(got["histogram"]["edges"]) == len(got["histogram"]["counts"]) + 1
    r0 = got["rows"][0]
    assert tuple(r0) == xval.COLUMNS and r0["hole"] in ("A-1", "B-2") and r0["status"] == 0
    assert r0["error"] == pytest.approx(r0["est"] - r0["true"], abs=1e-15)
    assert r0["z_error"] == pytest.approx(r0["error"] / math.sqrt(r0["kv"]), rel=1e-14) and r0["pass"] in (1, 2, 3)
    assert [r["i"] for r in got["rows"]] == [0, 1, 2, 3, 4]
    # the pages
    p2 = page(w, ident, 2, 2)
    assert [r["i"] for r in p2["rows"]] == [2, 3] and (p2["pages"], p2["total"]) == (3, 5)
    assert [r["i"] for r in page(w, ident, 3, 2)["rows"]] == [4] and page(w, ident, 9, 2)["rows"] == []
    for bad, name in ((dict(number=0), "page"), (dict(size=0), "size"), (dict(size=10_000), "size")):
        with pytest.raises(ApiError) as e:
            page(w, ident, **bad)
        assert (e.value.status, e.value.extra["param"]) == (422, name)
    # a second calculation replaces the first: one blob, the new mode
    first = owners(w, ident)
    again = xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "hole"})
    assert again["by"] == "hole" and len(owners(w, ident)) == 1 and owners(w, ident) != first
    assert page(w, ident)["by"] == "hole" and all(r["status"] != 0 for r in page(w, ident)["rows"])  # 2 holes


def test_the_table_is_parquet_with_nulls_where_there_is_no_value(w: W) -> None:
    import pyarrow.parquet as pq

    ident = make(w)["id"]
    xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident})  # by hole (the default): 2 holes
    sha = owners(w, ident)[0]
    table = pq.read_table(w.store.path_of(sha))
    assert tuple(table.column_names) == xval.COLUMNS and table.num_rows == 5
    est = table.column("est")
    assert est.null_count == 5 and table.column("pass").null_count == 5  # no neighbours: null, not 0
    assert table.column("true").null_count == 0 and set(table.column("status").to_pylist()) == {2}
    meta = json.loads(table.schema.metadata[xval.META_KEY])
    assert meta["by"] == "hole" and meta["estimation_id"] == ident and meta["summary"]["n"] == 0
    assert meta["summary"]["mean_error"] is None and meta["histogram"] == {"edges": [], "counts": []}
    with dialect.read_tx(w.project.engine) as conn:
        kinds = conn.execute(sa.select(blobs.c.media_type).where(blobs.c.sha256 == sha)).scalar()
    assert kinds == "application/vnd.apache.parquet"


def test_the_page_is_stale_when_the_parameters_change(w: W) -> None:
    ident = make(w)["id"]
    xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"})
    assert page(w, ident)["stale"] is False
    version = get(w, ident)["version"]
    put(w, ident, version, method={"kind": "ok", "min_hint": None}, disc=[2, 2, 2])
    assert page(w, ident)["stale"] is True


def test_refusals_in_russian(w: W) -> None:
    ident = make(w)["id"]
    cases = [({}, "bad_param", "estimation_id"), ({"estimation_id": "1"}, "bad_param", "estimation_id"),
             ({"estimation_id": True}, "bad_param", "estimation_id"),
             ({"estimation_id": ident, "by": "block"}, "bad_param", "by")]  # fmt: skip
    for params, code, param in cases:
        with pytest.raises(ApiError) as e:
            xval.run_xval(w.project.engine, w.project.folder, params)
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, code, param)
        assert e.value.message and e.value.hint
    with pytest.raises(ApiError) as e:
        xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident + 999})
    assert (e.value.status, e.value.code) == (404, "estimation_not_found")
    with pytest.raises(ApiError) as e:
        page(w, ident + 999)
    assert e.value.code == "estimation_not_found"


def test_a_cancel_or_a_failure_leaves_nothing_and_keeps_the_old_table(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = make(w)["id"]
    store_files = lambda: sorted(p.name for p in (w.project.folder / "blobs").rglob("*") if p.is_file())  # noqa: E731
    before = store_files()
    calls = {"n": 0}

    def cancel_at_second_portion(p: float | None = None, m: str | None = None) -> None:
        if m == xval.STAGE_XVAL:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise JobCancelled

    monkeypatch.setattr(xval, "CHUNK", 2)
    with pytest.raises(JobCancelled):
        xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"},
                      cancel_at_second_portion, threads=1)  # fmt: skip
    assert calls["n"] >= 2 and owners(w, ident) == [] and store_files() == before
    xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"})
    old, files = owners(w, ident), store_files()
    assert len(old) == 1

    def cancel_before_write(p: float | None = None, m: str | None = None) -> None:
        if m == xval.STAGE_WRITE:
            raise JobCancelled

    with pytest.raises(JobCancelled):
        xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "hole"}, cancel_before_write)
    assert owners(w, ident) == old and store_files() == files and page(w, ident)["by"] == "sample"

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("write failed")

    monkeypatch.setattr(xval.BlobStore, "commit_staged", boom)
    with pytest.raises(RuntimeError):
        xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "hole"})
    assert owners(w, ident) == old and store_files() == files  # the staged file is dropped, the old table stays


def test_the_blob_goes_with_the_row(w: W) -> None:
    from bf_core.geostat.estimate import store as st

    ident = make(w)["id"]
    xval.run_xval(w.project.engine, w.project.folder, {"estimation_id": ident, "by": "sample"})
    assert len(owners(w, ident)) == 1
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, ident)
    assert owners(w, ident) == []


# --- the job in a worker ------------------------------------------------------------------------------------------


def test_the_job_in_a_worker_gives_the_answer_of_the_contract(  # type: ignore[no-untyped-def]
    pclient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`estimate.xval` registered by name until the orchestrator's line (`jobs/registry.py`): the `spawn` worker
    imports it, runs the heavy job, the answer is `{estimation_id, by, n, summary, events}`."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.jobs.service import jobs_of
    from bf_core.projects.service import projects_of
    from tests import test_vario_store as vs
    from tests.estutil import fill_world
    from tests.test_import_job import API, wait_job

    mod = "bf_core.geostat.estimate.xval"
    if mod not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", mod)
    importlib.import_module(mod)
    assert pclient.post(API + "/projects", json={"name": "Проверка"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    set_id, a, b = vs.fill(project.engine)
    world_ = fill_world(project, set_id, a, b)
    ident = make(world_)["id"]
    key, job_id = jobs_of(pclient.core).submit(xval.KIND, {"estimation_id": ident, "by": "sample"})
    job = wait_job(pclient, key, job_id, timeout=120)
    assert job["status"] == "done", job
    got = job["result"]
    assert (got["estimation_id"], got["by"], got["n"]) == (ident, "sample", 5) and got["summary"]["n"] == 5
    assert got["events"] == [["geostat.changed", {"kind": "estimation", "id": ident}]]
    with dialect.read_tx(project.engine) as conn:
        assert xval.read_page(conn, world_.store, ident)["summary"] == got["summary"]
    key, job_id = jobs_of(pclient.core).submit(xval.KIND, {"estimation_id": ident + 999})
    failed = wait_job(pclient, key, job_id, timeout=120)
    assert failed["status"] == "failed" and failed["error"]["code"] == "estimation_not_found"
    assert failed["error"]["message"] and failed["error"]["hint"]


def test_samples_load_matches_the_stored_origin(w: W) -> None:
    """The job reads the samples with the origin of the row (the same for the estimation, the preview and here)."""
    ident = make(w)["id"]
    meta = get(w, ident)["meta"]
    s = load(w.project, sb.load_spec(meta), origin=np.asarray(meta["origin"]))
    assert s.origin.tolist() == meta["origin"] and s.n == 5
