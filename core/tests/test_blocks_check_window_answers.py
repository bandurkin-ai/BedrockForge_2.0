"""The answers of the core the «Перепроверить» of the block model is read against (подшаг 17.41; ТЗ 7.31, Т8.7-5;
docs/progress/e17-api.md §11): `POST /blocks/{id}/check` with the job `blocks.check` through the real routes, the job
followed to its end (`status`, `message`, `result`), `GET /blocks/{id}` before and after (`checks`, the version), the
refusals (404, 423) and the model changed while it was checked (the job fails with `version_conflict`).

The file `tests/data/blocks/check_window_answers.json` is read by vitest (`ui/test/blocks-check.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_CHECK=1 uv run pytest -q tests/test_blocks_check_window_answers.py`. What changes from
run to run (the numbers of the jobs, the times, the seconds of the stages) is not stored; the floats are compared to 9
digits."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks import check_job
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.context import JobError
from tests.golden import assert_same
from tests.test_archive import wait_job
from tests.test_blocks_fields_window_answers import answer, masked
from tests.test_blocks_report import BL, put, rc  # noqa: F401

DATA = Path(__file__).parent / "data" / "blocks" / "check_window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_CHECK") == "1"


def model(c: Any, name: str, dirty: bool) -> int:
    """24 parents (4 × 3 × 2). A clean one has a gap in a grade. A dirty one is divided into sub-blocks: two parents
    hold a block laid over another one (an overlap), a field has no value at all, a field has one value everywhere."""
    grid: dict[str, Any] = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [10.0, 10.0, 5.0]}
    n = 24
    p = np.arange(n, dtype=np.uint64)
    au = 0.2 + 0.1 * np.arange(n)
    au[4] = np.nan
    cols = {"AU": au, "SG": 2.2 + 0.05 * np.arange(n)}
    fields: list[dict[str, Any]] = [
        {"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
        {"name": "SG", "dtype": "f8", "unit": "т/м³"},
    ]
    if not dirty:
        return put(c, grid, fields, p, cols, name=name)
    grid["div"] = [2, 2, 2]
    laid = np.isin(np.arange(n), [3, 10])  # the parents with a second block over the first
    p = np.repeat(p, 1 + laid.astype(int))
    w = np.full((len(p), 3), 2, dtype=np.int64)
    s = np.zeros((len(p), 3), dtype=np.int64)
    second = np.flatnonzero(np.r_[False, p[1:] == p[:-1]])  # the later row of a parent that has two
    s[second] = (1, 0, 0)
    w[second] = (1, 1, 1)
    n = len(p)
    cols = {"AU": np.linspace(0.1, 3.0, n), "SG": np.full(n, 2.7), "EMPTYF": np.full(n, np.nan)}
    fields.append({"name": "EMPTYF", "dtype": "f8"})
    return put(c, grid, fields, p, cols, s, w, name=name)


def follow(c: Any, r: Any, request: dict[str, Any]) -> dict[str, Any]:
    """A route that starts a job: the refusal as it is, or the job followed to its end (what the interface reads)."""
    if r.status_code != 200:
        return {"request": request, **answer(r)}
    started = r.json()
    assert sorted(started) == ["id", "source"] and isinstance(started["id"], int)
    done = wait_job(c, started["source"], started["id"], timeout=120)
    keys = ("kind", "title", "queue", "status", "message", "error", "result")
    return {
        "request": request,
        "status": 200,
        "started": {"keys": sorted(started)},
        "job": {k: done.get(k) for k in keys},
    }


def check(c: Any, mid: int) -> dict[str, Any]:
    return follow(c, c.post(f"{BL}/{mid}/check", json={}), {})


def conflict(c: Any, mid: int) -> dict[str, Any]:
    """The model is changed by another window while the check runs. The workers are processes, so the handler of the job
    runs here (the same function the worker calls) and the failed job is told as the core tells it: `{code, message,
    hint}` of its `JobError`."""

    def moved(_p: float | None = None, m: str | None = None) -> None:
        if m == "Запись":
            with dialect.write_tx(c.project.engine) as conn:
                conn.execute(sa.update(objects).where(objects.c.id == mid).values(version=objects.c.version + 1))

    ctx = SimpleNamespace(
        project_folder=c.project.folder, params={"model_id": mid}, engine=lambda: c.project.engine, tick=moved
    )
    with pytest.raises(JobError) as e:
        check_job.check_job(ctx)  # type: ignore[arg-type]
    error = {"code": e.value.code, "message": e.value.message, "hint": e.value.hint}
    job = {"kind": "blocks.check", "title": f"Проверка блочной модели №{mid}", "queue": "quick", "status": "failed",
           "message": e.value.message, "error": error, "result": None}  # fmt: skip
    return {"request": {}, "status": 200, "started": {"keys": ["id", "source"]}, "job": job}


def build(c: Any) -> dict[str, Any]:
    clean = model(c, "Чистая", False)
    dirty = model(c, "С замечаниями", True)
    out: dict[str, Any] = {"ids": {"clean": clean, "dirty": dirty}}
    for key, mid in (("clean", clean), ("dirty", dirty)):
        out[key] = {
            "before": answer(c.get(f"{BL}/{mid}")),
            "check": check(c, mid),
            "after": answer(c.get(f"{BL}/{mid}")),
        }
    out["missing"] = check(c, 987654)

    c.project.read_only = True
    try:
        out["read_only"] = check(c, dirty)
    finally:
        c.project.read_only = False

    out["conflict"] = {"before": answer(c.get(f"{BL}/{clean}")), "check": conflict(c, clean)}
    out["conflict"]["after"] = answer(c.get(f"{BL}/{clean}"))
    out["rules"] = "ответы настоящего ядра через маршруты блочных моделей (test_blocks_check_window_answers.py)"
    return out


def test_check_window_answers_are_what_the_core_answers(rc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(rc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    a = json.loads(DATA.read_text(encoding="utf-8"))
    assert_same(masked(a), masked(built))
    ids = a["ids"]
    for key in ("clean", "dirty"):
        c = a[key]
        assert c["before"]["body"]["checks"] is None  # the import of this test has not checked it
        job = c["check"]["job"]
        assert (job["kind"], job["queue"], job["status"]) == ("blocks.check", "quick", "done"), job
        res = job["result"]
        assert res["model_id"] == ids[key] and res["events"] == [
            ["objects.changed", {"kind": "block_model", "id": ids[key]}]
        ]
        assert c["after"]["body"]["checks"]["volume"] == res["checks"]["volume"]
        assert c["after"]["body"]["version"] == c["before"]["body"]["version"]  # the version does not move
    assert a["clean"]["check"]["job"]["result"]["checks"]["duplicates"] == 0
    d = a["dirty"]["check"]["job"]["result"]["checks"]
    assert d["overlaps"]["n"] > 0 and d["empty"] == ["EMPTYF"] and d["constant"] == ["SG"] and d["examples"]
    for key, status, code in (("missing", 404, "block_model_not_found"), ("read_only", 423, "project_read_only")):
        case = a[key]
        assert (case["status"], case["body"]["code"]) == (status, code), case
        assert case["body"]["message"] and case["body"]["hint"]
    job = a["conflict"]["check"]["job"]
    assert job["status"] == "failed" and job["error"]["code"] == "version_conflict" and job["error"]["hint"], job
    assert a["conflict"]["after"]["body"]["version"] == a["conflict"]["before"]["body"]["version"] + 1
    assert a["conflict"]["after"]["body"]["checks"] == a["conflict"]["before"]["body"]["checks"]  # nothing is written
