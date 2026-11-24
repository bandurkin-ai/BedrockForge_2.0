"""The answers of the core the window «Фильтры» is read against (подшаг 17.33; ADR 0023 §8, «Интерфейс»; e17-api.md
§13): the library of filters through the real routes — create (a tree, an expression, wireframes and surfaces, lists,
numbers), the list, the whole filter, the refusals (`name_taken`, `name_empty`, `filter_rules {param}`, `expr_syntax
{param, pos, line, column}`, `wireframe_not_found`), `PUT` with the version and the 409 `version_conflict`, «Где
используется» (an object and a view), 422 `filter_in_use` with the list, delete, «Проверить на модели» (the answers
of the routes `/filters/{id}/test` and `/filters/test` as they are — 422 `not_ready` until `blocks/restrict.py`, 17.8),
`GET /blocks` and `GET /wireframes` for the lists of the window.

The file `tests/data/blocks/filters_window_answers.json` is read by vitest (`ui/test/filters-constructor.test.ts`).
Regenerate: `BF_REGEN_FILTERS_WINDOW=1 uv run pytest -q tests/test_filters_window_answers.py`. What changes from run to
run (the times) is compared by its form only. The two answers `test_*` are compared only while `blocks/restrict.py`
is not there (after it they are made again by the regeneration and the window is read against them)."""

from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_filters_store import API, FL, cond, consumer, fc, group, make, model, wire  # noqa: F401

DATA = Path(__file__).parent / "data" / "blocks" / "filters_window_answers.json"
REGEN = os.environ.get("BF_REGEN_FILTERS_WINDOW") == "1"
VARYING = {"created_at", "updated_at"}
READY = importlib.util.find_spec("bf_core.blocks.restrict") is not None


def answer(r: Any) -> dict[str, Any]:
    """The status and the body of an answer (the body is the whole answer of the core)."""
    return {"status": r.status_code, "body": r.json()}


def post(c: Any, rules: dict[str, Any]) -> Any:
    return c.post(FL, json={"name": "Плохо", "rules": rules})


def build(c: Any) -> dict[str, Any]:
    wf = c.get(f"{API}/wireframes")
    one, two, relief = c.wf["Рудное тело 1"], c.wf["Рудное тело 2"], c.wf["Рельеф"]
    tree = group("and", cond("AU", ">", 1), cond("DOMAIN", "=", 2))
    rock = group("or", cond("Порода", "in", values=["руда", "O'Z", 3, "4,5"]), cond("Порода", "empty"))
    misc = group("and", cond("Au ppm", "<>", "Руда"), group("not", cond("CU", "notempty")),
                 group("or", cond("CU", "<=", "0,5"), cond("CU", ">=", -1.25)))  # fmt: skip
    rules_wire = group("or", group("and", cond("AU", ">", 1), wire(one, by="centre")), wire(two, "outside", m=2),
                       {"surface": {"id": relief, "side": "above"}})  # fmt: skip
    numbers = group("and", cond("AU", ">", 1e-7), cond("CU", "<", 123456789012345678.0), cond("ZN", ">=", 0.1))
    created = {
        "tree": answer(c.post(FL, json={"name": "Руда", "rules": tree, "note": "Борт 1 г/т", "folder": "Отчёты"})),
        "expr": answer(c.post(FL, json={"name": "Выражение", "expr": "if(au > 1, 1, 0) = 1 and domain in (1, 2)"})),
        "list": answer(c.post(FL, json={"name": "Список", "rules": rock})),
        "misc": answer(c.post(FL, json={"name": "Разное", "rules": misc})),
        "wire": answer(c.post(FL, json={"name": "Каркасы", "rules": rules_wire})),
        "numbers": answer(c.post(FL, json={"name": "Числа", "rules": numbers})),
    }
    for k, v in created.items():
        assert v["status"] == 200, (k, v)
    ids = {k: v["body"]["id"] for k, v in created.items()}
    refused = {
        "name_taken": answer(c.post(FL, json={"name": "руда ", "expr": "AU > 2"})),
        "name_empty": answer(c.post(FL, json={"name": "  ", "expr": "AU > 2"})),
        "no_rules": answer(c.post(FL, json={"name": "Пусто"})),
        "expr_syntax": answer(c.post(FL, json={"name": "Плохо", "expr": "AU > (1"})),
        "expr_in_tree": answer(post(c, group("and", cond("A", "=", 1), {"expr": "AU >"}))),
        "filter_rules": answer(post(c, group("and", cond("A", "=", 1), cond("B", ">", "много")))),
        "wireframe_not_found": answer(post(c, group("and", wire(one), wire(99999, "outside")))),
    }
    for k, v in refused.items():
        assert v["status"] in (409, 422), (k, v)
    url = f"{FL}/{ids['tree']}"
    updated = answer(c.put(url, json={"expected_version": 1, "name": "Руда 1 г/т", "expr": "AU > 1 and DOMAIN = 2"}))
    assert updated["status"] == 200, updated
    conflict = answer(c.put(url, json={"expected_version": 1, "name": "Опять"}))
    assert (conflict["status"], conflict["body"]["code"]) == (409, "version_conflict")
    missing_version = answer(c.put(url, json={"name": "Без версии"}))
    assert missing_version["status"] == 409
    # the users: an object of a later step and the view of a block model
    user = consumer(c, ids["tree"], "Поле AU2")
    mid = model(c, "Модель Б")
    styles = {str(mid): {"visible": True, "filter_id": ids["tree"]}}
    assert c.put(API + "/view/block_styles", json={"value": styles, "expected_version": 0}).status_code == 200
    usage = answer(c.get(f"{url}/usage"))
    assert usage["status"] == 200 and [u["kind"] for u in usage["body"]["usage"]] == ["object", "view"]
    assert usage["body"]["usage"][0]["id"] == user
    in_use = answer(c.delete(url))
    assert (in_use["status"], in_use["body"]["code"]) == (422, "filter_in_use")
    free = answer(c.get(f"{FL}/{ids['numbers']}/usage"))
    assert free["body"] == {"usage": []}
    tests = {
        "stored": answer(c.post(f"{FL}/{ids['tree']}/test", json={"model_id": mid})),
        "inline": answer(c.post(f"{FL}/test", json={"model_id": mid, "rules": tree})),
        "no_model": answer(c.post(f"{FL}/{ids['tree']}/test", json={"model_id": 99999})),
        "no_model_id": answer(c.post(f"{FL}/{ids['tree']}/test", json={})),
    }
    gone = answer(c.delete(f"{FL}/{ids['numbers']}"))
    assert gone["body"] == {"deleted": ids["numbers"]}
    return {
        "rules": "ответы настоящего ядра через маршруты /filters (test_filters_window_answers.py); ключи test_* — "
                 "пока нет blocks/restrict.py (17.8) ответ 422 not_ready",  # fmt: skip
        "ids": ids,
        "model_id": mid,
        "created": created,
        "refused": refused,
        "list": answer(c.get(FL)),
        "full": {k: answer(c.get(f"{FL}/{i}")) for k, i in ids.items() if k != "numbers"},
        "updated": updated,
        "conflict": conflict,
        "missing_version": missing_version,
        "usage": usage,
        "usage_free": free,
        "in_use": in_use,
        "deleted": gone,
        "tests": tests,
        "blocks": answer(c.get(f"{API}/blocks")),
        "wireframes": {"status": wf.status_code, "body": {"wireframes": [
            {k: w[k] for k in ("id", "name", "folder", "version", "dtm")} for w in wf.json()["wireframes"]]}},
    }


