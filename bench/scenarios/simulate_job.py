"""The jobs `simulate.run` and `simulate.post` whole, with the files written (ТЗ 8.8.1, № 84; ADR 0024 §9 «Замеры»;
Э18б, 18.36): full — 1 млн узлов × 20 реализаций на 4 ядрах, ci — 100×100×1 × 20 на 2 ядрах.

A project of the world of `simulate_common`, in metres (a cell is 10 × 10 × 5 м, the range 100 м): the set — a hole of
one interval of 1 м for every sample (AU), the model of the variogram in normal scores (`nscore`, the spherical 0.1 +
0.9), the regular model of `rows` blocks of one layer with the category DOM (руда — 90 % of the columns) and the
field SEC, one stored simulation row: SGS by the domain «руда», n_real 20, 12 nodes, 24 samples, the search from the
model. The body of the handlers: the samples are read (`samples.load`), the nodes found, the realizations of the
kernel, the file `real` (Parquet, f4) written, the row and the graph recorded; then `simulate.post` — E-type, σ,
P10/P50/P90, P(Z ≥ t) into the new fields of the block model (a file of fields, the version of the model grows).
  run_s, nodes, us_per_node, forecast_1m_s   the call of `simulate.run`: the time of ТЗ; `stages` — «Пробы»,
                   «Нормальные оценки», «Реализации», «Запись» as the answer gives them (`samples_s`, `nscore_s`,
                   `real_s`, `write_s`)
  post_s, post_stages   the call of `simulate.post` with 5 statistics and 2 thresholds;  file_mb, fields_mb — the size
                   of the file of realizations and of the fields written
What is checked (a failure stops the run): the answer — the nodes are the blocks of «руда», 20 realizations, the file
has the columns p, dom, r001…; the file against the kernel called straight on the same frame (`job.grid_frame`, the
realizations 0 and 1 — a seed does not depend on `n_real`): the same bits (f4); a gap is never a value that is not a
number; the fields of the post-processing against `stats.local.local_*` of the file (1e−12) at the nodes of the
domain, the rows outside it are empty (never 0); the folder has no file of a stopped job."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import estimate_common as ec
from . import estimate_world as ew
from . import simulate_common as sc

CELL = ec.SIZE  # m, a block of the model
MODEL_Y = {"type": "semivariogram", "c0": 0.1, "common_axes": True,
           "structures": [{"type": "spherical", "c": 0.9, "a1": 100.0, "a2": 100.0, "a3": 100.0, "ang1": 0.0}]}
MODEL_Z = {"type": "semivariogram", "c0": 0.05, "common_axes": True,
           "structures": [{"type": "spherical", "c": 0.2, "a1": 100.0, "a2": 100.0, "a3": 100.0, "ang1": 0.0}]}
PREFIX = "AU_SGS_"
THRESHOLDS = [2.0, 4.0]
CHECK_REAL = 2  # the realizations compared with the straight call of the kernel
PROBES = 2000  # the nodes of the post-processing compared with `local_*`


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def write_set(engine: Any, w: sc.World, lith: bool = False) -> tuple[int, int, int | None]:
    """The set: a hole at every sample (one interval of 1 м at the layer of the model), «Пробы» (AU); with `lith` the
    table «Фации» (the codes of the categories as a number field LITH of the same intervals)."""
    from bf_core.db import dialect
    from bf_core.drillholes import store as dh

    n = w.z.size
    names = np.array([f"СКВ-{i + 1:05d}" for i in range(n)], dtype=object)
    top = ec.CORNER[2] + 0.5 * CELL[2] + 0.5  # the middle of the interval is the centre of the layer
    collars = [dh.CollarRow(str(name), x=float(ec.CORNER[0] + x * CELL[0]), y=float(ec.CORNER[1] + y * CELL[1]),
                            z=top, td=1.0, azimuth=0.0, dip=-90.0)
               for name, (x, y) in zip(names, w.xyz[:, :2], strict=True)]  # fmt: skip
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, collars)
    zeros, ones = np.zeros(n), np.ones(n)
    au = dh.TableData(name="Пробы", kind="assay", holes=names.tolist(), from_m=zeros, to_m=ones,
                      columns=[dh.ColumnSpec("AU", "number", "г/т", "component")], values=[w.z.tolist()])  # fmt: skip
    table = int(dh.write_table(engine, set_id, au, job_id=None, tick=noop)["table_id"])
    facies = None
    if lith:
        codes = w.cat_all.astype(float).tolist()
        fac = dh.TableData(name="Фации", kind="interval", holes=names.tolist(), from_m=zeros, to_m=ones,
                           columns=[dh.ColumnSpec("LITH", "number")], values=[codes])  # fmt: skip
        facies = int(dh.write_table(engine, set_id, fac, job_id=None, tick=noop)["table_id"])
    return set_id, table, facies


def write_model(ctx: bc.Ctx, w: sc.World) -> int:
    """The regular model of one layer: DOM (the first 90 % of the columns are «руда») and the secondary SEC."""
    dep = ec.Deposit((w.nx, w.ny, 1), int(w.z.size))
    ore = ew.ore_columns(dep)
    at = {"lo": 0}

    def make(_rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
        p = np.arange(at["lo"], at["lo"] + n, dtype=np.int64)
        at["lo"] += n
        return {"DOM": np.where(p % w.nx < ore, 0, 1).astype(np.int32), "SEC": w.sec[p]}

    fields: list[dict[str, Any]] = [{"name": "DOM", "dtype": "cat", "labels": ew.DOM_LABELS, "role": "code"},
                                    {"name": "SEC", "dtype": "f8"}]  # fmt: skip
    got = bc.regular_model(ctx, dep.rows, fields, make, name="Модель моделирования", nz=1)
    assert got["counts"] == [w.nx, w.ny, 1], got
    return int(got["model_id"])


def body(meta: dict[str, Any], **over: Any) -> dict[str, Any]:
    """The row of a simulation: SGS by the domain «руда», the samples AU, the variogram in Y, the defaults of Д-23."""
    out: dict[str, Any] = {
        "model_id": meta["model_id"], "target": {"domain_field": "DOM", "label": ew.DOM_LABELS[0]},
        "samples": {"set_id": meta["set_id"], "table": str(meta["table"]), "field": "AU"},
        "variogram": {"model_id": meta["vy"]}, "n_real": meta["n_real"], "nodes": 12, "seed": 12345}  # fmt: skip
    out.update(over)
    return out


def create(ctx: bc.Ctx, raw: dict[str, Any]) -> int:
    from bf_core.db import dialect
    from bf_core.geostat.simulation import store as st
    from bf_core.geostat.simulation.spec import parse_simulation

    with dialect.write_tx(ctx.engine) as conn:
        return int(st.create(conn, ctx.project, parse_simulation(raw))["id"])


def build(work: Path, params: dict[str, Any], *, lith: bool = False) -> dict[str, Any]:
    """The project of the world in `work` → the ids (`meta`, kept for the child)."""
    from types import SimpleNamespace

    from tests.estutil import vmodel

    sc.World.build(work, params)
    w = sc.World.open(work)
    ctx = bc.Ctx.create(work)
    try:
        set_id, table, facies = write_set(ctx.engine, w, lith)
        model_id = write_model(ctx, w)
        v = SimpleNamespace(project=ctx.project, store=ctx.store, set_id=set_id, a=table)
        meta: dict[str, Any] = {"set_id": set_id, "table": table, "lith": facies, "model_id": model_id,
                                "n_real": int(params.get("n_real", 20)), "rows": w.nx * w.ny, "side": w.nx,
                                "vy": vmodel(v, "AU · Y", transform="nscore", model=MODEL_Y),
                                "vz": vmodel(v, "AU · Z", model=MODEL_Z)}  # fmt: skip
        meta["sgs"] = create(ctx, body(meta, name="SGS"))
        ctx.keep(**meta)
        return meta
    finally:
        ctx.close()


def prepare(params: dict[str, Any], work: Path) -> None:
    build(work, params)


def load() -> None:
    """What the product's warm-up has done before the first job: the steps of the quick worker that the jobs of the
    module need (`ops/warm.py`: the trajectories and the statistics of the samples, the blocks, the search and the
    kernels of the estimation, the simulation), outside the timing."""
    from bf_core.blocks.warm import warm as warm_blocks
    from bf_core.composites.warm import warm as warm_composites
    from bf_core.geostat.estimate.warm import warm as warm_estimate
    from bf_core.geostat.simulation.sgs import warm_sgs
    from bf_core.stats.warm import warm as warm_stats

    warm_composites()
    warm_stats()
    warm_blocks()
    warm_estimate()
    warm_sgs()


def targets_of(model: Any, label: int = 0) -> np.ndarray:
    dom = model.columns(["DOM"])["DOM"]
    return np.unique(model.p()[dom == label]).astype(np.uint64)


def check_file(ctx: bc.Ctx, meta: dict[str, Any], ident: int, out: dict[str, Any],
               info: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """The file of the realizations against the kernel called straight on the same frame → (p, f8 [n_real, n])."""
    from bf_core.db import dialect
    from bf_core.geostat.estimate import samples as sm
    from bf_core.geostat.simulation import job, nodes, real, sgs
    from bf_core.geostat.simulation import store as st
    from bf_core.geostat.simulation import store_check as scheck
    from bf_core.storage.blobs import BlobStore

    model = ctx.model(meta["model_id"])
    p = targets_of(model)
    assert out["nodes"] == p.size == ew.ore_columns(ec.Deposit((meta["side"], meta["side"], 1), 1)) * meta["side"], out
    assert (out["simulation_id"], out["realizations"]) == (ident, meta["n_real"]), out
    with dialect.read_tx(ctx.engine) as conn:
        stored = st.get(conn, ident)
        sha = st.real_sha(conn, ident)
    assert sha is not None and stored["stale"] is False
    reals = real.Reals(BlobStore(ctx.folder), sha)
    assert (reals.n, reals.n_real, reals.columns) == (p.size, meta["n_real"], real.column_names(meta["n_real"]))
    assert np.array_equal(reals.p(), p) and not reals.dom().any()
    info["file_mb"] = round(ctx.store.path_of(sha).stat().st_size / 2**20, 2)
    table = reals.rows(0, reals.n).astype(np.float32)
    assert table.dtype == np.float32 and bool(np.isfinite(table).all()), "a gap in the file"
    spec = scheck.load_spec(stored["meta"])
    project = ctx.project
    samples = sm.load(project, spec)
    ng = nodes.NodeGrid.of(model.grid.counts, p)
    kmodel, ell, local, steps = job.grid_frame(spec, samples, model.grid)
    par = sgs.SgsParams(kmodel, ell, steps, spec.seed, CHECK_REAL, spec.search.passes[0].n_max, spec.nodes,
                        *spec.nscore.tails(), spec.nscore.ltpar, spec.nscore.utpar)  # fmt: skip
    res = sgs.simulate(par, local, ng, sgs.grid_centres(ng, steps), threads=1)
    assert np.array_equal(table[:CHECK_REAL], res.real.astype(np.float32)), "the file differs from the kernel"
    return p, table.astype(np.float64)


def check_post(ctx: bc.Ctx, meta: dict[str, Any], p: np.ndarray, x: np.ndarray, out: dict[str, Any]) -> float:
    """The fields of the block model against `stats.local.local_*` of the file → the largest relative difference."""
    from bf_core.stats import local

    model = ctx.model(meta["model_id"])
    names = [f"{PREFIX}MEAN", f"{PREFIX}STD", f"{PREFIX}P10", f"{PREFIX}P50", f"{PREFIX}P90",
             *[f"{PREFIX}PROB_GT_{t:g}" for t in THRESHOLDS]]  # fmt: skip
    assert out["fields"] == names and out["nodes"] == p.size, out
    cols = model.columns(["DOM", *names])
    assert np.array_equal(model.p(), np.arange(model.p().size)), "the model has sub-blocks"
    want_rows = np.zeros(model.p().size, bool)
    want_rows[p.astype(np.int64)] = True
    q = local.percentiles(x, [10, 50, 90])
    ref = {"MEAN": local.local_expectation(x), "STD": local.local_std(x), "P10": q[0], "P50": q[1], "P90": q[2],
           **{f"PROB_GT_{t:g}": local.local_prob_exceed(x, t) for t in THRESHOLDS}}  # fmt: skip
    worst = 0.0
    for key, want in ref.items():
        col = np.asarray(cols[f"{PREFIX}{key}"], dtype=np.float64)
        assert np.isnan(col[~want_rows]).all(), f"{key}: a row outside the domain has a value"
        got = col[p.astype(np.int64)]
        assert bool(np.isfinite(got).all()), f"{key}: a node of the domain has no value"
        err = ec.rel_err(got, want)
        limit = 1e-6 if key.startswith("PROB") else 1e-12  # the probabilities are f4 in the model
        assert err <= limit, f"{key}: differs from local_* by {err:.2e}"
        worst = max(worst, err)
    return worst


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.simulation import job, post

    threads = sc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    try:
        t0 = time.perf_counter()
        out = job.run_simulate(ctx.engine, ctx.folder, {"simulation_id": meta["sgs"]}, threads=threads)
        run_s = time.perf_counter() - t0
        peak = sc.peak_mb()
        stages = {k: round(float(v), 3) for k, v in out["stages"].items()}
        info: dict[str, Any] = {"threads": threads, "nodes": out["nodes"], "n_real": out["realizations"],
                                "model_blocks": meta["rows"], "run_s": round(run_s, 3), "stages": stages,
                                **sc.cost(run_s, out["nodes"], out["realizations"]), "peak_mb_run": peak}  # fmt: skip
        for key, seconds in stages.items():
            info[f"{key.lower()}_s"] = seconds
        p, x = check_file(ctx, meta, meta["sgs"], out, info)
        t1 = time.perf_counter()
        version = ctx.version(meta["model_id"])
        posted = post.run_post(ctx.engine, ctx.folder, {"simulation_id": meta["sgs"], "thresholds": THRESHOLDS,
                                                        "expected_version": version})  # fmt: skip
        info["post_s"] = round(time.perf_counter() - t1, 3)
        info["post_fields"] = len(posted["fields"])
        info["post_err"] = check_post(ctx, meta, p, x, posted)
        tmp = ctx.store.tmp_dir
        left = sorted(f.name for f in tmp.iterdir()) if tmp.is_dir() else []
        assert not left, f"files of a stopped job are left: {left}"
        return info
    finally:
        ctx.close()
