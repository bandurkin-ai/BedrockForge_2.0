"""The dendrogram of the fields of a table — what the window «Дендрограмма» asks (ТЗ 7.27; ADR 0019 «Замеры»:
10 полей; full, 5 млн проб — Пирсон, ориентир ≤ 3 с, Спирмен — строкой отчёта (в ТЗ цели нет); ci — 500 тыс.,
ориентир ≤ 1 с).

The set of `_samples`; the ten numeric fields of «Пробы»: eight grades (AU and AG correlated, the others not,
1–2 % of the samples without a value in each — the rows differ in what is missing), DENSITY and `length`;
weights by length, the correlations of ln, missing values «по парам». The route `POST /stats/dendrogram` as a
function (`_charts.ask`):
  pearson_s           Pearson — from the snapshot of the table (ten columns read); stages: `pearson_read_s`,
                      `pearson_corr_s` (two passes of the kernel over the rows, all the pairs at once),
                      `pearson_links_s` (the clustering of scipy and the layout of the tree);
  pearson_again_s     the same once more: the columns are in the cache (13.35: ten fields of 5 млн rows fit it);
  tree_s              another linkage, distance and a threshold of groups — from the cache (the correlations
                      are counted again: nothing but the columns is kept);
  spearman_s          Spearman, missing «по парам»: a ranking of its own for every pair whose rows differ —
                      the line of the report without a budget; `spearman_mb` — the peak of the process after it
                      (`pearson_mb` — before);
  spearman_complete_s Spearman over the rows where every field has a value: one ranking a field.
What is checked (a failure stops the run), against the columns by numpy and scipy: r of AU and AG is the weighted
Pearson of their ln over the rows where both have a value (1e-9), Spearman — the weighted Pearson of their
average ranks over the same rows (1e-9; `check_s`); the matrix is symmetric with ones on the diagonal, the
numbers of rows of the pairs are those of the columns; the tree joins all the ten fields; the threshold of
groups cuts it into more than one group.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "corr": ("corr",), "links": ("links",)}


def spots() -> _charts.Spots:
    from bf_core.stats import dendrogram, source

    return ((source, "_read_lines", "read"), (dendrogram, "correlations", "corr"), (dendrogram, "cluster", "links"))


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "fields": [*meta["numbers"], "length"]}
    return {
        "pearson": dict(base),
        "tree": {**base, "linkage": "complete", "distance": "1-abs", "cut": 0.5},
        "spearman": {**base, "method": "spearman"},
        "spearman_complete": {**base, "method": "spearman", "missing": "complete"},
    }


def _weighted_r(a: Any, b: Any, w: Any) -> float:
    import numpy as np

    da, db = a - np.average(a, weights=w), b - np.average(b, weights=w)
    return float((w * da * db).sum() / np.sqrt((w * da * da).sum() * (w * db * db).sum()))


def check(got: dict[str, dict[str, Any]], cols: Any, fields: list[str]) -> None:
    import numpy as np
    from scipy.stats import rankdata

    k = len(fields)
    x, y, w = cols.number(fields[0]), cols.number(fields[1]), cols.number("length")
    both = np.isfinite(x) & np.isfinite(y) & (x > 0.0) & (y > 0.0) & (w > 0.0)
    n = int(both.sum())
    for name, answer in got.items():
        r = np.array(answer["correlation"], dtype=np.float64)
        assert answer["fields"] == fields and r.shape == (k, k), (name, answer["fields"])
        # r[i][j] and r[j][i] may differ in the last bit (the sums of a pair are joined in two orders)
        assert np.abs(r - r.T).max() <= 1e-12 and (np.diag(r) == 1.0).all() and (np.abs(r) <= 1.0).all(), name
        assert len(answer["linkage"]) == k - 1 and sorted(answer["leaves"]) == sorted(fields), name
        if answer["spec"]["missing"] == "pairwise":
            assert answer["pairs"][0][1] == n and answer["counts"]["rows"] == cols.n, (name, answer["pairs"][0][1], n)
    pearson = _weighted_r(np.log(x[both]), np.log(y[both]), w[both])
    assert _samples.close(got["pearson"]["correlation"][0][1], pearson, 1e-9), (got["pearson"]["correlation"][0][1],
                                                                                pearson)  # fmt: skip
    assert got["tree"]["correlation"] == got["pearson"]["correlation"] and got["tree"]["group_count"] > 1
    assert got["tree"]["spec"]["linkage"] == "complete" and got["pearson"]["group_count"] is None
    ranks = _weighted_r(rankdata(x[both]), rankdata(y[both]), w[both])
    assert _samples.close(got["spearman"]["correlation"][0][1], ranks, 1e-9), (got["spearman"]["correlation"][0][1],
                                                                               ranks)  # fmt: skip
    whole = got["spearman_complete"]
    assert whole["counts"]["used"] < got["spearman"]["counts"]["used"] and whole["counts"]["incomplete"] > 0
    assert all(p == whole["counts"]["used"] for row in whole["pairs"] for p in row), whole["counts"]


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for body in bodies(meta).values():
            _charts.ask(_charts.project_of(engine, folder), "dendrogram", body)

    _samples.warm(every)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    fields = [*meta["numbers"], "length"]
    info: dict[str, Any] = {"rows": meta["rows"], "fields": len(fields)}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        got = {"pearson": _charts.measure(project, "dendrogram", ask["pearson"], "pearson", info, spots(), STAGES)}
        got["tree"] = _charts.measure(project, "dendrogram", ask["tree"], "tree", info, cold=False, again=False)
        info["pearson_mb"] = _samples.peak_mb()
        for key in ("spearman", "spearman_complete"):
            got[key] = _charts.measure(project, "dendrogram", ask[key], key, info, spots(), STAGES, cold=False,
                                       again=False)  # fmt: skip
        info["spearman_mb"] = _samples.peak_mb()
        t0 = time.perf_counter()
        check(got, _charts.columns(project, meta["set_id"], [*fields[:2], "length"], meta["samples"]), fields)
        info["check_s"] = round(time.perf_counter() - t0, 3)
        from bf_core.stats import source

        info["cache_mb"] = round(source.COLUMNS.size / 2**20, 1)
    finally:
        engine.dispose()
    info["r_au_ag"] = round(got["pearson"]["correlation"][0][1], 6)
    info["groups"] = got["tree"]["group_count"]
    info["peak_mb"] = _samples.peak_mb()
    return info
