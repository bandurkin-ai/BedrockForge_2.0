"""The job «Вариограммы» `variography.experimental` and the preview (ТЗ 8.6.2, 8.6.4; ADR 0022 §4, §12, решения 6,
13, 14, 24, 25; подшаг 16.14; the forms — docs/progress/e16-api.md).

    out = run_experimental(engine, folder, params, tick=tick, threads=None)   # the job: the form + fresh, stages
    form = preview(project, spec)                                            # synchronous: ≤ 20 000 points
    key = key_of(spec, inputs)                                               # SHA-256 of the inputs and params

The params of the job (`routes._submit`): {"body": the body of the request, "rows": rows of the table, "dirs":
directions, "nlag": lags}. The body is parsed again against the project (`spec.parse_experimental`), the defaults
that follow the lag are filled in from the median length of the points (`spec.resolved`), and the key of the
calculation — SHA-256 of {algo, the canonical resolved body, `inputs`} (the tables read {id, name_key, token, rev}
and the key of the trajectories of the set, `inputs_of`: read before the table, without building the
trajectories) — names the record in the cache of the project (`store.write_result`): `cache/variography/<key>.json`
(the form), `<key>.nscore.parquet` (the table of the normal scores: value, score, weight — `nscore` only) and
`<key>.sums.parquet` (the full sums of the pairs by [direction, lag] and along the holes, ADR §12). The same key —
no calculation: the form is read from the cache (`fresh: true`). When the body gives both lags (`lag`, `dh_lag`)
the key is known before the table is read and a repeat reads nothing but the cache; otherwise the points are read
first (the default lag needs their median length).

Stages (the message of the job): «Чтение таблицы», «Траектории», «Нормальные оценки» (`nscore`), «Пары: оси»
(or «Пары: направления»), «Пары: вдоль скважин», «Запись»; a cancel — between the strips of a kernel (the tick of
`pairs.run` and `downhole`), the record is written last, whole or not at all (a cancel leaves nothing). Threads —
`composites.strips.default_threads()`; the result does not depend on them, bit for bit (ADR §4).

The preview reads the same points, takes ≤ `spec.PREVIEW_MAX` of them (`points.sample` with the seed of the body)
and computes the axes and the variogram along the holes over that sample: the form with `key: null` and `sampled`
{n, of, seed} (when there were more points). Errors — ApiError {code, message, hint, param?} in Russian; in the job
they become a failure of the job with the same code (`JobError`, no retry).
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler

from . import spec as sp
from . import store

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

    from .downhole import Downhole
    from .experimental import Experimental
    from .points import Points

KIND = "variography.experimental"
TITLE = "Вариограммы"
ALGO = store.ALGO
Tick = Callable[[float | None, str | None], None]
STAGE_AXES = "Пары: оси"
STAGE_DIRS = "Пары: направления"
STAGE_DH = "Пары: вдоль скважин"
STAGE_WRITE = "Запись"
#: the message of a stage → its key in `stages` (seconds)
STAGE_KEYS = {"Чтение таблицы": "read", "Траектории": "trajectories", "Нормальные оценки": "nscore",
              STAGE_AXES: "pairs", STAGE_DIRS: "pairs", STAGE_DH: "downhole", STAGE_WRITE: "write"}  # fmt: skip
#: the share of the progress bar a stage starts at
AT = {"read": 0.02, "trajectories": 0.08, "nscore": 0.15, "pairs": 0.2, "downhole": 0.85, "write": 0.96}
SUM_NAMES = ("s_h", "s_d2", "s_t", "s_hd", "s_tt", "s_hh", "s_th", "s_rel")  # the order of `pairs.S_H…S_REL`


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the key -------------------------------------------------------------------------------------------------


def inputs_of(conn: sa.Connection, data: sp.Data) -> dict[str, Any]:
    """`points.Points.inputs` without reading the rows: the tables {id, name_key, token, rev} (the samples, then the
    table of the domain) and the key of the trajectories of the set (`store.trajectories_key`)."""
    from bf_core.stats import source

    by_id = {t.id: t for t in source.tables(conn, data.set_id)}
    ids = [data.table_id]
    if data.domain is not None and data.domain.table_id != data.table_id:
        ids.append(data.domain.table_id)
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ApiError(404, "table_not_found", "Таблица не найдена в наборе.", "Обновите список таблиц.",
                       {"param": "table" if missing[0] == data.table_id else "domain.table"})  # fmt: skip
    tables = [by_id[i] for i in ids]
    return {"tables": [{"id": t.id, "name_key": t.name_key, "token": t.token, "rev": t.rev} for t in tables],
            "trajectories": store.trajectories_key(conn, data.set_id)}  # fmt: skip


def key_of(spec: sp.ExperimentalSpec | sp.MapSpec, inputs: dict[str, Any]) -> str:
    """SHA-256 (64 hex) of the algorithm, the canonical resolved body (with its `op`) and the inputs."""
    payload = {"algo": ALGO, "params": spec.hash_params(), "inputs": inputs}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sums_path(folder: Path, key: str) -> Path:
    return store.cache_dir(folder) / f"{store.check_key(key, 'key')}.sums.parquet"


# --- the calculation -----------------------------------------------------------------------------------------


def directions_of(spec: sp.ExperimentalSpec) -> list[Any]:
    """`pairs.Direction` of a resolved spec: the axes a1, a2, a3 of (ang1, ang2, ang3) or the given directions."""
    from . import experimental
    from .pairs import Direction

    c = spec.common
    if spec.axes is not None:
        return experimental.axes(*spec.axes, _f(c.az_tol), _f(c.dip_tol), _f(c.band_h), _f(c.band_v))
    return [Direction(d.az, d.plunge, _f(d.az_tol), _f(d.dip_tol), _f(d.band_h), _f(d.band_v))
            for d in spec.directions]  # fmt: skip


def _f(x: float | None) -> float:
    assert x is not None, "resolve the spec first"
    return x


def _kernel(fn: Callable[[], Any]) -> Any:
    """A refusal of a kernel (`StatsError`) as ApiError 422 with its code and `param`."""
    from bf_core.stats.weighted import StatsError

    try:
        return fn()
    except StatsError as e:
        raise store.as_api(e) from e


def compute(
    spec: sp.ExperimentalSpec, p: Points, *, threads: int = 1, tick: Tick = _noop, limit: float = sp.PAIRS_MAX
) -> tuple[Experimental, Downhole | None]:
    """The axes (directions) over the points — sampled above `limit` checks of pairs — and along the holes over
    all of them (`downhole: false` — None)."""
    from . import downhole, experimental

    lags, dh = spec.lags, spec.dh
    tick(AT["pairs"], STAGE_AXES if spec.axes is not None else STAGE_DIRS)
    e = _kernel(lambda: experimental.calculate(
        p, directions_of(spec), _f(lags.lag), lags.nlag, lags.tol, limit=limit, seed=spec.seed, threads=threads,
        tick=lambda f: tick(AT["pairs"] + (AT["downhole"] - AT["pairs"]) * f, None)))  # fmt: skip
    d = None
    if spec.downhole:
        tick(AT["downhole"], STAGE_DH)
        d = _kernel(lambda: downhole.calculate(
            p, _f(dh.lag), dh.nlag, dh.tol, threads=threads,
            tick=lambda f: tick(AT["downhole"] + (AT["write"] - AT["downhole"]) * f, None)))  # fmt: skip
    return e, d


def form_of(
    spec: sp.ExperimentalSpec, p: Points, e: Experimental, d: Downhole | None, key: str | None,
    sampled: dict[str, int] | None,
) -> dict[str, Any]:  # fmt: skip
    """The form of a calculation (e16-api.md): numbers float64, a lag without pairs — null, never 0."""
    from . import experimental

    data = spec.data
    skipped = e.pw_skipped + (d.pw_skipped if d is not None else 0)
    warning = experimental.pairwise_warning(skipped)
    form = {"key": key, "set_id": data.set_id, "table": str(data.table_id), "field": data.field, "unit": data.unit,
            "transform": data.transform, "sigma2": float(p.sigma2), "sigma2_z": float(p.sigma2_z),
            "origin": [float(v) for v in p.origin], "counts": dict(p.counts), "sampled": sampled,
            "params": spec.body, "directions": e.to_json(spec.min_pairs, axis=spec.axes is not None),
            "downhole": None if d is None else d.to_json(spec.min_pairs),
            "warnings": [warning] if warning else []}  # fmt: skip
    return store._plain(form)  # type: ignore[no-any-return]


def _nscore_bytes(p: Points) -> bytes:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    w = p.weights if p.weights is not None else np.ones(p.n)
    out = io.BytesIO()
    pq.write_table(pa.table({"value": p.z, "score": p.values, "weight": w}), out)
    return out.getvalue()


def _sums_bytes(e: Experimental, d: Downhole | None, axes: bool) -> bytes:
    """The sums of the pairs: a row per [part, direction, lag] — part «axis» / «direction» / «downhole»."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    parts = [("axis" if axes else "direction", e.sums, e.lag)]
    if d is not None:
        parts.append(("downhole", d.sums, d.lag))
    cols: dict[str, list[Any]] = {k: [] for k in ("part", "index", "k", "lag", "n", "n_rel", *SUM_NAMES, "shift",
                                                   "both")}  # fmt: skip
    for part, s, lag in parts:
        ndir, nlag = s.n.shape
        for i in range(ndir):
            for k in range(nlag):
                cols["part"].append(part)
                cols["index"].append(i)
                cols["k"].append(k + 1)
                cols["lag"].append(float(lag) * (k + 1))
                cols["n"].append(int(s.n[i, k]))
                cols["n_rel"].append(int(s.n_rel[i, k]))
                for j, name in enumerate(SUM_NAMES):
                    cols[name].append(float(s.sums[i, k, j]))
                cols["shift"].append(float(s.shift))
                cols["both"].append(bool(np.asarray(s.both)[i]))
    out = io.BytesIO()
    pq.write_table(pa.table(cols), out)
    return out.getvalue()


