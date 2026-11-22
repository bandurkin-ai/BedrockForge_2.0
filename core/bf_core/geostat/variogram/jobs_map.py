"""The job «Веер и карта» `variography.map`, «Перестроить» of a stored model `variography.rebuild`, and the
synchronous `/curve` (ТЗ 8.6.1–8.6.4; ADR 0022 §4–5, §11–16, решения 11–16, 24; подшаг 16.15; the forms —
docs/progress/e16-api.md).

    out = run_map(engine, folder, params, tick=tick, threads=None)       # the job: the form + fresh, stages
    out = run_rebuild(engine, folder, params, tick=tick)                 # the job: new points of a model
    table = table_of(folder, key)                                        # NormalScoreTransform of a record
    answer = curve(body)                                                 # POST /curve

The map. The body (`spec.parse_map`) is parsed again against the project; the defaults that follow the lag come
from the median length of the points unless `lag` is given (then the key is known before the table is read and a
repeat reads nothing but the cache). The key — `jobs.key_of` with `op: "map"` — names the record of the cache
(`store.write_result`): `cache/variography/<key>.json` with the form of the map; the same key — no calculation
(`fresh: true`). Stages: «Чтение таблицы», «Траектории», «Нормальные оценки» (`nscore`), «Пары: веер», «Пары:
карта» (what is asked for), «Запись»; a cancel — between the strips of the kernels (`varmap.calculate`), the record
is written last, whole or not at all. The fan and the map go over one sample above 2·10⁹ checks of pairs
(`sampled`).

«Перестроить» (ADR 0022 §12). The points of a stored model are computed again on the current data with the
parameters of its calculation (`meta.calc.params`; the table and the table of the domain found by id, then by
name, as `store.freshness`): the job `variography.experimental` in the same process (the cache by the key), then
the model takes the new record (`store.update(staged=…)`). The model (c0, structures, angles, type) is never
changed; what follows the points is computed again: `fit.residual`, `fit.n` with the rule of weights of the stored
fit (трактовка: N / γ² — with γ of the model itself), `back` (the model of Z by the new table of normal scores,
the tails «linear», no curves) when the model had one. The event `geostat.changed` travels in the result
(`events`: a worker does not publish). The source lost — 409 `source_lost` (route) / the failure of the job.
The model is read once, with its version; the write is `UPDATE … WHERE version = …` over it: a model saved (renamed,
recoloured, refitted) while the points were rebuilt — the job fails with 409 `version_conflict`, nothing written.

Errors — ApiError {code, message, hint, param?} in Russian; in a job — a failure with the same code (no retry).
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler

from . import jobs as vj
from . import spec as sp
from . import store

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

    from .points import Points
    from .varmap import MapResult

KIND_MAP = "variography.map"
TITLE_MAP = "Веер и карта вариограмм"
KIND_REBUILD = "variography.rebuild"
TITLE_REBUILD = "Перестроить модель вариограммы"
STAGE_FAN = "Пары: веер"  # = varmap.STAGE_FAN (varmap loads numpy)
STAGE_MAP = "Пары: карта"
STAGE_KEYS = {"Чтение таблицы": "read", "Траектории": "trajectories", "Нормальные оценки": "nscore",
              STAGE_FAN: "fan", STAGE_MAP: "map", vj.STAGE_WRITE: "write"}  # fmt: skip
AT = {"read": 0.02, "trajectories": 0.08, "nscore": 0.15, "pairs": 0.2, "write": 0.96}
EVENT = "geostat.changed"
CURVE_N = 100
CURVE_N_MAX = 1000
CURVE_H_MAX = 10_000_000.0  # m
CURVE_SPAN = 1.5  # × the largest range of the model: h_max by default
LOST_HINT = "Набор или таблица расчёта удалены: рассчитайте вариограммы заново и сохраните модель."


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the map ---------------------------------------------------------------------------------------------------


def _parse(engine: sa.Engine, body: dict[str, Any]) -> tuple[sp.MapSpec, dict[str, Any]]:
    with dialect.read_tx(engine) as conn:
        spec = sp.parse_map(conn, body)
        return spec, vj.inputs_of(conn, spec.data)


def _cached(folder: Path, key: str) -> dict[str, Any] | None:
    try:
        return store.read_result(folder, key, "key")["result"]  # type: ignore[no-any-return]
    except ApiError:
        return None


def form_of(spec: sp.MapSpec, p: Points, r: MapResult, key: str | None) -> dict[str, Any]:
    """The form of the map (e16-api.md): the data of the calc, `params` and {plane, sampled, fan, rose, map,
    warnings} of the kernel; a cell or a lag without pairs — null, never 0."""
    data = spec.data
    form = {"key": key, "set_id": data.set_id, "table": str(data.table_id), "field": data.field, "unit": data.unit,
            "transform": data.transform, "sigma2": float(p.sigma2), "sigma2_z": float(p.sigma2_z),
            "origin": [float(v) for v in p.origin], "counts": dict(p.counts), "params": spec.body,
            **r.to_json()}  # fmt: skip
    return store._plain(form)  # type: ignore[no-any-return]


def run_map(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: vj.Tick = _noop, threads: int | None = None,
    limit: float = sp.PAIRS_MAX,
) -> dict[str, Any]:  # fmt: skip
    """The body of `variography.map`: the form of the map with `fresh` and `stages` {read, trajectories, nscore?,
    fan?, map?, write, total} (seconds)."""
    from bf_core.composites import strips

    from . import points, varmap

    folder = Path(folder)
    clock = vj._Clock(tick, STAGE_KEYS, AT)
    raw, inputs = _parse(engine, dict(params.get("body") or {}))
    given = None if raw.lags.lag is None else sp.Defaults(lag=raw.lags.lag, dh_lag=raw.lags.lag)
    if given is not None:  # the map has no lag along the holes: the lag given — the key before the table
        spec = raw.resolved(given)
        key = vj.key_of(spec, inputs)
        hit = _cached(folder, key)
        if hit is not None:
            return {**hit, "fresh": True, "stages": clock.stages()}
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    p = points.load(project, raw.data, tick=clock)
    inputs = vj.fresher(inputs, p)
    spec = raw.resolved(given or sp.defaults_for(p.median_length))
    key = vj.key_of(spec, inputs)
    hit = _cached(folder, key)
    if hit is not None:
        return {**hit, "fresh": True, "stages": clock.stages()}
    n_threads = strips.default_threads() if threads is None else threads
    span = AT["write"] - AT["pairs"]

    def pairs_tick(f: float | None, m: str | None) -> None:
        clock(None if f is None else AT["pairs"] + span * f, m)

    r = vj._kernel(lambda: varmap.calculate(p, spec, limit=limit, threads=n_threads, tick=pairs_tick))
    form = form_of(spec, p, r, key)
    clock(AT["write"], vj.STAGE_WRITE)
    source = {k: v for k, v in spec.data.canonical().items() if k != "transform"}
    store.write_result(folder, key, form, source=source, inputs=inputs,
                       calc={"params": spec.body, "sampled": r.sampled})  # fmt: skip
    return {**form, "fresh": False, "stages": clock.stages()}


def memory_mb(params: dict[str, Any]) -> int:
    """The points and the accumulators of the strips of the fan (`dirs` rays) and of the map (≤ 201 × 201)."""
    return vj.memory_mb(params) + int(256 * 201 * 201 * 16 / 2**20)


@handler(KIND_MAP, TITLE_MAP, queue="heavy", estimate_memory=memory_mb)
def map_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.composites import jobs as cj

    assert ctx.project_folder is not None
    try:
        return run_map(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None


# --- the table of normal scores of a record ----------------------------------------------------------------------


def table_of(folder: Path, key: str) -> Any:
    """`NormalScoreTransform` of the record `key` (`<key>.nscore.parquet`: value, score, weight — NaN no weight,
    1 without a field of weights) built again as `points.load` built it; None — no table (`transform: none`)."""
    import numpy as np
    import pyarrow.parquet as pq

    from bf_core.stats.transforms import nscore

    path = store.nscore_path(Path(folder), key)
    if not path.is_file():
        return None
    t = pq.read_table(path)
    z = np.asarray(t.column("value").to_numpy(), dtype=np.float64)
    w = np.asarray(t.column("weight").to_numpy(), dtype=np.float64)
    if bool(np.all(w == 1.0)):
        return nscore(z)[1]
    good = np.isfinite(w) & (w > 0.0)
    return nscore(z[good], w[good])[1]


# --- «Перестроить» -----------------------------------------------------------------------------------------------


def _lost() -> ApiError:
    return ApiError(409, "source_lost", "Перестроить нельзя: источник модели удалён.", LOST_HINT)


def _current(by_id: dict[int, Any], by_key: dict[str, Any], t: Any) -> Any:
    """The table of `inputs.tables[]` now: by id, then by name (as `store.freshness`)."""
    if not isinstance(t, dict):
        return None
    cur = by_id.get(int(t["id"])) if t.get("id") is not None else None
    if cur is None or cur.name_key != t.get("name_key"):
        cur = by_key.get(str(t.get("name_key")))
    return cur


def rebuild_body(conn: sa.Connection, meta: dict[str, Any]) -> dict[str, Any]:
    """The body of `experimental` of a stored model: the parameters of its calc, the current ids of its tables."""
    from bf_core.stats import source as st

    if store.freshness(conn, meta)["source_lost"]:
        raise _lost()
    src = dict(meta.get("source") or {})
    calc = (meta.get("calc") or {}).get("params") or {}
    tables = list((meta.get("inputs") or {}).get("tables") or [])
    now = st.tables(conn, int(src["set_id"]))
    by_id, by_key = {t.id: t for t in now}, {t.name_key: t for t in now}
    found = [_current(by_id, by_key, t) for t in tables]
    if not found or any(c is None for c in found):
        raise _lost()
    body = {k: v for k, v in calc.items() if k not in ("set_id", "table", "field", "domain", "weights", "transform")}
    if body.get("axes") is None:
        body.pop("axes", None)
    if not body.get("directions"):
        body.pop("directions", None)
    domain = src.get("domain")
    if isinstance(domain, dict):
        own = str(domain.get("table")) == str(src.get("table"))
        domain = {**domain, "table": str(found[0].id if own or len(found) < 2 else found[1].id)}
    weights = src.get("weights")
    return {**body, "set_id": int(src["set_id"]), "table": str(found[0].id), "field": src.get("field"),
            "domain": domain, "weights": "" if weights in (None, False) else weights,
            "transform": meta.get("transform") or "none"}  # fmt: skip


def residual_of(form: dict[str, Any], meta: dict[str, Any], rule: str) -> tuple[float | None, int]:
    """(Σ r², the number of points) of the stored model on the axes of a calc — r of `fit` (√w·(γ̂ − γ) / S*); a
    calc without points of the axes (or of a list of directions) — (None, 0)."""
    import numpy as np

    from . import exchange
    from . import fit as vf

    kind, model = exchange.api_model({k: meta.get(k) for k in ("type", "c0", "structures", "common_axes")})
    try:
        pts, info = vf.points_of(form, kind)
    except ValueError:
        return None, 0
    if pts.size == 0:
        return None, 0
    g = model.gamma(pts.h[:, None] * pts.u)
    floor = np.maximum(g, vf.GAMMA_FLOOR * info["scale"])
    w = vf.point_weights(rule, pts.n, pts.h, floor if rule == "n_g2" else None)
    r = np.sqrt(w) * (pts.value - g) / info["scale"]
    return float(np.dot(r, r)), int(pts.size)


def _back(folder: Path, form: dict[str, Any], key: str, meta: dict[str, Any]) -> Any:
    from . import hermite

    table = table_of(folder, key)
    model = {k: meta.get(k) for k in ("type", "c0", "structures", "common_axes")}
    try:
        out = hermite.back_variogram(form, model, table)
    except ValueError:
        return None  # the model no longer back-transforms (e.g. a sill of Y above 2): `back` is dropped
    return {k: v for k, v in out.items() if k != "curves"}


def run_rebuild(engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: vj.Tick = _noop,
                threads: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of `variography.rebuild`: {model_id, key, fresh, stages, fit, back, events}."""
    from bf_core.stats.routes_charts import _safe
    from bf_core.storage.blobs import BlobStore

    folder = Path(folder)
    model_id = int(params["model_id"])
    with dialect.read_tx(engine) as conn:
        _name, meta, version = store.versioned(conn, model_id)
        body = rebuild_body(conn, meta)
    out = vj.run_experimental(engine, folder, {"body": body}, tick=tick, threads=threads)
    key = str(out["key"])
    form = {k: v for k, v in out.items() if k not in ("fresh", "stages")}
    has: set[str] = set()
    fit = meta.get("fit")
    if isinstance(fit, dict):
        residual, n = residual_of(form, meta, str(fit.get("weights") or "n_h"))
        fit = {**fit, "residual": residual, "n": n}
        has.add("fit")
    back = meta.get("back")
    if back is not None:
        back = _safe(_back(folder, form, key, meta))
        has.add("back")
    blobs = BlobStore(folder)
    staged = store.stage(blobs, folder, key)
    try:
        with dialect.write_tx(engine) as conn:  # a model saved meanwhile: 409, nothing written
            store.update(conn, blobs, model_id, expected_version=version, staged=staged, fit=fit, back=back,
                         has=frozenset(has))  # fmt: skip
    except ApiError as exc:
        if exc.code != "version_conflict":
            raise
        raise store.conflict(int(exc.extra.get("version") or 0), "Модель изменили, пока её перестраивали.",
                             "Запустите «Перестроить» снова.") from None  # fmt: skip
    finally:
        staged.discard()
    return {"model_id": model_id, "key": key, "fresh": out["fresh"], "stages": out["stages"],
            "fit": fit, "back": back,
            "events": [[EVENT, {"kind": store.KIND, "id": model_id}]]}  # fmt: skip


