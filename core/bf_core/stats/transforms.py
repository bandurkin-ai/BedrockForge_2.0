"""Normal score transform and back-transform — GSLIB `nscore` / `backtr` (ТЗ 7.29, 10.2; ADR 0019 §4, §7) — the
port of BF `api/app/stats/transforms.py` (NumPy / SciPy, from GeostatsPy), behaviour and tests kept.

    nscore(x, wts=None, zmin=None, zmax=None, reference=None, reference_wts=None) -> (scores, transform)
        the normal score of every value and the table: the values ascending and their scores
    backtr(y, transform, ltail="linear", utail="linear", ltpar=None, utpar=None) -> values
    NormalScoreTransform(values, scores, zmin, zmax): .forward(x), .inverse(y, …), .as_dict()
    check_tails(ltail, utail, ltpar, utpar)        the tails of `backtr` checked (StatsError `bad_param`)
    as_values(a, label), as_weights(wts, n)        the inputs checked — for `bootstrap` and the tab too

The table. The values are sorted (equal ones stay in the order of the rows); the score of a row is
Φ⁻¹((C − w / 2) / W) — the middle of its step of the cumulative weight, the rule of the percentiles and of Q-Q
(`stats/weighted.py`); without weights (i − ½) / n. The scores rise strictly, so equal values get distinct
scores by the order of the rows (GSLIB breaks ties with a random epsilon). `reference` — another distribution to
build the table of (GSLIB `ismooth`); the scores of `x` are then read from it.

Reading the table (`forward`, `inverse`): linear between two rows, the end rows outside; at a run of equal
values — its last row. A score of the table comes back as its value exactly: `backtr(nscore(x)) = x`.

Tails of `backtr` — below the first score and above the last one (GSLIB): None — the end value of the table;
"linear" and "power" — between the end value and the trimming limit (`zmin`, `zmax`; by default the least
and the greatest value of the data, so the tails are flat) by the share of the probability left,
(Φ(y) / Φ(y₁))^(1 / ltpar) below and ((Φ(y) − Φ(yₙ)) / (1 − Φ(yₙ)))^(1 / utpar) above (linear — the power 1);
"hyperbolic" (above only, the greatest value > 0) — (zₙ^ω·(1 − Φ(yₙ)) / (1 − Φ(y)))^(1 / ω), ω = `utpar` ≥ 1,
without a limit. 1 − Φ is taken as the survival function: the far upper tail keeps its digits.

Differences from BF, none on its tests: a weight of 0 (a sample whose polygon of declustering is smaller than
a node of the grid) is left out of the table — BF gave it a score of ±∞ at an end of the table — and its own
score is read from the table; the tails start strictly beyond the end scores (an end score is its value
exactly, whatever the limits); the limits may not cut the data (`bad_param`); the hyperbolic tail of data
with a greatest value ≤ 0 is refused. Differences of BF from GeostatsPy (kept): ties by the order of the
rows; the upper hyperbolic tail per value; named tails instead of the integer codes of GSLIB; GeostatsPy gives
the least sample the score read from the next two rows of its table, here it is the first score.

Inputs must be finite (the tab leaves out the rows without a value first, `stats/tab.py`); NaN given to
`forward` stays NaN. Errors — `StatsError` {code, message, hint}: `bad_input`, `no_values`, `bad_param`
with `param` (a ValueError, as in BF).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .weighted import StatsError

LOWER_TAILS = (None, "linear", "power")
UPPER_TAILS = (None, "linear", "power", "hyperbolic")
_P_MIN, _P_MAX = float(np.finfo(np.float64).tiny), 1.0 - float(np.finfo(np.float64).epsneg)


def _bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


def as_values(a: Any, label: str = "Значения") -> np.ndarray:
    """A 1D float64 array of finite numbers or StatsError `bad_input` (BF `as_1d` and its checks)."""
    try:
        out = np.asarray(a, dtype=np.float64)
    except (TypeError, ValueError):
        out = np.empty((0, 0))
    if out.ndim != 1:
        raise StatsError("bad_input", f"{label}: нужна одна колонка чисел.", "Передайте одномерный массив значений.")
    if not bool(np.isfinite(out).all()):
        raise StatsError(
            "bad_input", f"{label}: есть пропуски или бесконечные числа.", "Исключите строки без значения."
        )
    return out


def as_weights(wts: Any, n: int) -> np.ndarray | None:
    """Weights of the values (BF `check_weights`, not rescaled): one per value, finite, not negative, not all
    zero; None — equal weights."""
    if wts is None:
        return None
    try:
        w = np.asarray(wts, dtype=np.float64)
    except (TypeError, ValueError):
        w = np.empty((0, 0))
    if w.ndim != 1 or w.shape[0] != n:
        raise StatsError("bad_input", "Веса и значения разной длины.", "Передайте вес для каждого значения.")
    if not bool(np.isfinite(w).all()) or bool((w < 0.0).any()):
        raise StatsError("bad_input", "Веса должны быть числами не меньше нуля.", "Проверьте веса декластеризации.")
    if not float(w.sum()) > 0.0:
        raise StatsError("no_values", "Все веса равны нулю.", "Проверьте веса декластеризации.")
    return w


def _limit(raw: Any, default: float, param: str) -> float:
    """A trimming limit: the default, or a number that does not cut the data."""
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    low = param == "zmin"
    if not math.isfinite(value) or (value > default if low else value < default):
        side, word = ("нижний", "не больше наименьшего") if low else ("верхний", "не меньше наибольшего")
        raise _bad(
            param,
            f"Предел хвоста ({side}) должен быть числом {word} значения данных ({default:g}).",
            "Оставьте поле пустым — пределом станет крайнее значение данных.",
        )
    return value


def _read(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """fp at x by a table (xp and fp ascending, equal rows allowed): linear between two rows, the end rows
    outside, the last row of a run of equal xp; NaN stays NaN."""
    n = int(xp.shape[0])
    j = np.searchsorted(xp, x, side="right")  # rows with xp ≤ x
    lo, hi = np.clip(j - 1, 0, n - 1), np.clip(j, 0, n - 1)
    span = xp[hi] - xp[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.where(span > 0.0, (x - xp[lo]) / span, 0.0)
    out = np.minimum(fp[lo] + np.clip(t, 0.0, 1.0) * (fp[hi] - fp[lo]), fp[hi])
    return np.where(np.isnan(x), np.nan, out)


def _powint(ylow: float, yhigh: float, share: np.ndarray, power: float) -> np.ndarray:
    """GSLIB `powint`: from ylow (share 0) to yhigh (share 1) by share^power; the ends are exact."""
    f = np.clip(share, 0.0, 1.0) ** power
    return np.clip(ylow * (1.0 - f) + yhigh * f, min(ylow, yhigh), max(ylow, yhigh))


def check_tails(ltail: Any = "linear", utail: Any = "linear", ltpar: Any = None, utpar: Any = None) -> None:
    """The tails of the back-transform as GSLIB takes them, or StatsError `bad_param` with `param`."""
    if ltail not in LOWER_TAILS:
        raise _bad("ltail", f"Нижний хвост «{ltail}» неизвестен.", "Допустимо: linear, power или без хвоста.")
    if utail not in UPPER_TAILS:
        raise _bad(
            "utail", f"Верхний хвост «{utail}» неизвестен.", "Допустимо: linear, power, hyperbolic или без хвоста."
        )
    if ltail == "power" and not _positive(ltpar):
        raise _bad("ltpar", "Для степенного нижнего хвоста нужен показатель больше нуля.", "Задайте, например, 2.")
    if utail == "power" and not _positive(utpar):
        raise _bad("utpar", "Для степенного верхнего хвоста нужен показатель больше нуля.", "Задайте, например, 2.")
    if utail == "hyperbolic" and not _positive(utpar, 1.0):
        raise _bad(
            "utpar", "Для гиперболического верхнего хвоста нужен показатель не меньше 1.", "Задайте, например, 1,5."
        )


def _positive(value: Any, least: float | None = None) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x) and (x > 0.0 if least is None else x >= least)


@dataclass(frozen=True, eq=False)
class NormalScoreTransform:
    """The fitted table: the values ascending and their normal scores (rising strictly)."""

    values: np.ndarray
    scores: np.ndarray
    zmin: float  # the trimming limits: where the tails of the back-transform end
    zmax: float

    def forward(self, x: Any) -> np.ndarray:
        """Values → normal scores by the table; outside its range — the end scores; NaN stays NaN."""
        return _read(np.asarray(x, dtype=np.float64), self.values, self.scores)

    def inverse(
        self, y: Any, ltail: Any = "linear", utail: Any = "linear", ltpar: Any = None, utpar: Any = None
    ) -> np.ndarray:
        """Normal scores → values with the tails (see the module docstring)."""
        from scipy.stats import norm

        scores = as_values(y, "Нормальные оценки")
        check_tails(ltail, utail, ltpar, utpar)
        vr, vrg = self.values, self.scores
        first, last = float(vr[0]), float(vr[-1])
        if utail == "hyperbolic" and not last > 0.0:
            raise _bad(
                "utail",
                "Гиперболический хвост не определён: наибольшее значение данных не больше нуля.",
                "Выберите линейный или степенной верхний хвост.",
            )
        out = _read(scores, vrg, vr)
        lo = scores < vrg[0]
        if ltail is not None and bool(lo.any()):
            power = 1.0 if ltail == "linear" else 1.0 / float(ltpar)
            edge = float(norm.cdf(vrg[0]))
            share = norm.cdf(scores[lo]) / edge if edge > 0.0 else np.ones(int(lo.sum()))
            out[lo] = _powint(self.zmin, first, share, power)
        hi = scores > vrg[-1]
        if utail is not None and bool(hi.any()):
            edge = float(norm.sf(vrg[-1]))
            rest = norm.sf(scores[hi])  # 1 − Φ(y) without the loss of digits
            if utail == "hyperbolic":
                with np.errstate(divide="ignore", over="ignore"):
                    tail = (last ** float(utpar) * edge / rest) ** (1.0 / float(utpar))
                out[hi] = np.maximum(tail, last)
            else:
                power = 1.0 if utail == "linear" else 1.0 / float(utpar)
                share = 1.0 - rest / edge if edge > 0.0 else np.zeros(int(hi.sum()))
                out[hi] = _powint(last, self.zmax, share, power)
        return out

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: the table and the limits."""
        return {
            "values": [float(v) for v in self.values],
            "scores": [float(s) for s in self.scores],
            "zmin": self.zmin,
            "zmax": self.zmax,
        }


