"""The global check of an estimate (ТЗ 8.8.5; ADR 0024 §8; e18-api §8.3–8.4; Э18, 18.16): the norm of 5 % (an estimate
6 % off the declustered mean is flagged, 4 % is not), C̄_VV against the variance of the blocks, the Q-Q of the estimate
against the samples = `stats.qq.qq_samples`, STATUS ≠ 0 are not in the statistics, a gap is not a zero, NN on the fly =
the kernel of the row with the method `nn`, the same bits on 1 and 4 threads, the refusals in Russian, the cancel, the
swath settings, the job in a worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Chunk, Model
from bf_core.db import dialect
from bf_core.geostat.estimate import checks as ck
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate.cov import BlockCov
from bf_core.jobs.context import JobCancelled
from bf_core.stats.qq import qq_samples
from bf_core.stats.weighted import sort_groups
from tests.estutil import W, world
from tests.test_blocks_fields_store import add_fields
from tests.test_estimate_kna import kmake


def add_weighted_samples(w: W, seed: int = 11) -> tuple[int, int]:
    """The holes of `add_samples` (KNA) with a column of declustering weights DECL_W: the dense corner weighs less."""
    from bf_core.drillholes import store as dh
    from tests.test_vario_store import noop

    rng = np.random.default_rng(seed)
    m = model_of(w)
    xy = [(x, y) for x in np.arange(2.5, 17.5, 5.0) for y in np.arange(2.5, 17.5, 5.0)]
    xy += [(x, y) for x in (22.0, 32.0, 42.0) for y in (3.0, 13.0, 23.0, 33.0)]
    xy += [(47.0, 37.0), (28.0, 38.0), (8.0, 36.0)]
    q = m.grid.to_project(np.array([[x, y, 15.0] for x, y in xy]))
    names = [f"K{i:02d}" for i in range(len(xy))]
    with dialect.write_tx(w.project.engine) as conn:
        set_id = dh.create_set(conn, "Для проверки")
        collars = [dh.CollarRow(n, x=float(p[0]), y=float(p[1]), z=float(p[2]), td=15.0, azimuth=0.0, dip=-90.0)
                   for n, p in zip(names, q, strict=True)]  # fmt: skip
        dh.replace_collars(conn, set_id, collars)
    holes = [n for n in names for _ in range(6)]
    frm = [2.5 * k for _ in names for k in range(6)]
    au = [1.0 + 0.04 * xy[i][0] + rng.normal(0, 0.25) for i in range(len(names)) for _ in range(6)]
    dw = [0.4 if xy[i][0] < 18 and xy[i][1] < 18 else 1.6 for i in range(len(names)) for _ in range(6)]
    table = dh.write_table(w.project.engine, set_id, dh.TableData(
        name="Пробы", kind="assay", holes=holes, from_m=frm, to_m=[f + 2.5 for f in frm],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component"), dh.ColumnSpec("DECL_W", "number")],
        values=[au, dw]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(table["table_id"])


@pytest.fixture()
def kw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    w.kna_set, w.kna_table = add_weighted_samples(w)
    yield w
    w.project.engine.dispose()


def row_of(w: W, **over: Any) -> tuple[int, Any, Any]:
    """(id, spec, origin) of a stored row of the world."""
    eid = kmake(w, **over)
    _, _, spec, org = ck._row(w.project.engine, {"estimation_id": eid})  # noqa: SLF001
    return eid, spec, org


def model_of(w: W) -> Model:
    with dialect.read_tx(w.project.engine) as conn:
        return Model.open(conn, w.store, w.model)


def kriged(w: W, spec: Any, org: Any) -> tuple[np.ndarray, np.ndarray]:
    """What the job would write: EST and STATUS of every row of the model by the kernel of the row."""
    m = model_of(w)
    s = sm.load(w.project, spec, origin=org)
    rk = kn.compile_row(spec, s, block_size=tuple(m.grid.regular()), rotation_deg=float(m.grid.rotation))
    res = kn.estimate(rk, m.centres())
    return np.asarray(res.values["EST"]), np.asarray(res.status)


def write(w: W, spec: Any, est: np.ndarray, status: np.ndarray) -> None:
    prefix = spec.outputs.prefix
    add_fields(w.project, w.store, w.model,
               [{"name": prefix + key, "dtype": dt, "replace": True} for key, dt in (("EST", "f8"), ("STATUS", "i1"))],
               {prefix + "EST": est, prefix + "STATUS": status.astype(np.float64)})  # fmt: skip


def reference(w: W, est: np.ndarray, status: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The values and volumes of the blocks that count, by numpy only."""
    vol = model_of(w).volume()
    ok = (status == 0) & np.isfinite(est) & np.isfinite(vol) & (vol > 0)
    return est[ok], vol[ok]