def _cached(folder: Path, key: str, spec: sp.ExperimentalSpec) -> dict[str, Any] | None:
    """The form of the record `key` when it is whole (with its table of the normal scores); None — compute."""
    try:
        record = store.read_result(folder, key, "key")
    except ApiError:
        return None
    if spec.data.transform == "nscore" and not store.nscore_path(folder, key).is_file():
        return None
    return record["result"]  # type: ignore[no-any-return]


class _Clock:
    """Seconds of the stages from the messages of the ticks (`STAGE_KEYS`)."""

    def __init__(self, tick: Tick, keys: dict[str, str] | None = None, at: dict[str, float] | None = None) -> None:
        self.tick, self.t0, self.at = tick, time.perf_counter(), time.perf_counter()
        self.keys, self.starts = STAGE_KEYS if keys is None else keys, AT if at is None else at
        self.stage: str | None = None
        self.took: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        key = self.keys.get(message or "")
        if key is not None and key != self.stage:
            self.close()
            self.stage = key
            if progress is None and key in self.starts:
                progress = self.starts[key]
        self.tick(progress, message)

    def close(self) -> None:
        now = time.perf_counter()
        if self.stage is not None:
            self.took[self.stage] = self.took.get(self.stage, 0.0) + now - self.at
        self.at = now

    def stages(self) -> dict[str, float]:
        self.close()
        self.stage = None
        return {k: round(v, 3) for k, v in self.took.items()} | {"total": round(time.perf_counter() - self.t0, 3)}


