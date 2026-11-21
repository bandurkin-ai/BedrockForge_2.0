"""Q-Q plots — what the window «Q-Q график» asks (ТЗ 7.26; ADR 0019 «Замеры»: full, 5 млн проб — ≤ 2 с;
ci — 500 тыс., ориентир ≤ 0,5 с).

The set of `_samples`; AU and AG of «Пробы» (heavy-tailed, ties at the detection limit, 1–2 % of the samples
without a value), weights by length. The route `POST /stats/qq` as a function (`_charts.ask`):
  theory_s        AU against the lognormal distribution — from the snapshot of the table; stages: `theory_read_s`
                  (the columns), `theory_select_s`, `theory_sort_s` (`weighted.sort_groups`), `theory_quantiles_s`
                  (the positions, Φ⁻¹, the thinning to 2000 points, PPCC);
  theory_again_s  the same once more, the columns are in the cache;
  sample_s        two samples, AU against AG, 99 quantiles — from the snapshot (both columns read; the sides are
                  sorted in two threads, so `sample_sort_s` adds up both); `sample_again_s` — from the cache;
  same_s          AU against itself, from the cache — for the check.
What is checked (a failure stops the run), against the arrays of the generator: every sample with a value is in
the chart, the mean of the side is the mean weighted by length (1e-9); the points of a chart ascend along both
axes, PPCC lies in (0,9; 1]; two samples give 99 points and a distance 0 ≤ D ≤ 1; a sample against itself lies
on the line 1 : 1 exactly with D = 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "select": ("select",), "sort": ("sort",), "quantiles": ("quantiles",)}
X, Y = "AU", "AG"


def spots() -> _charts.Spots:
    from bf_core.stats import qq, select, source

    return ((source, "_read_lines", "read"), (select, "select_values", "select"), (select, "sort_groups", "sort"),
            (qq, "qq_distribution", "quantiles"), (qq, "qq_samples", "quantiles"))  # fmt: skip


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": X}
    return {
        "theory": {**base, "distribution": "lognormal"},
        "sample": {**base, "distribution": "sample", "y_field": Y},
        "same": {**base, "distribution": "sample", "y_field": X},
    }


def _ascends(values: list[float]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:], strict=False))


def check(theory: dict[str, Any], sample: dict[str, Any], same: dict[str, Any], ref: dict[str, Any]) -> None:
    for answer in (theory, sample, same):
        side = answer["x"]
        assert side["counts"]["used"] == ref["au_n"] == side["stats"]["count"], side["counts"]
        assert _samples.close(side["stats"]["mean"], ref["au_mean"], 1e-9), (side["stats"]["mean"], ref["au_mean"])
        assert _ascends([p[0] for p in answer["points"]]) and _ascends([p[1] for p in answer["points"]])
    assert theory["mode"] == "theory" and theory["count"] == ref["au_n"] and 3 <= len(theory["points"]) <= 2000
    assert 0.9 < theory["ppcc"] <= 1.0 and theory["distribution"]["name"] == "lognormal", theory["ppcc"]
    assert sample["mode"] == "sample" and len(sample["points"]) == 99 and 0.0 <= sample["ks"] <= 1.0, sample["ks"]
    assert sample["y"]["counts"]["used"] == sample["y"]["stats"]["count"] > 0 and sample["y_field"] == Y
    assert same["ks"] == 0.0 and all(p[0] == p[1] for p in same["points"]), same["ks"]


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for body in bodies(meta).values():
            _charts.ask(_charts.project_of(engine, folder), "qq", body)

    _samples.warm(every)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        theory = _charts.measure(project, "qq", ask["theory"], "theory", info, spots(), STAGES)
        sample = _charts.measure(project, "qq", ask["sample"], "sample", info, spots(), STAGES)
        same = _charts.measure(project, "qq", ask["same"], "same", info, cold=False, again=False)
    finally:
        engine.dispose()
    check(theory, sample, same, meta["ref"])
    info["points"] = len(theory["points"])
    info["ppcc"] = round(theory["ppcc"], 6)
    info["ks"] = round(sample["ks"], 6)
    info["peak_mb"] = _samples.peak_mb()
    return info