def wmean(v: np.ndarray, w: np.ndarray) -> float:
    return float((v * w).sum() / w.sum())


def wvar(v: np.ndarray, w: np.ndarray) -> float:
    return float((w * (v - wmean(v, w)) ** 2).sum() / w.sum())


def refused(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    assert (e.value.status, e.value.code) == (status, code), (e.value.code, e.value.message)
    assert e.value.message and e.value.hint
    return e.value


def check(w: W, eid: int, **kw: Any) -> dict[str, Any]:
    return ck.run_global(w.project.engine, w.project.folder, {"estimation_id": eid}, **kw)


# --- the pieces -----------------------------------------------------------------------------------


def test_streaming_statistics_do_not_depend_on_the_portions_and_keep_far_values() -> None:
    rng = np.random.default_rng(3)
    v = 1.0e6 + rng.normal(0.0, 2.0, 5000)  # far from zero: the squares must not eat the variance
    w = rng.uniform(0.5, 4.0, 5000)
    sizes = ([5000], [1000] * 5, [1, 4999], [7] * 714 + [2])
    got = []
    for cut in sizes:
        acc, a = ck.Acc(), 0
        for c in cut:
            acc.add(v[a : a + c], w[a : a + c])
            a += c
        got.append(acc.stats())
    for g in got:
        assert g["n"] == 5000 and g["mean"] == pytest.approx(wmean(v, w), rel=1e-14)
        assert g["var"] == pytest.approx(wvar(v, w), rel=1e-9) and g["volume"] == pytest.approx(w.sum(), rel=1e-14)
        assert g["cv"] == pytest.approx(np.sqrt(wvar(v, w)) / wmean(v, w), rel=1e-9)
    assert ck.Acc().stats() == {"n": 0, "volume": None, "mean": None, "var": None, "cv": None}  # nothing — null, not 0


def test_the_norm_is_five_percent_inclusive() -> None:
    assert ck.diff_pct(1.06, 1.0) == pytest.approx(6.0) and ck.diff_pct(0.96, 1.0) == pytest.approx(-4.0)
    assert ck.is_norm(5.0) is True and ck.is_norm(-5.0) is True and ck.is_norm(5.0001) is False
    assert ck.is_norm(-6.0) is False and ck.is_norm(None) is None
    assert ck.diff_pct(1.0, 0.0) is None and ck.diff_pct(None, 1.0) is None  # no reference — no verdict


# --- the global check on a real model -------------------------------------------------------------


def test_an_estimate_6_percent_off_is_flagged_and_4_percent_is_not(kw: W) -> None:
    eid, spec, org = row_of(kw)
    s = sm.load(kw.project, spec, origin=org)
    m_ref = s.means["declus"]
    assert m_ref is not None and m_ref > 0
    n = model_of(kw).rows
    status = np.zeros(n)
    write(kw, spec, np.full(n, 1.06 * m_ref), status)
    biased = check(kw, eid)
    assert biased["diff_pct"] == pytest.approx(6.0, abs=1e-9) and biased["norm"] is False
    assert biased["composites"]["reference"] == "declus"
    assert biased["composites"]["declus"]["mean"] == pytest.approx(m_ref, rel=1e-12)
    write(kw, spec, np.full(n, 0.96 * m_ref), status)  # the new fields replace: a new version of the model
    close = check(kw, eid)
    assert close["diff_pct"] == pytest.approx(-4.0, abs=1e-9) and close["norm"] is True and close["limit_pct"] == 5.0
    assert close["estimate"]["n"] == n and close["estimate"]["var"] == pytest.approx(0.0, abs=1e-18)


def test_the_answer_of_a_real_estimate_matches_numpy_and_nn_on_the_fly(kw: W) -> None:
    eid, spec, org = row_of(kw)
    est, status = kriged(kw, spec, org)
    assert (status == 0).sum() > 0  # the cloud of holes reaches blocks
    write(kw, spec, est, status)
    out = check(kw, eid)
    v, w = reference(kw, est, status)
    e = out["estimate"]
    assert e["n"] == len(v) and e["volume"] == pytest.approx(w.sum(), rel=1e-13)
    assert e["mean"] == pytest.approx(wmean(v, w), rel=1e-12) and e["var"] == pytest.approx(wvar(v, w), rel=1e-9)
    assert out["field"] == spec.outputs.prefix + "EST" and out["estimation_id"] == eid and out["name"]
    # NN: the kernel of the row with the method nn at the centres of the same blocks (nothing was written for it)
    m = model_of(kw)
    s = sm.load(kw.project, spec, origin=org)
    rk = kn.compile_row(ck.nn_spec(spec), s)
    res = kn.estimate(rk, m.centres())
    nn = np.asarray(res.values["EST"])
    vol = m.volume()
    ok = (status == 0) & (res.status == 0) & np.isfinite(nn) & (vol > 0)
    assert out["nn"]["n"] == int(ok.sum()) and out["nn"]["mean"] == pytest.approx(wmean(nn[ok], vol[ok]), rel=1e-12)
    assert out["nn"]["var"] == pytest.approx(wvar(nn[ok], vol[ok]), rel=1e-9)
    d = 100.0 * (e["mean"] - out["nn"]["mean"]) / out["nn"]["mean"]
    assert out["diff_nn_pct"] == pytest.approx(d, rel=1e-9) and out["norm_nn"] is (abs(d) <= 5.0)
    ref = out["composites"]["declus"]["mean"]
    assert out["diff_pct"] == pytest.approx(100.0 * (e["mean"] - ref) / ref, rel=1e-9)
    # the same samples as the row: naive and declustered statistics
    z = s.z[s.dom == sm.D]
    assert out["composites"]["naive"]["n"] == len(z) and out["composites"]["naive"]["mean"] == pytest.approx(z.mean())
    assert out["blocks"]["by_status"] == [int((status == c).sum()) for c in range(4)]


def test_blocks_with_another_status_and_gaps_are_not_in_the_statistics_and_not_zeros(kw: W) -> None:
    eid, spec, org = row_of(kw)
    n = model_of(kw).rows
    rng = np.random.default_rng(8)
    est = rng.uniform(1.0, 3.0, n)
    status = np.zeros(n)
    status[::7] = 2  # few samples
    status[3::11] = 3  # degenerate
    status[5::13] = 1  # outside of the domain
    est[status != 0] = 1.0e9  # whatever a stale or foreign value is: it must not count
    gaps = np.flatnonzero(status == 0)[:6]
    est[gaps] = np.nan  # STATUS 0 without a value: out of the sum, counted, never 0
    write(kw, spec, est, status)
    out = check(kw, eid)
    v, w = reference(kw, est, status)
    assert out["estimate"]["n"] == len(v) < int((status == 0).sum())
    assert out["estimate"]["mean"] == pytest.approx(wmean(v, w), rel=1e-12) and out["estimate"]["mean"] < 3.0
    assert out["blocks"]["empty"] == 6 and out["blocks"]["used"] == len(v)
    assert out["blocks"]["by_status"] == [int((status == c).sum()) for c in range(4)]
    assert out["warnings"] and all(isinstance(x, str) for x in out["warnings"])
    zero = est.copy()
    zero[gaps] = 0.0  # had the gaps been zeros the mean would be lower: it is not
    vz, wz = reference(kw, zero, status)
    assert out["estimate"]["mean"] > wmean(vz, wz)


def test_blocks_of_another_domain_of_the_prefix_are_not_in_the_checks(kw: W) -> None:
    """№ 77: rows of different domains write one field of a prefix; the checks of a row count its domain only."""
    eid, spec, org = row_of(kw, target={"domain_field": "DOM", "label": "руда"})
    m = model_of(kw)
    code = m.columns(["DOM"])["DOM"]
    mine = code == m.labels("DOM").index("руда")
    assert 0 < int(mine.sum()) < m.rows
    est = np.where(mine, 1.5, 1.0e9)  # the sibling's blocks hold STATUS 0 and a value of their own
    write(kw, spec, est, np.zeros(m.rows))
    out = check(kw, eid)
    assert out["estimate"]["n"] == int(mine.sum()) and out["estimate"]["mean"] == pytest.approx(1.5, rel=1e-12)
    assert out["blocks"]["by_status"] == [int(mine.sum()), 0, 0, 0]
    got = ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid})
    assert got["y"]["n"] == int(mine.sum()) and got["extremes"]["y"][1] < 3.0


