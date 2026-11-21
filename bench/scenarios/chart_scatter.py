"""The scatter plot of two fields — what the window «Рассеяние» asks (ТЗ 8.3 № 224; ADR 0019 «Замеры»: full,
5 млн проб — ≤ 2 с; ci — 500 тыс., ориентир ≤ 0,5 с).

The set of `_samples`; AU against AG of «Пробы» (correlated, 1–2 % of the samples without a value in either),
the colour — the text field ROCK, weights by length. The route `POST /stats/scatter` as a function
(`_charts.ask`): the columns, the pairs, the lines OLS, RMA and the weighted one, r of Pearson and of Spearman,
the cloud as numbers of pairs in 100 × 100 cells (more than 200 тыс. pairs are not sent as points):
  first_s        both axes logarithmic, the colour by ROCK — from the snapshot of the table; stages:
                 `first_read_s` (the columns), `first_pairs_s` (the rows that make a point), `first_sums_s`
                 (the regressions and Pearson), `first_ranks_s` (Spearman: two sorts in two threads),
                 `first_grid_s` (the density);
  first_again_s  the same once more, the columns are in the cache;
  linear_s       AU against CU, linear axes, no weights and no colour — CU is read (the lines of the table are
                 in the cache), the rest comes from the cache.
What is checked (a failure stops the run), against the columns themselves by numpy: the pairs are the rows with
both values, Pearson is that of log10 of the pairs (1e-9), the slope of OLS is cov / σ²x (1e-9), RMA has the sign
of r and the slope σy / σx, the cells hold every pair, Spearman lies within [−1; 1] near Pearson of the ranks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "pairs": ("pairs",), "sums": ("sums",), "ranks": ("ranks",), "grid": ("grid",)}
X, Y, OTHER, COLOR = "AU", "AG", "CU", _samples.ROCK


def spots() -> _charts.Spots:
    from bf_core.stats import scatter, source

    return ((source, "_read_lines", "read"), (scatter, "pairs", "pairs"), (scatter, "regressions", "sums"),
            (scatter, "spearman", "ranks"), (scatter, "density", "grid"))  # fmt: skip


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "x": X}
    return {
        "first": {**base, "y": Y, "log_x": True, "log_y": True, "color": COLOR, "regression": "rma"},
        "linear": {**base, "y": OTHER, "weight": "none", "regression": "ols"},
    }


def check(first: dict[str, Any], linear: dict[str, Any], cols: Any) -> None:
    import numpy as np

    x, y = cols.number(X), cols.number(Y)
    both = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y > 0.0)
    u, v = np.log10(x[both]), np.log10(y[both])
    n = int(both.sum())
    c = first["counts"]
    assert c["used"] == n == first["n"] and c["rows"] == cols.n and first["weighted"], (c, n)
    du, dv = u - u.mean(), v - v.mean()
    r = float((du * dv).sum() / np.sqrt((du * du).sum() * (dv * dv).sum()))
    assert _samples.close(first["pearson"], r, 1e-9), (first["pearson"], r)
    slope = float((du * dv).sum() / (du * du).sum())
    assert _samples.close(first["fits"]["ols"]["b"], slope, 1e-9), (first["fits"]["ols"], slope)
    assert _samples.close(first["fits"]["rma"]["b"], float(np.sqrt((dv * dv).sum() / (du * du).sum())), 1e-9)
    assert first["fits"]["weighted"] is not None and -1.0 <= first["spearman"] <= 1.0
    assert abs(first["spearman"] - r) < 0.05, (first["spearman"], r)  # the same cloud: the ranks agree with it
    if first["mode"] == "density":
        d = first["density"]
        assert d["nx"] == d["ny"] == 100 and sum(d["counts"]) == n and max(d["counts"]) == d["max"], d["max"]
    else:
        assert len(first["x"]) == n and first["color"]["kind"] == "text", first["mode"]
    assert first["color_field"] == COLOR and first["x_field"] == X and first["y_field"] == Y
    z = cols.number(OTHER)
    pairs = int((np.isfinite(x) & np.isfinite(z)).sum())
    assert linear["counts"]["used"] == pairs == linear["n"] and not linear["weighted"], linear["counts"]
    assert linear["fits"]["weighted"] is None and not linear["log_x"] and -1.0 <= linear["pearson"] <= 1.0


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for body in bodies(meta).values():
            _charts.ask(_charts.project_of(engine, folder), "scatter", body)

    _samples.warm(every)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        first = _charts.measure(project, "scatter", ask["first"], "first", info, spots(), STAGES)
        linear = _charts.measure(project, "scatter", ask["linear"], "linear", info, spots(), STAGES, cold=False,
                                 again=False)  # fmt: skip
        check(first, linear, _charts.columns(project, meta["set_id"], [X, Y, OTHER], meta["samples"]))
    finally:
        engine.dispose()
    info["pairs"] = first["n"]
    info["mode"] = first["mode"]
    info["pearson"] = round(first["pearson"], 6)
    info["spearman"] = round(first["spearman"], 6)
    info["peak_mb"] = _samples.peak_mb()
    return info