def _given(raw: sp.ExperimentalSpec) -> sp.Defaults | None:
    """Both lags given by the body: the defaults do not depend on the points (no median length needed)."""
    if raw.lags.lag is None or raw.dh.lag is None:
        return None
    return sp.Defaults(lag=raw.lags.lag, dh_lag=raw.dh.lag)


def _parse(engine: sa.Engine, body: dict[str, Any]) -> tuple[sp.ExperimentalSpec, dict[str, Any]]:
    with dialect.read_tx(engine) as conn:
        spec = sp.parse_experimental(conn, body)
        return spec, inputs_of(conn, spec.data)


def run_experimental(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop, threads: int | None = None,
    limit: float = sp.PAIRS_MAX,
) -> dict[str, Any]:  # fmt: skip
    """The body of `variography.experimental` (the handler, the tests and the bench in the process): the form of
    the calculation with `fresh` (read from the cache) and `stages` (seconds)."""
    from bf_core.composites import strips

    from . import points

    folder = Path(folder)
    clock = _Clock(tick)
    raw, inputs = _parse(engine, dict(params.get("body") or {}))
    given = _given(raw)
    if given is not None:  # the key is known before the table is read
        spec = raw.resolved(given)
        key = key_of(spec, inputs)
        hit = _cached(folder, key, spec)
        if hit is not None:
            return {**hit, "fresh": True, "stages": clock.stages()}
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    p = points.load(project, raw.data, tick=clock)
    spec = raw.resolved(given or sp.defaults_for(p.median_length))
    key = key_of(spec, inputs)
    hit = _cached(folder, key, spec)
    if hit is not None:
        return {**hit, "fresh": True, "stages": clock.stages()}
    n_threads = strips.default_threads() if threads is None else threads
    e, d = compute(spec, p, threads=n_threads, tick=clock, limit=limit)
    form = form_of(spec, p, e, d, key, e.sampled)
    clock(AT["write"], STAGE_WRITE)
    # no tick from here on: the record is written whole, its JSON last (the files before it are its parts)
    _write(folder, key, form, spec, p, e, d, inputs)
    return {**form, "fresh": False, "stages": clock.stages()}