def test_the_variance_of_the_blocks_is_compared_with_cvv_of_the_model(kw: W) -> None:
    eid, spec, org = row_of(kw)
    est, status = kriged(kw, spec, org)
    write(kw, spec, est, status)
    out = check(kw, eid)
    m = model_of(kw)
    cm = kn._variogram_cov(spec)  # noqa: SLF001
    size = tuple(m.grid.regular())
    cvv = BlockCov(cm).vv(size, spec.disc, float(m.grid.rotation))  # the row discretizes its blocks (2×2×1)
    bv = out["block_var"]
    assert bv["expected"] == pytest.approx(cvv, rel=1e-13) and bv["sill"] == pytest.approx(cm.sill, rel=1e-14)
    assert 0.0 < bv["expected"] < bv["sill"]  # smoothing within a block: C̄_VV < C(0)
    assert bv["size"] == pytest.approx(list(size)) and bv["disc"] == list(spec.disc)
    v, w = reference(kw, est, status)
    assert bv["estimate"] == pytest.approx(wvar(v, w), rel=1e-9)
    assert bv["ratio"] == pytest.approx(bv["estimate"] / cvv, rel=1e-9)
    # a point row has the support of a block all the same: 4×4×4
    _, point, porg = row_of(kw, disc=[1, 1, 1], name="Точка")
    got = ck.expected_variance(point, float(m.grid.rotation), size)
    assert got[0] == pytest.approx(BlockCov(cm).vv(size, (4, 4, 4), float(m.grid.rotation))) and got[2] == (4, 4, 4)
    assert porg is not None


