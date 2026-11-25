"""The answers of the core the client of swath with a filter of the library is read against (подшаг 17.10б; ADR 0023
decision 21; ТЗ 7.30, 7.35 Д-02; e17-api.md): `POST /stats/swath` through the real route with `filter_id` in a series of
blocks — on the model of the demo (a filter alone, a filter and a selection by categories), on a model with sub-blocks
and a turn (the same answer, the notes «блоки на границе полос делятся…»), the filter of a surface (the warning of its
leaf in `notes`), the refusals — 404 `filter_not_found` and 422 `filter_field_missing` as the error of the series (the
other series are counted), 422 `bad_param {param: series[k].filter_id}` — and the samples «внутри блоков» of such a
series (`blocks_unsupported` with a hint). The library of filters (`GET /filters`) and the sources are as the window
reads them.

The file `tests/data/blocks/swath_filter_answers.json` is read by vitest (`ui/test/swath-filter.test.ts`).
Regenerate: `BF_REGEN_SWATH_FILTER=1 uv run pytest -q tests/test_swath_filter_window_answers.py`. The numbers are
compared to 1e−9 relative; the times — by their type only."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from bf_core.stats import blocks_cache as bc
from bf_core.stats import swath_sub as ss
from tests.test_filters_store import API, FL, cond, fc, group, make, wire  # noqa: F401
from tests.test_swath_api import blocks, samples, settings, swath_routes  # noqa: F401
from tests.test_swath_sub import DIV, GRID, put, rows_of

DATA = Path(__file__).parent / "data" / "blocks" / "swath_filter_answers.json"
REGEN = os.environ.get("BF_REGEN_SWATH_FILTER") == "1"
VARYING = {"created_at", "updated_at"}
S = f"{API}/stats"


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def swath(c: Any, *series: dict[str, Any], **kw: Any) -> dict[str, Any]:
    request = {"settings": settings(*series, **kw)}
    return {"request": request, **answer(c.post(f"{S}/swath", json=request))}


def build(c: Any) -> dict[str, Any]:
    bc.MODELS.clear()
    ss.MASKS.clear()
    src = c.get(f"{S}/swath/sources").json()
    set_id, demo = src["sets"][0]["id"], src["models"][0]["id"]
    rich = make(c, "Руда > 1", expr="AU > 1")["id"]
    needs = make(c, "Нужен ZZZ", expr="ZZZ > 1")["id"]
    relief = c.wf["Рельеф"]
    above = group("and", cond("AU", ">", 0), {"surface": {"id": relief, "side": "below"}})
    surface = make(c, "Выше рельефа", rules=above)
    body = make(c, "В каркасе", rules=group("and", cond("AU", ">", 0), wire(c.wf["Рудное тело 1"], by="centre")))
    sub = put(c.project, {**GRID, "div": DIV}, rows_of(), "Субблоки")
    sub_rich = make(c, "Субблоки AU > 1", expr="AU > 1")["id"]
    ore = {"field": "DOMAIN", "labels": ["руда"]}
    ok = {
        "none": swath(c, blocks(demo), width=50.0),
        "filter": swath(c, blocks(demo), blocks(demo, filter_id=rich), width=50.0),
        "filter_and_labels": swath(c, blocks(demo, filter_id=rich, filter=ore), width=50.0),
        "filter_null": swath(c, blocks(demo, filter_id=None), width=50.0),
        "surface": swath(c, blocks(demo, filter_id=surface["id"]), width=50.0),
        "wireframe": swath(c, blocks(demo, filter_id=body["id"]), width=50.0),
        "turned": swath(c, blocks(sub), blocks(sub, filter_id=sub_rich), direction="angles", azimuth=37, dip=-20,
                        roll=10, width=8.0),
    }  # fmt: skip
    for k, v in ok.items():
        assert v["status"] == 200, (k, v)
    refused = {
        "filter_not_found": swath(c, blocks(demo), blocks(demo, filter_id=987654), width=50.0),
        "filter_field_missing": swath(c, blocks(demo), blocks(demo, filter_id=needs), width=50.0),
        "bad_param": swath(c, blocks(demo, filter_id="x"), width=50.0),
        "bad_param_bool": swath(c, blocks(demo), blocks(demo, filter_id=True), width=50.0),
        "within_turned": swath(c, blocks(sub, filter_id=sub_rich), samples(set_id, within="blocks", within_series=0),
                               direction="angles", azimuth=37, width=8.0),
    }  # fmt: skip
    return {
        "rules": "ответы настоящего ядра через маршрут POST /stats/swath (test_swath_filter_window_answers.py)",
        "ids": {
            "set": set_id,
            "demo": demo,
            "sub": sub,
            "rich": rich,
            "needs": needs,
            "surface": surface["id"],
            "sub_rich": sub_rich,
        },  # fmt: skip
        "filters": answer(c.get(FL)),
        "sources": {"status": 200, "body": c.get(f"{S}/swath/sources").json()},
        "ok": ok,
        "refused": refused,
    }


def masked(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: (type(v).__name__ if k in VARYING else masked(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def floats(x: Any) -> Any:
    if isinstance(x, dict):
        for v in x.values():
            yield from floats(v)
    elif isinstance(x, list):
        for v in x:
            yield from floats(v)
    elif isinstance(x, float):
        yield x


def test_swath_filter_answers_are_what_the_core_answers(swath_routes, fc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(fc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the client relies on
    ok, bad = stored["ok"], stored["refused"]
    plain, filt = ok["filter"]["body"]["series"]
    assert ok["none"]["body"]["series"][0]["counts"]["filtered_out"] == 0
    assert plain["mean"] == ok["none"]["body"]["series"][0]["mean"]  # a series without a filter is the same
    assert filt["error"] is None and filt["counts"]["filtered_out"] > 0
    assert filt["counts"]["used"] + filt["counts"]["filtered_out"] <= filt["counts"]["rows"]
    assert ok["filter_null"]["body"]["series"][0]["counts"]["filtered_out"] == 0
    both = ok["filter_and_labels"]["body"]["series"][0]
    assert both["counts"]["filtered_out"] >= filt["counts"]["filtered_out"]  # the selection joins the filter
    turned = ok["turned"]["body"]
    assert turned["series"][1]["counts"]["filtered_out"] > 0
    assert any("делятся между полосами" in n for n in turned["notes"])
    for k, code in (("filter_not_found", "filter_not_found"), ("filter_field_missing", "filter_field_missing")):
        b = bad[k]["body"]
        assert bad[k]["status"] == 200 and b["series"][0]["error"] is None
        e = b["series"][1]["error"]
        assert e["code"] == code and e["message"] and e["hint"], k
        assert b["series"][1]["mean"] and all(v is None for v in b["series"][1]["mean"])  # not 0: nothing
    for k in ("bad_param", "bad_param_bool"):
        assert bad[k]["status"] == 422 and bad[k]["body"]["code"] == "bad_param"
        assert bad[k]["body"]["param"] == f"series[{1 if k == 'bad_param_bool' else 0}].filter_id"
        assert bad[k]["body"]["message"] and bad[k]["body"]["hint"]
    w = bad["within_turned"]["body"]["series"][1]
    assert w["error"]["code"] == "blocks_unsupported" and w["error"]["hint"]
    assert all(math.isfinite(v) for v in floats(stored))
    assert pytest is not None
