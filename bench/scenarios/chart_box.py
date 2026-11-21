"""Box plots and the log-probability plot by domains — what the windows «Ящики с усами» and «Лог-вероятностный»
ask (ТЗ 8.3 № 225, 226; ADR 0019 «Замеры»: 10 доменов; full, 5 млн проб — ≤ 2 с; ci — 500 тыс., ориентир ≤ 0,6 с).

The set of `_samples` and the interval table «Домены» (`_charts.zones`): ten zones in stretches of 20 m, the
bottoms of the holes and every 37th hole without a zone. The field — AU of «Пробы», weights by length; a sample
lies in the zone of its middle. The routes `POST /stats/box`, `/probplot`, `/domains` as functions (`_charts.ask`):
  box_s          boxes on the log scale by ZONE of «Домены» — from the snapshots of both tables; stages:
                 `box_read_s` (the columns of both), `box_domain_s` (the zone of every sample,
                 `source.domain_at`), `box_select_s`, `box_sort_s` (one sort for all the domains,
                 `weighted.sort_groups`); the boxes, whiskers and outliers are the rest;
  box_again_s    the same once more: the columns and the zones of the samples are in the cache;
  probplot_s     the log-probability lines of the ten zones — from the cache;
  domains_s      the table of statistics by the zones — from the cache;
  rock_s         boxes by ROCK, a text field of «Пробы» itself (six rocks): ROCK is read, the rest is cached.
What is checked (a failure stops the run), against the depths and the values by numpy: ten zones and the
samples without one; the number of samples and the mean weighted by length of every group (1e-9); the groups
hold every sample with a value, and so do the lines of the log-probability plot and the rows of the table;
quartiles ascend; the lines have ≤ 2000 points each and ascend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "domain": ("domain",), "select": ("select",), "sort": ("sort",)}
FIELD = "AU"


def spots() -> _charts.Spots:
    from bf_core.stats import domain_stats, select, source

    return ((source, "_read_lines", "read"), (source, "domain_at", "domain"), (select, "select_values", "select"),
            (domain_stats, "sort_groups", "sort"))  # fmt: skip


def bodies(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    base = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD}
    zone = {**base, "domain": _charts.ZONE, "domain_table": _charts.ZONES}
    return {
        "box": {**zone, "log": True},
        "probplot": dict(zone),
        "domains": dict(zone),
        "rock": {**base, "domain": _samples.ROCK, "log": True},
    }


def check(got: dict[str, dict[str, Any]], cols: Any, ref: dict[str, Any]) -> None:
    import numpy as np

    v, w = cols.number(FIELD), cols.number("length")
    have = np.isfinite(v)
    code = _charts.zone_codes(cols)
    box = got["box"]
    groups = {g["label"]: g for g in box["groups"]}
    assert len(groups) == _charts.ZONE_COUNT + 1 and box["domain_table"] == _charts.ZONES, list(groups)
    assert box["domain_outside"] == int((code < 0).sum()) > 0, box["domain_outside"]
    for k in range(-1, _charts.ZONE_COUNT):
        g = groups[_charts.zone_label(k) if k >= 0 else None]
        rows = have & (code == k)
        assert g["n"] == int(rows.sum()) and g["role"] == ("domain" if k >= 0 else "none"), (k, g["n"])
        mean = float((v[rows] * w[rows]).sum() / w[rows].sum())
        assert _samples.close(g["mean"], mean, 1e-9), (k, g["mean"], mean)
        assert g["min"] <= g["whisker_low"] <= g["q1"] <= g["median"] <= g["q3"] <= g["whisker_high"] <= g["max"], g
        assert g["outliers_total"] == g["outliers_low"] + g["outliers_high"] >= len(g["outliers"])
    assert box["counts"]["used"] == ref["au_n"] == sum(g["n"] for g in box["groups"]), box["counts"]
    lines = got["probplot"]["series"]
    assert len(lines) == len(groups) and sum(s["count"] for s in lines) == ref["au_n"], len(lines)
    for s in lines:
        p = s["points"]
        assert 3 <= len(p) <= 2000 and all(a[0] <= b[0] and a[1] <= b[1] for a, b in zip(p, p[1:], strict=False))
        assert s["count"] == groups[s["label"]]["n"] and _samples.close(s["stats"]["mean"], groups[s["label"]]["mean"],
                                                                        1e-12)  # fmt: skip
    table = got["domains"]
    assert len(table["rows"]) == len(groups) and table["total"]["n"] == ref["au_n"], table["total"]
    assert _samples.close(table["total"]["mean"], ref["au_mean"], 1e-9), (table["total"]["mean"], ref["au_mean"])
    rock = got["rock"]
    assert rock["domain_table"] is None and rock["domain_outside"] == 0 and len(rock["groups"]) > 2, rock["domain"]
    assert sum(g["n"] for g in rock["groups"]) == ref["au_n"], rock["counts"]


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params, more=_charts.zones)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for kind, body in bodies(meta).items():
            _charts.ask(_charts.project_of(engine, folder), "box" if kind == "rock" else kind, body)

    _samples.warm(every, more=_charts.zones)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "zone_rows": meta["zone_rows"]}
    try:
        project = _charts.project_of(engine, folder)
        ask = bodies(meta)
        got = {"box": _charts.measure(project, "box", ask["box"], "box", info, spots(), STAGES)}
        for kind in ("probplot", "domains"):
            got[kind] = _charts.measure(project, kind, ask[kind], kind, info, cold=False, again=False)
        got["rock"] = _charts.measure(project, "box", ask["rock"], "rock", info, cold=False, again=False)
        check(got, _charts.columns(project, meta["set_id"], [FIELD, "length"], meta["samples"]), meta["ref"])
    finally:
        engine.dispose()
    info["groups"] = len(got["box"]["groups"])
    info["outside"] = got["box"]["domain_outside"]
    info["peak_mb"] = _samples.peak_mb()
    return info