def test_nn_and_idw_without_a_variogram_have_no_expected_variance(kw: W) -> None:
    _, spec, _ = row_of(kw, method={"kind": "nn"}, variogram=None, name="NN")
    assert ck.expected_variance(spec, 30.0, (5.0, 5.0, 2.5)) == (None, None, None)


def test_threads_give_the_same_bits(kw: W) -> None:
    eid, spec, org = row_of(kw)
    est, status = kriged(kw, spec, org)
    write(kw, spec, est, status)
    assert check(kw, eid, threads=1) == check(kw, eid, threads=4)


def test_a_row_without_weights_is_compared_with_the_naive_mean(kw: W) -> None:
    eid, spec, org = row_of(kw, samples={"set_id": kw.kna_set, "table": str(kw.kna_table), "field": "AU",
                                         "weights": False}, name="Без весов")  # fmt: skip
    est, status = kriged(kw, spec, org)
    write(kw, spec, est, status)
    out = check(kw, eid)
    assert out["composites"]["declus"] is None and out["composites"]["reference"] == "naive"
    assert any("весов" in x for x in out["warnings"])
    ref = out["composites"]["naive"]["mean"]
    assert out["diff_pct"] == pytest.approx(100.0 * (out["estimate"]["mean"] - ref) / ref, rel=1e-9)