def nscore(
    x: Any, wts: Any = None, zmin: Any = None, zmax: Any = None, reference: Any = None, reference_wts: Any = None
) -> tuple[np.ndarray, NormalScoreTransform]:
    """The normal scores of `x` and the table (see the module docstring).

    :param wts: weights of `x` for the table (declustering); None — equal
    :param zmin, zmax: the trimming limits kept for the tails of `backtr`; by default — the least and the
        greatest value of the data of the table
    :param reference: another distribution to build the table of; `wts` are then not read, `reference_wts` are
    """
    from scipy.stats import norm

    data = as_values(x, "Значения")
    use_ref = reference is not None
    src = as_values(reference, "Опорное распределение") if use_ref else data
    if src.size == 0:
        raise StatsError("no_values", "Нет значений для нормального преобразования.", "Проверьте поле и отбор.")
    w = as_weights(reference_wts if use_ref else wts, int(src.size))
    order = np.argsort(src, kind="stable")
    if w is None:
        p = (np.arange(src.size, dtype=np.float64) + 0.5) / src.size
    else:
        order = order[w[order] > 0.0]  # a weight of 0 is not in the table
        ws = w[order]
        cw = np.cumsum(ws)
        p = np.clip((cw - 0.5 * ws) / cw[-1], _P_MIN, _P_MAX)  # weights 16 orders apart: never ±∞
    tr = NormalScoreTransform(
        values=src[order],
        scores=np.asarray(norm.ppf(p), dtype=np.float64),
        zmin=_limit(zmin, float(src.min()), "zmin"),
        zmax=_limit(zmax, float(src.max()), "zmax"),
    )
    if use_ref:
        return tr.forward(data), tr
    if order.size == data.size:  # the rows of the table: the scores by rank, so ties stay distinct
        ns = np.empty(data.size)
        ns[order] = tr.scores
    else:
        ns = tr.forward(data)
        ns[order] = tr.scores
    return ns, tr


def backtr(
    y: Any,
    transform: NormalScoreTransform,
    ltail: Any = "linear",
    utail: Any = "linear",
    ltpar: Any = None,
    utpar: Any = None,
) -> np.ndarray:
    """Back-transform normal scores by a fitted table (`NormalScoreTransform.inverse`)."""
    return transform.inverse(y, ltail, utail, ltpar, utpar)
