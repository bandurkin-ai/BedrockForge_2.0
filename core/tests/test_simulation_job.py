"""The job `simulate.run` (подшаг 18.32; ТЗ 8.8.1; ADR 0024 §2, §9; e18-api.md §9): the file of the realizations
(p, dom, r001 … f4) and the targets (the parents of the blocks of the domain; the sub-blocks take their parent, № 83),
the same bits as the kernel on the same frame, on 1, 2, 4 threads (Т8.8-13), the frame of a turned model (the variogram
model and the ellipsoid keep their angles relative to the grid axes), a body makes its row, a repeat replaces the
file, a cancel and a failure leave nothing, 404 / 409 / 422, the stages, `memory_mb`, the graph, the database
(`-m db`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.drillholes import store as dh
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.simulation import job, nodes, real, sgs
from bf_core.geostat.simulation import store as st
from bf_core.geostat.simulation import store_check as sc
from bf_core.geostat.variogram.model import parse_model
from bf_core.jobs.context import JobCancelled
from bf_core.storage.blobs import BlobStore
from tests.simutil import SW, body, get, left_over, make, model_of, put, reals, refused, run, sha_of, table, world


@pytest.fixture()
def sw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def targets_of(sw: SW, model: int | None = None, label: int = 0) -> np.ndarray:
    """The parents of the blocks of the domain «руда» (the code 0 of DOM), ascending, as the file must have them."""
    m = model_of(sw, model)
    dom = m.columns(["DOM"])["DOM"]
    return np.unique(m.p()[dom == label]).astype(np.uint64)


# --- the file ----------------------------------------------------------------------------------------------------


def test_the_job_writes_the_realizations(sw: SW) -> None:
    ident = make(sw)
    out = run(sw, simulation_id=ident)
    assert (out["simulation_id"], out["realizations"]) == (ident, 4)
    assert sorted(out) == ["counts", "events", "log", "nodes", "realizations", "seconds", "simulation_id", "stages"]
    assert out["events"] == [["geostat.changed", {"kind": "simulation", "id": ident}]]
    p = targets_of(sw)
    assert out["nodes"] == p.size == 980
    r = reals(sw, ident)
    assert (r.n, r.n_real, r.columns) == (p.size, 4, ["p", "dom", "r001", "r002", "r003", "r004"])
    assert np.array_equal(r.p(), p) and not r.dom().any()
    x = table(sw, ident)
    assert x.dtype == np.float32 and bool(np.isfinite(x).all())  # a gap is never a value that is not a number
    assert not np.array_equal(x[0], x[1])  # the realizations differ
    got = get(sw, ident)
    last = got["meta"]["last_run"]
    assert (last["nodes"], last["realizations"], last["job"]) == (p.size, 4, None) and last["params"]
    assert got["file"]["rows"] == p.size and got["file"]["columns"] == r.columns and got["stale"] is False
    assert last["counts"] == {"degenerate": 0, "free": 0, "on_sample": 0} and last["cond"]["max"] == 24
    assert len(last["summary"]["per"]) == 4 and last["summary"]["all"]["min"] > 0.0  # grades are positive (AU)
    one = last["summary"]["per"][0]
    assert one["n"] == p.size and abs(one["mean"] - float(x[0].astype(np.float64).mean())) < 1e-4
    with dialect.read_tx(sw.project.engine) as conn:
        node = lineage.Node("object:simulation", ident)
        assert lineage.status(conn, [node])[node].state == "current"
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
            blob_owners.c.owner_id == ident, blob_owners.c.role == "real")).scalar() == 1
    assert left_over(sw) == []


def test_the_summary_is_that_of_the_file(sw: SW) -> None:
    ident = make(sw)
    run(sw, simulation_id=ident)
    kept = get(sw, ident)["meta"]["last_run"]["summary"]
    from_file = real.summary(reals(sw, ident))
    for a, b in zip(kept["per"], from_file["per"], strict=True):
        assert a["n"] == b["n"]
        for key in ("mean", "std", "min", "max"):
            assert abs(a[key] - b[key]) <= 1e-5 * max(1.0, abs(a[key]))  # the file keeps f4
    assert abs(kept["all"]["mean"] - from_file["all"]["mean"]) < 1e-5


def test_the_kernel_on_the_same_frame_gives_the_same_bits(sw: SW) -> None:
    """The job is the kernel and nothing else: the realizations of the file are `sgs.simulate` on the grid frame."""
    ident = make(sw, n_real=3)
    run(sw, simulation_id=ident)
    meta = get(sw, ident)["meta"]
    spec = sc.load_spec(meta)
    m = model_of(sw)
    samples = sm.load(sw.project, spec)
    p = targets_of(sw)
    ng = nodes.NodeGrid.of(m.grid.counts, p)
    steps = m.grid.regular()
    local = dataclasses.replace(samples, local=samples.xyz - np.asarray(m.grid.corner))
    assert spec.variogram is not None and spec.variogram.snapshot is not None
    par = sgs.SgsParams(parse_model(spec.variogram.snapshot), sgs_ell(spec), steps, 777, 3, 24, 8)
    res = sgs.simulate(par, local, ng, sgs.grid_centres(ng, steps), threads=1)
    assert np.array_equal(table(sw, ident), res.real.astype(np.float32))


def sgs_ell(spec: Any) -> Any:
    from bf_core.geostat.estimate.search import Ellipsoid

    return Ellipsoid.of(spec.search.angles, spec.search.radii)


def test_the_same_bits_on_any_number_of_threads(sw: SW) -> None:
    """Т8.8-13: a seed gives the same file whatever the number of threads; another seed gives another one."""
    ident = make(sw)
    shas = []
    for threads in (1, 2, 4):
        run(sw, threads=threads, simulation_id=ident)
        shas.append(sha_of(sw, ident))
    assert len(set(shas)) == 1 and shas[0] is not None
    put(sw, ident, 1, seed=778)
    run(sw, simulation_id=ident)
    assert sha_of(sw, ident) != shas[0]


def test_a_repeat_replaces_the_file_and_keeps_one_owner(sw: SW) -> None:
    ident = make(sw)
    run(sw, simulation_id=ident)
    first = sha_of(sw, ident)
    put(sw, ident, 1, n_real=3)
    out = run(sw, simulation_id=ident)
    assert out["realizations"] == 3 and sha_of(sw, ident) != first and reals(sw, ident).n_real == 3
    with dialect.read_tx(sw.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
            blob_owners.c.owner_id == ident, blob_owners.c.role == "real")).scalar() == 1
    assert get(sw, ident)["stale"] is False  # the row was run with these parameters


# --- the frame: sub-blocks, a turn -------------------------------------------------------------------------------


def test_sub_blocks_take_the_parents_and_a_turned_model_runs(sw: SW) -> None:
    """№ 83: the nodes are the parents of the blocks of the domain; the model with a turn of 20° and sub-blocks."""
    assert model_of(sw, sw.sub).grid.rotation == 20 and model_of(sw, sw.sub).sub
    ident = make(sw, model_id=sw.sub)
    out = run(sw, simulation_id=ident)
    p = targets_of(sw, sw.sub)
    assert out["nodes"] == p.size and 0 < p.size < model_of(sw, sw.sub).parents
    r = reals(sw, ident)
    assert np.array_equal(r.p(), p) and bool(np.isfinite(table(sw, ident)).all())


def test_the_nodes_of_the_grid_frame_are_the_centres_of_the_parents(sw: SW) -> None:
    """The centres of the kernel (local axes of the grid) turned back to the project are the centres of the parents
    of the model (a turn of 20°): the nodes, the samples and the model are in one frame."""
    m = model_of(sw, sw.sub)
    p = targets_of(sw, sw.sub)
    ng = nodes.NodeGrid.of(m.grid.counts, p)
    local = sgs.grid_centres(ng, m.grid.regular())
    project = m.grid.to_project(local)
    rows = m.p().astype(np.int64)
    whole = np.flatnonzero(np.bincount(rows)[rows] == 1)  # a parent that is one row: the row is the parent
    whole = whole[np.isin(rows[whole], p.astype(np.int64))]
    assert whole.size > 20
    at = np.searchsorted(p, m.p()[whole])
    assert np.allclose(m.centres(whole), project[at], rtol=0, atol=1e-9)
    assert np.allclose(m.grid.to_local(project), local, rtol=0, atol=1e-9)


def test_the_frame_keeps_the_angles_relative_to_the_grid(sw: SW) -> None:
    """`grid_frame`: C′(R_z(−φ)·d) = C(d) for the variogram model and h′ = h for the ellipsoid, at any angles."""
    from bf_core.geostat.rotation import rotation_matrix

    ident = make(sw, model_id=sw.sub, search={"from": "manual", "r1": 50, "r2": 30, "r3": 20, "ang1": 35, "ang2": 10,
                                               "ang3": 5})  # fmt: skip
    spec = sc.load_spec(get(sw, ident)["meta"])
    snap = dict(spec.variogram.snapshot)  # type: ignore[union-attr]
    snap["structures"] = [{**s, "ang1": 40.0 + 10 * i, "ang2": 8.0, "ang3": 3.0}
                          for i, s in enumerate(snap["structures"])]  # fmt: skip
    spec = dataclasses.replace(spec, variogram=dataclasses.replace(spec.variogram, snapshot=snap))
    samples = sm.load(sw.project, spec)
    grid = model_of(sw, sw.sub).grid
    phi = np.deg2rad(grid.rotation)
    kmodel, ell, local, steps = job.grid_frame(spec, samples, grid)
    model0 = parse_model(snap)
    assert steps == grid.regular() and np.array_equal(local.xyz, samples.xyz)
    assert np.allclose(local.local, grid.to_local(samples.xyz), rtol=0, atol=1e-12)
    rng = np.random.default_rng(5)
    d = rng.normal(0.0, 30.0, (200, 3))
    rz = np.array([[np.cos(phi), -np.sin(phi), 0.0], [np.sin(phi), np.cos(phi), 0.0], [0.0, 0.0, 1.0]])
    dl = d @ rz  # R_z(−φ)·d for the rows
    assert np.allclose(kmodel.cov(dl), model0.cov(d), rtol=0, atol=1e-12)
    from bf_core.geostat.estimate.search import Ellipsoid

    ell0 = Ellipsoid.of(spec.search.angles, spec.search.radii)
    assert np.allclose(ell.h(dl), ell0.h(d), rtol=1e-12, atol=1e-12)
    assert rotation_matrix(10.0, 0.0, 0.0).shape == (3, 3)
    # no turn: the same model, the angles untouched
    plain = model_of(sw).grid
    k2, e2, _l, _s = job.grid_frame(spec, samples, plain)
    assert k2.structures == model0.structures and e2.angles == ell0.angles


def test_a_soft_boundary_and_the_passes_run(sw: SW) -> None:
    soft = {**body(sw)["samples"], "soft": {"domains": ["перв"], "dist": 6}}
    ident = make(sw, samples=soft, search={"passes": [{"n_max": 12, "n_oct": 3, "factor": 0.8}]})
    out = run(sw, simulation_id=ident)
    assert out["counts"]["cond"]["max"] <= 12 and bool(np.isfinite(table(sw, ident)).all())


# --- a body: the row is made at the end --------------------------------------------------------------------------


def test_a_body_makes_its_row_in_the_same_transaction(sw: SW) -> None:
    out = run(sw, simulation=body(sw, name="Из тела"))
    ident = out["simulation_id"]
    got = get(sw, ident)
    assert (got["name"], got["version"], got["meta"]["last_run"]["nodes"]) == ("Из тела", 1, 980)
    assert got["meta"]["variogram"]["scale"] == "y" and got["file"] is not None and not got["stale"]
    refused(lambda: run(sw, simulation=body(sw, name="ИЗ ТЕЛА")), 409, "name_taken", "name")  # before the long work
    again = run(sw, simulation=body(sw))["simulation_id"]
    assert get(sw, again)["name"] == "AU · SGS · руда" and again != ident
    assert sha_of(sw, again) == sha_of(sw, ident)  # the same parameters, the same file
    assert left_over(sw) == []


# --- a cancel and a failure leave nothing ------------------------------------------------------------------------


def rows_count(sw: SW) -> int:
    with dialect.read_tx(sw.project.engine) as conn:
        return int(conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind == "simulation")).scalar() or 0)


def cancel_at(stage: str, after: int = 1) -> Any:
    seen = [0]

    def tick(_p: float | None = None, message: str | None = None) -> None:
        if message is not None and message.startswith(stage):
            seen[0] += 1
            if seen[0] >= after:
                raise JobCancelled

    return tick


@pytest.mark.parametrize(("stage", "after"), [("Пробы", 2), ("Нормальные оценки", 1), ("Реализации", 2),
                                              ("Запись", 1)])  # fmt: skip
def test_a_cancel_leaves_nothing(sw: SW, stage: str, after: int) -> None:
    n0 = rows_count(sw)
    with pytest.raises(JobCancelled):
        job.run_simulate(sw.project.engine, sw.project.folder, {"simulation": body(sw)}, cancel_at(stage, after),
                         threads=2)  # fmt: skip
    assert rows_count(sw) == n0 and left_over(sw) == []
    ident = make(sw)
    run(sw, simulation_id=ident)
    sha = sha_of(sw, ident)
    meta = get(sw, ident)["meta"]
    put(sw, ident, 1, seed=9)
    with pytest.raises(JobCancelled):
        job.run_simulate(sw.project.engine, sw.project.folder, {"simulation_id": ident}, cancel_at(stage, after),
                         threads=2)  # fmt: skip
    got = get(sw, ident)
    assert sha_of(sw, ident) == sha and got["meta"]["last_run"] == meta["last_run"] and left_over(sw) == []


def test_a_failure_in_the_commit_leaves_nothing(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(st, "commit_run", boom)
    n0 = rows_count(sw)
    with pytest.raises(RuntimeError):
        run(sw, simulation=body(sw))
    assert rows_count(sw) == n0 and left_over(sw) == []  # the row of the body was made in the same transaction


def test_a_failure_after_the_write_leaves_no_file(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    """The summary fails (the temp file is already written), or the commit fails after the file was placed (the row of
    `blobs` rolls back): no temp file and no orphan file by the SHA."""
    staged: list[Any] = []
    real_write = real.write

    def spy(*a: Any, **k: Any) -> Any:
        out = real_write(*a, **k)
        staged.append(out)
        return out

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(real, "write", spy)
    monkeypatch.setattr(real, "stats_of", boom)
    with pytest.raises(RuntimeError):
        run(sw, simulation=body(sw))
    assert left_over(sw) == [] and not BlobStore(sw.project.folder).path_of(staged[-1][1]).exists()
    monkeypatch.undo()
    monkeypatch.setattr(real, "write", spy)
    monkeypatch.setattr(st, "_lineage", boom)  # after `commit_staged` has put the file in place
    n0 = rows_count(sw)
    with pytest.raises(RuntimeError):
        run(sw, simulation=body(sw))
    assert rows_count(sw) == n0 and left_over(sw) == []
    assert not BlobStore(sw.project.folder).path_of(staged[-1][1]).exists()


def test_a_stale_version_leaves_no_file_by_the_sha(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = make(sw)
    staged: list[Any] = []
    real_write = real.write

    def write_then_edit(*a: Any, **k: Any) -> Any:
        out = real_write(*a, **k)
        staged.append(out)
        with dialect.write_tx(sw.project.engine) as conn:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(version=7))
        return out

    monkeypatch.setattr(real, "write", write_then_edit)
    refused(lambda: run(sw, simulation_id=ident), 409, "version_conflict")
    assert not BlobStore(sw.project.folder).path_of(staged[-1][1]).exists() and left_over(sw) == []


def test_a_stale_version_at_the_commit(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another window edits the row while the job runs: 409, the file is not placed."""
    ident = make(sw)
    real_write = real.write

    def write_then_edit(*a: Any, **k: Any) -> Any:
        out = real_write(*a, **k)
        with dialect.write_tx(sw.project.engine) as conn:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(version=7))
        return out

    monkeypatch.setattr(real, "write", write_then_edit)
    err = refused(lambda: run(sw, simulation_id=ident), 409, "version_conflict")
    assert err.extra == {"version": 7} and sha_of(sw, ident) is None and left_over(sw) == []