# --- the refusals and the cancel ------------------------------------------------------------------


def test_a_model_without_the_fields_of_the_row_is_not_estimated(kw: W) -> None:
    eid, _, _ = row_of(kw)
    err = refused(lambda: check(kw, eid), 409, "not_estimated")
    assert "AU" in err.message and err.extra["param"] == "estimation_id"
    refused(lambda: ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid}), 409, "not_estimated")


def test_no_block_with_status_0_is_a_refusal_not_a_zero(kw: W) -> None:
    eid, spec, _ = row_of(kw)
    n = model_of(kw).rows
    write(kw, spec, np.full(n, np.nan), np.full(n, 2.0))
    refused(lambda: check(kw, eid), 422, "no_estimated_blocks")
    refused(lambda: ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid}), 422, "no_values")


def test_bad_bodies_and_a_gone_row(kw: W) -> None:
    for params in ({}, {"estimation_id": True}, {"estimation_id": "1"}, {"estimation_id": 0}):
        refused(lambda p=params: ck.run_global(kw.project.engine, kw.project.folder, p), 422, "bad_param")  # type: ignore[misc]
    refused(lambda: check(kw, 99999), 404, "estimation_not_found")
    eid, _, _ = row_of(kw)
    for extra, param in (({"weighted": "yes"}, "weighted"), ({"quantiles": 1}, "quantiles"),
                         ({"quantiles": 2.5}, "quantiles")):  # fmt: skip
        err = refused(lambda e=extra: ck.run_qq(kw.project.engine, kw.project.folder,  # type: ignore[misc]
                                                {"estimation_id": eid, **e}), 422, "bad_param")  # fmt: skip
        assert err.extra["param"] == param


def test_cancel_raises_and_changes_nothing(kw: W) -> None:
    eid, spec, org = row_of(kw)
    est, status = kriged(kw, spec, org)
    write(kw, spec, est, status)
    before = model_of(kw).version
    calls = [0]

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        calls[0] += 1
        if calls[0] > 3:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        check(kw, eid, tick=tick)
    assert model_of(kw).version == before
    with dialect.read_tx(kw.project.engine) as conn:
        from bf_core.geostat.estimate import store as st

        assert st.get(conn, eid)["name"]


# --- Q-Q ------------------------------------------------------------------------------------------


def test_qq_is_qq_samples_of_the_samples_and_the_blocks(kw: W) -> None:
    eid, spec, org = row_of(kw)
    est, status = kriged(kw, spec, org)
    write(kw, spec, est, status)
    got = ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid})
    s = sm.load(kw.project, spec, origin=org)
    d = s.dom == sm.D
    v, w = reference(kw, est, status)
    want = qq_samples(sort_groups(s.z[d], s.w[d])[0], sort_groups(v, w)[0], 99)
    for key in ("points", "ratio", "extremes", "ks", "marks"):
        assert got[key] == want[key], key
    assert got["weighted"] is True and got["estimation_id"] == eid and len(got["points"]) == 99
    assert got["x"]["n"] == int(d.sum()) and got["y"]["n"] == len(v) and got["field"] == spec.outputs.prefix + "EST"
    plain = ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid, "quantiles": 19, "weighted": False})
    unweighted = qq_samples(sort_groups(s.z[d], np.ones(int(d.sum())))[0], sort_groups(v, w)[0], 19)
    assert plain["points"] == unweighted["points"] and plain["weighted"] is False and len(plain["points"]) == 19
    assert json.loads(json.dumps(got)) == got  # an answer of JSON: gaps are null, nothing but numbers and text


def test_qq_of_blocks_without_status_0_leaves_them_out(kw: W) -> None:
    eid, spec, org = row_of(kw)
    n = model_of(kw).rows
    est = np.linspace(1.0, 2.0, n)
    status = np.zeros(n)
    status[::2] = 3
    est[status == 3] = 1.0e9
    write(kw, spec, est, status)
    got = ck.run_qq(kw.project.engine, kw.project.folder, {"estimation_id": eid})
    assert got["y"]["n"] == int((status == 0).sum()) and got["extremes"]["y"][1] < 3.0


