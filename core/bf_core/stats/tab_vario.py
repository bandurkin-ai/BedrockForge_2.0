"""The variograms of the tab «Статистика» (ТЗ 7.29; ADR 0022 §11, решения 2 и 21): the experimental variogram in
plan and the variogram map (ΔX, ΔY) of the points of the tab, the model of BF and its curve without data — the
port of BF `api/app/stats/variogram.py` and of the routes `variogram`, `variogram-map`, `variogram-model` of
`routers/stats.py` on the kernels of 8.6 (`geostat.variogram`: one definition of a pair in the programme; the
code of BF is the reference of the tests, `tests/ref_bf_variogram.py`). Nothing is stored; the answers are
JSON-ready: an empty lag or cell is None, never 0.

    tab_variogram(points, *, lag=None, nlag=10, lag_tol=None, directions="omni", min_pairs=30, standardize=False,
                  model=None, threads=1, tick=None) -> dict
        `experimental.experimental` at z = 0: "omni" — Direction(0, 0, 90°, 90°), "four" — 0°, 45°, 90°, 135° with
        the tolerance 22,5° and no band (BF `FOUR`); the tolerance of the plunge 90°. → {count, sigma2, lag, nlag,
        lag_tol, min_pairs, standardize, lags (k·L), directions [{az, az_tol, omni, h, gamma, n, few,
        model_gamma?}], model {nugget, sill, structures} | None, counts}
    tab_variogram_model(nugget=0, structures=(), *, h_max=None, n=101, directions="omni") -> dict
        the curve of the model without data: n points from 0 to h_max along every direction → {nugget, sill,
        structures, h_max, n, directions [{az, h, gamma, cov}]}
    tab_map(points, *, cell=None, n=10, min_pairs=5, standardize=False, threads=1, tick=None) -> dict
        `varmap.varmap` with `frame` None: u = ΔX (east), v = ΔY (north) → {count, sigma2, cell, nu, nv,
        min_pairs, standardize, u, v, gamma [j][i], n [j][i], counts}: the row j by ΔY from the south (v[j]), the
        column i by ΔX from the west (u[i]) — BF `gam[iy, ix]`; N < min_pairs — None
    default_lag(points) -> float      BF `defaultLag`: a round step of the larger extent in plan / 20; 50 without
    nice_step(x) -> float             1, 2, 5 × 10^k nearest to x by the rule of BF `niceStep`

The model of BF (`it, cc, hmaj, hmin, azi`, up to 4 structures; `model.model_2d`): `{nugget, structures}` (the
name `nug` of BF is read too). In the variogram it is drawn at the lag centres k·L of every direction, in the
units of the answer as BF (with `standardize` the model is entered in shares of σ²). σ² — the variance of the
values (ddof 0, BF `v.var()`); `standardize` divides γ by it.

Ranges: lag (0; 100 000] m, nlag 1–200 (BF), lag_tol 0 … L (the kernel; BF allowed more), min_pairs 1–1000, the
cell of the map (0; 100 000] m, n 1–100 cells each way (the kernel, ≤ 201 × 201), its min_pairs 1–1000, the curve
of the model 2–2000 points, h_max (0; 100 000] m (by default 1,5 × the largest range). A number may be a text
«0,5» / «0.5»; absent, None or "" — the default.

The points are `tab.TabPoints` (≤ 20 000, `tab.tab_points`; more — `too_many_samples` here too). Errors —
`StatsError {code, message, hint}` with `param` (the key of the body; the model — `model.<key>` in the variogram):
`bad_param`, `too_many_samples`, `too_few_values` (fewer than 2 points), `no_spread` (all values equal, ADR 0022
§16). numpy is loaded with the module, the kernels with the first call (the router does not import this).

Time (16.10; Linux, 2 threads, 20 000 composites of 1000 inclined holes in 2 × 2 km, the lag by default 100 m ×
10 — the pairs up to half the extent, 1,0·10⁸ of them): omnidirectional 1,7 s, four directions 1,8 s (0,9 s
with 4 threads; L = 25 m — 0,2 s); the map 21 × 21 — 0,5 s, 201 × 201 — 1,5 s; the process 0,23 GB.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from .tab import MAX_SAMPLES, TabPoints
from .weighted import StatsError, Tick

LEN_MAX = 100_000.0  # m: the longest lag, cell or range accepted (`geostat.variogram.spec.LEN_MAX`)
NLAG_MAX = 200  # BF `VariogramRequest.nlag`
MAP_N_MAX = 100  # `varmap.N_MAX`: 201 × 201 cells
CURVE_MAX = 2000  # points of the curve of the model (BF `ModelCurveRequest.lags`)
DIRECTION_SETS: dict[str, tuple[tuple[float, float], ...]] = {
    "omni": ((0.0, 90.0),),
    "four": ((0.0, 22.5), (45.0, 22.5), (90.0, 22.5), (135.0, 22.5)),
}  # (azimuth, its tolerance), as BF `OMNI` and `FOUR`
DEFAULT_LAG = 50.0  # BF `defaultLag(null)`
_COMPOSITE = "«Анализ → Обработка проб → Композиты»"


# --- the parameters -----------------------------------------------------------------------------------------------


def bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _absent(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def to_float(raw: Any) -> float:
    """A number or a text «0,5» / «0.5»; NaN otherwise (bool is not a number)."""
    if isinstance(raw, bool):
        return math.nan
    if isinstance(raw, int | float | np.integer | np.floating):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip().replace(",", "."))
        except ValueError:
            return math.nan
    return math.nan


def number(
    raw: Any, param: str, label: str, default: float, *, lo: float = 0.0, hi: float = LEN_MAX, lo_open: bool = True,
    unit: str = " м",
) -> float:  # fmt: skip
    """A finite number in [lo; hi] ((lo; hi] with `lo_open`); absent — `default`."""
    if _absent(raw):
        return default
    x = to_float(raw)
    span = f"{'больше' if lo_open else 'от'} {_fmt(lo)} до {_fmt(hi)}{unit}"
    if not math.isfinite(x):
        raise bad(param, f"{label}: нужно число.", f"Введите число {span}, например 0,5.")
    if x < lo or (lo_open and x == lo) or x > hi:
        raise bad(param, f"{label}: значение {_fmt(x)} вне допустимого диапазона.", f"Задайте значение {span}.")
    return x + 0.0


def whole(raw: Any, param: str, label: str, default: int, lo: int, hi: int) -> int:
    """A whole number lo … hi (15, 15.0 or "15"); absent — `default`."""
    if _absent(raw):
        return default
    x = to_float(raw)
    if not (math.isfinite(x) and x == int(x) and lo <= x <= hi):
        raise bad(param, f"{label}: нужно целое число от {lo} до {hi}.", f"Задайте целое число от {lo} до {hi}.")
    return int(x)


def flag(raw: Any, param: str, label: str) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise bad(param, f"«{label}» — да или нет.", "Передайте true или false.")
    return raw


def _directions(raw: Any, param: str = "directions") -> tuple[tuple[float, float], ...]:
    key = "omni" if _absent(raw) else raw
    if not isinstance(key, str) or key not in DIRECTION_SETS:
        raise bad(param, "Направления вариограммы: omni (всенаправленная) или four (0°, 45°, 90°, 135°).",
                  "Выберите всенаправленную вариограмму или четыре направления с допуском 22,5°.")  # fmt: skip
    return DIRECTION_SETS[key]


def nice_step(x: float) -> float:
    """1, 2, 5 or 10 × 10^k by the rule of BF `niceStep`: the share of the power of ten below 1,5 → 1, below 3,5
    → 2, below 7,5 → 5, else 10. Not positive — 1."""
    if not (math.isfinite(x) and x > 0.0):
        return 1.0
    e = math.floor(math.log10(x))
    f = x / 10.0**e if e >= 0 else x * 10.0 ** (-e)
    m = 1.0 if f < 1.5 else 2.0 if f < 3.5 else 5.0 if f < 7.5 else 10.0
    return m * 10.0**e if e >= 0 else m / 10.0 ** (-e)


def default_lag(points: TabPoints) -> float:
    """BF `defaultLag(planSpan)`: `niceStep(span / 2, 10)` — about 20 lags across the larger extent in plan; 50
    when the points have no extent."""
    if points.n < 2:
        return DEFAULT_LAG
    span = max(float(np.ptp(points.x)), float(np.ptp(points.y)))
    return nice_step(span / 20.0) if span > 0.0 else DEFAULT_LAG


# --- the points ---------------------------------------------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def check_limit(points: TabPoints) -> None:
    """More than 20 000 points are not computed (Т7.29-2): the refusal of `tab.tab_points`."""
    if points.n > MAX_SAMPLES:
        raise StatsError(
            "too_many_samples",
            f"Слишком много интервалов: {_n(points.n)}, вкладка «Статистика» считает не больше {_n(MAX_SAMPLES)}.",
            f"Скомпозитируйте пробы ({_COMPOSITE}) и выберите таблицу композитов. Вариограммы без предела по "
            "числу проб считает окно «Вариография» (вкладка «Геостатистика»).",
            param="table",
        )


def _pairs_of(points: TabPoints, what: str) -> float:
    """σ² of the values after the checks of a variogram: the limit, two points, not all values equal."""
    check_limit(points)
    if points.n < 2:
        raise StatsError(
            "too_few_values",
            f"Недостаточно данных: интервалов со значением — {points.n}, для {what} нужно не меньше 2.",
            "Проверьте компонент и траектории скважин.",
        )
    sigma2 = float(np.var(points.v))
    if not sigma2 > 0.0:
        raise StatsError(
            "no_spread",
            f"Все значения компонента одинаковы: {what} тождественно равна нулю.",
            "Выберите другой компонент или другие скважины.",
        )
    return sigma2


# --- the model ----------------------------------------------------------------------------------------------------


def _model(nugget: Any, structures: Any, prefix: str = "") -> Any:
    """`model.model_2d` with the parameters of the tab: `nug` of the kernel is `nugget` here."""
    from bf_core.geostat.variogram.model import model_2d

    try:
        return model_2d(0.0 if _absent(nugget) else nugget, structures)
    except StatsError as e:
        name = "nugget" if e.param == "nug" else (e.param or "structures")
        raise StatsError(e.code, e.message, e.hint, param=prefix + name) from e


def _model_of(raw: Any) -> Any:
    """The model of `tab_variogram`: {nugget (or `nug` of BF), structures} or None."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise bad("model", "Модель вариограммы задана неверно.", "Ожидается объект с полями nugget и structures.")
    nugget = raw.get("nugget", raw.get("nug"))
    return _model(nugget, raw.get("structures"), "model.")


