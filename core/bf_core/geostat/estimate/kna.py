"""KNA — the analysis of the kriging parameters of a row of the plan (ТЗ 8.8.5; ADR 0024 §8, решение 14, № 80;
e18-api.md §8.2; Э18, 18.15). The job `estimate.kna` (quick) of `POST /estimate/kna`.

    out = analyse(project, spec, blocks, vary, origin=None, tick=None)   # → the answer of §8.2 (`contract_answers.kna`)
    out = analyse_blocks(rk, blocks, vary, tick=…)                       # the same over a compiled row and BlockAt
    picks = auto_blocks(rk, targets)                                     # the three blocks: много, средне, мало
    n = recommend(rows)                                                  # the rule of the recommendation
    run_kna(engine, folder, params, tick)                                # the handler: {estimation_id | estimation,
                                                                         #  blocks?, vary?}

The test blocks — up to ten, pointed at (`{row} | {ijk} | {xyz}`) or, without them, three picked by the rule: of the
blocks of the row (`kna_blocks.Targets`) those the first passes can reach at all (at least the smallest `n_min` of
samples inside the radius of the LAST pass) are ranked by the number of samples inside the radius of the FIRST pass,
`n_r1` (ties — by the number of the row), and the block of rank round(q·(m − 1)) is taken for q = 0.9, 0.5, 0.1 —
«много», «средне», «мало». No random: the same row gives the same blocks; two quantiles that fall on one block give
it once, under the first label.

One parameter at a time (№ 80): each of `vary` is run over its own series with the others as the row has them —

    n_max   4, 8, … 64      `n_max` of every pass (its `n_min` is cut to it)
    radius  ×0.5 … ×2 by 0.25   every pass radius × the factor (the ellipsoid keeps its shape)
    disc    d × d × d, d = 1 … 5    d = 1 is the point
    size    ×0.5, ×1, ×2    the size of the block along every axis (the parent's with `sub: parent`)

Each variant is `kernel.estimate_block` of the block — the same code as the job, the preview and the inspector — bit
for bit; the indicators are KE, SLOPE, KV, SNW (the sum of the negative weights), `w_far` (the weight of the farthest
selected sample), LAGR (μ), NS, `status`; a block with the status 1–3 has them empty (null), never 0. The kriging
is OK or SK: NN, IDW and IK have no KE and SLOPE (422 `kna_method`).

The recommendation (8.8.5): the smallest `n_max` of the series whose SLOPE is at least 0.95 of the best of that block
(the blocks with the status 0 only; a best SLOPE of 0 or less — none); the common one — the LARGEST of the blocks'
(№ 80, the slope must hold for the block that wants the most). It is a value only, nothing is applied by itself.
"""

from __future__ import annotations

import dataclasses
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.jobs.registry import handler

from . import samples as sm
from .kna_blocks import BlockAt, Blocks, Targets
from .spec import EstimationSpec, parse_at, parse_estimation

if TYPE_CHECKING:  # numba stays out of the process of the API, which only registers the handler (№ 18.16)
    from . import kernel as kn

KIND = "estimate.kna"
TITLE = "Анализ параметров кригинга"
VARY = ("n_max", "radius", "disc", "size")
N_MAX = tuple(range(4, 65, 4))
RADIUS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
DISC = (1, 2, 3, 4, 5)
SIZE = (0.5, 1.0, 2.0)
SERIES: dict[str, tuple[Any, ...]] = {"n_max": N_MAX, "radius": RADIUS, "disc": DISC, "size": SIZE}
SHARE = 0.95  # the SLOPE of the recommended n_max, of the best one
QUANTILES = ((0.9, "много"), (0.5, "средне"), (0.1, "мало"))
BLOCKS_MAX = 10
STAGE_SAMPLES = "Пробы"
STAGE_VARIANTS = "Варианты"
AT_VARIANTS = 0.3  # the share of the progress bar the stage «Варианты» starts at
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def _bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


def _f(x: Any) -> float | None:
    """A number of an answer: a gap is null, never 0."""
    v = float(x)
    return v if math.isfinite(v) else None


# --- the variants of a compiled row ----------------------------------------------------------------------------------


def _fresh(rk: kn.RowKernel, **change: Any) -> kn.RowKernel:
    """A row with other fields; its own cache of the geometry of the blocks (it depends on the discretization)."""
    return dataclasses.replace(rk, _geo={}, _lock=threading.Lock(), **change)


def _with_passes(rk: kn.RowKernel, *, n_max: int | None = None, scale: float | None = None) -> kn.RowKernel:
    passes = []
    for p in rk.searcher.passes:
        if n_max is not None:
            p = dataclasses.replace(p, n_max=int(n_max), n_min=min(p.n_min, int(n_max)))
        if scale is not None:
            p = dataclasses.replace(p, factor=p.factor * float(scale))
        passes.append(p)
    return _fresh(rk, searcher=rk.searcher.with_passes(passes))


