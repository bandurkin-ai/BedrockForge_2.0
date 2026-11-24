"""The answers of the core the windows of the building of a block model are read against (подшаг 17.37; ADR 0023 §6,
§9, §13, решения 8, 9, 14; e17-api.md §6, §7, §9, §15): «Прототип модели» (the preview with the frame, the job),
«Заполнить каркасами» (the job, the stages, the codes and the volumes, the warning «Каркас не пересекает сетку в
плане», the refusals before the job and the failure of the job), «Кодировать каркасами» (by the centre / with
splitting, 409 `version_conflict` / `field_exists`, 422 `frac_exists`), «Регуляризация» (the totals before / after,
the refusals),
«Перестроить» (`dropped`, 409 `source_lost`, 422 `not_rebuildable`) and the lists of the windows (`GET /blocks`,
`GET /blocks/{id}`, `GET /blocks/{id}/fields`, `GET /wireframes`) — all through the real routes and the real jobs.

The file `tests/data/blocks/build_window_answers.json` is read by vitest (`ui/test/blocks-build-*.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_BUILD=1 uv run pytest -q tests/test_blocks_build_window_answers.py`. What changes from
run to run (the times, the numbers of the jobs) is not stored; the floats are compared to 9 digits."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.wireframes import store as wf
from tests.test_archive import wait_job
from tests.test_blocks_fill_jobs import A_BOX, B_BOX, GRID_VOLUME, _surface, add_field, body, plain_model, prototype  # noqa: F401
from tests.test_blocks_ops_api import BL, setup_core
from tests.test_fill_octree import box

API = "/api/v1"
DATA = Path(__file__).parent / "data" / "blocks" / "build_window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_BUILD") == "1"
SKIPPED = (
    "created_at",
    "updated_at",
    "size",
    "at",
    "file",
)  # the times and the size of the file of cells (not the grid's)
STAGES = {
    "prototype": ["Охват", "Запись"],
    "fill": ["Каркасы", "Столбцы", "Граничные родители", "Дробление", "Слияние", "Запись"],
    "regularize": ["Чтение", "Перекрытия", "Запись"],
}


@pytest.fixture()
def oc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    return setup_core(pclient, monkeypatch, "Построение")


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def started(c: Any, path: str, payload: Any) -> dict[str, Any]:
    """`POST path` → the job followed to its end: the request the window sends and what it reads."""
    r = c.post(BL + path, json=payload)
    assert r.status_code == 200, (path, payload, r.text)
    s = r.json()
    assert sorted(s) == ["id", "source"] and isinstance(s["id"], int)
    job = wait_job(c, s["source"], s["id"], timeout=240)
    keys = ("kind", "title", "queue", "status", "progress", "message", "error", "result")
    return {"path": "/blocks" + path, "request": payload, "job": {k: job.get(k) for k in keys}}


def refused(c: Any, path: str, payload: Any, status: int, code: str, param: str | None = None) -> dict[str, Any]:
    r = c.post(BL + path, json=payload)
    assert (r.status_code, r.json()["code"]) == (status, code), (path, payload, r.text)
    assert param is None or r.json()["param"] == param, (path, payload, r.text)
    return {"path": "/blocks" + path, "request": payload, **answer(r)}


def wires(*items: Any) -> list[dict[str, Any]]:
    return [w if isinstance(w, dict) else {"id": w} for w in items]


def build(c: Any) -> dict[str, Any]:  # noqa: PLR0915
    p = c.p
    a = body(p, *A_BOX, name="Руда")
    b = body(p, *B_BOX, name="Зона")
    far = body(p, (500.0, 500.0, 500.0), (520.0, 520.0, 520.0), "Далеко")
    surface = _surface(p)
    v1, t1 = box((10.0, 10.0, 10.0), (40.0, 40.0, 30.0))
    v2, t2 = box((30.0, 30.0, 20.0), (55.0, 50.0, 40.0))
    with dialect.write_tx(p.engine) as conn:
        crossing = wf.put_wireframe(conn, p.store, np.concatenate([v1, v2]), np.concatenate([t1, t2 + len(v1)]),
                                    name="Две оболочки")  # fmt: skip
    out: dict[str, Any] = {"ids": {"wire_a": a, "wire_b": b, "far": far, "surface": surface, "crossing": crossing}}

    out["wireframes"] = answer(c.get(f"{API}/wireframes"))  # before «source_lost» takes one away

    # --- the prototype ---------------------------------------------------------------------------------------------
    box_req = {"size": [10, 10, 10], "extent": [0, 0, 0, 60, 50, 40], "levels": [1, 1, 1]}
    objects_req = {"size": [10, 10, 5], "object_ids": [a, b], "pad": 5, "rotation": 30, "levels": [2, 2, 1]}
    previews = {}
    for key, req in (("extent", box_req), ("objects", objects_req), ("pad3", {**box_req, "pad": [5, 5, 10]}),
                     ("full", {**box_req, "full": True})):  # fmt: skip
        r = c.post(BL + "/prototype/preview", json=req)
        assert r.status_code == 200, r.text
        previews[key] = {"request": req, **answer(r)}
    proto_refusals = {
        "no_extent_and_objects": refused(
            c, "/prototype/preview", {"size": [10, 10, 10]}, 422, "bad_param", "object_ids"
        ),
        "both": refused(c, "/prototype/preview", {**box_req, "object_ids": [a]}, 422, "bad_param", "object_ids"),
        "object_missing": refused(
            c, "/prototype/preview", {"size": 10, "object_ids": [999_999]}, 404, "object_not_found", "object_ids.0"
        ),  # fmt: skip
        "size_zero": refused(c, "/prototype/preview", {**box_req, "size": [10, 0, 10]}, 422, "bad_param", "size"),
        "pad_negative": refused(c, "/prototype/preview", {**box_req, "pad": -1}, 422, "bad_param", "pad"),
        "levels": refused(c, "/prototype/preview", {**box_req, "levels": [1, 9, 0]}, 422, "subblock_div"),
        "extent_five": refused(
            c, "/prototype/preview", {**box_req, "extent": [0, 0, 0, 5, 5]}, 422, "bad_param", "extent"
        ),
        "too_large": refused(
            c,
            "/prototype/preview",
            {"size": [0.001, 0.001, 0.001], "extent": [0, 0, 0, 5000, 5000, 5000]},
            422,
            "block_model_too_large",
        ),  # fmt: skip
    }
    proto = started(c, "/prototype", {**box_req, "name": "Сетка 10 м"})
    assert proto["job"]["status"] == "done", proto
    proto_full = started(c, "/prototype", {**box_req, "full": True, "name": "Все родители"})
    assert proto_full["job"]["status"] == "done", proto_full
    pid = proto["job"]["result"]["model_id"]
    out["prototype"] = {"previews": previews, "refused": proto_refusals, "job": proto, "full": proto_full}

    # --- the fill ---------------------------------------------------------------------------------------------------
    fills = {
        "both": started(
            c,
            "/fill",
            {
                "prototype_id": pid,
                "wireframes": wires({"id": a, "code": "Руда", "priority": 2}, b),
                "mode": "both",
                "m": 4,
                "outside_code": "вне",
                "name": "Заполнение",
            },
        ),  # fmt: skip
        "inside": started(
            c, "/fill", {"prototype_id": pid, "wireframes": wires(a), "mode": "inside", "levels": [0, 0, 0], "m": 8}
        ),  # fmt: skip
        "outside_far": started(c, "/fill", {"prototype_id": pid, "wireframes": wires(far), "mode": "inside", "m": 4}),
        "crossing": started(c, "/fill", {"prototype_id": pid, "wireframes": wires(crossing), "mode": "inside", "m": 4}),
    }
    for k in ("both", "inside", "outside_far"):
        assert fills[k]["job"]["status"] == "done", (k, fills[k])
    assert fills["crossing"]["job"]["status"] == "failed", fills["crossing"]
    fid = fills["both"]["job"]["result"]["model_id"]
    plain = plain_model(p)
    fill_refusals = {
        "mode": refused(
            c, "/fill", {"prototype_id": pid, "wireframes": wires(a), "mode": "сбоку"}, 422, "fill_mode", "mode"
        ),
        "m": refused(c, "/fill", {"prototype_id": pid, "wireframes": wires(a), "m": 99}, 422, "bad_param", "m"),
        "no_wireframes": refused(c, "/fill", {"prototype_id": pid, "wireframes": []}, 422, "bad_param", "wireframes"),
        "no_prototype": refused(
            c, "/fill", {"prototype_id": 777_777, "wireframes": wires(a)}, 404, "block_model_not_found"
        ),
        "not_prototype": refused(
            c, "/fill", {"prototype_id": plain, "wireframes": wires(a)}, 422, "not_prototype", "prototype_id"
        ),  # fmt: skip
        "wireframe_missing": refused(
            c,
            "/fill",
            {"prototype_id": pid, "wireframes": wires(777_777)},
            404,
            "wireframe_not_found",
            "wireframes.0.id",
        ),  # fmt: skip
        "wireframe_open": refused(
            c, "/fill", {"prototype_id": pid, "wireframes": wires(a, surface)}, 422, "wireframe_open", "wireframes.1.id"
        ),  # fmt: skip
        "levels": refused(
            c, "/fill", {"prototype_id": pid, "wireframes": wires(a), "levels": [1, 9, 1]}, 422, "bad_param", "levels.1"
        ),  # fmt: skip
    }
    out["fill"] = {"jobs": fills, "refused": fill_refusals}

    # --- the coding -------------------------------------------------------------------------------------------------
    codes = {
        "centre": started(
            c, f"/{plain}/code", {"expected_version": 1, "wireframes": wires(a), "mode": "inside", "code_field": "ZONE"}
        ),  # fmt: skip
        "split": started(
            c,
            f"/{plain}/code",
            {
                "expected_version": 2,
                "wireframes": wires({"id": a, "code": "Руда"}),
                "mode": "both",
                "split": True,
                "m": 4,
                "outside_code": "вне",
                "code_field": "KOD",
                "frac_field": "DOLYA",
                "name": "С дроблением",
            },
        ),  # fmt: skip
    }
    for k, v in codes.items():
        assert v["job"]["status"] == "done", (k, v)
    code_refusals = {
        "version": refused(
            c, f"/{plain}/code", {"expected_version": 1, "wireframes": wires(a)}, 409, "version_conflict"
        ),
        "no_version": refused(c, f"/{plain}/code", {"wireframes": wires(a)}, 409, "version_conflict"),
        "field_exists": refused(
            c,
            f"/{plain}/code",
            {"expected_version": 2, "wireframes": wires(a), "code_field": "AU"},
            409,
            "field_exists",
        ),  # fmt: skip
        "frac_exists": refused(
            c,
            f"/{fid}/code",
            {"expected_version": 1, "wireframes": wires(a), "split": True, "code_field": "K2", "frac_field": "FRAC2"},
            422,
            "frac_exists",
        ),  # fmt: skip
        "empty": refused(c, f"/{pid}/code", {"expected_version": 1, "wireframes": wires(a)}, 422, "block_model_empty"),
        "split_type": refused(
            c, f"/{plain}/code", {"expected_version": 2, "wireframes": wires(a), "split": "да"}, 422, "bad_param"
        ),  # fmt: skip
        "missing": refused(
            c, "/777777/code", {"expected_version": 1, "wireframes": wires(a)}, 404, "block_model_not_found"
        ),
    }
    out["code"] = {"jobs": codes, "refused": code_refusals}

    # --- «Перестроить» ------------------------------------------------------------------------------------------------
    add_field(p, fid, "X", np.zeros(int(fills["both"]["job"]["result"]["rows"])))
    fields_before = answer(c.get(f"{BL}/{fid}/fields"))
    detail_before = answer(c.get(f"{BL}/{fid}"))
    version = detail_before["body"]["version"]
    rebuild = started(c, f"/{fid}/rebuild", {"expected_version": version})
    assert rebuild["job"]["status"] == "done" and rebuild["job"]["result"]["dropped"] == ["X"], rebuild
    rebuild_refusals = {
        "version": refused(c, f"/{fid}/rebuild", {"expected_version": version}, 409, "version_conflict"),
        "not_rebuildable": refused(c, f"/{plain}/rebuild", {"expected_version": 2}, 422, "not_rebuildable"),
        "missing": refused(c, "/777777/rebuild", {"expected_version": 1}, 404, "block_model_not_found"),
    }
    # the body goes: the model coded with splitting cannot be rebuilt
    split_id = codes["split"]["job"]["result"]["model_id"]
    with dialect.write_tx(p.engine) as conn:
        import sqlalchemy as sa

        from bf_core.db.schema import objects

        conn.execute(sa.delete(objects).where(objects.c.id == a))
    rebuild_refusals["source_lost"] = refused(c, f"/{split_id}/rebuild", {"expected_version": 1}, 409, "source_lost")
    out["rebuild"] = {"fields_before": fields_before, "detail_before": detail_before, "job": rebuild,
                      "refused": rebuild_refusals}  # fmt: skip

    # --- the regularization -----------------------------------------------------------------------------------------
    regs = {
        "grades": started(
            c,
            f"/{plain}/regularize",
            {"size": [20, 20, 20], "grades": [{"field": "AU", "weight": "volume"}], "name": "Блоки 20"},
        ),  # fmt: skip
        "all": started(c, f"/{plain}/regularize", {"size": [20, 20, 10]}),
        "tonnes": started(
            c,
            f"/{plain}/regularize",
            {"size": [30, 25, 20], "grades": [{"field": "AU", "weight": "tonnes"}], "density": 2.7},
        ),  # fmt: skip
        "categories": started(
            c, f"/{fid}/regularize", {"size": [20, 20, 20], "categories": ["CODE"], "name": "Коды 20"}
        ),  # fmt: skip
    }
    for k, v in regs.items():
        assert v["job"]["status"] == "done", (k, v)
    reg_refusals = {
        "size_zero": refused(c, f"/{plain}/regularize", {"size": [0, 1, 1]}, 422, "bad_param", "size"),
        "no_size": refused(c, f"/{plain}/regularize", {}, 422, "bad_param", "size"),
        "grade_missing": refused(
            c,
            f"/{plain}/regularize",
            {"size": [20, 20, 20], "grades": [{"field": "NOPE"}]},
            404,
            "block_field_not_found",
        ),  # fmt: skip
        "tonnes_no_density": refused(
            c,
            f"/{plain}/regularize",
            {"size": [20, 20, 20], "grades": [{"field": "AU", "weight": "tonnes"}]},
            422,
            "bad_param",
            "density",
        ),  # fmt: skip
        "category_number": refused(
            c, f"/{plain}/regularize", {"size": [20, 20, 20], "categories": ["AU"]}, 422, "bad_param", "categories.0"
        ),  # fmt: skip
        "version": refused(
            c, f"/{plain}/regularize", {"size": [20, 20, 20], "expected_version": 99}, 409, "version_conflict"
        ),  # fmt: skip
        "empty": refused(c, f"/{pid}/regularize", {"size": [20, 20, 20]}, 422, "block_model_empty"),
        "missing": refused(c, "/777777/regularize", {"size": [20, 20, 20]}, 404, "block_model_not_found"),
    }
    out["regularize"] = {"jobs": regs, "refused": reg_refusals}

    # --- the lists of the windows -------------------------------------------------------------------------------------
    out["ids"].update({"prototype": pid, "fill": fid, "plain": plain, "split": split_id, "oc": c.mid,
                       "full": proto_full["job"]["result"]["model_id"]})  # fmt: skip
    out["blocks"] = answer(c.get(f"{API}/blocks"))
    out["models"] = {str(i): answer(c.get(f"{BL}/{i}")) for i in (pid, plain, fid)}
    out["rules"] = ("ответы настоящего ядра через маршруты /blocks/prototype, /fill, /{id}/code, /{id}/regularize, "
                    "/{id}/rebuild и задания blocks.*; номера заданий и времена не хранятся")  # fmt: skip
    out["stages"] = STAGES
    return out


def masked(x: Any) -> Any:
    """The floats to 9 significant digits; the seconds of the stages are not kept (they are the times of the run)."""
    if isinstance(x, dict):
        return {
            k: ({s: 0.0 for s in v} if k == "stages" and isinstance(v, dict) else masked(v))
            for k, v in x.items()
            if k not in SKIPPED or isinstance(v, list)  # `size` of a grid is a list, of the file of cells a number
        }
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


def test_build_window_answers_are_what_the_core_answers(oc) -> None:  # type: ignore[no-untyped-def]
    built = json.loads(json.dumps(masked(build(oc)), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the windows rely on
    pv = stored["prototype"]["previews"]
    assert pv["extent"]["body"]["parents"] == 6 * 5 * 4 and len(pv["extent"]["body"]["frame"]) == 8
    assert pv["extent"]["body"]["disk"] > 0 and pv["objects"]["body"]["grid"]["rotation"] == 30
    proto = stored["prototype"]["job"]["job"]["result"]
    assert proto["parents"] == 120 and proto["name"] == "Сетка 10 м" and set(proto) >= {"model_id", "grid", "events"}
    both = stored["fill"]["jobs"]["both"]["job"]["result"]
    assert both["volume"]["Руда"] > 0 and set(both["codes"]) == {"Руда", "Зона", "вне"} and both["dropped"] == []
    assert set(both["stages"]) >= {"wireframes", "columns", "boundary", "split", "merge", "write"}
    assert any(
        "не пересекает сетку в плане" in w for w in stored["fill"]["jobs"]["outside_far"]["job"]["result"]["warnings"]
    )
    assert stored["fill"]["jobs"]["crossing"]["job"]["error"]["code"] == "wireframe_orientation"
    assert stored["rebuild"]["job"]["job"]["result"]["dropped"] == ["X"]
    totals = stored["regularize"]["jobs"]["grades"]["job"]["result"]["totals"]
    assert math.isclose(totals["before"]["volume"], totals["after"]["volume"], rel_tol=1e-9)
    for section in ("prototype", "fill", "code", "rebuild", "regularize"):
        for k, v in stored[section]["refused"].items():
            e = v["body"]
            assert e["code"] and e["message"] and e["hint"], (section, k, e)
            assert any("а" <= ch.lower() <= "я" for ch in e["message"]), (section, k, e)
    assert all(math.isfinite(v) for v in numbers(stored))