def _model_json(m: Any) -> dict[str, Any]:
    """The model in the form of BF: {nugget, sill, structures [{it, cc, hmaj, hmin, azi}]}."""
    return {
        "nugget": m.c0,
        "sill": m.sill,
        "structures": [{"it": s.type, "cc": s.c, "hmaj": s.a1, "hmin": s.a2, "azi": s.ang1} for s in m.structures],
    }


def _nulls(a: Any) -> list[Any]:
    return [None if not math.isfinite(x) else float(x) for x in np.asarray(a, dtype=np.float64).tolist()]


# --- the analyses -------------------------------------------------------------------------------------------------


def tab_variogram(
    points: TabPoints,
    *,
    lag: Any = None,
    nlag: Any = None,
    lag_tol: Any = None,
    directions: Any = None,
    min_pairs: Any = None,
    standardize: Any = None,
    model: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """The experimental variogram of the tab (see the module): BF `variogram` on the pair kernel of 8.6."""
    from bf_core.geostat.variogram.experimental import experimental
    from bf_core.geostat.variogram.pairs import Direction

    sigma2 = _pairs_of(points, "вариограммы")
    L = number(lag, "lag", "Лаг", default_lag(points))
    nl = whole(nlag, "nlag", "Число лагов", 10, 1, NLAG_MAX)
    tol = number(lag_tol, "lag_tol", "Допуск лага", L / 2.0, hi=L, lo_open=False)
    dset = _directions(directions)
    mp = whole(min_pairs, "min_pairs", "Наименьшее число пар", 30, 1, 1000)
    std = flag(standardize, "standardize", "Нормировать на дисперсию")
    m = _model_of(model)
    dirs = [Direction(az, 0.0, az_tol, 90.0) for az, az_tol in dset]
    e = experimental(points.x, points.y, None, points.v, dirs, L, nl, tol, threads=threads, tick=tick)
    lags = [L * (i + 1) for i in range(nl)]
    few = e.few(mp)
    out_dirs = []
    for k, (az, az_tol) in enumerate(dset):
        g = e.gamma[k] / sigma2 if std else e.gamma[k]
        d: dict[str, Any] = {"az": az, "az_tol": az_tol, "omni": az_tol >= 90.0, "h": _nulls(e.h[k]),
                             "gamma": _nulls(g), "n": e.n[k].tolist(), "few": few[k].tolist()}  # fmt: skip
        if m is not None:
            d["model_gamma"] = _nulls(m.gamma_along(np.asarray(lags), az))
        out_dirs.append(d)
    return {
        "count": points.n,
        "sigma2": sigma2,
        "lag": L,
        "nlag": nl,
        "lag_tol": tol,
        "min_pairs": mp,
        "standardize": std,
        "lags": lags,
        "directions": out_dirs,
        "model": None if m is None else _model_json(m),
        "counts": dict(points.counts),
    }


def tab_variogram_model(
    nugget: Any = None, structures: Any = None, *, h_max: Any = None, n: Any = None, directions: Any = None
) -> dict[str, Any]:
    """The curve of the model of BF without data (BF `variogram-model`): γ and C at n points from 0 to h_max."""
    m = _model(nugget, structures)
    ranges = [r for s in m.structures for r in (s.a1, s.a2)]
    if _absent(h_max) and not ranges:
        raise bad("h_max", "Не задана длина кривой модели: у модели нет структур.",
                  "Задайте наибольшее расстояние кривой в метрах или добавьте структуру.")  # fmt: skip
    top = number(h_max, "h_max", "Наибольшее расстояние кривой", 1.5 * max(ranges or [0.0]))
    count = whole(n, "n", "Число точек кривой", 101, 2, CURVE_MAX)
    h = np.linspace(0.0, top, count)
    out_dirs = []
    for az, _ in _directions(directions):
        gamma = m.gamma_along(h, az)
        out_dirs.append({"az": az, "h": h.tolist(), "gamma": _nulls(gamma), "cov": _nulls(m.sill - gamma)})
    return {**_model_json(m), "h_max": top, "n": count, "directions": out_dirs}


def tab_map(
    points: TabPoints,
    *,
    cell: Any = None,
    n: Any = None,
    min_pairs: Any = None,
    standardize: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """The variogram map of the tab (see the module): BF `variogram-map` on the map kernel of 8.6."""
    from bf_core.geostat.variogram.varmap import varmap

    sigma2 = _pairs_of(points, "карты вариограммы")
    c = number(cell, "cell", "Ячейка карты", default_lag(points))
    nn = whole(n, "n", "Число ячеек карты от центра", 10, 1, MAP_N_MAX)
    mp = whole(min_pairs, "min_pairs", "Наименьшее число пар в ячейке", 5, 1, 1000)
    std = flag(standardize, "standardize", "Нормировать на дисперсию")
    try:
        vm = varmap(points.x, points.y, None, points.v, c, nu=nn, nv=nn, min_pairs=mp, threads=threads, tick=tick)
    except StatsError as e:  # the names of the job (`map.cell`) are the keys of the body here
        param = (e.param or "").removeprefix("map.") or None
        raise StatsError(e.code, e.message, e.hint, param=param) from e
    g = vm.gamma / sigma2 if std else vm.gamma
    return {
        "count": points.n,
        "sigma2": sigma2,
        "cell": c,
        "nu": nn,
        "nv": nn,
        "min_pairs": mp,
        "standardize": std,
        "u": vm.u.tolist(),
        "v": vm.v.tolist(),
        "gamma": [_nulls(row) for row in g],
        "n": vm.n.tolist(),
        "counts": dict(points.counts),
    }
