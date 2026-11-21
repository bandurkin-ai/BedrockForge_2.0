"""The analysis of the top cut — what the window «Урезка» asks before a threshold is chosen (ADR 0018 «Замеры»,
решение 8: the analysis is synchronous and writes nothing; ТЗ 8.2.3: «Анализ урезки — ≤ 3 с» на 5 млн проб;
ci — 500 тыс., ≤ 1 с).

The set of `_samples`; the field — AU of «Пробы»: heavy-tailed, with nuggets, thousands of equal values at the
detection limit and 1 % of the samples without a value. The route `POST /composites/topcut/analysis` is called as
a function (`routes_prep.topcut_analysis`): the request, the column read from the snapshot of the table (решение
16), the codes of the domain, one sort, the curves P 90…99,9 %, the deciles and the quantiles — twice:
  all_s      without a domain: all the samples are one group;
  domain_s   by the rock (ROCK of the same table: a text column read and coded, 6 rocks and the samples without one).
Stages of each (`all_…`, `domain_…`): `read_s` — `jobs_prep.read_prep`; `codes_s` — the codes of the domain;
`sort_s` — `stats.weighted.sort_groups`; `curves_s` — the curves, deciles and quantiles of every group.
`answer_kb` — the JSON of the larger answer. What is checked (a failure stops the run), against the arrays of the
generator: the samples with a value all take part, the mean weighted by length and the maximum are theirs (1e-9),
by domain the groups add up to the same number and metal; along a curve the threshold does not fall and the loss
does not grow; the deciles hold all the metal.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "codes": ("codes",), "sort": ("sort",), "curves": ("curves",)}
FIELD = "AU"


def analyse(meta: dict[str, Any], engine: Any, folder: Path, domain: bool) -> tuple[dict[str, Any], dict[str, float]]:
    """The route with the clocks of its stages."""
    from bf_core.composites import jobs_prep, routes_prep, topcut

    body: dict[str, Any] = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD}
    if domain:
        body["domain"] = _samples.ROCK
    with _samples.stages((jobs_prep, "read_prep", "read"), (jobs_prep, "src_codes", "codes"),
                         (topcut, "sort_groups", "sort"), (topcut, "_analysis", "curves")) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = routes_prep.topcut_analysis(_samples.service(engine, folder), body)
        took["total"] = time.perf_counter() - t0
    return out, took


def check(answer: dict[str, Any], ref: dict[str, Any], groups: int) -> None:
    domains = answer["domains"]
    assert answer["field"] == FIELD and len(domains) == groups, (answer["field"], len(domains))
    assert sum(d["n"] for d in domains) == ref["au_n"], (sum(d["n"] for d in domains), ref["au_n"])
    assert _samples.close(sum(d["metal"] for d in domains), ref["metal_au"], 1e-9)
    assert _samples.close(max(d["max"] for d in domains), ref["au_max"], 0.0)
    if groups == 1:
        assert _samples.close(domains[0]["mean"], ref["au_mean"], 1e-9), (domains[0]["mean"], ref["au_mean"])
    for d in domains:
        thr, loss = d["curves"]["threshold"], d["curves"]["loss_pct"]
        assert all(a <= b for a, b in zip(thr, thr[1:], strict=False)), d["domain"]
        assert all(a >= b for a, b in zip(loss, loss[1:], strict=False)) and loss[-1] >= 0.0, d["domain"]
        assert _samples.close(sum(x["share_pct"] for x in d["deciles"]), 100.0, 1e-9), d["domain"]


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def both(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for domain in (False, True):
            analyse(meta, engine, folder, domain)

    _samples.warm(both)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    size = 0
    try:
        for key, domain in (("all", False), ("domain", True)):
            answer, took = analyse(meta, engine, folder, domain)
            info[f"{key}_s"] = round(took["total"], 3)
            info.update(_samples.timings(took, STAGES, f"{key}_"))
            check(answer, meta["ref"], len(answer["domains"]) if domain else 1)
            info[f"{key}_groups"] = len(answer["domains"])
            size = max(size, len(json.dumps(answer, ensure_ascii=False).encode()))
    finally:
        engine.dispose()
    assert info["domain_groups"] > 1, info
    info["answer_kb"] = round(size / 1024.0, 1)
    info["peak_mb"] = _samples.peak_mb()
    return info
