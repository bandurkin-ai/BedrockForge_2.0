"""The global check of an estimate (ТЗ 8.8.5 «Глобальная статистика», «Гистограммы и Q-Q»; ADR 0024 §8; e18-api.md
§8.3–8.4; Э18, 18.16). The job `estimate.global` (quick) of `POST /estimate/global`; the synchronous Q-Q of `POST
/estimate/qq`; the draft of the swath series.

    out = global_check(project, spec, origin=None, tick=None, threads=None)    # → the answer of §8.3
    out = qq_check(project, spec, origin=None, quantiles=None, weighted=True)   # → the answer of §8.4
    run_global(engine, folder, params, tick)       # the handler: {estimation_id}
    run_qq(engine, folder, params, tick)           # {estimation_id, quantiles?, weighted?}
    swath_settings(spec, direction="z")            # the series of the swath window (Э13) for the fields of the row

The blocks are those the job wrote for THIS row: the blocks of its domain (the field and the label of the target; rows
of other domains share the prefix's fields, № 77) that pass its restrictor and whose `<prefix>STATUS` is 0 (Д-27); a
block with another status is not in a sum at all (its EST is empty), and a gap of EST or of the volume is never a zero
(8.0.1). The weight of a block is its volume, `FRAC` included (`geometry.row_volumes`). Compared:

    composites   the samples of the domain D (as the row estimates them: after the top cut and the averaging), naive
                 and declustered by `DECL_W` (the weights of the row); mean, variance (population, Σw(z − m)²/Σw), CV
    nn           the nearest sample for the SAME blocks, on the fly, nothing is written: the passes, n_min and the
                 domains of the row, only the method is `nn`; `sub: parent` — at the centre of the parent
    estimate     the field of the estimate of the row (`<prefix>EST`; for IK `<prefix>ETYPE`)
    diff_pct     100·(mean_est − mean_ref)/mean_ref, ref — the declustered mean (the naive one when the row has no
                 weights: `composites.reference`); `norm` — |diff_pct| ≤ 5 % (`LIMIT_PCT`, 8.8.5); `diff_nn_pct` and
                 `norm_nn` — the same against NN
    block_var    `expected` — C̄_VV of the variogram of the row for the block (its discretization, a point row takes
                 4×4×4: the support of the block, not of a point); `estimate` — the variance of the field; `ratio`
                 = estimate/expected (kriging smooths: below 1); `sill` — C(0). Without a variogram of z (NN, IDW
                 without one, IK) — `expected` is null

Streaming: the field is read by portions of `PORTION` rows (`Model.iter`), the sums of a portion are exact pairwise
sums, portions are joined by the formula of Chan (mean, M2) in the order of the file; NN goes through
`kernel.run` on threads. The same bits with 1, 2, 4, 8 threads. `tick(progress, message)` between the portions raises
to cancel; nothing is written.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job, geometry, restrict
from bf_core.blocks.model import Chunk, Model
from bf_core.db import dialect
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import samples as sm
from . import store_base as sb
from .job_plan import domain_mask
from .spec import EstimationSpec
from .spec_parts import Outputs
from .store_check import freshness, with_trend

if TYPE_CHECKING:  # numba and scipy stay out of the process of the API, which only registers the handler (№ 18.16)
    from bf_core.stats.weighted import Sorted

    from . import kernel as kn

KIND = "estimate.global"
TITLE = "Глобальная проверка оценки"
STAGE_READ, STAGE_NN, STAGE_STATS = "Чтение", "Ближайший сосед", "Статистика"
LIMIT_PCT = 5.0  # ТЗ 8.8.5: the means of the estimate and of the declustered samples differ by at most 5 %
PORTION = 1 << 16
POINT_DISC = (4, 4, 4)  # C̄_VV of a row that estimates in points: the block is still a block
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def _bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


def _num(x: Any) -> float | None:
    """A number of an answer: a gap is null, never 0."""
    v = float(x)
    return v if math.isfinite(v) else None


# --- the streaming statistics of the blocks -----------------------------------------------------------------------


class Acc:
    """Σw, the weighted mean and M2 = Σw(v − m)² of the pairs (v, w) that come by portions (Chan et al.): stable, the
    result depends only on the portions and their order. The caller gives finite values and weights > 0."""

    def __init__(self) -> None:
        self.n = 0
        self.w = 0.0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, v: np.ndarray, w: np.ndarray) -> None:
        from bf_core.stats.weighted import pair_sums

        if v.size == 0:
            return
        v = np.ascontiguousarray(v, dtype=np.float64)
        w = np.ascontiguousarray(w, dtype=np.float64)
        wp, swv = pair_sums(w, v)
        mp = swv / wp
        m2p = pair_sums(w, (v - mp) * (v - mp))[1]
        total = self.w + wp
        delta = mp - self.mean
        self.mean += delta * wp / total
        self.m2 += m2p + delta * delta * self.w * wp / total
        self.w = total
        self.n += int(v.size)

    def stats(self) -> dict[str, Any]:
        """{n, volume, mean, var, cv}; nothing accumulated — nulls (never 0)."""
        if self.n == 0:
            return {"n": 0, "volume": None, "mean": None, "var": None, "cv": None}
        var = max(0.0, self.m2 / self.w)
        mean = self.mean
        return {"n": self.n, "volume": self.w, "mean": _num(mean), "var": _num(var),
                "cv": _num(math.sqrt(var) / mean) if mean > 0.0 else None}  # fmt: skip


def sorted_stats(s: Sorted) -> dict[str, Any]:
    """{n, mean, var, cv} of a sorted sample of `stats.weighted` (empty — nulls)."""
    if s.n == 0:
        return {"n": 0, "mean": None, "var": None, "cv": None}
    return {"n": s.n, "mean": _num(s.mean), "var": _num(s.variance), "cv": _num(s.cv)}


def diff_pct(mean: float | None, ref: float | None) -> float | None:
    """100·(mean − ref)/ref; no number or ref = 0 — null."""
    if mean is None or ref is None or ref == 0.0:
        return None
    return _num(100.0 * (mean - ref) / ref)


def is_norm(diff: float | None) -> bool | None:
    """|diff| ≤ 5 % (null — no verdict)."""
    return None if diff is None else bool(abs(diff) <= LIMIT_PCT)


def composites(s: sm.RowSamples) -> tuple[Sorted, Sorted | None]:
    """The samples of the domain D: (naive, declustered | None — the row has no weights)."""
    from bf_core.stats.weighted import sort_groups

    d = s.dom == sm.D
    z = s.z[d]
    naive = sort_groups(z, np.ones(z.shape))[0]
    declus = None if s.w is None else sort_groups(z, s.w[d])[0]
    return naive, declus


# --- the blocks of the row ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fields:
    est: str  # the stored names
    status: str
    frac: str | None


def field_names(model: Model, spec: EstimationSpec) -> Fields:
    """The fields the row wrote; 409 `not_estimated` when the model has none."""
    prefix = spec.outputs.prefix
    key = "ETYPE" if spec.method.kind == "ik" else "EST"
    got = []
    for name in (prefix + key, prefix + "STATUS"):
        try:
            got.append(str(model.field(name)["name"]))
        except ApiError:
            raise ApiError(
                409,
                "not_estimated",
                f"В блочной модели нет поля «{name}»: оценка ещё не выполнялась.",
                "Запустите оценку этой строки (или выберите другую).",
                {"param": "estimation_id"},
            ) from None
    frac = model.role_field("frac")
    return Fields(got[0], got[1], None if frac is None else str(frac["name"]))


@dataclass
class Part:
    """A portion of the blocks of the row: the chunk, the rows that count (STATUS 0, a value, a volume) and their
    values and volumes."""

    chunk: Chunk
    rows: np.ndarray  # intp: positions in the chunk
    est: np.ndarray
    vol: np.ndarray
    by_status: np.ndarray  # i8 [4]
    empty: int  # STATUS 0 without a value
    no_volume: int  # STATUS 0 with a value but without a volume (FRAC empty)


@dataclass
class Domain:
    """The blocks of the row in the shared fields of its prefix (№ 77): the domain of the target and the restrictor, the
    same functions the job uses (`job_plan.domain_mask`, `restrict`). `fields` — what a chunk must hold for them."""

    model: Model
    field: str | None
    label: Any
    restrictor: Any  # restrict.Restrictor

    @property
    def fields(self) -> list[str]:
        own = [] if self.field is None else [str(self.model.field(self.field)["name"])]
        return list(dict.fromkeys([*own, *self.restrictor.fields]))

    def inside(self, ch: Chunk) -> np.ndarray:
        """bool [ch.n]: the block is in the domain of the row."""
        return domain_mask(self.model, self.field, self.label, ch.cols, ch.n)

    def passes(self, ch: Chunk) -> np.ndarray:
        """bool [ch.n]: the block passes the restrictor of the target."""
        return np.asarray(self.restrictor.mask(ch), dtype=np.bool_)


def domain_of(conn: Any, store: BlobStore, model: Model, spec: EstimationSpec) -> Domain:
    """The domain and the restrictor of the target of `spec` on `model` (as `job_plan._row` makes them)."""
    t = spec.target
    tree = calc_job.rules_tree(conn, t.filter_id, t.rules)
    return Domain(model, t.domain_field, t.label, restrict.prepare(model, tree, conn=conn, store=store))


def parts(model: Model, f: Fields, cancel: Callable[[], None] | None = None, domain: Domain | None = None
          ) -> Iterator[Part]:  # fmt: skip
    """The portions of the field with the rows that count. A gap of the value or of the volume leaves the sum (and is
    counted), a block with another STATUS is not there at all; with `domain` — nor a block of another domain of the
    prefix (its STATUS is that row's) or one the restrictor of the target does not let pass."""
    names = [f.est, f.status] + ([f.frac] if f.frac else [])
    if domain is not None:
        names = list(dict.fromkeys([*names, *domain.fields]))
    for ch in model.iter(names, PORTION):
        if cancel is not None:
            cancel()
        status = np.asarray(ch.cols[f.status], dtype=np.float64)
        est = np.asarray(ch.cols[f.est], dtype=np.float64)
        frac = None if f.frac is None else np.asarray(ch.cols[f.frac], dtype=np.float64)
        vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, frac)
        passed = np.ones(ch.n, dtype=np.bool_)
        if domain is not None:
            status = np.where(domain.inside(ch), status, np.nan)  # another domain: no status here at all
            passed = domain.passes(ch)
        zero = status == 0.0
        have = zero & np.isfinite(est)
        use = have & np.isfinite(vol) & (vol > 0.0) & passed
        code = status[np.isfinite(status) & (status >= 0.0) & (status <= 3.0)].astype(np.int64)
        rows = np.flatnonzero(use)
        yield Part(ch, rows, est[rows], vol[rows], np.bincount(code, minlength=4).astype(np.int64),
                   int((zero & ~np.isfinite(est)).sum()), int((have & ~np.isfinite(vol)).sum()))  # fmt: skip


