"""The tab «Статистика» (ТЗ 7.29; ADR 0019 §7): the points of the tab and its analyses of Э13 — declustering,
normal scores, the bootstrap — on arrays; the port of the data and of the three analyses of BF
`api/app/routers/stats.py` (`load_samples`, `compute_weights`, declustering, nscore, backtr, bootstrap). The
variograms, the variogram map and the local uncertainty are Э16. Nothing is stored: the API (13.17) reads the
table (`stats/source.py`: the field and `midpoints`), calls these and returns their dicts (JSON-ready; an
undefined number is None, never 0).

    tab_points(positions, values, least=1) -> TabPoints
        the points of the tab: an interval with a value and a place — the middle of the interval on the path of
        its hole, in plan (X, Y); the rest is counted: counts {rows, missing, no_position, used}. More than
        `MAX_SAMPLES` (20 000) — `too_many_samples` with the advice to composite (Т7.29-2)
    tab_declus(points, params=None, threads=1, tick=None, weights=False) -> dict
        declustering in plan on the kernels of Э12 (`stats/declus3d.py`, `stats/declustering.py`): the mean with
        the weights and without, the curve «размер ячейки → среднее», the best size; cells or polygons
    tab_nscore(points, declus=None, zmin=None, zmax=None, threads=1) -> dict
        the table of the normal score transform (with the weights of declustering — `declus` are the
        parameters of `tab_declus`) and the score of every point
    tab_backtr(points, scores, declus=None, zmin=None, zmax=None, ltail="linear", utail="linear", ltpar=None,
               utpar=None, threads=1) -> {"values": […]}       scores → values by the table of the data
    tab_bootstrap(points, n_boot=1000, seed=None, confidence=0.95, declus=None, threads=1, tick=None) -> dict
        the intervals of the mean, the median, σ, CV, P10, P90 (`stats/bootstrap.py`)

Parameters of declustering — the names of the command `composite.declus` (Э12): method cell | polygon;
cmin, cmax, ncell, noff, minmax — the cells (by default, as BF: 2 % and 50 % of the larger extent in plan, 20
steps of the size, 8 shifts of the origin, the least mean); nx, ny — the grid of the polygons (by default by
the number of holes, Э12), bounds [xmin, xmax, ymin, ymax] — the area the polygons are cut by (by default the
extent of the points, as BF and Э12). "cell3d" is not of the tab: it works in plan (ТЗ 7.29).

A regular grid without clusters (Т7.29-1). The declustered mean equals the plain one exactly where the method
has no edge effect: cells not larger than the step of the grid, cells of whole steps laid on the grid,
polygons bounded half a step around the holes, a grade that the border does not bias. The sweep of GSLIB over
cells larger than the step takes the size of the least mean whatever made it: on random grades of a regular
grid that is the edge of the grid (its holes weigh more) — a few per cent; `uniform`, `best_cell_size` and the
curve show it. The kernels of Э12 are used as they are.

Errors — `StatsError` {code, message, hint} with `param`: `too_many_samples`, `no_values`, `too_few_values`,
`no_spread`, `bad_param`, `bad_input`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .transforms import as_values, check_tails, nscore
from .weighted import StatsError, Tick

MAX_SAMPLES = 20_000  # ТЗ 7.29: more intervals are not computed
METHODS = ("cell", "polygon")
CELL_SHARES = (0.02, 0.5)  # the default cell sizes: shares of the larger extent in plan (BF)
MAX_SCORES = 20_000  # scores of one back-transform (BF)
_COMPOSITE = "«Анализ → Обработка проб → Композиты»"


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


# --- the points -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class TabPoints:
    """The points of the tab: the middles of the intervals in plan and the value of the component."""

    x: np.ndarray
    y: np.ndarray
    v: np.ndarray
    rows: np.ndarray  # int64: the rows of the table they are, ascending
    counts: dict[str, int]  # rows, missing — no value, no_position — no place on a hole path, used

    @property
    def n(self) -> int:
        return int(self.v.shape[0])


def tab_points(positions: Any, values: Any, *, least: int = 1) -> TabPoints:
    """The points of the tab of the rows of a table: `positions` — the middles of the intervals (n×3 or n×2;
    NaN — the hole has no trajectory; `stats.source.midpoints`), `values` — the component (NaN — empty).
    StatsError: `no_values`, `too_few_values` (fewer than `least`), `too_many_samples` (Т7.29-2)."""
    pos = np.asarray(positions, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    if pos.ndim != 2 or pos.shape[1] not in (2, 3) or v.shape != (pos.shape[0],):
        raise ValueError("positions are n×3 (or n×2), values hold one number per row")
    has = np.isfinite(v)
    placed = np.isfinite(pos[:, 0]) & np.isfinite(pos[:, 1])
    rows = np.flatnonzero(has & placed)
    counts = {
        "rows": int(v.shape[0]),
        "missing": int((~has).sum()),
        "no_position": int((has & ~placed).sum()),
        "used": int(rows.size),
    }
    told = f"Строк — {_n(counts['rows'])}, без значения — {_n(counts['missing'])}, без положения на скважине — "
    told += f"{_n(counts['no_position'])}."
    if rows.size == 0:
        raise StatsError(
            "no_values",
            "Нет интервалов со значением и положением на скважине.",
            f"{told} Проверьте компонент и траектории скважин.",
        )
    if rows.size < least:
        raise StatsError(
            "too_few_values",
            f"Недостаточно данных: интервалов со значением — {_n(int(rows.size))}, нужно не меньше {least}.",
            f"{told} Проверьте компонент и траектории скважин.",
        )
    if rows.size > MAX_SAMPLES:
        raise StatsError(
            "too_many_samples",
            f"Слишком много интервалов: {_n(int(rows.size))}, вкладка «Статистика» считает не больше "
            f"{_n(MAX_SAMPLES)}.",
            f"Скомпозитируйте пробы ({_COMPOSITE}) и выберите таблицу композитов. Веса декластеризации для "
            "всей таблицы считает «Анализ → Обработка проб → Декластеризация».",
            param="table",
        )
    return TabPoints(
        np.ascontiguousarray(pos[rows, 0]), np.ascontiguousarray(pos[rows, 1]), np.ascontiguousarray(v[rows]),
        rows.astype(np.int64), counts,
    )  # fmt: skip


# --- declustering -----------------------------------------------------------------------------------------------


def _bounds(raw: Any) -> tuple[float, float, float, float]:
    try:
        b = [float(x) for x in raw]
    except (TypeError, ValueError):
        b = []
    if len(b) != 4 or not all(math.isfinite(x) for x in b) or not (b[1] > b[0] and b[3] > b[2]):
        raise _bad(
            "bounds",
            "Границы полигонов: нужны четыре числа — X от и до, Y от и до, «от» меньше «до».",
            "Исправьте границы или уберите их — полигоны обрежутся по габариту проб.",
        )
    return b[0], b[1], b[2], b[3]


def _weigh(p: TabPoints, raw: Mapping[str, Any] | None, threads: int, tick: Tick | None) -> tuple[np.ndarray, dict]:
    """The weights of declustering of the points (they sum to their number) and the answer of `tab_declus`
    without the counts."""
    from . import declus3d as d3
    from .declustering import polygonal_declus
    from .weighted import pair_sums

    params = dict(raw or {})
    method = params.get("method") or "cell"
    if method not in METHODS:
        hint = "Допустимо: cell — ячейки в плане, polygon — полигоны в плане."
        if method == "cell3d":
            hint += " Ячейки в 3D считает «Анализ → Обработка проб → Декластеризация»."
        raise _bad("method", f"Метод декластеризации «{method}» на вкладке «Статистика» не считается.", hint)
    if p.n < 2:
        raise StatsError(
            "too_few_values",
            f"Недостаточно данных: интервалов со значением — {p.n}, для декластеризации нужно не меньше 2.",
            "Проверьте компонент и траектории скважин.",
        )
    span = max(float(np.ptp(p.x)), float(np.ptp(p.y)))
    if not span > 0.0:
        raise StatsError(
            "no_spread",
            "Все интервалы в одной точке плана: декластеризация невозможна.",
            "Нужны пробы хотя бы двух скважин, разнесённых в плане.",
        )
    naive = float(p.v.mean())
    if method == "cell":
        given: dict[str, Any] = {k: params.get(k) for k in ("cmin", "cmax", "ncell", "noff", "minmax")}
        if given["cmin"] in (None, ""):
            given["cmin"] = max(0.01, CELL_SHARES[0] * span)
        if given["cmax"] in (None, ""):
            try:
                given["cmax"] = max(CELL_SHARES[1] * span, float(given["cmin"]))
            except (TypeError, ValueError):
                given["cmax"] = CELL_SHARES[1] * span  # the wrong «от» is told by the check below
        dp = d3.DeclusParams.of({"method": "cell", **given})
        d = d3.declus_weights(p.x, p.y, None, p.v, dp, threads=threads, tick=tick)
        rep = d.report
        out: dict[str, Any] = {
            "method": "cell",
            "count": p.n,
            "naive_mean": rep["naive_mean"],
            "declustered_mean": rep["mean"],
            "weight_min": rep["min_weight"],
            "weight_max": rep["max_weight"],
            "zero_weights": rep["n_zero"],
            "uniform": rep["uniform"],
            "best_cell_size": rep["best_size"],
            "cell_sizes": [0.0, *rep["sizes"]],  # as BF: size 0 holds the plain mean
            "means": [rep["naive_mean"], *rep["means"]],
            "cmin": dp.cmin, "cmax": dp.cmax, "ncell": dp.ncell, "noff": dp.noff, "minmax": dp.minmax,
            "notes": list(rep["notes"]),
        }  # fmt: skip
        return d.weights, out
    dp = d3.DeclusParams.of({"method": "polygon", "nx": params.get("nx"), "ny": params.get("ny")})
    given_bounds = params.get("bounds")
    bounds = _bounds(given_bounds) if given_bounds is not None else None
    side = d3.polygon_grid(int(np.unique(p.x + 1j * p.y).size))
    nx, ny = dp.nx or side, dp.ny or side
    w = polygonal_declus(p.x, p.y, bounds=bounds, nx=nx, ny=ny, workers=max(1, int(threads)), tick=tick)
    total, metal = pair_sums(w, p.v)
    zero = int((w == 0.0).sum())
    notes = [f"Проб с нулевым весом: {zero} — их полигоны меньше ячейки сетки {nx} × {ny}."] if zero else []
    box = bounds or (float(p.x.min()), float(p.x.max()), float(p.y.min()), float(p.y.max()))
    return w, {
        "method": "polygon",
        "count": p.n,
        "naive_mean": naive,
        "declustered_mean": float(metal / total),
        "weight_min": float(w.min()),
        "weight_max": float(w.max()),
        "zero_weights": zero,
        "uniform": False,
        "best_cell_size": None,
        "cell_sizes": None,
        "means": None,
        "nx": nx, "ny": ny, "bounds": [float(b) for b in box],
        "notes": notes,
    }  # fmt: skip


def tab_declus(
    points: TabPoints,
    params: Mapping[str, Any] | None = None,
    *,
    threads: int = 1,
    tick: Tick | None = None,
    weights: bool = False,
) -> dict[str, Any]:
    """Declustering of the tab (see the module docstring): method, count, naive_mean, declustered_mean,
    weight_min, weight_max, zero_weights, uniform (no cell size moved the mean: the weights are 1),
    best_cell_size, cell_sizes and means (the curve; size 0 — the plain mean; None for polygons), the
    parameters used (cmin, cmax, ncell, noff, minmax | nx, ny, bounds), notes, counts; with `weights` — rows
    (of the table) and the weight of each (they sum to their number). Nothing is written."""
    w, out = _weigh(points, params, threads, tick)
    out["counts"] = dict(points.counts)
    if weights:
        out["rows"] = [int(r) for r in points.rows]
        out["weights"] = [float(x) for x in w]
    return out


def _declus(points: TabPoints, declus: Mapping[str, Any] | None, threads: int) -> np.ndarray | None:
    return None if declus is None else _weigh(points, declus, threads, None)[0]


# --- normal scores ----------------------------------------------------------------------------------------------


def tab_nscore(
    points: TabPoints, *, declus: Mapping[str, Any] | None = None, zmin: Any = None, zmax: Any = None, threads: int = 1
) -> dict[str, Any]:
    """The normal score transform of the tab: count, the table (values ascending, scores; `table` — its rows:
    fewer than `count` when declustering gave weights of 0), zmin, zmax, rows (of the table of samples) and
    sample_scores — the score of every point, weighted, counts."""
    w = _declus(points, declus, threads)
    ns, tr = nscore(points.v, wts=w, zmin=zmin, zmax=zmax)
    return {
        "count": points.n,
        **tr.as_dict(),
        "table": int(tr.values.size),
        "rows": [int(r) for r in points.rows],
        "sample_scores": [float(s) for s in ns],
        "weighted": w is not None,
        "counts": dict(points.counts),
    }


def tab_backtr(
    points: TabPoints,
    scores: Any,
    *,
    declus: Mapping[str, Any] | None = None,
    zmin: Any = None,
    zmax: Any = None,
    ltail: Any = "linear",
    utail: Any = "linear",
    ltpar: Any = None,
    utpar: Any = None,
    threads: int = 1,
) -> dict[str, Any]:
    """Normal scores → values of the component by the table of the data (the same `declus`, `zmin`, `zmax` as
    `tab_nscore`): {"values": […]}; None — the tail has no number there (the hyperbolic one far out)."""
    y = as_values(scores, "Нормальные оценки")
    if not 1 <= y.size <= MAX_SCORES:
        raise _bad(
            "scores",
            f"Нормальных оценок для обратного преобразования: {_n(int(y.size))}, нужно от 1 до {_n(MAX_SCORES)}.",
            "Введите хотя бы одно число.",
        )
    check_tails(ltail, utail, ltpar, utpar)
    _, tr = nscore(points.v, wts=_declus(points, declus, threads), zmin=zmin, zmax=zmax)
    back = tr.inverse(y, ltail, utail, ltpar, utpar)
    return {"values": [float(v) if math.isfinite(v) else None for v in back]}


# --- bootstrap ----------------------------------------------------------------------------------------------------


def tab_bootstrap(
    points: TabPoints,
    *,
    n_boot: Any = 1000,
    seed: Any = None,
    confidence: Any = 0.95,
    declus: Mapping[str, Any] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """The bootstrap of the tab — `bootstrap.bootstrap_stats` of the values of the points (with `declus` the
    points are drawn with the probability of their weights) and the counts."""
    from .bootstrap import bootstrap_stats

    if points.n < 2:
        raise StatsError(
            "too_few_values",
            f"Недостаточно данных: интервалов со значением — {points.n}, для бутстрапа нужно не меньше 2.",
            "Проверьте компонент и траектории скважин.",
        )
    out = bootstrap_stats(points.v, n_boot, _declus(points, declus, threads), seed, confidence, tick=tick)
    out["counts"] = dict(points.counts)
    return out