def _with_disc(rk: kn.RowKernel, d: int) -> kn.RowKernel:
    """d × d × d; the offsets and C̄_VV of a block come from `rk.geometry(size)` (the block cache of the model's
    covariance is shared), a point has the centre and C(0). Must be called on a row compiled without `block_size`."""
    disc = (d, d, d)
    return _fresh(rk, spec=dataclasses.replace(rk.spec, disc=disc), disc=disc)


def variants(rk: kn.RowKernel, name: str) -> list[tuple[Any, kn.RowKernel, float]]:
    """The series of a parameter: [(value as the answer shows it, the row with it, the factor of the block size)]."""
    out: list[tuple[Any, kn.RowKernel, float]] = []
    for v in SERIES[name]:
        if name == "n_max":
            out.append((int(v), _with_passes(rk, n_max=v), 1.0))
        elif name == "radius":
            out.append((float(v), _with_passes(rk, scale=v), 1.0))
        elif name == "disc":
            out.append(([int(v)] * 3, _with_disc(rk, int(v)), 1.0))
        else:
            out.append((float(v), rk, float(v)))
    return out


# --- the indicators of a block ---------------------------------------------------------------------------------------


def indicators(rk: kn.RowKernel, d: Any) -> dict[str, Any]:
    """KE, SLOPE, KV, SNW, w_far, LAGR, NS, status of a `BlockDetail` (null where there is none)."""
    v = d.values
    w_far = math.nan
    if d.sel.size and d.lam.size:
        h = rk.ell.h(rk.local[d.sel] - (d.centre - rk.samples.origin))
        w_far = float(d.lam[h.shape[0] - 1 - int(np.argmax(h[::-1]))])  # the farthest, of equal ones the last
    ns = v.get("NS", math.nan)
    return {"KE": _f(v.get("KE", math.nan)), "SLOPE": _f(v.get("SLOPE", math.nan)),
            "KV": _f(v.get("KV", math.nan)), "SNW": _f(v.get("SNW", math.nan)), "w_far": _f(w_far),
            "LAGR": _f(d.mu), "NS": int(ns) if math.isfinite(ns) else None, "status": int(d.status)}  # fmt: skip


def run_block(rk: kn.RowKernel, b: BlockAt, scale: float = 1.0) -> dict[str, Any]:
    """The indicators of block `b` (its size × `scale`) by `rk`: `kernel.estimate_block`."""
    from . import kernel as kn

    d = kn.estimate_block(rk, b.centre, b.size * scale, active=b.active)
    return indicators(rk, d)