def nn_spec(spec: EstimationSpec) -> EstimationSpec:
    """The row of the nearest sample: the same samples, passes and search, the method `nn`, a point, EST only."""
    method = replace(spec.method, kind="nn", thresholds=())
    return replace(spec, method=method, disc=(1, 1, 1), outputs=Outputs(spec.outputs.prefix, ("STATUS", "EST")))


def block_centres(model: Model, part: Part, parent: bool) -> tuple[np.ndarray, np.ndarray]:
    """(centres [m, 3] of the blocks to estimate, the index of the block of every row of `part.rows`): `sub: parent`
    — the centres of the distinct parents, a row takes its parent's; `sub: own` — the centre of the row."""
    ch, r = part.chunk, part.rows
    if parent:
        _, first, inverse = np.unique(ch.p[r], return_index=True, return_inverse=True)
        return geometry.centres(model.grid, ch.p[r][first]), np.asarray(inverse).reshape(-1)
    s = None if ch.s is None else ch.s[r]
    w = None if ch.w is None else ch.w[r]
    return geometry.centres(model.grid, ch.p[r], s, w), np.arange(r.size)


def block_size(model: Model, part: Part, parent: bool) -> tuple[float, float, float]:
    """The size of a block of the row: the parent's, or the median of the rows' (a variable step has many sizes)."""
    ch, r = part.chunk, part.rows
    s = None if parent or ch.s is None else ch.s[r]
    w = None if parent or ch.w is None else ch.w[r]
    widths = geometry.row_widths(model.grid, ch.p[r], s, w)
    med = np.median(widths, axis=0)
    return float(med[0]), float(med[1]), float(med[2])