# --- the draft of the swath, the portion of a parent ----------------------------------------------


def test_swath_settings_are_the_series_of_the_row(kw: W) -> None:
    _, spec, _ = row_of(kw, target={"domain_field": "DOM", "label": "руда"})
    s = ck.swath_settings(spec)
    blocks, samples = s["series"]
    assert blocks == {
        "kind": "blocks",
        "model_id": kw.model,
        "field": spec.outputs.prefix + "EST",
        "weight": "volume",
        "density": None,
        "filter": {"field": "DOM", "labels": ["руда"]},
        "filter_id": None,
    }
    assert samples["kind"] == "samples" and samples["within"] == "blocks" and samples["within_series"] == 0
    assert samples["weight"] == "field" and samples["weight_field"] == "DECL_W" and samples["field"] == "AU"
    _, nospec, _ = row_of(kw, samples={"set_id": kw.kna_set, "table": str(kw.kna_table), "field": "AU",
                                       "weights": False}, name="Без весов 2")  # fmt: skip
    assert ck.swath_settings(nospec)["series"][1]["weight"] == "none"
    assert ck.swath_settings(nospec)["series"][0]["filter"] is None


def test_block_centres_of_a_parent_are_estimated_once(kw: W) -> None:
    """`sub: parent`: the rows of one parent are estimated once, at the parent's centre; a row takes its parent's."""
    from bf_core.blocks import geometry

    m = model_of(kw)
    p = m.p()[:6]
    p = np.concatenate([p[:3], p[:3], p[3:]])[[0, 3, 1, 4, 2, 5, 6, 7, 8]]  # three parents twice, then three once
    chunk = Chunk(0, p, None, None, {})
    part = ck.Part(chunk, np.arange(len(p)), np.ones(len(p)), np.ones(len(p)), np.zeros(4, np.int64), 0, 0)
    centres, back = ck.block_centres(m, part, True)
    assert len(centres) == len(np.unique(p)) and np.array_equal(centres[back], geometry.centres(m.grid, p))
    own, same = ck.block_centres(m, part, False)
    assert np.array_equal(own, geometry.centres(m.grid, p)) and np.array_equal(same, np.arange(len(p)))
    assert ck.block_size(m, part, True) == tuple(m.grid.regular())


# --- the job in a worker --------------------------------------------------------------------------


def test_the_job_in_a_worker_gives_what_the_body_gives(pclient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`estimate.global` registered here and in the worker (`BF_EXTRA_JOB_MODULES`) until the line of the orchestrator
    (`jobs/registry.py`); the route is 18.13's."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.jobs.service import jobs_of
    from bf_core.projects.service import projects_of
    from tests import estutil as eu
    from tests.test_import_job import API, wait_job
    from tests.test_vario_store import fill

    module = "bf_core.geostat.estimate.checks"
    if module not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", module)
    importlib.import_module(module)
    assert pclient.post(API + "/projects", json={"name": "Проверка"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    set_id, a, b = fill(project.engine)
    w = eu.fill_world(project, set_id, a, b)
    w.kna_set, w.kna_table = add_weighted_samples(w)
    eid, spec, org = row_of(w)
    est, status = kriged(w, spec, org)
    write(w, spec, est, status)
    params = {"estimation_id": eid}
    here = ck.run_global(project.engine, project.folder, params)
    key, job_id = jobs_of(pclient.core).submit(ck.KIND, params, title="Глобальная проверка")
    job = wait_job(pclient, key, job_id, timeout=240)
    assert job["status"] == "done", job
    assert job["result"] == json.loads(json.dumps(here)) and job["result"]["norm"] in (True, False)
    bad = jobs_of(pclient.core).submit(ck.KIND, {"estimation_id": 999_999}, title="Глобальная проверка")
    failed = wait_job(pclient, *bad, timeout=120)
    assert failed["status"] == "failed" and failed["error"]["code"] == "estimation_not_found"
    assert failed["error"]["hint"]
