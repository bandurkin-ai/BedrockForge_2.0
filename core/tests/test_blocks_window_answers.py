"""The answers of the core the window «Блочная модель · …» is read against (подшаг 17.34; ADR 0023 §11, decisions 16–19;
e17-api.md §4, §10, §12, §13): the model turned by 30° with sub-blocks, roles, units, descriptions and the checks of the
import (`GET /blocks`, `GET /blocks/{id}`: `grid.rotation`, `grid.div`, `grid.levels`, `rows`, `parents`, `sub`,
`checks {duplicates, overlaps, empty, constant, volume, examples}`), the properties of the fields
(`GET /blocks/{id}/fields`), a model without turn and sub-blocks, and the library of filters a quick restrictor is saved
into (`POST /filters` with the tree the window builds, the refusals in Russian).

The shell answers the window needs (the resolved range of the scale, breaks, categories, LOD) are `viz_answers.json` and
`layer_fixture.json` (17.27, 17.30). The file `tests/data/blocks/window_answers.json` is read by vitest
(`ui/test/blocks-window*.test.ts`). Regenerate: `BF_REGEN_BLOCKS_WINDOW=1 uv run pytest -q
tests/test_blocks_window_answers.py`. The times and the size of the file are not stored."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.blocks import checks as ck
from bf_core.blocks import store as bm
from bf_core.blocks.schema import GridV2
from bf_core.db import dialect
from tests.test_blocks_layer_fixture import grid_of, split_model
from tests.test_blocks_viz import plain, put, vc  # noqa: F401

API = "/api/v1"
BL = API + "/blocks"
FL = API + "/filters"
DATA = Path(__file__).parent / "data" / "blocks" / "window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_WINDOW") == "1"
SKIPPED = ("created_at", "updated_at", "size", "at")  # the times and the size of the file of cells
ROCKS = ["руда", "порода", "окисленная", "дайка", "вода"]
FIELDS: list[dict[str, Any]] = [
    {"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade", "description": "Золото, в пересчёте на сухую массу"},
    {"name": "OK", "dtype": "f8"},
    {"name": "FLAT", "dtype": "f8", "unit": "%"},
    {"name": "EMPTYF", "dtype": "f8"},
    {"name": "ROCK", "dtype": "cat", "labels": ROCKS, "role": "code"},
]


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def checked(c: Any, grid: GridV2, p: Any, s: Any, w: Any, values: dict[str, Any]) -> int:
    """A model v2 as the import leaves it: the file, the roles and the findings of `ModelChecker` in `meta.checks`."""
    report = ck.check_arrays(grid, p, s, w, values, fields=FIELDS, frac=None)
    meta = {"corner": list(grid.corner), "counts": list(grid.counts), "size": list(grid.regular()),
            "div": list(grid.sub.div), "rotation": grid.rotation, "order": "F"}  # fmt: skip
    with bm.CellsWriterV2(c.store, meta, FIELDS, row_group=1000) as wr:
        wr.write(p, values, s, w)
        staged = wr.finish()
    staged.v2 = {**(staged.v2 or {}), "checks": report.checks}
    try:
        with dialect.write_tx(c.project.engine) as conn:
            return bm.commit_block_model(conn, c.store, staged, name="Повёрнутая с субблоками",
                                         source={"format": "dat", "file": "turned.dat"})  # fmt: skip
    finally:
        staged.discard()


def build(c: Any) -> dict[str, Any]:
    grid = grid_of((6, 5, 4), (10.0, 10.0, 5.0), (2, 2, 2), 30.0)
    p, s, w = split_model(5, grid)
    rng = np.random.default_rng(7)
    au = np.exp(rng.normal(0.0, 1.2, len(p)))
    au[rng.random(len(p)) < 0.08] = np.nan
    ok = (rng.random(len(p)) < 0.9).astype(np.float64)
    rock = (np.arange(len(p)) * 7 % 5).astype(np.int32)
    rock[::11] = -1
    values = {"AU": au, "OK": ok, "FLAT": np.ones(len(p)), "EMPTYF": np.full(len(p), np.nan), "ROCK": rock}
    turned = checked(c, grid, p, s, w, values)
    flat, _m = plain(c)
    quick = {"op": "and", "items": [{"cond": {"field": "OK", "op": ">", "value": 0.5}},
                                    {"cond": {"field": "AU", "op": ">=", "value": 1.5}}]}  # fmt: skip
    inverted = {"op": "not", "items": [quick]}
    saved = answer(c.post(FL, json={"name": "Ограничитель · Повёрнутая", "rules": quick}))
    twice = answer(c.post(FL, json={"name": "Ограничитель · Повёрнутая", "rules": quick}))
    zz = {"cond": {"field": "ZZ", "op": ">", "value": 1}}
    missing = answer(c.post(FL + "/test", json={"model_id": turned, "rules": zz}))
    bad_value = answer(c.post(FL, json={"name": "Без значения", "rules": {"cond": {"field": "AU", "op": ">"}}}))
    inv = answer(c.post(FL, json={"name": "Обратный", "rules": inverted}))
    return {
        "rules": "ответы настоящего ядра (test_blocks_window_answers.py); времена и размер файла не хранятся",
        "ids": {"turned": turned, "plain": flat, "filter": saved["body"].get("id")},
        "blocks": answer(c.get(BL)),
        "turned": answer(c.get(f"{BL}/{turned}")),
        "plain": answer(c.get(f"{BL}/{flat}")),
        "fields": answer(c.get(f"{BL}/{turned}/fields")),
        "quick": {"rules": quick, "inverted": inverted},
        "filters": {
            "saved": saved,
            "twice": twice,
            "missing_field": missing,
            "bad_value": bad_value,
            "inverted": inv,
            "list": answer(c.get(FL)),
        },
    }


def masked(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items() if k not in SKIPPED}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def test_window_answers_are_what_the_core_answers(vc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(vc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the window relies on
    t = stored["turned"]["body"]
    assert t["grid"]["rotation"] == 30.0 and t["grid"]["div"] == [2, 2, 2] and t["sub"] is True
    assert t["rows"] == t["cells"] and t["parents"] <= t["rows"] and t["meta_v"] == 2
    assert [f["role"] for f in t["fields"]] == ["grade", None, None, None, "code"]
    assert t["fields"][0]["description"].startswith("Золото") and t["fields"][4]["labels"] == ROCKS
    chk = t["checks"]
    assert chk["duplicates"] == 0 and chk["overlaps"]["n"] == 0 and chk["empty"] == ["EMPTYF"]
    assert "FLAT" in chk["constant"] and chk["volume"] > 0
    f = stored["fields"]["body"]
    assert f["id"] == stored["ids"]["turned"] and [x["name"] for x in f["fields"]][:2] == ["AU", "OK"]
    flat = stored["plain"]["body"]
    assert flat["grid"]["rotation"] == 0.0 and flat["sub"] is False and flat["checks"] is None
    assert [b["name"] for b in stored["blocks"]["body"]["block_models"]] == ["Плоская", "Повёрнутая с субблоками"]
    fl = stored["filters"]
    assert fl["saved"]["status"] == 200 and fl["saved"]["body"]["rules"] == stored["quick"]["rules"]
    assert fl["inverted"]["status"] == 200 and fl["inverted"]["body"]["rules"] == stored["quick"]["inverted"]
    for name in ("twice", "missing_field", "bad_value"):
        e = fl[name]["body"]
        assert fl[name]["status"] in (409, 422) and e["code"] and e["message"] and e["hint"], (name, e)
        assert any("а" <= ch.lower() <= "я" for ch in e["message"]), (name, e)
    assert fl["twice"]["body"]["code"] == "name_taken"