@handler(KIND_REBUILD, TITLE_REBUILD, queue="heavy", estimate_memory=vj.memory_mb)
def rebuild_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.composites import jobs as cj

    assert ctx.project_folder is not None
    try:
        return run_rebuild(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None


# --- the curve ---------------------------------------------------------------------------------------------------


def _curve_h(raw: dict[str, Any], top: float | None) -> Any:
    import numpy as np

    h = raw.get("h")
    if h is not None:
        if not isinstance(h, list) or not 1 <= len(h) <= CURVE_N_MAX * 10:
            raise sp.bad("bad_param", "Расстояния заданы неверно.", "Задайте от 1 до 10 000 расстояний.", "h")
        vals = [sp._number(x, "Расстояние", f"h.{i}") for i, x in enumerate(h)]
        if any(not 0.0 <= x <= CURVE_H_MAX for x in vals):
            raise sp.bad("bad_param", "Расстояние вне диапазона от 0 до 10 000 000 м.", "Задайте h ≥ 0.", "h")
        return np.asarray(vals, dtype=np.float64)
    n = raw.get("n")
    count = CURVE_N if n in (None, "") else sp._number(n, "Число расстояний", "n")
    if count != math.floor(count) or not 2 <= count <= CURVE_N_MAX:
        raise sp.bad("bad_param", "Число расстояний: целое от 2 до 1000.", "Задайте, например, 100.", "n")
    h_max = raw.get("h_max")
    if h_max in (None, ""):
        if top is None:
            raise sp.bad("bad_param", "У модели без структур не задано наибольшее расстояние.",
                         "Задайте h_max — например, наибольший лаг расчёта.", "h_max")  # fmt: skip
        hm = CURVE_SPAN * top
    else:
        hm = sp._number(h_max, "Наибольшее расстояние", "h_max")
        if not 0.0 < hm <= CURVE_H_MAX:
            raise sp.bad("bad_param", "Наибольшее расстояние вне диапазона.", "Задайте h_max больше 0 м.", "h_max")
    return np.linspace(0.0, hm, int(count))


def curve(raw: dict[str, Any]) -> dict[str, Any]:
    """`POST /variography/curve` {model, directions?, h?, h_max?, n?} → {directions: [{axis?, az, plunge, h, gamma,
    cov}]}: without `directions` — the three axes of the model (those of the first structure; no structures — of
    the angles 0); without `h` — `n` distances 0…h_max (1,5 × the largest range by default); cov = C0 + Σc − γ,
    γ(0) = 0."""
    from bf_core.geostat.rotation import rotation_matrix

    from . import exchange, fan

    try:
        _kind, model = exchange.api_model(raw.get("model"))
    except ValueError as e:
        raise store.as_api(e) from e
    given = raw.get("directions")
    dirs: list[tuple[int | None, float, float]] = []
    if given is None:
        axes = model.structures[0].axes if model.structures else rotation_matrix(0.0, 0.0, 0.0)
        dirs = [(i + 1, *fan.az_plunge(axes[i])) for i in range(3)]
    else:
        if not isinstance(given, list) or not 1 <= len(given) <= sp.DIRECTIONS_MAX:
            raise sp.bad("bad_param", "Направления заданы неверно.", "Задайте от 1 до 72 направлений.", "directions")
        for i, d in enumerate(given):
            d = sp._obj(d, "Направление", f"directions.{i}")
            plunge = sp.ranged(d.get("plunge"), "plunge", f"directions.{i}.plunge")
            dirs.append((None, sp._angle(d.get("az"), "az", 0.0, f"directions.{i}.az"), plunge or 0.0))
    top = max((a for s in model.structures for a in s.ranges), default=None)
    h = _curve_h(raw, top)
    out = []
    for axis, az, plunge in dirs:
        g = model.gamma_along(h, az, plunge)
        out.append({"axis": axis, "az": az, "plunge": plunge, "h": h.tolist(), "gamma": g.tolist(),
                    "cov": (model.sill - g).tolist()})  # fmt: skip
    return {"directions": out}
