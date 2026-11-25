"""The post-processing `simulate.post` (подшаг 18.32; ТЗ 8.8.1; ADR 0024 §9; e18-api.md §9): the fields of the block
model are `stats.local.local_*` of the realizations of the file to 1e−12 (the mean, σ, P10/P50/P90, the share ≥ t), the
rows outside the domain are empty (never 0), the sub-blocks take their parent, the cut of the model into chunks changes
no bit, 409 (`field_exists`, `version_conflict`, `no_realizations`, `source_lost`), a cancel and a failure leave no
field, the source and the graph of the fields, the database (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import dh_tables
from bf_core.geostat.simulation import post
from bf_core.jobs.context import JobCancelled
from bf_core.stats import local
from tests import estjob_util as u
from tests.simutil import SW, left_over, make, model_of, put, refused, run, run_post, table, world


@pytest.fixture()
def sw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


@pytest.fixture()
def done(sw: SW) -> tuple[SW, int]:
    ident = make(sw, n_real=6)
    run(sw, simulation_id=ident)
    return sw, ident


def col(sw: SW, name: str, model: int | None = None) -> np.ndarray:
    return model_of(sw, model).columns([name])[name]


def expected_nodes(sw: SW, ident: int) -> dict[str, np.ndarray]:
    """What `local_*` give for the realizations of the file (f4 read back as f8): the nodes in the order of the file."""
    x = table(sw, ident).astype(np.float64)
    q = local.percentiles(x, [10, 50, 90])
    return {"MEAN": local.local_expectation(x), "STD": local.local_std(x), "P10": q[0], "P50": q[1], "P90": q[2],
            "P10c": local.local_percentile(x, 10), "P50c": local.local_percentile(x, 50),
            "P90c": local.local_percentile(x, 90)}  # fmt: skip


# --- the statistics ----------------------------------------------------------------------------------------------


def test_post_is_the_local_functions() -> None:
    rng = np.random.default_rng(3)
    r = rng.lognormal(0.0, 0.7, (20, 500))
    r[3, 10] = np.nan  # a gap in a realization is skipped as `local_*` skip it, never a zero
    r[:, 11] = np.nan  # a node without a value: NaN in every statistic
    got = post.post(r, ("mean", "std", "p10", "p50", "p90", "prob"), (0.5, 2.0))
    assert np.array_equal(got["mean"], local.local_expectation(r), equal_nan=True)
    assert np.array_equal(got["std"], local.local_std(r), equal_nan=True)
    for key, q in (("p10", 10), ("p50", 50), ("p90", 90)):
        assert np.allclose(got[key], local.local_percentile(r, q), rtol=0, atol=1e-12, equal_nan=True)
    for t in (0.5, 2.0):
        assert np.array_equal(got[f"prob:{t!r}"], local.local_prob_exceed(r, t), equal_nan=True)
    assert np.isnan(got["mean"][11]) and np.isnan(got["prob:0.5"][11]) and np.isfinite(got["mean"][10])
    assert sorted(post.post(r, ("p50", "mean"))) == ["mean", "p50"]  # only what is asked


def test_the_fields_are_the_local_functions_of_the_file(done: tuple[SW, int]) -> None:
    sw, ident = done
    out = run_post(sw, ident, thresholds=[1.0, 2.5])
    assert out["fields"] == ["AU_SGS_MEAN", "AU_SGS_STD", "AU_SGS_P10", "AU_SGS_P50", "AU_SGS_P90",
                             "AU_SGS_PROB_GT_1", "AU_SGS_PROB_GT_2_5"]  # fmt: skip
    assert (out["simulation_id"], out["model_id"], out["version"], out["prefix"]) == (ident, sw.model, 2, "AU_SGS_")
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": sw.model}]]
    m = model_of(sw)
    kinds = {f: (m.field(f)["dtype"], m.field(f).get("role"), m.field(f).get("unit")) for f in out["fields"]}
    assert kinds == {"AU_SGS_MEAN": ("f8", "grade", "г/т"), "AU_SGS_STD": ("f8", None, "г/т"),
                     "AU_SGS_P10": ("f8", "grade", "г/т"), "AU_SGS_P50": ("f8", "grade", "г/т"),
                     "AU_SGS_P90": ("f8", "grade", "г/т"), "AU_SGS_PROB_GT_1": ("f4", None, None),
                     "AU_SGS_PROB_GT_2_5": ("f4", None, None)}  # fmt: skip
    src = m.field("AU_SGS_MEAN")["source"]
    assert (src["op"], src["algo"]) == ("simulate", "sim-1") and len(src["inputs"]) == 1
    assert src["params"]["simulation_id"] == ident and src["params"]["thresholds"] == [1.0, 2.5]
    # the model rows: the targets get the value of their node, the others — empty, never 0
    p, dom = m.p(), col(sw, "DOM")
    in_dom = dom == 0
    nodes = np.unique(p[in_dom])
    want = expected_nodes(sw, ident)
    at = np.searchsorted(nodes, p[in_dom])
    for name, key in (("AU_SGS_MEAN", "MEAN"), ("AU_SGS_STD", "STD"), ("AU_SGS_P10", "P10"), ("AU_SGS_P50", "P50"),
                      ("AU_SGS_P90", "P90")):  # fmt: skip
        got = col(sw, name)
        assert np.allclose(got[in_dom], want[key][at], rtol=0, atol=1e-12)
        assert np.all(np.isnan(got[~in_dom])) and np.isfinite(got[in_dom]).all()
        if key.startswith("P"):  # the quick percentiles are `local_percentile`
            assert np.allclose(want[key], want[key + "c"], rtol=0, atol=1e-12)
    x = table(sw, ident).astype(np.float64)
    for t, name in ((1.0, "AU_SGS_PROB_GT_1"), (2.5, "AU_SGS_PROB_GT_2_5")):
        got = col(sw, name)
        prob = local.local_prob_exceed(x, t)[at].astype(np.float32).astype(np.float64)
        assert np.array_equal(got[in_dom], prob) and np.all(np.isnan(got[~in_dom]))
        assert float(got[in_dom].min()) >= 0.0 and float(got[in_dom].max()) <= 1.0
    assert left_over(sw) == []


def test_sub_blocks_take_their_parent_and_the_domain_stays(sw: SW) -> None:
    ident = make(sw, model_id=sw.sub, n_real=5)
    run(sw, simulation_id=ident)
    run_post(sw, ident, model=sw.sub, stats=["mean", "p50"])
    m = model_of(sw, sw.sub)
    p, dom = m.p(), col(sw, "DOM", sw.sub)
    mean, p50 = col(sw, "AU_SGS_MEAN", sw.sub), col(sw, "AU_SGS_P50", sw.sub)
    x = table(sw, ident).astype(np.float64)
    nodes = np.unique(p[dom == 0])
    ok = dom == 0
    assert np.allclose(mean[ok], local.local_expectation(x)[np.searchsorted(nodes, p[ok])], rtol=0, atol=1e-12)
    assert np.allclose(p50[ok], local.local_percentile(x, 50)[np.searchsorted(nodes, p[ok])], rtol=0, atol=1e-12)
    assert np.all(np.isnan(mean[~ok]))  # a sub-block of a target parent in another domain is empty
    some = np.flatnonzero(ok)[:50]
    parents = {int(q) for q in p[some]}
    for q in parents:  # the sub-blocks of one parent in the domain have one value
        v = mean[ok & (p == q)]
        assert np.all(v == v[0])
    assert "AU_SGS_STD" not in [f["name"] for f in m.fields]  # only the statistics asked for


def test_the_chunks_change_no_bit(done: tuple[SW, int], monkeypatch: pytest.MonkeyPatch) -> None:
    sw, ident = done
    run_post(sw, ident, prefix="A_", thresholds=[1.0])
    monkeypatch.setattr(post, "CHUNK", 300)  # the cut of the model and of the nodes of the file
    run_post(sw, ident, prefix="B_", thresholds=[1.0])
    for k in ("MEAN", "STD", "P10", "P50", "P90", "PROB_GT_1"):
        a, b = col(sw, f"A_{k}"), col(sw, f"B_{k}")
        assert np.array_equal(a, b, equal_nan=True), k


# --- 409 / 422 ---------------------------------------------------------------------------------------------------


def test_the_refusals(done: tuple[SW, int]) -> None:
    sw, ident = done
    refused(lambda: run_post(sw, ident + 99), 404, "simulation_not_found")
    refused(lambda: run_post(sw, ident, expected_version=9), 409, "version_conflict")
    refused(lambda: run_post(sw, ident, stats=["prob"]), 422, "bad_param", "thresholds")
    fresh = make(sw, name="Ещё не считалось")
    err = refused(lambda: run_post(sw, fresh), 409, "no_realizations")
    assert "Ещё не считалось" in err.message
    run_post(sw, ident)
    err = refused(lambda: run_post(sw, ident), 409, "field_exists")
    assert err.extra == {"field": "AU_SGS_MEAN"}
    assert col(sw, "AU_SGS_P90").size  # the first run is there
    out = run_post(sw, ident, replace=True, stats=["mean"])
    assert out["fields"] == ["AU_SGS_MEAN"] and out["version"] == 3
    run_post(sw, ident, prefix="Z_", stats=["std"])  # another prefix: free names
    assert "Z_STD" in [f["name"] for f in model_of(sw).fields]
    with dialect.write_tx(sw.project.engine) as conn:
        from bf_core.blocks import store as bm

        bm.delete_block_model(conn, sw.model)
    refused(lambda: run_post(sw, ident, expected_version=3), 409, "source_lost")
    assert left_over(sw) == []


def test_a_stale_simulation_still_posts_with_a_line_in_the_log(done: tuple[SW, int]) -> None:
    sw, ident = done
    with dialect.write_tx(sw.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == sw.b).values(rev=dh_tables.c.rev + 1))
    out = run_post(sw, ident, stats=["mean"])
    assert any("устарело" in line and "Литология" in line for line in out["log"])


# --- a cancel and a failure leave nothing ------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["Расчёт", "Запись"])
def test_a_cancel_leaves_no_field(done: tuple[SW, int], stage: str) -> None:
    sw, ident = done
    version = u.version_of(sw)
    seen = [0]

    def tick(_p: float | None = None, message: str | None = None) -> None:
        if message is not None and message.startswith(stage):
            seen[0] += 1
            if seen[0] >= (2 if stage == "Расчёт" else 1):
                raise JobCancelled

    with pytest.raises(JobCancelled):
        post.run_post(sw.project.engine, sw.project.folder, {"simulation_id": ident, "expected_version": version},
                      tick)  # fmt: skip
    assert u.version_of(sw) == version and left_over(sw) == []
    assert not [f for f in model_of(sw).fields if f["name"].startswith("AU_SGS_")]


def test_a_failure_leaves_no_field(done: tuple[SW, int], monkeypatch: pytest.MonkeyPatch) -> None:
    sw, ident = done
    version = u.version_of(sw)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(post, "_record_fields", boom)
    with pytest.raises(RuntimeError):
        run_post(sw, ident)
    assert u.version_of(sw) == version and left_over(sw) == []  # the transaction of the fields went back
    assert not [f for f in model_of(sw).fields if f["name"].startswith("AU_SGS_")]


# --- the graph ---------------------------------------------------------------------------------------------------


def test_the_fields_are_nodes_of_the_graph(done: tuple[SW, int]) -> None:
    sw, ident = done
    run_post(sw, ident, stats=["mean"])
    node = lineage.Node("bm_field", sw.model, "au_sgs_mean")
    sim = lineage.Node("object:simulation", ident)
    with dialect.read_tx(sw.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "current"
        origin = lineage.origin(conn, node)
        assert [(o["kind"], o["id"]) for o in origin] == [("object:simulation", ident)]
        hit = lineage.impact(conn, sim)  # who is built from the simulation: its fields
        assert [(n["kind"], n["id"], n["sub"]) for n in hit["nodes"]] == [("bm_field", sw.model, "au_sgs_mean")]
    put(sw, ident, 1, seed=5)  # the seed decides the numbers: the realizations, and the fields, are «устарел»
    with dialect.read_tx(sw.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "stale"
        assert lineage.status(conn, [sim])[sim].state == "stale"  # the row: «изменены параметры» (direct check)
    put(sw, ident, 2, seed=777)  # the old one back: the same parameters, the same state
    with dialect.read_tx(sw.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "current"
    with dialect.write_tx(sw.project.engine) as conn:  # the table of the samples changes: the field is «устарел»
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == sw.a).values(rev=dh_tables.c.rev + 1))
    with dialect.read_tx(sw.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "stale"


# --- the same on SQLite and PostgreSQL ---------------------------------------------------------------------------


@pytest.mark.db
def test_post_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The write of the fields and the graph on SQLite and PostgreSQL (`-m db`; BF_TEST_PG)."""
    w = world(tmp_path, db_url)
    try:
        ident = make(w, n_real=5)
        run(w, simulation_id=ident)
        out = run_post(w, ident, thresholds=[1.0])
        assert out["version"] == 2 and len(out["fields"]) == 6
        in_dom = col(w, "DOM") == 0
        assert np.isfinite(col(w, "AU_SGS_MEAN")[in_dom]).all() and np.isnan(col(w, "AU_SGS_MEAN")[~in_dom]).all()
        node = lineage.Node("bm_field", w.model, "au_sgs_mean")
        with dialect.read_tx(w.project.engine) as conn:
            assert lineage.status(conn, [node])[node].state == "current"
        assert left_over(w) == []
    finally:
        w.project.engine.dispose()
