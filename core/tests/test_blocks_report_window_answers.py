"""The answers of the core the window «Содержание–тоннаж» is read against (подшаг 17.35; ADR 0023 §10, «Интерфейс»;
e17-api.md §10, §15): the report through the real routes — `POST /blocks/{id}/report` → `{source, id}`, the job
`blocks.report` followed to its end (`status`, `progress`, `message`, `result`), the whole result (the table by cut-offs
with groups, the curve, `excluded`, `warnings`, the CSV), the gaps (a grade with no value is `null`, never 0), the
restrictor of the library (`filter_id`) and the quick one (`restrict`), the refusals in Russian (404
`block_field_not_found`, `block_model_not_found`; 422 `bad_param {param}`, `filter_field_missing`, `bad_request`) and
the lists of the window (`GET /blocks`, `GET /blocks/{id}`, `GET /filters`).

The file `tests/data/blocks/report_window_answers.json` is read by vitest (`ui/test/blocks-report.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_REPORT=1 uv run pytest -q tests/test_blocks_report_window_answers.py`. What changes from
run to run (the numbers of the jobs and the times) is not stored; the floats are compared to 9 digits."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from tests.test_archive import wait_job
from tests.test_blocks_report import API, BL, asked, bf_blocks, bf_model, put, rc  # noqa: F401

DATA = Path(__file__).parent / "data" / "blocks" / "report_window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_REPORT") == "1"
FL = API + "/filters"
SKIPPED = ("created_at", "updated_at", "size")  # the times and the size of the file of cells


def answer(r: Any) -> dict[str, Any]:
    """The status and the body of an answer (the body is the whole answer of the core)."""
    return {"status": r.status_code, "body": r.json()}


def run(c: Any, mid: int, body: dict[str, Any]) -> dict[str, Any]:
    """`POST /blocks/{id}/report`, the job followed to its end: what the window reads (no numbers of jobs)."""
    r = asked(c, mid, body)
    assert r.status_code == 200, r.text
    started = r.json()
    assert sorted(started) == ["id", "source"] and isinstance(started["id"], int)
    job = wait_job(c, started["source"], started["id"])
    return {
        "request": body,  # the body the window sends: `bodyOfArgs` of the interface gives exactly this
        "started": {"keys": sorted(started), "source": type(started["source"]).__name__},  # the project id varies
        "job": {k: job.get(k) for k in ("kind", "title", "queue", "status", "progress", "message", "error", "result")},
    }


def gaps_model(c: Any) -> int:
    """Four parent blocks: a grade with gaps, a grade without a single value, a grade of an unknown unit, no density."""
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [2.0, 2.0, 2.0]}
    fields = [
        {"name": "AU_CUT", "dtype": "f8", "unit": "g/t", "role": "grade"},
        {"name": "CU_CUT", "dtype": "f8", "unit": "%", "role": "grade"},
        {"name": "ZN_CUT", "dtype": "f8", "unit": "усл. ед.", "role": "grade"},
    ]
    cols = {
        "AU_CUT": np.array([0.5, np.nan, 2.0, 4.0]),
        "CU_CUT": np.full(4, np.nan),
        "ZN_CUT": np.array([1.0, 2.0, 3.0, 4.0]),
    }
    return put(c, grid, fields, np.arange(4, dtype=np.uint64), cols, name="Пропуски")


def bare_model(c: Any) -> int:
    """A model with a grade and no density at all: the defaults of 7.31 fail (422 `bad_param`, `density`)."""
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "size": [2.0, 2.0, 2.0]}
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "g/t"}]
    return put(c, grid, fields, np.arange(2, dtype=np.uint64), {"AU_CUT": np.array([1.0, 2.0])}, name="Без плотности")


def build(c: Any) -> dict[str, Any]:
    mid = bf_model(c, bf_blocks())
    gid = gaps_model(c)
    bid = bare_model(c)
    rule = {"cond": {"field": "CU_CUT", "op": ">", "value": 0.2}}
    flt = answer(c.post(FL, json={"name": "Руда выше борта", "rules": rule}))
    assert flt["status"] == 200, flt
    fid = flt["body"]["id"]
    quick = {"cond": {"field": "AU", "op": ">", "value": 1}}
    reports = {
        "defaults": run(c, mid, {"cutoffs": [0, 0.5, 1], "curve": 20, "csv": True}),
        "groups": run(c, mid, {"grades": ["AU_CUT", "CU_CUT"], "density": 2.7, "cutoffs": [0.5, 1], "groups": "OXIDE",
                               "categories": {"field": "ALT", "values": ["G", "Руда окисл. ёЁ"]}, "curve": 10,
                               "csv": True}),  # fmt: skip
        "filtered": run(c, mid, {"grades": ["AU_CUT"], "cutoffs": [0, 1], "curve": 0, "csv": False, "filter_id": fid,
                                 "restrict": quick}),  # fmt: skip
        "gaps": run(c, gid, {"grades": ["AU_CUT", "CU_CUT", "ZN_CUT"], "density": 2.5, "cutoffs": [0, 1, 1e9],
                             "curve": 6, "csv": True}),  # fmt: skip
        "gaps_by_zn": run(c, gid, {"grades": ["AU_CUT", "ZN_CUT"], "cutoff_field": "ZN_CUT", "density": 2.5,
                                   "cutoffs": [0, 2], "curve": 0, "csv": False}),  # fmt: skip
        "unknown_unit": run(c, gid, {"grades": ["ZN_CUT"], "density": 2.5, "cutoffs": [0], "curve": 6, "csv": False}),
        "density_field": run(c, mid, {"grades": ["AU_CUT"], "density": "DENSITY", "cutoffs": [0], "curve": 0,
                                      "csv": False}),
    }
    for k, v in reports.items():
        assert v["job"]["status"] == "done", (k, v)
    refused = {
        "not_object": answer(asked(c, mid, [])),
        "grades_not_list": answer(asked(c, mid, {"grades": "AU_CUT"})),
        "grade_missing": answer(asked(c, mid, {"grades": ["NOPE"]})),
        "grade_category": answer(asked(c, mid, {"grades": ["ALT"]})),
        "density_category": answer(asked(c, mid, {"density": "ALT"})),
        "density_zero": answer(asked(c, mid, {"density": 0})),
        "no_density": answer(asked(c, bid, {"cutoffs": [0]})),
        "cutoffs_empty": answer(asked(c, mid, {"cutoffs": []})),
        "cutoff_text": answer(asked(c, mid, {"cutoffs": [0, "a"]})),
        "curve_one": answer(asked(c, mid, {"curve": 1})),
        "groups_number": answer(asked(c, mid, {"groups": "AU_CUT"})),
        "category_label": answer(asked(c, mid, {"categories": {"field": "ALT", "values": ["нет такой"]}})),
        "filter_missing": answer(asked(c, mid, {"filter_id": 99999})),
        "restrict_field": answer(asked(c, mid, {"restrict": {"cond": {"field": "ZZZ", "op": ">", "value": 1}}})),
        "model_missing": answer(asked(c, 99999, {})),
        "too_many_grades": answer(asked(c, mid, {"grades": ["AU_CUT"] * 21})),
    }
    for k, v in refused.items():
        assert v["status"] in (404, 422), (k, v)
    return {
        "rules": "ответы настоящего ядра через маршрут /blocks/{id}/report и задание blocks.report "
                 "(test_blocks_report_window_answers.py); номера заданий и времена не хранятся",  # fmt: skip
        "ids": {"model": mid, "gaps": gid, "bare": bid, "filter": fid},
        "blocks": answer(c.get(f"{API}/blocks")),
        "model": answer(c.get(f"{BL}/{mid}")),
        "gaps_model": answer(c.get(f"{BL}/{gid}")),
        "filters": answer(c.get(FL)),
        "reports": reports,
        "refused": refused,
    }


def masked(x: Any) -> Any:
    """The floats to 9 significant digits (the same run gives the same bits; another machine may differ in the last)."""
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items() if k not in SKIPPED}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def numbers(x: Any) -> Any:
    if isinstance(x, dict):
        for v in x.values():
            yield from numbers(v)
    elif isinstance(x, list):
        for v in x:
            yield from numbers(v)
    elif isinstance(x, float):
        yield x


def test_report_window_answers_are_what_the_core_answers(rc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(rc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the window relies on
    rep = {k: v["job"]["result"] for k, v in stored["reports"].items()}
    d = rep["defaults"]
    assert d["cutoff_field"] == "AU_CUT" and d["density"] == {"field": "SG", "value": None}
    assert d["units"] == {"volume": "м³", "tonnes": "т"} and isinstance(d["csv"], str) and d["curve"] is not None
    assert [r["group"] for r in d["rows"]] == [None, None, None] and [r["cutoff"] for r in d["rows"]] == [0, 0.5, 1]
    assert set(d["excluded"]) == {"filtered", "no_volume", "no_density", "no_cutoff"}
    assert d["excluded"]["no_density"] == 1
    g = rep["groups"]
    assert {r["group"] for r in g["rows"]} >= {None, "Ox", "Sulf"} and g["density"] == {"field": None, "value": 2.7}
    assert g["excluded"]["filtered"] > 0 and g["csv"].splitlines()[0].startswith("Группа;Борт;Блоков")
    f = rep["filtered"]
    assert f["curve"] is None and f["excluded"]["filtered"] > 0
    zero = next(r for r in rep["gaps"]["rows"] if r["cutoff"] == 0)
    assert zero["grades"]["CU_CUT"]["grade"] is None and zero["grades"]["CU_CUT"]["metal"] is None  # never 0
    assert zero["grades"]["CU_CUT"]["missing_blocks"] == 3 and rep["gaps"]["excluded"]["no_cutoff"] == 1
    by_zn = next(r for r in rep["gaps_by_zn"]["rows"] if r["cutoff"] == 0)
    assert by_zn["blocks"] == 4 and by_zn["grades"]["AU_CUT"]["missing_blocks"] == 1  # a gap: outside the mean
    assert by_zn["grades"]["AU_CUT"]["missing_tonnes"] > 0 and by_zn["grades"]["AU_CUT"]["grade"] is not None
    assert zero["grades"]["ZN_CUT"]["metal_unit"] is None and zero["grades"]["ZN_CUT"]["metal"] is None
    top = next(r for r in rep["gaps"]["rows"] if r["cutoff"] == 1e9)
    assert top["blocks"] == 0 and top["grades"]["AU_CUT"]["grade"] is None
    unit = rep["unknown_unit"]["curve"]  # a metal of an unknown unit: no value in any point
    assert unit["metal"] == [None] * 6 and unit["metal_unit"] is None and None not in unit["grade"]
    assert rep["density_field"]["density"] == {"field": "DENSITY", "value": None}
    for k, v in stored["refused"].items():
        e = v["body"]
        assert e["code"] and e["message"] and e["hint"], (k, e)
        assert any("а" <= ch.lower() <= "я" for ch in e["message"]), (k, e)
    assert stored["refused"]["grade_missing"]["body"]["code"] == "block_field_not_found"
    assert stored["refused"]["grade_missing"]["body"]["param"] == "grades.0"
    assert stored["refused"]["no_density"]["body"]["param"] == "density"
    assert stored["refused"]["restrict_field"]["body"]["code"] == "filter_field_missing"
    assert stored["refused"]["model_missing"]["body"]["code"] == "block_model_not_found"
    assert all(math.isfinite(v) for v in numbers(stored))
    assert {b["name"] for b in stored["blocks"]["body"]["block_models"]} == {"Модель", "Пропуски", "Без плотности"}
    assert [x["name"] for x in stored["filters"]["body"]["filters"]] == ["Руда выше борта"]
