"""The answers of the core the windows of the estimation are read against (подшаг 18.13; e18-api.md §13): the real
routes on the synthetic project of `tests/estapi_world.py` (25 holes, a block model of three domains, a variogram
model), the jobs followed to their end — one file, `core/tests/data/estimate/contract_answers.json`, with the same keys
as the samples of the contract (`{status, body}`), read by vitest (`ui/test/estimation/*`).

Regenerate: `BF_REGEN_ESTIMATE=1 uv run pytest -q tests/test_estimate_api_answers.py` — the keys of this test replace
the samples, the others (`inspect` — 18.Д3, `simulation*` — 18.32) stay as they are. Without it the test checks that
the core still answers what the file holds; the times, the fingerprints and hashes, the versions of the libraries and
the names of the jobs are left out, the floats are compared to 1e−7 (another machine may differ in the last digits)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from tests import estapi_util as U
from tests.estapi_util import API, GS, finish, make_row, row_body

reg, ea = U.reg, U.ea  # the fixtures
DATA = Path(__file__).parent / "data" / "estimate" / "contract_answers.json"
REGEN = os.environ.get("BF_REGEN_ESTIMATE") == "1"
NOTE = ("Ответы настоящего ядра (BF_REGEN_ESTIMATE=1 uv run pytest -q tests/test_estimate_api_answers.py, 18.13) на "
        "синтетическом проекте tests/estapi_world.py; inspect, simulation* — образцы контракта до 18.Д3 и 18.32. "
        "Формы: docs/progress/e18-api.md §3–§8.")  # fmt: skip
TIMES = ("created_at", "updated_at")
SKIP = ("versions", "seconds", "stages", "fp", "sha", "token", "params", "created_at", "updated_at")


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def job_answer(c: Any, r: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(the answer of the route that starts the job, the job at its end as an answer, the job)."""
    assert r.status_code == 200, r.text
    started = r.json()
    job = finish(c, started)
    assert job["status"] == "done", job
    return ({"status": 200, "body": {"source": "project", "id": started["id"]}},
            {"status": 200, "body": job["result"]}, job)  # fmt: skip


def build(c: Any) -> dict[str, Any]:
    w = c.w
    out: dict[str, Any] = {}
    ids = {"model_id": w.model, "set_id": w.set_id, "table": str(w.a)}
    out["defaults"] = answer(c.get(f"{GS}/estimate/defaults", params={
        **ids, "field": "AU", "block_domain": "ZONE", "domain_table": str(w.b), "domain_field": "ZONE",
        "domain_values": "окисл", "variogram_id": w.vz, "method": "ok"}))  # fmt: skip
    first = c.post(f"{GS}/estimations", json=row_body(w, "окисл"))
    out["estimation_post"] = answer(first)
    a = first.json()["id"]
    b = make_row(c, "перех", samples={"set_id": w.set_id, "table": str(w.a), "field": "AU", "domain": {
        "table": str(w.b), "field": "ZONE", "values": ["перех"]}})["id"]  # fmt: skip
    ok_put = c.put(f"{GS}/estimations/{a}", json={"expected_version": 1, "disc": [2, 2, 1]})
    assert ok_put.status_code == 200  # the version moves; the refusal below reads the new one
    stale = c.put(f"{GS}/estimations/{a}", json={"expected_version": 1, "disc": [2, 2, 2]})
    out["estimation_put_conflict"] = answer(stale)
    passes = [{"factor": 1}, {"factor": 2, "n_max": 129}]
    out["bad_param"] = answer(c.post(f"{GS}/estimations", json=row_body(w, search={"passes": passes})))
    from tests.estutil import vmodel

    y = vmodel(w, "AU · Y", transform="nscore")
    out["variogram_scale"] = answer(c.post(f"{GS}/estimations", json=row_body(w, variogram={"model_id": y},
                                                                               name="Модель в Y")))  # fmt: skip
    plan = c.post(f"{GS}/estimation-plans", json={"model_id": w.model, "items": [a, b]})
    assert plan.status_code == 200
    version = c.get(f"{API}/blocks/{w.model}").json()["version"]
    run = {"plan_id": plan.json()["id"], "expected_version": version}
    out["run_submit"], out["run_result"], _ = job_answer(c, c.post(f"{GS}/estimate/run", json=run))
    out["estimation"] = answer(c.get(f"{GS}/estimations/{a}"))  # after the run: `last_run`, the fields that exist
    out["estimations"] = answer(c.get(f"{GS}/estimations"))
    out["plan"] = answer(c.get(f"{GS}/estimation-plans/{plan.json()['id']}"))
    version = c.get(f"{API}/blocks/{w.model}").json()["version"]
    out["run_field_exists"] = answer(c.post(f"{GS}/estimate/run", json={**run, "expected_version": version}))
    out["preview"] = answer(c.post(f"{GS}/estimate/preview", json={"estimation_id": a, "at": {"ijk": [4, 4, 7]}}))
    out["xval_submit"], out["xval_result"], _ = job_answer(c, c.post(f"{GS}/estimate/xval", json={
        "estimation_id": a, "by": "hole"}))  # fmt: skip
    out["xval"] = answer(c.get(f"{GS}/estimations/{a}/xval", params={"page": 1, "size": 8}))
    _, out["kna"], _ = job_answer(c, c.post(f"{GS}/estimate/kna", json={
        "estimation_id": a, "vary": ["n_max", "radius"], "blocks": [{"ijk": [4, 4, 7]}]}))  # fmt: skip
    _, out["global"], _ = job_answer(c, c.post(f"{GS}/estimate/global", json={"estimation_id": a}))
    out["qq"] = answer(c.post(f"{GS}/estimate/qq", json={"estimation_id": a, "quantiles": 10}))
    out["swath"] = answer(c.post(f"{GS}/estimate/swath", json={"estimation_id": a, "direction": "z"}))
    return json.loads(json.dumps(out, ensure_ascii=False))  # type: ignore[no-any-return]


