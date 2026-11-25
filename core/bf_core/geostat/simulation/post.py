"""The post-processing of a simulation — E-type, dispersion, P(Z ≥ t), quantiles of the realizations into the fields
of the block model (подшаг 18.32; ТЗ 8.8.1; ADR 0024 §9, решение 18; docs/progress/e18-api.md §9–§10).

    out = post(realizations, stats, thresholds)        {key: f8 [N]} by `stats.local.local_*` (1e−12)
    res = run_post(engine, folder, params, tick, job_id=None)       the body of the job `simulate.post`

`post` takes the realizations as `[n_real, N]` (axis 0 — the realization, as `local_*` do) and returns for every
statistic of `stats` (`spec.STATS`) the array over the nodes: `mean` — `local_expectation`, `std` — `local_std`
(ddof 0), `p10`, `p50`, `p90` — `local_percentile` (`stats.local.percentiles`: one sort, the same numbers),
`prob:<t>` — `local_prob_exceed` (the share of the realizations **≥ t**: the function of the tab «Статистика»; for a
continuous grade it is P(Z > t)). The realizations are f4 in the file and f8 here; a node without a value is NaN
(never 0).

The job `simulate.post` (`quick`): `POST /simulations/{id}/post {expected_version (of the model), prefix?, stats?,
thresholds?, replace?}`.
  * «Чтение» — the row against the project (404, 409 `source_lost`, `no_realizations`, `version_conflict`,
    `field_exists` {field} without `replace`), the targets again from the domain and the restrictor of the row (a
    sub-block of a target parent in another domain, or that does not pass the restrictor, is empty);
  * «Расчёт» — the model by chunks of `CHUNK` rows; the parents of a chunk are the nodes `[lo, hi)` of the file, read
    by row groups; the value of a node goes to every row of its parent (№ 83);
  * «Запись» — one file of fields (`FieldsWriter`), and in one transaction `commit_fields` with `source` {op:
    «simulate», algo, inputs [SHA-256 of the realizations], params {simulation_id, stats, thresholds}} and
    `lineage.record` of every field (inputs: the node `object:simulation`). A cancel, a failure and a stale version
    leave no file and no field.
Fields (`spec.post_columns`): `<prefix>MEAN`, `STD`, `P10`, `P50`, `P90` (f8), `PROB_GT_<t>` (f4). A categorical SIS
(the blob holds the codes of the categories): `PROB_<code>` (f4: the share of the realizations with that category,
Σ over the categories = 1) and `MODE` (a category field: the most frequent category, the first of the equal ones in the
order of the categories; labels — the names of the categories). Heavy imports are
inside the functions (the process of the API registers this module).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.estimate.spec_parts import default_prefix, id_of
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import store as sto
from . import store_check as sc
from .spec import ALGO, PostSpec, SimulationSpec, parse_post, post_columns

if TYPE_CHECKING:
    import numpy as np

    from bf_core.jobs.context import JobContext

KIND = "simulate.post"
TITLE = "Постобработка моделирования"
S_READ, S_CALC, S_WRITE = "Чтение", "Расчёт", "Запись"
Tick = Callable[[float | None, str | None], None]
CHUNK = 1 << 17  # rows of the model per piece
AT_CALC, AT_WRITE = 0.05, 0.95


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the statistics ---------------------------------------------------------------------------------------------------


def post(realizations: Any, stats: Sequence[str], thresholds: Sequence[float] = (),
         codes: Sequence[int] | None = None) -> dict[str, np.ndarray]:  # fmt: skip
    """The statistics over the realizations `[n_real, N]` (module docstring) → {key: f8 [N]}; the keys of `stats`
    (`mean`, `std`, `p10`, `p50`, `p90`) and `prob:<t!r>` of every threshold when `prob` is among them. With `codes`
    (a categorical SIS): `prob:<code>` of every category (the share of the realizations) and `mode` — the position in
    `codes` of the most frequent one (int)."""
    import numpy as np

    from bf_core.stats import local

    r = np.asarray(realizations, dtype=np.float64)
    out: dict[str, np.ndarray] = {}
    if codes is not None:
        shares = np.stack([(r == float(c)).mean(axis=0) for c in codes])  # [K, N]
        if "prob" in stats:
            for i, c in enumerate(codes):
                out[f"prob:{c}"] = shares[i]
        if "mode" in stats:
            out["mode"] = np.argmax(shares, axis=0).astype(np.int32)
        return out
    if "mean" in stats:
        out["mean"] = local.local_expectation(r)
    if "std" in stats:
        out["std"] = local.local_std(r)
    qs = [(k, float(k[1:])) for k in ("p10", "p50", "p90") if k in stats]
    if qs:
        got = local.percentiles(r, [q for _k, q in qs])
        for i, (k, _q) in enumerate(qs):
            out[k] = got[i]
    if "prob" in stats:
        for t in thresholds:
            out[f"prob:{t!r}"] = local.local_prob_exceed(r, float(t))
    return out


# --- the memory --------------------------------------------------------------------------------------------------


def memory_mb(params: dict[str, Any]) -> int:
    """What the scheduler waits for: the interpreter, the nodes `p` of the file (`nodes` of the route), a piece of the
    model with its realizations and the statistics (the copies of f8)."""
    n_real = int(params.get("n_real") or 20)
    nodes = int(params.get("nodes") or 1_000_000)
    return int(300 + nodes * 8 / 2**20 + CHUNK * (n_real * 8 * 4 + 400) / 2**20)


# --- the run ----------------------------------------------------------------------------------------------------------


def _check_names(model: Any, columns: list[dict[str, Any]], replace: bool) -> None:
    have = {str(f["name"]).casefold(): str(f["name"]) for f in model.fields}
    for c in columns:
        key = c["name"].casefold()
        if key in have and not replace:
            raise ApiError(409, "field_exists", f"Поле «{have[key]}» в модели уже есть.",
                           "Задайте другой префикс или замените поля.", {"field": have[key]})  # fmt: skip


@dataclass
class PostPlan:
    """What the job and the route know before the first chunk: the row, its parameters, the model, the blob, the
    targets of the row and the fields to write."""

    ident: int
    name: str
    spec: SimulationSpec
    stale: dict[str, Any]
    model: Any
    sha: str
    plan: Any
    prefix: str
    columns: list[dict[str, Any]]
    post: PostSpec


def plan_post(conn: sa.Connection, store: BlobStore, params: dict[str, Any]) -> PostPlan:
    """The checks of a post-processing in the caller's read transaction: 422 `bad_param`; 404 `simulation_not_found`;
    409 `source_lost`, `no_realizations`, `version_conflict {version}` (the model), `field_exists {field}` (without
    `replace`); 422 `field_not_found`, `domain_unmapped`, `filter_*`."""
    from bf_core.blocks.fields_store import version_conflict
    from bf_core.blocks.model import Model

    from . import targets

    ident = id_of(params.get("simulation_id"), "моделирование", "simulation_id")
    assert ident is not None
    row = sc.stored(conn, ident)
    meta = dict(row.meta or {})
    spec = sc.load_spec(meta)
    pst = parse_post(params, spec.categorical)
    fresh = sc.freshness(conn, meta)
    if fresh["source_lost"]:
        raise ApiError(409, "source_lost", f"У моделирования «{row.name}» удалён источник: таблица, набор или модель.",
                       "Выберите существующие данные в параметрах или удалите моделирование.")  # fmt: skip
    sha = sto.real_sha(conn, ident)
    if sha is None:
        raise ApiError(409, "no_realizations", f"У моделирования «{row.name}» нет реализаций.",
                       "Запустите моделирование и повторите.")  # fmt: skip
    model = Model.open(conn, store, spec.model_id)
    if model.version != pst.expected_version:
        raise version_conflict(model.version)
    plan = targets.TargetPlan.of(conn, store, spec, model)
    prefix = pst.prefix or default_prefix(spec.samples.field, spec.kind)
    cats = None
    if spec.method is not None and spec.method.categorical:
        cats = (spec.method.categories, spec.method.labels or tuple(str(c) for c in spec.method.categories))
    columns = post_columns(prefix, pst.stats, pst.thresholds, spec.samples.unit, cats)
    _check_names(model, columns, pst.replace)
    return PostPlan(ident, str(row.name), spec, fresh, model, sha, plan, prefix, columns, pst)


def run_post(engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *,
             job_id: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of `simulate.post` (the handler, the tests in the process; module docstring)."""
    import numpy as np

    from bf_core.blocks.fields_store import FieldsWriter, commit_fields
    from bf_core.geostat.variogram import store as ms

    from . import real, targets

    t0 = time.perf_counter()
    folder = Path(folder)
    store = BlobStore(folder)
    tick(0.01, S_READ)
    with dialect.read_tx(engine) as conn:
        pl = plan_post(conn, store, params)
    ident, pst, model, sha, plan, prefix, columns = pl.ident, pl.post, pl.model, pl.sha, pl.plan, pl.prefix, pl.columns
    reals = real.Reals(store, sha)
    nodes = reals.p()
    codes = pl.spec.method.categories if pl.spec.method is not None and pl.spec.method.categorical else None
    log = [f"Моделирование «{pl.name}»: {len(nodes)} узлов × {reals.n_real} реализаций."]
    if pl.stale["stale"]:
        log.append(f"Моделирование устарело ({pl.stale['stale_reason']}): поля посчитаны по прежним реализациям.")
    total = max(model.rows, 1)
    done = 0
    tick(AT_CALC, S_CALC)
    with FieldsWriter(store, model, pst.expected_version) as w:  # type: ignore[arg-type]  # ModelView: settable
        for c in columns:
            w.add(c["name"], c["dtype"], unit=c["unit"], role=c["role"], description=c["description"],
                  labels=c.get("labels"), replace=pst.replace)  # fmt: skip
        for ch in model.iter(plan.read, CHUNK):
            tick(None, None)
            pos, found = targets.locate(nodes, ch.p)
            ok = plan.mask(ch) & found
            vals = {c["name"]: np.full(ch.n, -1, dtype=np.int32) if c["dtype"] == "cat" else np.full(ch.n, np.nan)
                    for c in columns}  # fmt: skip
            if bool(ok.any()):
                at = pos[ok]
                lo, hi = int(at.min()), int(at.max()) + 1
                got = post(reals.rows(lo, hi), pst.stats, pst.thresholds, codes)
                for c in columns:
                    vals[c["name"]][ok] = got[c["key"]][at - lo]
            w.write(vals)
            done += ch.n
            tick(AT_CALC + (AT_WRITE - AT_CALC) * done / total, S_CALC)
        tick(AT_WRITE, S_WRITE)
        staged = w.finish()
    source = {"op": "simulate", "algo": ALGO, "versions": ms._versions(),  # noqa: SLF001
              "inputs": [sha], "params": {"simulation_id": ident, "stats": list(pst.stats),
                                          "thresholds": list(pst.thresholds), "prefix": prefix}}  # fmt: skip
    try:
        with dialect.write_tx(engine) as conn:
            version = commit_fields(conn, store, staged, source=source)
            _record_fields(conn, model.id, ident, columns)
    finally:
        staged.discard()
    tick(1.0, None)
    return {"simulation_id": ident, "model_id": model.id, "version": version, "prefix": prefix,
            "fields": [c["name"] for c in columns], "nodes": int(len(nodes)), "realizations": int(reals.n_real),
            "log": log, "seconds": round(time.perf_counter() - t0, 3),
            "events": [["objects.changed", {"kind": "block_model", "id": model.id}]]}  # fmt: skip


def _record_fields(conn: sa.Connection, model_id: int, ident: int, columns: list[dict[str, Any]]) -> None:
    """`lineage.record` of every field written: its input is the node of the simulation (ADR 0025 §4: in the
    transaction of the write)."""
    from bf_core import lineage

    for c in columns:
        lineage.record(conn, lineage.Node("bm_field", model_id, c["name"].casefold()),
                       [lineage.Node(sc.LINEAGE_KIND, ident)])  # fmt: skip


# --- the handler ----------------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="quick", max_attempts=1, estimate_memory=memory_mb)
def post_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_post(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, job_id=ctx.id)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
