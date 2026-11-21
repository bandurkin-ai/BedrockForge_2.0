"""A change of the filter of the tab «QAQC»: every section of the active element computed anew (ТЗ 8.4.3: на 1 млн
контрольных записей пересчёт после смены фильтра ≤ 3 с; ADR 0020 «Замеры», решение 22: full — 1 млн контрольных
записей, ≤ 3 с и ≤ 1 ГБ; ci — 100 тыс., ориентир ≤ 0,5 с).

The data of `_qaqc` (the generator, вопрос № 38), the element AU. A round is the eight requests the tab makes for
its subtabs, each `POST /qaqc/calc/{section}` as a function (`_qaqc.ask`: `routes_calc.compute` and the answer as
JSON), the columns of the element in the cache of the process: `summary`, `standards`, `blanks`, `batches` (the
first page of 200), `duplicates` of DUP_F, DUP_C and DUP_P (the points thinned to 100 thousand, Thompson–Howarth),
`external` (the bootstrap of the RMA intervals, 1000 realizations a pair of laboratories).
  cold_read_s        the columns of the element read from the Parquet file of the records (not a part of ТЗ's
                     time: решение 22 — «при колонках активного элемента в кеше»; no budget)
  recalc_s           the round without a filter — every record (what a reset of the filter costs: the most);
                     its parts: `recalc_summary_s`, `recalc_standards_s`, `recalc_blanks_s`, `recalc_batches_s`,
                     `recalc_duplicates_s` (the three types), `recalc_external_s`; the clocks inside them —
                     `recalc_mask_s` (the types, the mask and the rows of the filter: eight contexts),
                     `recalc_bootstrap_s` (the bootstrap of `external`); `recalc_kb` — the eight answers
  filter_s           the round with a filter (two laboratories of three and «EXT», nine months of twelve) —
                     the same parts under `filter_…`
What is checked (a failure stops the run): the counts of the head against the generator (rows, control records,
none unrecognized; the records by type); 60 groups of standards holding every CRM sample, failures within 0.5–3 %
(1 % are shifted by 4σ); every blank in a group, some failed; the batches of the generator, some failed; of every
type of duplicates — its pairs, the criterion and K·b of Thompson–Howarth near the error of the generator / √2;
three pairs of laboratories of the external control with a bias near −2 % and an interval of the slope around
it; the filter leaves fewer records and changes the answers; the same request again gives the same bytes; the
standards by the filter and over the records taken beforehand (`recs.take(mask)`) are the same bytes (Т8.4-8).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from . import _charts, _qaqc, _samples

ROUND: tuple[tuple[str, dict[str, Any]], ...] = (
    ("summary", {}),
    ("standards", {}),
    ("blanks", {}),
    ("batches", {"offset": 0, "limit": 200}),
    ("duplicates", {"kind": "DUP_F", "max_points": 100_000}),
    ("duplicates", {"kind": "DUP_C", "max_points": 100_000}),
    ("duplicates", {"kind": "DUP_P", "max_points": 100_000}),
    ("external", {}),
)
STAGES = {"mask": ("mask",), "bootstrap": ("boot",)}
FILTER = {"v": 1, "labs": ["LAB1", "LAB2", "EXT"], "date_from": "2024-01-01", "date_to": "2024-09-30"}


def spots() -> _charts.Spots:
    from bf_core.qaqc import calc, calc_pairs

    return ((calc, "context", "mask"), (calc_pairs, "rma_intervals", "boot"))


def ask_round(project: Any, meta: dict[str, Any], flt: dict[str, Any] | None, key: str,
              info: dict[str, Any]) -> list[dict[str, Any]]:  # fmt: skip
    """The eight requests of a round timed into `info` under `key`; the answers in the order of `ROUND`."""
    answers, size = [], 0
    parts = {section: 0.0 for section, _params in ROUND}
    with _samples.stages(*spots()) as took:
        for section, params in ROUND:
            body = {"data_id": meta["data_id"], "element": _qaqc.ELEMENT, **params}
            if flt is not None:
                body["filter"] = flt
            answer, seconds, kb = _qaqc.ask(project, section, body)
            answers.append(answer)
            parts[section] += seconds
            size += kb
    info[f"{key}_s"] = round(sum(parts.values()), 3)
    info.update({f"{key}_{name}_s": round(seconds, 3) for name, seconds in parts.items()})
    info.update(_samples.timings(took, STAGES, f"{key}_"))
    info[f"{key}_kb"] = round(size / 1024.0, 1)
    return answers


def check(meta: dict[str, Any], answers: list[dict[str, Any]], filtered: list[dict[str, Any]]) -> None:
    summary, standards, blanks, batches, *dups, external = answers
    control, rows = meta["control"], meta["rows"]
    expected = {name: int(round(control * share)) for name, share in _qaqc.SHARES.items()}
    expected["CRM"] += control - sum(expected.values())
    pairs = sum(n for name, n in expected.items() if name not in ("CRM", "BLANK"))
    assert summary["counts"]["rows"] == rows == control + pairs, (summary["counts"], rows)
    assert summary["counts"]["control"] == control and summary["counts"]["unmapped"] == 0, summary["counts"]
    assert summary["types"] == {"ORIG": pairs, **expected}, summary["types"]
    groups = standards["groups"]
    assert len(groups) == _qaqc.STANDARDS * _qaqc.LABS and all(g["state"] == "ok" for g in groups), len(groups)
    assert sum(g["n"] + g["missing"] for g in groups) == expected["CRM"]
    failed = sum(g["fail"] for g in groups) / sum(g["n"] for g in groups)
    assert 0.005 < failed < 0.03, failed
    assert sum(g["n"] + g["missing"] for g in blanks["groups"]) == expected["BLANK"]
    assert 0 < sum(g["fail"] for g in blanks["groups"]) < 0.03 * expected["BLANK"]
    assert all(g["not_assessed"] == 0 for g in blanks["groups"]), blanks["notes"]
    assert batches["total"] == meta["batches"] and 0 < batches["failed"] < batches["total"], batches["total"]
    assert len(batches["batches"]) == min(200, batches["total"]) and batches["no_batch"] == 0
    for answer in dups:
        kind = answer["kind"]
        assert answer["pairs"] == expected[kind] and 0.95 * answer["pairs"] < answer["used"] <= answer["pairs"]
        assert len(answer["points"]["row"]) == min(answer["used"], 100_000) and answer["th"]["state"] == "ok"
        slope = answer["th"]["k"] * answer["th"]["b"]
        assert _samples.close(slope, _qaqc.SPREAD[kind] / math.sqrt(2.0), 0.15), (kind, slope)
        assert answer["passed"] is (answer["share_at_limit"] >= 90.0), (kind, answer["share_at_limit"])
    assert len(external["groups"]) == _qaqc.LABS, [(g["lab_main"], g["lab_check"]) for g in external["groups"]]
    for g in external["groups"]:
        assert g["lab_check"] == "EXT" and g["used"] > 0.95 * g["pairs"] > 0, g["pairs"]
        assert -3.0 < g["bias"] < -1.0 and g["p"] < 1e-6, (g["bias"], g["p"])  # the control reads 2 % higher
        low, high = g["rma"]["b_ci"]
        assert low < g["rma"]["b"] < high and 1.0 < g["rma"]["b"] < 1.05 and g["rma"]["n_boot"] == 1000, g["rma"]
    less = filtered[0]["counts"]
    assert 0.2 * rows < less["filtered"] < 0.9 * rows and less["rows"] == rows, less
    assert all(_charts.digest(a) != _charts.digest(b) for a, b in zip(answers, filtered, strict=True))


def taken_beforehand(project: Any, meta: dict[str, Any], answer: dict[str, Any]) -> None:
    """Т8.4-8 on the whole data: the standards by the filter and over `recs.take(mask)` — the same bytes."""
    from bf_core.qaqc import calc, filters, records, routes_calc

    got = routes_calc.inputs(project, {"data_id": meta["data_id"]})
    types = filters.map_types(got.recs.text(records.TYPE_CODE), got.config)
    part = got.recs.take(filters.row_mask(got.recs, filters.parse_filter(FILTER), types))
    other = calc.calc("standards", part, got.config, got.crm, _qaqc.ELEMENT)
    assert calc.canonical(other) == calc.canonical(answer), "the standards by a filter and over the records taken"


def prepare(params: dict[str, Any], work: Path) -> None:
    _qaqc.build(work, params)
    load()


def load() -> None:
    _qaqc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.qaqc import calc, records, service

    meta, engine, folder = _qaqc.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "control": meta["control"]}
    try:
        project = _qaqc.project_of(engine, folder)
        records.CACHE.clear()
        t0 = time.perf_counter()
        recs = service.open_records(project, meta["data_id"])
        recs.load(calc.columns_of(recs.schema, _qaqc.ELEMENT))
        info["cold_read_s"] = round(time.perf_counter() - t0, 3)
        answers = ask_round(project, meta, None, "recalc", info)
        filtered = ask_round(project, meta, FILTER, "filter", info)
        check(meta, answers, filtered)
        again, _, _ = _qaqc.ask(project, "standards", {"data_id": meta["data_id"], "element": _qaqc.ELEMENT})
        assert _charts.digest(again) == _charts.digest(answers[1]), "the same request — another answer"
        taken_beforehand(project, meta, filtered[1])
    finally:
        engine.dispose()
    info["filtered"] = filtered[0]["counts"]["filtered"]
    info["groups"] = len(answers[1]["groups"])
    info["batches"] = answers[3]["total"]
    info["pairs"] = sum(a["used"] for a in answers[4:7])
    info["external_pairs"] = sum(g["used"] for g in answers[7]["groups"])
    info["peak_mb"] = _samples.peak_mb()
    return info