# --- the refusals ------------------------------------------------------------------------------------------------


def test_the_refusals_before_the_work(sw: SW) -> None:
    ident = make(sw)
    refused(lambda: run(sw, simulation_id=ident + 99), 404, "simulation_not_found")
    refused(lambda: run(sw, simulation_id=ident, expected_version=3), 409, "version_conflict")
    refused(lambda: run(sw), 422, "bad_param", "simulation_id")
    refused(lambda: run(sw, simulation=body(sw, kind="sis")), 422, "bad_param", "method")
    refused(lambda: run(sw, simulation=body(sw, variogram={"model_id": sw.vz})), 422, "variogram_scale",
            "variogram.model_id")  # fmt: skip
    refused(lambda: run(sw, simulation=body(sw, model_id=sw.model + 99)), 404, "block_model_not_found", "model_id")
    refused(lambda: run(sw, simulation=body(sw, n_real=101)), 422, "bad_param", "n_real")
    assert run(sw, simulation_id=ident, expected_version=1)["nodes"] == 980
    with dialect.write_tx(sw.project.engine) as conn:
        bm.delete_block_model(conn, sw.model)
    refused(lambda: run(sw, simulation_id=ident), 409, "source_lost")
    assert left_over(sw) == []


def test_no_targets_and_no_samples(sw: SW) -> None:
    with dialect.write_tx(sw.project.engine) as conn:  # a model whose blocks are all «порода»
        grid = {"corner": [0.0, 0.0, 60.0], "counts": [3, 3, 2], "size": [12.0] * 3}
        empty = bm.put_block_model(conn, sw.store, grid, [{"name": "DOM", "kind": "category",
                                                           "labels": ["руда", "порода"]}],
                                   np.arange(18, dtype=np.uint64), {"DOM": np.ones(18, dtype=np.int32)},
                                   name="Пустая")  # fmt: skip
    refused(lambda: run(sw, simulation=body(sw, model_id=empty)), 422, "no_targets", "target")
    dh.write_table(sw.project.engine, sw.set_id, dh.TableData(
        name="Пусто", kind="assay", holes=["H0-0"], from_m=[0.0], to_m=[2.0],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component")], values=[[None]]), job_id=None,
        tick=lambda *_: None)  # fmt: skip
    over = {"samples": {"set_id": sw.set_id, "table": "Пусто", "field": "AU"}}
    err = refused(lambda: run(sw, simulation=body(sw, **over)), 422, "no_samples", "samples")
    assert err.extra["counts"]["missing"] == 1
    assert left_over(sw) == []


# --- the stages, the memory --------------------------------------------------------------------------------------


def test_the_stages_and_the_progress(sw: SW) -> None:
    ident = make(sw, n_real=3)
    ticks: list[tuple[float | None, str | None]] = []
    out = job.run_simulate(sw.project.engine, sw.project.folder, {"simulation_id": ident},
                           lambda p, m=None: ticks.append((p, m)), threads=2)  # fmt: skip
    messages = [m for _p, m in ticks if m]
    first = [m for i, m in enumerate(messages) if m not in messages[:i]]
    assert first[0] == "Пробы" and "Нормальные оценки" in first and first[-1] == "Запись"
    assert [m for m in first if m.startswith("Реализации")] and first.index("Нормальные оценки") < first.index(
        next(m for m in first if m.startswith("Реализации")))  # fmt: skip
    progress = [p for p, _m in ticks if p is not None]
    assert progress == sorted(progress) and progress[-1] == 1.0 and 0.0 < progress[0] < 0.1
    assert set(out["stages"]) >= {"samples", "scores", "realizations", "write", "total"}


def test_the_memory_follows_the_work() -> None:
    small = job.memory_mb({"nodes": 10_000, "n_real": 20, "threads": 2})
    big = job.memory_mb({"nodes": 20_000_000, "n_real": 20, "threads": 2})
    many = job.memory_mb({"nodes": 20_000_000, "n_real": 100, "threads": 2})
    assert 300 < small < big < many and big > 3000  # 20 млн узлов × 20 реализаций — гигабайты, планировщик ждёт
    assert job._threads(8, 20_000_000, 20, 24, None, 1000) == 8  # noqa: SLF001
    assert job._threads(8, 20_000_000, 20, 24, 100.0, 1000) == 1  # noqa: SLF001  # a budget below the fixed part
    assert job._threads(8, 1_000_000, 20, 24, 800.0, 1000) >= 1  # noqa: SLF001


def test_the_memory_counts_the_index_of_the_grid_the_buffers_and_the_secondary() -> None:
    base = {"nodes": 1_000_000, "n_real": 5, "n_max": 24, "threads": 4, "samples": 1000}
    plain = job.memory_mb(base)
    sparse = job.memory_mb({**base, "cells": 400_000_000})  # i4 on every cell: 1,5 GB for 1 million targets
    assert sparse - plain >= 1400
    assert job.memory_mb({**base, "kind": "cosgs"}) - plain >= 20  # sec_in, sec_y, sec: 3 × f8 a node
    assert job.memory_mb({**base, "threads": 8}) - job.memory_mb({**base, "threads": 1}) >= 7 * 50  # backtr ≈ 40 B
    # the pool is cut by the same figures
    assert job._threads(8, 1_000_000, 5, 24, 800.0, 1000, cells=400_000_000) == 1  # noqa: SLF001


def test_a_grid_of_too_many_cells_is_refused_in_russian(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ApiError) as ei:
        nodes.NodeGrid.of((2000, 2000, 2000), np.arange(4, dtype=np.uint64))  # 8·10⁹ cells: nothing is allocated
    e = ei.value
    assert e.status == 422 and e.code == "grid_too_large" and "ячеек" in e.message and e.hint
    monkeypatch.setattr(nodes, "MAX_CELLS", 10)  # the same on the route of a run: before the long work
    err = refused(lambda: run(sw, simulation=body(sw)), 422, "grid_too_large")
    assert "ячеек" in err.message and err.hint


# --- the same on SQLite and PostgreSQL ---------------------------------------------------------------------------


@pytest.mark.db
def test_the_job_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The write of the file, `commit_run` and the graph on SQLite and PostgreSQL (`-m db`; BF_TEST_PG)."""
    w = world(tmp_path, db_url)
    try:
        out = run(w, simulation=body(w, name="БД"))
        ident = out["simulation_id"]
        assert out["nodes"] == 980 and np.array_equal(reals(w, ident).p(), targets_of(w))
        with dialect.read_tx(w.project.engine) as conn:
            node = lineage.Node("object:simulation", ident)
            assert lineage.status(conn, [node])[node].state == "current"
        put(w, ident, 1, seed=5)
        run(w, simulation_id=ident)
        assert get(w, ident)["stale"] is False and left_over(w) == []
        assert BlobStore(w.project.folder).exists(sha_of(w, ident) or "0" * 64)
    finally:
        w.project.engine.dispose()