def expected_variance(spec: EstimationSpec, grid_rotation: float, size: tuple[float, float, float]
                      ) -> tuple[float | None, float | None, tuple[int, int, int] | None]:  # fmt: skip
    """(C̄_VV, C(0), the discretization) of the variogram of the row for a block of `size`; nulls without a variogram of
    the field (NN and IDW may have none; IK models are of indicators)."""
    if spec.method.kind == "ik" or spec.variogram is None or spec.variogram.snapshot is None:
        return None, None, None
    from . import kernel as kn
    from .cov import BlockCov

    cm = kn._variogram_cov(spec)  # noqa: SLF001
    disc = spec.disc if spec.disc != (1, 1, 1) else POINT_DISC
    vv = BlockCov(cm).vv(size, disc, grid_rotation)
    return _num(vv), _num(cm.sill), disc


def _blocks_info(rows: int, by_status: np.ndarray, used: int, empty: int, no_volume: int) -> dict[str, Any]:
    return {"rows": rows, "by_status": [int(v) for v in by_status], "used": used, "empty": empty,
            "no_volume": no_volume}  # fmt: skip


def global_check(project: Any, spec: EstimationSpec, *, origin: np.ndarray | None = None, tick: Tick = _noop,
                 threads: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The answer of §8.3 for the row `spec` (module docstring). `project` needs `engine`, `uid`, `folder`.
    409 `not_estimated`, 422 `no_samples`, `no_estimated_blocks`, `search_unresolved`."""
    import os

    from . import kernel as kn

    beat = lambda: tick(None, None)  # noqa: E731
    tick(0.0, STAGE_READ)
    with dialect.read_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        model = Model.open(conn, store, spec.model_id)
        domain = domain_of(conn, store, model, spec)
    f = field_names(model, spec)
    if not spec.resolved_search:
        raise ApiError(422, "search_unresolved", "Эллипсоид поиска не определён.",
                       "Откройте параметры оценки и сохраните их заново.", {"param": "search"})  # fmt: skip
    s = sm.load(project, spec, origin=origin, cancel=beat)
    naive, declus = composites(s)
    rk = kn.compile_row(nn_spec(spec), s)
    tick(0.1, STAGE_NN)
    parent = spec.sub == "parent"
    n_threads = max(1, min(4, os.cpu_count() or 1)) if threads is None else max(1, int(threads))
    est_acc, nn_acc = Acc(), Acc()
    by_status = np.zeros(4, np.int64)
    empty = no_volume = 0
    size: tuple[float, float, float] | None = None
    queue: deque[tuple[np.ndarray, np.ndarray, int]] = deque()

    def portions() -> Iterator[kn.Portion]:
        nonlocal by_status, empty, no_volume, size
        for part in parts(model, f, beat, domain):
            by_status += part.by_status
            empty += part.empty
            no_volume += part.no_volume
            if part.rows.size == 0:
                continue
            est_acc.add(part.est, part.vol)
            if size is None:
                size = block_size(model, part, parent)
            centres, back = block_centres(model, part, parent)
            queue.append((part.vol, back, part.chunk.start + part.chunk.n))
            yield kn.Portion(centres)

    for res in kn.run(rk, portions(), threads=n_threads, tick=tick):
        vol, back, last = queue.popleft()
        nn = res.values["EST"][back]
        ok = np.isfinite(nn) & (res.status[back] == 0)
        nn_acc.add(nn[ok], vol[ok])
        tick(0.1 + 0.8 * last / max(1, model.rows), STAGE_NN)
    beat()
    tick(0.92, STAGE_STATS)
    if est_acc.n == 0 or size is None:
        raise ApiError(422, "no_estimated_blocks", "В блочной модели нет оценённых блоков (STATUS = 0).",
                       "Проверьте параметры оценки и запустите её снова.", {"param": "estimation_id"})  # fmt: skip
    est = est_acc.stats()
    nn_stats = nn_acc.stats()
    comp_naive, comp_declus = sorted_stats(naive), None if declus is None else sorted_stats(declus)
    ref_name = "naive" if comp_declus is None or comp_declus["mean"] is None else "declus"
    ref = comp_naive if ref_name == "naive" else comp_declus
    assert ref is not None
    d_ref = diff_pct(est["mean"], ref["mean"])
    d_nn = diff_pct(est["mean"], nn_stats["mean"])
    cvv, sill, disc = expected_variance(spec, float(model.grid.rotation), size)
    var = est["var"]
    warnings: list[str] = []
    if model.grid.tensors is not None:
        warnings.append("Переменный шаг сетки: C̄_VV посчитан для медианного размера блока.")
    if ref_name == "naive":
        warnings.append("У строки нет весов декластеризации: сравнение — с наивным средним проб.")
    if empty or no_volume:
        warnings.append(f"Блоки STATUS = 0 вне статистики: без значения — {empty}, без объёма — {no_volume}.")
    tick(1.0, STAGE_STATS)
    return {"field": f.est, "status_field": f.status, "model_id": spec.model_id, "method": spec.method.kind,
            "sub": spec.sub, "limit_pct": LIMIT_PCT,
            "composites": {"naive": comp_naive, "declus": comp_declus, "reference": ref_name},
            "nn": nn_stats, "estimate": est,
            "diff_pct": d_ref, "norm": is_norm(d_ref), "diff_nn_pct": d_nn, "norm_nn": is_norm(d_nn),
            "block_var": {"expected": cvv, "estimate": var, "sill": sill, "disc": None if disc is None else list(disc),
                          "size": list(size),
                          "ratio": None if cvv is None or var is None or cvv == 0.0 else _num(var / cvv)},
            "blocks": _blocks_info(model.rows, by_status, est_acc.n, empty, no_volume),
            "warnings": warnings}  # fmt: skip


# --- Q-Q ---------------------------------------------------------------------------------------------------------


def qq_check(project: Any, spec: EstimationSpec, *, origin: np.ndarray | None = None, quantiles: Any = None,
             weighted: bool = True, tick: Tick = _noop) -> dict[str, Any]:  # fmt: skip
    """The answer of §8.4: `stats.qq.qq_samples` of the samples of the domain D (x; weighted by `DECL_W` when
    `weighted` and the row has weights) against the field of the estimate over the blocks STATUS 0 (y; the weight is
    the volume): {…qq_samples, estimation field, weighted, x {n, mean, var, cv}, y {…}}."""
    from bf_core.stats import qq as qqmod
    from bf_core.stats.weighted import StatsError, sort_groups

    beat = lambda: tick(None, None)  # noqa: E731
    n_q = _quantiles(quantiles)
    tick(0.0, STAGE_READ)
    with dialect.read_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        model = Model.open(conn, store, spec.model_id)
        domain = domain_of(conn, store, model, spec)
    f = field_names(model, spec)
    s = sm.load(project, spec, origin=origin, cancel=beat)
    d = s.dom == sm.D
    use_w = bool(weighted) and s.w is not None
    x = sort_groups(s.z[d], s.w[d] if use_w and s.w is not None else np.ones(int(d.sum())))[0]
    tick(0.3, STAGE_READ)
    vals: list[np.ndarray] = []
    vols: list[np.ndarray] = []
    for part in parts(model, f, beat, domain):
        vals.append(part.est)
        vols.append(part.vol)
    tick(0.7, STAGE_STATS)
    y = sort_groups(np.concatenate(vals) if vals else np.zeros(0), np.concatenate(vols) if vols else np.zeros(0))[0]
    try:
        out = qqmod.qq_samples(x, y, n_q)
    except StatsError as exc:
        raise ApiError(422, exc.code, exc.message, exc.hint, {"param": "estimation_id"}) from None
    tick(1.0, STAGE_STATS)
    return {"field": f.est, "weighted": use_w, **out, "x": sorted_stats(x), "y": sorted_stats(y)}


def _quantiles(raw: Any) -> int:
    from bf_core.stats import qq as qqmod
    from bf_core.stats.weighted import StatsError

    try:
        return qqmod.check_quantiles(raw)
    except StatsError as exc:
        raise ApiError(422, exc.code, exc.message, exc.hint, {"param": "quantiles"}) from None


# --- the swath draft ----------------------------------------------------------------------------------------------


def swath_settings(spec: EstimationSpec, direction: str = "z") -> dict[str, Any]:
    """The settings of the swath window of Э13 (`POST /stats/swath`, ТЗ 7.30) for the row: the series `blocks` over the
    field of the estimate (weight — volume; the blocks with another STATUS have it empty, so the swath leaves them
    out) with the domain of the row as its category filter, and the series `samples` — the samples of the row inside
    the blocks of the first series (`within: blocks`), weighted by the weights of the row. The window opens with them
    (`estimate.swath`)."""
    key = "ETYPE" if spec.method.kind == "ik" else "EST"
    t = spec.target
    flt = None if t.domain_field is None else {"field": t.domain_field, "labels": [str(t.label)]}
    weights = spec.samples.weights
    if weights is False:
        weight = {"weight": "none", "weight_field": None}
    else:
        weight = {"weight": "field", "weight_field": "DECL_W" if weights in (None, True) else str(weights)}
    return {"direction": direction, "series": [
        {"kind": "blocks", "model_id": spec.model_id, "field": spec.outputs.prefix + key, "weight": "volume",
         "density": None, "filter": flt, "filter_id": t.filter_id},
        {"kind": "samples", "set_id": spec.samples.set_id, "table": spec.samples.table, "field": spec.samples.field,
         **weight, "negatives": "keep", "within": "blocks", "within_series": 0}]}  # fmt: skip


# --- the jobs -----------------------------------------------------------------------------------------------------


def _row(engine: Any, params: Any) -> tuple[int, str, EstimationSpec, np.ndarray | None]:
    """(id, name, the parameters, the stored `origin`) of the row of the body; 404 `estimation_not_found`, 409
    `source_lost`, 422 `bad_param`."""
    ident = params.get("estimation_id") if isinstance(params, dict) else None
    if isinstance(ident, bool) or not isinstance(ident, int) or ident < 1:
        raise _bad("estimation_id", "Не указаны параметры оценки для проверки.", "Выберите строку «Параметры оценки».")
    with dialect.read_tx(engine) as conn:
        row = sb.stored(conn, sb.KIND, ident)
        meta = dict(row.meta or {})
        if freshness(conn, meta)["source_lost"]:
            raise ApiError(409, "source_lost", "Источник параметров оценки удалён.",
                           "Восстановите таблицу, модель вариограммы или блочную модель либо удалите строку.",
                           {"param": "estimation_id"})  # fmt: skip
        spec = with_trend(conn, sb.load_spec(meta))  # `from: trend`: the ellipsoid of the trend, as the job takes it
        name = str(row.name)
    origin = meta.get("origin")
    org = np.asarray(origin, dtype=np.float64) if isinstance(origin, list) and len(origin) == 3 else None
    return ident, name, spec, org


def run_global(engine: Any, folder: Any, params: Any, tick: Tick = _noop, threads: int | None = None) -> dict[str, Any]:
    """The body of `estimate.global` (the handler, the tests and the bench in the process): `{estimation_id}` → §8.3
    with `estimation_id` and `name`. Nothing is written; a cancel (`tick` raises) leaves nothing."""
    ident, name, spec, org = _row(engine, params)
    project = SimpleNamespace(engine=engine, uid=f"job:{Path(folder)}", folder=Path(folder))
    return {"estimation_id": ident, "name": name,
            **global_check(project, spec, origin=org, tick=tick, threads=threads)}  # fmt: skip


def run_qq(engine: Any, folder: Any, params: Any, tick: Tick = _noop) -> dict[str, Any]:
    """The synchronous Q-Q (`POST /estimate/qq`): `{estimation_id, quantiles?, weighted?}` → §8.4."""
    ident, name, spec, org = _row(engine, params)
    weighted = params.get("weighted")
    if weighted is not None and not isinstance(weighted, bool):
        raise _bad("weighted", "Взвешивание — да или нет.", "Передайте true или false.")
    project = SimpleNamespace(engine=engine, uid=f"job:{Path(folder)}", folder=Path(folder))
    return {"estimation_id": ident, "name": name,
            **qq_check(project, spec, origin=org, quantiles=params.get("quantiles"),
                       weighted=True if weighted is None else weighted, tick=tick)}  # fmt: skip


def memory_mb(params: Any) -> int:  # noqa: ARG001
    return 768


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def global_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_global(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