def _write(
    folder: Path, key: str, form: dict[str, Any], spec: sp.ExperimentalSpec, p: Points, e: Experimental,
    d: Downhole | None, inputs: dict[str, Any],
) -> None:  # fmt: skip
    sums = _sums_bytes(e, d, spec.axes is not None)
    store._write_atomic(sums_path(folder, key), sums)
    source = {k: v for k, v in spec.data.canonical().items() if k != "transform"}
    scores = _nscore_bytes(p) if spec.data.transform == "nscore" else None
    store.write_result(folder, key, form, source=source, inputs=inputs,
                       calc={"params": spec.body, "sampled": e.sampled}, nscore=scores)  # fmt: skip


def memory_mb(params: dict[str, Any]) -> int:
    """The points (≈ 40 float64 a row with the reading and the trajectories) and the accumulators of the strips
    (≤ 256 strips × directions × lags × 10 numbers, ADR §4)."""
    rows = int(params.get("rows") or 0)
    acc = 256 * int(params.get("dirs") or 3) * int(params.get("nlag") or 15) * 10 * 8
    return max(256, int((rows * 40 * 8 + acc) / 2**20) + 128)


# --- the preview ---------------------------------------------------------------------------------------------


def preview(project: Any, body: dict[str, Any], *, threads: int = 1, tick: Tick = _noop) -> dict[str, Any]:
    """`POST /variography/preview`: the points of the body, ≤ `PREVIEW_MAX` of them by the seed, the axes and
    along the holes over that sample; the form with `key: null`. Nothing is written."""
    from . import points

    with dialect.read_tx(project.engine) as conn:
        raw = sp.parse_experimental(conn, body)
    p = points.load(project, raw.data, tick=tick)
    spec = raw.resolved(_given(raw) or sp.defaults_for(p.median_length))
    q, sampled = points.sample(p, sp.PREVIEW_MAX, spec.seed)
    e, d = compute(spec, q, threads=threads, tick=tick)
    if e.sampled is not None:  # the checks of the pairs of the sample above the limit too: n of all the points
        sampled = {"n": int(e.sampled["n"]), "of": p.n, "seed": spec.seed}
    return form_of(spec, p, e, d, None, sampled)


# --- the warm-up -----------------------------------------------------------------------------------------------


def warm() -> dict[str, float]:
    """The kernels of the job and the preview on a few points (the dtypes of `points.load`): the pairs by the axes
    and by a vertical direction, along the holes — compiled into NUMBA_CACHE_DIR or loaded from it. `ops/warm.py`
    calls it as its step `geostat`."""
    import numpy as np

    from . import downhole, experimental

    took: dict[str, float] = {}
    rng = np.random.Generator(np.random.PCG64(7))
    n = 240
    hole = np.repeat(np.arange(12, dtype=np.int32), 20)
    depth = np.tile(np.arange(20, dtype=np.float64) * 2.0 + 1.0, 12)
    xyz = np.column_stack([np.repeat(rng.uniform(-50, 50, 12), 20), np.repeat(rng.uniform(-50, 50, 12), 20),
                           -depth])  # fmt: skip
    v = rng.lognormal(0.0, 1.0, n)
    t0 = time.perf_counter()
    dirs = experimental.axes(30.0, -10.0, 0.0, 22.5, 22.5, 30.0, 20.0)
    experimental.experimental(xyz[:, 0], xyz[:, 1], xyz[:, 2], v, dirs, 10.0, 5, 5.0)
    experimental.experimental(xyz[:, 0], xyz[:, 1], xyz[:, 2], v, experimental.axes(0.0, 0.0, 0.0, 90.0, 90.0),
                              10.0, 5, 5.0)  # fmt: skip
    took["pairs"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    downhole.downhole(hole, depth, v, 2.0, 5, 1.0)
    took["downhole"] = time.perf_counter() - t0
    return took


# --- the handler ---------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="heavy", estimate_memory=memory_mb)
def experimental_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.composites import jobs as cj

    assert ctx.project_folder is not None
    try:
        return run_experimental(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