def masked(x: Any, key: str = "") -> Any:
    if isinstance(x, dict):
        return {k: masked(v, k) for k, v in x.items() if k not in SKIP}
    if isinstance(x, list):
        return [masked(v, key) for v in x]
    if key == "at" and isinstance(x, str):
        return "<время>"  # the time of a calculation (`at` of a block is an object)
    return x


def diff(a: Any, b: Any, path: str = "") -> str | None:
    """The path of the first difference of two answers; floats to 1e−7."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            d = diff(a.get(k), b.get(k), f"{path}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return next((d for i, (x, y) in enumerate(zip(a, b, strict=True)) if (d := diff(x, y, f"{path}/{i}"))), None)
    if isinstance(a, float | int) and isinstance(b, float | int) and not isinstance(a, bool | type(None)):
        return None if math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9) else f"{path}: {a!r} != {b!r}"
    return None if a == b else f"{path}: {str(a)[:100]!r} != {str(b)[:100]!r}"


def test_the_answers_are_what_the_core_answers(ea) -> None:  # type: ignore[no-untyped-def]
    built = build(ea)
    old = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    if REGEN:
        merged = {"_note": NOTE, **{k: v for k, v in old.items() if k != "_note" and k not in built}, **built}
        DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        old = merged
    for key, value in built.items():
        assert key in old, f"нет ответа «{key}» в contract_answers.json: BF_REGEN_ESTIMATE=1"
        found = diff(masked(old[key]), masked(value), f"/{key}")
        assert found is None, found
    # what the windows rely on, whatever the numbers
    assert built["estimation_post"]["status"] == 200 and built["estimation_put_conflict"]["status"] == 409
    assert built["estimation_put_conflict"]["body"]["code"] == "version_conflict"
    assert built["bad_param"]["body"]["param"] == "search.passes.1.n_max"
    assert (built["variogram_scale"]["status"], built["variogram_scale"]["body"]["code"]) == (422, "variogram_scale")
    assert built["run_field_exists"]["status"] == 409 and built["run_field_exists"]["body"]["field"] == "AU_OK_STATUS"
    assert built["run_result"]["body"]["fields"][:2] == ["AU_OK_STATUS", "AU_OK_EST"]
    assert built["preview"]["body"]["status"] == 0 and built["xval"]["body"]["rows"] and built["kna"]["body"]["blocks"]
    assert built["global"]["body"]["field"] == "AU_OK_EST" and built["qq"]["body"]["field"] == "AU_OK_EST"
