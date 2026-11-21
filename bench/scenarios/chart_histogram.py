"""The histogram of a sample field — what the window «Гистограмма» asks (ТЗ 7.25, Т7.25-4: «гистограмма по 5 млн
проб строится»; ADR 0019 «Замеры»: full — ≤ 2 с, ≤ 1 ГБ; ci — 500 тыс. проб, ориентир ≤ 0,5 с).

The set of `_samples`; the field — AU of «Пробы»: heavy-tailed, with nuggets, thousands of equal values at the
detection limit and 1 % of the samples without a value. The route `POST /stats/histogram` as a function
(`_charts.ask`): the columns from the snapshot of the table, the selection, one sort, the statistics with the
weights and without them, the bins:
  first_s        weight by length, automatic linear bins — from the snapshot (the columns are not in the cache);
                 its stages: `first_read_s` — the columns (`source._read_lines`), `first_select_s` — the rules of
                 the selection, `first_sort_s` — `weighted.sort_groups`, `first_stats_s` — both statistics,
                 `first_bins_s` — the bins;
  first_again_s  the same once more: the columns are in the cache (a setting changed in the window);
  log_s          no weights, bins of 0,1 in log10, the top cut «заменить» at 30 г/т — from the cache.
`peak_mb` — the peak of the process (VmHWM). What is checked (a failure stops the run), against the arrays of the
generator: every sample with a value takes part, the mean weighted by length and the maximum are theirs (1e-9);
without weights the count is the weight; the bins with what lies below and above hold every sample; the top
cut makes the maximum the cut with the same number of samples (ТЗ 7.25 «Приёмка»).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "select": ("select",), "sort": ("sort",), "stats": ("stats",), "bins": ("bins",)}
FIELD, CUT = "AU", 30.0


def spots() -> _charts.Spots:
    from bf_core.stats import histogram, select, source

    return ((source, "_read_lines", "read"), (select, "select_values", "select"), (select, "sort_groups", "sort"),
            (histogram, "describe_both", "stats"), (histogram, "histogram", "bins"))  # fmt: skip


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD}
    return {
        "first": dict(base),
        "log": {**base, "weight": "none", "log": True, "bin_width": 0.1, "top_cut": CUT, "top_mode": "cap"},
    }


def check(first: dict[str, Any], log: dict[str, Any], ref: dict[str, Any], rows: int) -> None:
    c, st, plain = first["counts"], first["stats"], first["stats_unweighted"]
    assert c["rows"] == rows and c["used"] == ref["au_n"] and c["missing"] == rows - ref["au_n"], c
    assert st["count"] == ref["au_n"] and _samples.close(st["mean"], ref["au_mean"], 1e-9), (st["mean"], ref)
    assert _samples.close(st["weight_sum"], ref["au_m"], 1e-9) and st["max"] == ref["au_max"], st
    assert first["weighted"] and plain["count"] == ref["au_n"] and plain["weight_sum"] == float(ref["au_n"]), plain
    for answer in (first, log):
        b = answer["bins"]
        held = sum(b["counts"]) + b["below"]["count"] + b["above"]["count"]
        assert held == ref["au_n"] and len(b["edges"]) == len(b["counts"]) + 1, (held, ref["au_n"])
    s = log["stats"]
    assert not log["weighted"] and log["stats_unweighted"] is None and log["log"], log["weighted"]
    assert s["count"] == ref["au_n"] and s["max"] == CUT and log["counts"]["capped"] > 0, (s["max"], log["counts"])


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def both(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for body in bodies(meta).values():
            _charts.ask(_charts.project_of(engine, folder), "histogram", body)

    _samples.warm(both)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        first = _charts.measure(project, "histogram", ask["first"], "first", info, spots(), STAGES)
        log = _charts.measure(project, "histogram", ask["log"], "log", info, cold=False, again=False)
    finally:
        engine.dispose()
    check(first, log, meta["ref"], meta["rows"])
    info["bins"] = len(first["bins"]["counts"])
    info["peak_mb"] = _samples.peak_mb()
    return info
