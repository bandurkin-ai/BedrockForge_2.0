"""Top cut of outlier grades (ТЗ 8.2.2; ADR 0018 §7): thresholds by domain, the columns `<F>_CUT` and
`<F>_CUT_F`, the report «до/после» and the tools for choosing a threshold. The threshold is never applied by
itself: `topcut_analysis` only shows, `topcut` writes what it was told.

    p = TopcutParams.of({"percentile": 99} | {"value": 30} | {"thresholds": "ОК=30; ПР=12,5"})   # CompositeError
    t = topcut(values, lengths, p, codes=domain_codes, names=domain_names, field="AU", tick=…) -> Topcut
        t.cut     float64[n]  min(g, T) of the domain of the row; no value — NaN
        t.flag    float64[n]  1 — cut, 0 — not; no value — NaN
        t.thresholds  {domain: T | None}      None — the domain has no threshold, its rows are copied
        t.report  {"field", "columns", "mode", "percentile", "domains": [row], "total": row, "summary", "notes"}
            row: domain, threshold, n, n_cut, cut_pct, length, mean_before, mean_after, cv_before, cv_after,
                 max_before, max_after, loss_pct = 100·Σ l·(g − g_cut) / Σ l·g
            summary: {"before", "after"} as `length.summary` — count, length, fields {F: {mean, metal}}
    a = topcut_analysis(values, lengths, codes=…, names=…, field="AU", percentiles=None, decile_limit=40,
                        percentile_limit=10, tick=…) -> dict   # JSON-ready, NaN → None; per domain:
        n, length, mean, cv, min, max, metal
        curves     {p, threshold, mean, cv, loss_pct, n_cut} for P = 90,0 … 99,9 % (or `percentiles`)
        deciles    10 rows {from, to, min, max, mean, metal, share_pct}; top — the same for 90–91 … 99–100 %
        top_decile_pct, top_percentile_pct, flag_decile (> decile_limit), flag_percentile (> percentile_limit)
        quantiles  {p, z, value}: the cumulative curve for the log-probability plot, even in the normal score z

Inputs are rows of a table in any order: `values` — the grade, `lengths` — TO − FROM (the weight), `codes` —
int codes of the domain (`pandas.factorize`; −1 — no domain) with their `names`. Rows without a domain are a
group of their own, named "". A row takes part in the percentile and in the statistics when it has a value and
a length > 0; a row with a value and without a length is still cut by the threshold of its domain (counted in
the notes). A missing value stays missing.

T of a domain: `value` — the same for all; `percentile` — the percentile P (90–99,99 %) of the samples of the
domain weighted by length, the rule of BF `weighted_percentiles`; `thresholds` — given by domain, a domain
not listed is not cut. Means, CV and the metal are weighted by length (as the percentile and the loss).
Deciles are taken by cumulative length — with equal lengths that is by the number of samples; a sample at a
boundary is split by length. One sort and running sums answer everything (`stats/weighted.py`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.stats.weighted import Sorted, sort_groups

from .intervals import CompositeError
from .length import check_range
from .strips import Tick

P_MIN, P_MAX, P_DEFAULT = 90.0, 99.99, 99.0  # the percentile of the top cut, % (table of ТЗ 8.2.2)
NO_DOMAIN = ""  # the name of the group of rows without a domain
MODES = ("percentile", "value", "thresholds")
_BLOCK = 1 << 20  # rows between two ticks of `topcut`
_HINT_T = "Задайте порог больше нуля в единицах поля."


def column_names(name: str) -> tuple[str, str]:
    """The columns of the top cut of a field: (`<F>_CUT`, `<F>_CUT_F`)."""
    return f"{name}_CUT", f"{name}_CUT_F"


def _threshold(raw: Any, label: str, param: str = "thresholds") -> float:
    try:
        if isinstance(raw, bool):
            raise TypeError
        value = float(raw.strip().replace(" ", "").replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError, AttributeError):
        value = math.nan
    if not (math.isfinite(value) and value > 0.0):
        raise CompositeError("bad_param", f"{label}: нужно число больше нуля.", _HINT_T, param=param)
    return value


def _by_domain(raw: Any) -> dict[str, float]:
    """«КОД=значение; …», {code: value} or [{domain, value}] → {code: T}."""
    pairs: list[tuple[Any, Any]] = []
    if isinstance(raw, str):
        for part in raw.replace("\n", ";").split(";"):
            if not part.strip():
                continue
            code, sep, value = part.rpartition("=")
            if not sep:
                raise CompositeError(
                    "bad_param",
                    f"Пороги по доменам: «{part.strip()}» — нужно «КОД=значение».",
                    "Запишите пороги через «;», например «ОК=30; ПР=12,5».",
                    param="thresholds",
                )
            pairs.append((code, value))
    elif isinstance(raw, Mapping):
        pairs = list(raw.items())
    elif isinstance(raw, Sequence):
        for item in raw:
            if not isinstance(item, Mapping):
                raise CompositeError(
                    "bad_param", "Пороги по доменам: нужен список «домен — порог».", _HINT_T, param="thresholds"
                )
            pairs.append((item.get("domain", item.get("code", "")), item.get("value", item.get("threshold"))))
    else:
        raise CompositeError(
            "bad_param", "Пороги по доменам: нужен список «КОД=значение».", _HINT_T, param="thresholds"
        )
    out: dict[str, float] = {}
    for code, value in pairs:
        name = "" if code is None else str(code).strip()
        if name in out:
            raise CompositeError(
                "bad_param", f"Пороги по доменам: код «{name}» задан дважды.", "Оставьте один порог на домен.",
                param="thresholds",
            )  # fmt: skip
        out[name] = _threshold(value, f"Порог домена «{name}»" if name else "Порог проб без домена")
    if not out:
        raise CompositeError(
            "bad_param", "Пороги по доменам не заданы.", "Запишите пороги через «;», например «ОК=30; ПР=12,5».",
            param="thresholds",
        )  # fmt: skip
    return out


@dataclass(frozen=True)
class TopcutParams:
    """How T is set — the keys of the command `composite.topcut`: `value`, else `thresholds`, else `percentile`."""

    mode: str = "percentile"
    percentile: float = P_DEFAULT
    value: float | None = None
    thresholds: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> TopcutParams:
        """From the parameters of a request; absent, None or "" — not given. Checked: CompositeError `bad_param`."""

        def given(key: str) -> Any:
            v = raw.get(key)
            return None if v is None or (isinstance(v, str) and not v.strip()) else v

        if given("value") is not None:
            return cls("value", value=_threshold(given("value"), "Порог T", "value"))
        if given("thresholds") is not None:
            return cls("thresholds", thresholds=_by_domain(given("thresholds")))
        p = P_DEFAULT if given("percentile") is None else given("percentile")
        return cls("percentile", percentile=check_range(p, P_MIN, P_MAX, "Перцентиль урезки P", "percentile", " %"))


@dataclass(frozen=True)
class Topcut:
    cut: np.ndarray
    flag: np.ndarray
    thresholds: dict[str, float | None]
    report: dict[str, Any]


# --- groups ------------------------------------------------------------------------------------------------


def _groups(n: int, codes: Any, names: Sequence[str]) -> tuple[np.ndarray | None, list[str]]:
    """Group codes of the rows (None — one group) and the names of the groups; rows without a domain (−1 or a
    code past the names) are the last group, `NO_DOMAIN`, when there are any."""
    if codes is None:
        return None, [NO_DOMAIN]
    c = np.asarray(codes)
    if c.shape != (n,):
        raise ValueError("codes must have the length of values")
    c = c.astype(np.int64)
    labels = [str(x) for x in names]
    out = (c < 0) | (c >= len(labels))
    if out.any():
        c = np.where(out, len(labels), c)
        labels.append(NO_DOMAIN)
    return c, labels


def _f(x: Any) -> float | None:
    """A float for JSON: NaN and ±inf — None."""
    y = float(x)
    return y if math.isfinite(y) else None


def _list(a: Any) -> list[float | None]:
    return [_f(x) for x in np.asarray(a, dtype=np.float64).ravel()]


def _row(name: str | None, s: Sorted, t: float | None) -> dict[str, Any]:
    """The line of the report of one domain: before and after the cut at `t` (None — not cut)."""
    c = s.capped(math.inf if t is None else t)
    n = s.n
    return {
        "domain": name,
        "threshold": t,
        "n": n,
        "n_cut": int(c.n_cut[0]),
        "cut_pct": 100.0 * int(c.n_cut[0]) / n if n else None,
        "length": s.total,
        "mean_before": _f(s.mean),
        "mean_after": _f(c.mean[0]),
        "cv_before": _f(s.cv),
        "cv_after": _f(c.cv[0]),
        "max_before": _f(s.vmax),
        "max_after": _f(c.vmax[0]),
        "loss_pct": _f(c.loss[0]),
    }


def _total(parts: Sequence[Sorted], thr: Sequence[float | None]) -> tuple[dict[str, Any], float, float]:
    """The line «Всего» from the domains (each cut at its own threshold) and the metal Σ l·g before and after."""
    n = n_cut = 0
    length = metal = metal_cut = 0.0
    vmax = vmax_cut = -math.inf
    stats = []  # (W, mean, variance) before and after, by domain
    for s, t in zip(parts, thr, strict=True):
        if not s.n:
            continue
        c = s.capped(math.inf if t is None else t)
        m1 = float(c.mean[0])
        stats.append((s.total, s.mean, s.variance, m1, float(c.var[0])))
        n += s.n
        n_cut += int(c.n_cut[0])
        length += s.total
        metal += s.metal
        metal_cut += m1 * s.total
        vmax, vmax_cut = max(vmax, s.vmax), max(vmax_cut, float(c.vmax[0]))
    row: dict[str, Any] = {"domain": None, "threshold": None, "n": n, "n_cut": n_cut, "length": length}
    row["cut_pct"] = 100.0 * n_cut / n if n else None
    for key, im, iv in (("before", 1, 2), ("after", 3, 4)):
        mean = math.fsum(x[0] * x[im] for x in stats) / length if n else math.nan
        var = math.fsum(x[0] * (x[iv] + (x[im] - mean) ** 2) for x in stats) / length if n else math.nan
        row[f"mean_{key}"] = _f(mean)
        row[f"cv_{key}"] = _f(math.sqrt(var) / mean) if n and mean > 0.0 else None
    row["max_before"], row["max_after"] = (_f(vmax), _f(vmax_cut)) if n else (None, None)
    row["loss_pct"] = _f(100.0 * (metal - metal_cut) / metal) if metal > 0.0 else None
    return row, metal, metal_cut


# --- the cut -----------------------------------------------------------------------------------------------


def topcut(
    values: Any,
    lengths: Any,
    params: TopcutParams,
    *,
    codes: Any = None,
    names: Sequence[str] = (),
    field: str = "",
    tick: Tick | None = None,
) -> Topcut:
    """`<F>_CUT`, `<F>_CUT_F` and the report (see the module docstring). `tick(fraction)` may raise to cancel."""
    v = np.ascontiguousarray(values, dtype=np.float64)
    ln = np.ascontiguousarray(lengths, dtype=np.float64)
    if v.ndim != 1 or ln.shape != v.shape:
        raise ValueError("values and lengths must be 1D of one length")
    n = v.shape[0]
    c, labels = _groups(n, codes, names)
    step = (lambda f: None) if tick is None else tick
    parts = sort_groups(v, ln, c, len(labels), tick=lambda f: step(0.7 * f))
    notes: list[str] = []
    thr: list[float | None]
    if params.mode == "value":
        thr = [params.value] * len(labels)
    elif params.mode == "percentile":
        thr = [_f(s.percentiles(params.percentile)[0]) for s in parts]
    else:
        thr = [params.thresholds.get(name) for name in labels]
        unknown = [k for k in params.thresholds if k not in labels]
        if unknown:
            shown = ", ".join(f"«{k}»" if k else "«без домена»" for k in unknown)
            notes.append(f"Пороги не использованы — таких доменов в таблице нет: {shown}.")
        skipped = [name or "без домена" for name, t, s in zip(labels, thr, parts, strict=True) if t is None and s.n]
        if skipped:
            notes.append(f"Домены без порога не урезаны: {', '.join(skipped)}.")
    row_t = np.array([math.inf if t is None else t for t in thr], dtype=np.float64)
    cut = np.empty(n)
    flag = np.empty(n)
    for lo in range(0, n, _BLOCK):
        hi = min(lo + _BLOCK, n)
        t = np.full(hi - lo, row_t[0]) if c is None else row_t[c[lo:hi]]
        x = v[lo:hi]
        cut[lo:hi] = np.minimum(x, t)  # NaN stays NaN
        flag[lo:hi] = np.where(np.isnan(x), np.nan, (x > t).astype(np.float64))
        step(0.7 + 0.25 * hi / n)
    has = ~np.isnan(v)
    n_stat = sum(s.n for s in parts)
    if int(has.sum()) > n_stat:
        notes.append(
            f"Проб со значением, но без длины: {int(has.sum()) - n_stat} — урезаны по порогу домена, "
            "в перцентиль и отчёт не входят."
        )
    domains = [_row(name, s, t) for name, s, t in zip(labels, parts, thr, strict=True)]
    total, metal, metal_cut = _total(parts, thr)
    label = field or "value"
    summary = {
        "before": {
            "count": n_stat,
            "length": total["length"],
            "fields": {label: {"mean": total["mean_before"], "metal": metal if n_stat else None}},
        },
        "after": {
            "count": n_stat,
            "length": total["length"],
            "fields": {label: {"mean": total["mean_after"], "metal": metal_cut if n_stat else None}},
        },
    }
    report = {
        "field": field,
        "columns": list(column_names(field)),
        "mode": params.mode,
        "percentile": params.percentile if params.mode == "percentile" else None,
        "domains": domains,
        "total": total,
        "summary": summary,
        "notes": notes,
    }
    step(1.0)
    return Topcut(cut, flag, dict(zip(labels, thr, strict=True)), report)


# --- choosing a threshold ----------------------------------------------------------------------------------

CURVE_P = tuple(round(90.0 + 0.1 * k, 1) for k in range(100))  # 90,0 … 99,9 %
_Z = np.linspace(-3.2, 3.2, 129)  # normal scores of the points of the cumulative curve


def _slices(s: Sorted, edges: np.ndarray, scale: float) -> list[dict[str, Any]]:
    """The slices of cumulative length between `edges` (shares 0–1): grades, metal and its share of the whole."""
    metal = s.metal_at(edges)
    lo, hi = s.value_at(edges[:-1]), s.value_at(edges[1:], upper=True)
    out = []
    for k in range(len(edges) - 1):
        part = float(metal[k + 1] - metal[k])
        weight = float(edges[k + 1] - edges[k]) * s.total
        out.append({
            "from": round(float(edges[k]) * scale, 6),
            "to": round(float(edges[k + 1]) * scale, 6),
            "min": _f(lo[k]),
            "max": _f(hi[k]),
            "mean": _f(part / weight),
            "metal": _f(part),
            "share_pct": _f(100.0 * part / s.metal) if s.metal > 0.0 else None,
        })  # fmt: skip
    return out


def _analysis(name: str, s: Sorted, ps: np.ndarray, decile_limit: float, percentile_limit: float) -> dict[str, Any]:
    out: dict[str, Any] = {"domain": name, "n": s.n, "length": s.total}
    if not s.n:
        return out
    thr = s.percentiles(ps)
    c = s.capped(thr)
    deciles = _slices(s, np.linspace(0.0, 1.0, 11), 100.0)
    top = _slices(s, np.linspace(0.9, 1.0, 11), 100.0)
    d_share, p_share = deciles[-1]["share_pct"], top[-1]["share_pct"]
    prob = np.array([0.5 * math.erfc(-z / math.sqrt(2.0)) for z in _Z])
    out.update({
        "mean": _f(s.mean),
        "cv": _f(s.cv),
        "min": _f(s.vmin),
        "max": _f(s.vmax),
        "metal": _f(s.metal),
        "curves": {
            "p": _list(ps),
            "threshold": _list(thr),
            "mean": _list(c.mean),
            "cv": _list(c.cv),
            "loss_pct": _list(c.loss),
            "n_cut": [int(x) for x in c.n_cut],
        },
        "deciles": deciles,
        "top": top,
        "top_decile_pct": d_share,
        "top_percentile_pct": p_share,
        "flag_decile": d_share is not None and d_share > decile_limit,
        "flag_percentile": p_share is not None and p_share > percentile_limit,
        "quantiles": {"p": _list(100.0 * prob), "z": _list(_Z), "value": _list(s.percentiles(100.0 * prob))},
    })  # fmt: skip
    return out


def topcut_analysis(
    values: Any,
    lengths: Any,
    *,
    codes: Any = None,
    names: Sequence[str] = (),
    field: str = "",
    percentiles: Sequence[float] | None = None,
    decile_limit: float = 40.0,
    percentile_limit: float = 10.0,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """What the window «Урезка» shows to choose a threshold (see the module docstring); nothing is applied."""
    ps = np.asarray(CURVE_P if percentiles is None else percentiles, dtype=np.float64)
    if ps.ndim != 1 or ps.size == 0 or not np.all((ps >= 0.0) & (ps <= 100.0)):
        raise CompositeError(
            "bad_param", "Перцентили кривых: нужны числа от 0 до 100.", "Задайте, например, 90; 95; 99.",
            param="percentiles",
        )  # fmt: skip
    d_lim = check_range(decile_limit, 0.0, 100.0, "Порог доли верхнего дециля", "decile_limit", " %")
    p_lim = check_range(percentile_limit, 0.0, 100.0, "Порог доли верхнего процентиля", "percentile_limit", " %")
    v = np.ascontiguousarray(values, dtype=np.float64)
    ln = np.ascontiguousarray(lengths, dtype=np.float64)
    if v.ndim != 1 or ln.shape != v.shape:
        raise ValueError("values and lengths must be 1D of one length")
    c, labels = _groups(v.shape[0], codes, names)
    step = (lambda f: None) if tick is None else tick
    parts = sort_groups(v, ln, c, len(labels), tick=lambda f: step(0.8 * f))
    domains = []
    for k, (name, s) in enumerate(zip(labels, parts, strict=True)):
        domains.append(_analysis(name, s, ps, d_lim, p_lim))
        step(0.8 + 0.2 * (k + 1) / len(labels))
    return {"field": field, "limits": {"decile": d_lim, "percentile": p_lim}, "domains": domains}