def masked(x: Any) -> Any:
    """The times — their type; the numbers to 1e−9 relative; `tests` — only while the restrictor is not there."""
    if isinstance(x, dict):
        return {k: (type(v).__name__ if k in VARYING else masked(v)) for k, v in x.items()}
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


def test_filters_window_answers_are_what_the_core_answers(fc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(fc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    if READY:  # the answers of the checking are those of the restrictor: the window is read against the stored ones
        stored["tests"] = built["tests"] = {}
    assert masked(stored) == masked(built)
    # what the window relies on
    ids = stored["ids"]
    full = {k: v["body"] for k, v in stored["full"].items()}
    assert full["tree"]["text"] is None or full["tree"]["version"] == 2  # replaced by an expression (the PUT)
    assert full["tree"]["rules"] == {"expr": "AU > 1 and DOMAIN = 2"}
    assert full["tree"]["text"] == "AU > 1 AND DOMAIN = 2"
    assert full["wire"]["text"] is None and [w["available"] for w in full["wire"]["wireframes"]] == [True] * 3
    assert full["list"]["text"] == "Порода IN ('руда', 'O''Z', 3, 4.5) OR ISNULL(Порода)"
    assert stored["list"]["status"] == 200 and len(stored["list"]["body"]["filters"]) == 5  # `numbers` deleted
    assert all("rules" not in f for f in stored["list"]["body"]["filters"])
    c = stored["conflict"]["body"]
    assert c["code"] == "version_conflict" and c["version"] == 2 and c["message"] and c["hint"]
    e = stored["refused"]["expr_syntax"]["body"]
    assert (e["code"], e["param"], e["pos"], e["line"], e["column"], e["reason"]) == (
        "expr_syntax", "expr", 5, 1, 6, "unclosed_paren")  # fmt: skip
    assert stored["refused"]["expr_in_tree"]["body"]["param"] == "rules.items.1.expr"
    assert stored["refused"]["filter_rules"]["body"]["param"] == "rules.items.1.cond.value"
    assert stored["refused"]["wireframe_not_found"]["body"]["param"] == "rules.items.1.wireframe.id"
    u = stored["in_use"]["body"]
    assert u["code"] == "filter_in_use" and [x["role"] for x in u["usage"]] == ["filter", "block_style"]
    assert u["usage"] == stored["usage"]["body"]["usage"] and u["message"] and u["hint"] and ids["tree"]
    if not READY:
        for k in ("stored", "inline"):
            t = stored["tests"][k]
            assert (t["status"], t["body"]["code"], t["body"]["param"]) == (422, "not_ready", "model_id")
            assert t["body"]["message"] and t["body"]["hint"]
    assert stored["tests"] == {} or stored["tests"]["no_model"]["body"]["code"] == "block_model_not_found"
    assert all(math.isfinite(v) for v in numbers(stored))
    assert "Модель Б" in [b["name"] for b in stored["blocks"]["body"]["block_models"]]
    assert {w["name"] for w in stored["wireframes"]["body"]["wireframes"]} >= {"Рельеф", "Рудное тело 1"}
    assert pytest is not None