def recommend(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """The smallest `value` whose SLOPE is at least 0.95 of the best among the rows with status 0 (ascending by
    `value`); None — no row, or the best SLOPE is not above 0."""
    ok = [r for r in rows if r.get("status") == 0 and r.get("SLOPE") is not None]
    if not ok:
        return None
    best = max(float(r["SLOPE"]) for r in ok)
    if best <= 0.0:
        return None
    for r in sorted(ok, key=lambda r: r["value"]):
        if float(r["SLOPE"]) >= SHARE * best:
            return int(r["value"])
    return None


# --- the three blocks ------------------------------------------------------------------------------------------------


def pick_quantiles(n_first: np.ndarray, row: np.ndarray, eligible: np.ndarray) -> list[tuple[int, str]]:
    """[(index into the arrays, label)] of the quantiles 0.9, 0.5, 0.1 of `n_first` among the `eligible`; ties by
    `row`; a block that two quantiles hit is listed once, with the first label."""
    idx = np.flatnonzero(eligible)
    if idx.size == 0:
        return []
    order = idx[np.lexsort((row[idx], n_first[idx]))]
    out: list[tuple[int, str]] = []
    for q, label in QUANTILES:
        pos = int(math.floor(q * (order.size - 1) + 0.5))
        at = int(order[pos])
        if all(at != a for a, _ in out):
            out.append((at, label))
    return out


def _tree_of(rk: kn.RowKernel) -> tuple[Any, np.ndarray]:
    """The tree of the samples of D in the scaled space of the ellipsoid and the matrix that scales a centre."""
    from scipy.spatial import cKDTree

    pts = rk.local[rk.samples.dom == sm.D] @ rk.ell.S.T
    return cKDTree(pts, balanced_tree=False, compact_nodes=False), rk.ell.S


def count_in(rk: kn.RowKernel, centres: np.ndarray, radius: float) -> np.ndarray:
    """i8 [m]: the samples of the domain D inside the ellipsoid of the radius `radius` (along r1) round each centre."""
    tree, scale = _tree_of(rk)
    got = tree.query_ball_point((centres - rk.samples.origin) @ scale.T, float(radius), return_length=True, workers=-1)
    return np.asarray(got, dtype=np.int64)


def enough_in(rk: kn.RowKernel, centres: np.ndarray, radius: float, k: int) -> np.ndarray:
    """bool [m]: at least `k` samples of D inside the ellipsoid of the radius `radius` round each centre — the same
    answer as `count_in(...) >= k` without counting the whole ball (k nearest within the radius, many threads)."""
    tree, scale = _tree_of(rk)
    k = max(1, int(k))
    # `query` keeps the distances strictly below the bound, the ball keeps `≤ radius`: the next float up
    bound = float(np.nextafter(float(radius), np.inf))
    dist, _ = tree.query((centres - rk.samples.origin) @ scale.T, k=k, distance_upper_bound=bound, workers=-1)
    last = dist if k == 1 else dist[:, -1]
    return np.asarray(np.isfinite(last), dtype=bool)


def auto_blocks(rk: kn.RowKernel, targets: Targets) -> list[BlockAt]:
    """The blocks «много», «средне», «мало» (module docstring). 422 `kna_no_blocks` — no block of the row has enough
    samples within reach of any pass."""
    passes = rk.searcher.passes
    r1 = float(rk.ell.radii[0])
    n_first = count_in(rk, targets.centre, r1 * passes[0].factor)
    eligible = enough_in(rk, targets.centre, r1 * max(p.factor for p in passes), min(p.n_min for p in passes))
    picks = pick_quantiles(n_first, targets.row, eligible)
    if not picks:
        raise ApiError(422, "kna_no_blocks", "Ни у одного блока нет достаточно проб в радиусе поиска.",
                       "Увеличьте радиусы поиска или уменьшите минимум проб.", {"param": "search"})  # fmt: skip
    return [targets.block(i, auto=label, n_r1=int(n_first[i])) for i, label in picks]


# --- the analysis ----------------------------------------------------------------------------------------------------


def parse_vary(raw: Any, param: str = "vary") -> tuple[str, ...]:
    """The parameters to vary, in the order of `VARY`; nothing given — all of them."""
    if raw is None:
        return VARY
    if not isinstance(raw, list | tuple) or not raw:
        raise _bad(param, "Не выбраны параметры для перебора.", "Выберите n_max, радиусы, дискретизацию или размер.")
    for i, v in enumerate(raw):
        if v not in VARY:
            raise _bad(f"{param}.{i}", "Неизвестный параметр перебора.",
                       "Допустимо: n_max, radius, disc, size.")  # fmt: skip
    return tuple(v for v in VARY if v in raw)


def analyse_blocks(rk: kn.RowKernel, blocks: Sequence[BlockAt], vary: Sequence[str] = VARY, *,
                   tick: Tick = _noop) -> dict[str, Any]:  # fmt: skip
    """The blocks of the answer and the recommendation over a row compiled WITHOUT `block_size` (the size of every
    block goes with the block). `tick(progress, message)` before every variant; it raises to cancel."""
    series = {name: variants(rk, name) for name in vary}
    steps = len(blocks) * (1 + sum(len(s) for s in series.values()))
    done = 0
    out: list[dict[str, Any]] = []
    for b in blocks:
        tick(AT_VARIANTS + (1.0 - AT_VARIANTS) * done / steps, STAGE_VARIANTS)
        base = run_block(rk, b)
        done += 1
        rows: dict[str, list[dict[str, Any]]] = {}
        for name, seq in series.items():
            rows[name] = []
            for value, variant, scale in seq:
                tick(AT_VARIANTS + (1.0 - AT_VARIANTS) * done / steps, STAGE_VARIANTS)
                rows[name].append({"value": value, **run_block(variant, b, scale)})
                done += 1
        rec = {"n_max": recommend(rows["n_max"])} if "n_max" in rows else {}
        out.append({"at": b.at(), "centre": [float(v) for v in b.centre], "size": [float(v) for v in b.size],
                    "n_r1": b.n_r1, "auto": b.auto, "base": base, "vary": rows, "recommend": rec})  # fmt: skip
    common: dict[str, Any] = {}
    if "n_max" in vary:
        found = [b["recommend"]["n_max"] for b in out if b["recommend"]["n_max"] is not None]
        common = {"n_max": max(found) if found else None}
    return {"blocks": out, "recommend": common}


def _blocks_of(bl: Blocks, raw: Any) -> list[BlockAt] | None:
    """The blocks the body points at (None — three automatically)."""
    if raw is None:
        return None
    if not isinstance(raw, list) or not 1 <= len(raw) <= BLOCKS_MAX:
        raise _bad("blocks", f"Тестовых блоков должно быть от 1 до {BLOCKS_MAX}.",
                   "Щёлкните блоки в 3D или оставьте выбор автоматическим.")  # fmt: skip
    return [bl.place(parse_at(a, f"blocks.{i}"), f"blocks.{i}") for i, a in enumerate(raw)]


def analyse(project: Any, spec: EstimationSpec, blocks: Any = None, vary: Any = None, *,
            origin: np.ndarray | None = None, tick: Tick = _noop) -> dict[str, Any]:  # fmt: skip
    """The answer of §8.2 for the row `spec`: `blocks` — the raw list of the body (None — three automatically),
    `origin` — the stored one of the row. `project` needs `engine`, `uid`, `folder`."""
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    names = parse_vary(vary)
    if spec.method.kind not in ("ok", "sk"):
        raise ApiError(422, "kna_method", "Анализ параметров кригинга — только для простого и обычного кригинга.",
                       "Выберите метод SK или OK.", {"param": "method.kind"})  # fmt: skip
    beat = lambda: tick(None, None)  # noqa: E731
    tick(0.02, STAGE_SAMPLES)
    s = sm.load(project, spec, origin=origin, cancel=beat)
    tick(0.15, STAGE_SAMPLES)
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        bl = Blocks.open(conn, store, spec, cancel=beat)
    placed = _blocks_of(bl, blocks)
    from . import kernel as kn

    rk = kn.compile_row(spec, s, rotation_deg=bl.rotation, blockcov=None)
    tick(0.25, STAGE_SAMPLES)
    targets = None
    if placed is None:
        targets = bl.targets()
        placed = auto_blocks(rk, targets)
    else:
        first = float(rk.ell.radii[0]) * rk.searcher.passes[0].factor
        n_first = count_in(rk, np.stack([b.centre for b in placed]), first)
        placed = [dataclasses.replace(b, n_r1=int(n)) for b, n in zip(placed, n_first, strict=True)]
    out = analyse_blocks(rk, placed, names, tick=tick)
    warnings = []
    if rk.point and "size" in names:
        warnings.append("Дискретизация блока 1×1×1 (точка): размер блока не меняет оценку.")
    return {"method": spec.method.kind, "vary": list(names), "threshold": SHARE, **out,
            "candidates": None if targets is None else targets.total, "warnings": warnings}  # fmt: skip


# --- the job ---------------------------------------------------------------------------------------------------------


def _spec_of(engine: Any, params: Mapping[str, Any]
             ) -> tuple[EstimationSpec, np.ndarray | None, int | None, str | None]:  # fmt: skip
    """The row to analyse: a stored one (`estimation_id`: its parameters and its `origin`) or a one-off body."""
    from bf_core.db import dialect

    from . import store
    from . import store_base as sb
    from .store_check import with_trend

    have = [k for k in ("estimation_id", "estimation") if params.get(k) is not None]
    if len(have) != 1:
        raise _bad("estimation_id", "Укажите параметры оценки: сохранённые (estimation_id) или тело (estimation).",
                   "Выберите строку оценки.")  # fmt: skip
    if have[0] == "estimation_id":
        eid = params["estimation_id"]
        if isinstance(eid, bool) or not isinstance(eid, int) or eid < 1:
            raise _bad("estimation_id", "Номер параметров оценки — целое число.", "Выберите строку оценки.")
        with dialect.read_tx(engine) as conn:
            row = store.get(conn, eid)
            meta = row["meta"]
            spec = with_trend(conn, sb.load_spec(meta))  # `from: trend`: the ellipsoid of the trend, as the job takes
        origin = meta.get("origin")
        return (spec, None if not origin else np.asarray(origin, dtype=np.float64), eid, row["name"])
    with dialect.read_tx(engine) as conn:
        return with_trend(conn, parse_estimation(params["estimation"]).resolved()), None, None, None


def run_kna(engine: Any, folder: Any, params: Mapping[str, Any], tick: Tick = _noop) -> dict[str, Any]:
    """The body of `estimate.kna` (the handler, the tests and the bench in the process): the answer of §8.2 with
    `estimation_id` and `name` of a stored row. A cancel (`tick` raises) leaves nothing — the job writes nothing."""
    spec, origin, eid, name = _spec_of(engine, params)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    out = analyse(project, spec, params.get("blocks"), params.get("vary"), origin=origin, tick=tick)
    return {"estimation_id": eid, "name": name, **out}


def memory_mb(params: Mapping[str, Any]) -> int:  # noqa: ARG001
    return 512


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def kna_job(ctx: Any) -> dict[str, Any]:
    from bf_core.composites import jobs as cj

    assert ctx.project_folder is not None
    try:
        return run_kna(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None  # noqa: SLF001
