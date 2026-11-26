"""The answers of the core the windows of the implicit models are read against (подшаг 19.16; e19-api.md §13): the real
routes on the fixture scene of `tests/modapi_world.py` (100 holes, the layers INT and SS), the jobs followed to their
end — one file, `core/tests/data/implicit/contract_answers.json`, with the same keys as the samples of the contract
(`{status, body}`), read by vitest (`ui/test/implicit/*`).

Regenerate: `BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_contract.py` — the keys of this test replace
the samples, the others stay as they are: `defaults`, `bad_param`, `refusals` are written by `spec.py`
(`test_implicit_spec.py`); the structural trend (19.8) and the keys `*_b` (Э19б) are samples of the contract until
their routes write them. The checks, the support, the comparison with the holes and `drillholes.from_model` (19.25)
are the real answers: the check by `sort=source` (the order of the residuals is a machine's), the vector of the
support is cut to its first 12 numbers (`support_one`) or given as its bytes (`support_f4`). Without the variable the
test checks that the core still answers what the file holds; the times, the fingerprints, the versions of the
libraries and the numbers a machine may round otherwise (the residuals, the conditions, the lines of the journal) are
left out, the floats are compared to 1e−7."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import object_refs
from tests import modapi_world as W
from tests.estapi_util import finish, read_only
from tests.modapi_world import IMP, body, im, reg  # noqa: F401 - the fixtures
from tests.test_import_job import API

DATA = Path(__file__).parent / "data" / "implicit" / "contract_answers.json"
REGEN = os.environ.get("BF_REGEN_IMPLICIT") == "1"
NOTE = ("ЧАСТЬ КЛЮЧЕЙ — ответы настоящего ядра (BF_REGEN_IMPLICIT=1 uv run pytest -q tests/test_implicit_contract.py, "
        "19.16, фикстура tests/modapi_world.py), части ключей — ЧЕРНОВИК форм (docs/progress/e19-api.md §5–§10): "
        "trend_*, build_iso_error и ключи *_b пишут подшаги 19.8, "
        "19.32+; defaults, bad_param, refusals пишет spec.py (BF_REGEN_IMPLICIT=1 uv run pytest -q "
        "tests/test_implicit_spec.py). Интерфейс принимается только на настоящих ответах.")  # fmt: skip
#: the planned holes (19.39: the windows read these; the route is `/planned_holes`, e19-api.md §10)
PLANNED = ("planned_post", "planned_target", "planned_list", "planned_put", "planned_conflict", "planned_bad",
           "planned_target_far", "planned_evaluate", "planned_evaluate_set", "planned_evaluate_bad", "planned_compare",
           "planned_compare_bad", "planned_export_csv", "planned_export_dxf", "planned_export_exists",
           "planned_delete", "planned_read_only")  # fmt: skip
#: the keys this test writes, in the order of the file (the samples of the others follow)
KEYS = ("defaults", "bad_param", "refusals", "models", "model_post", "name_taken", "model_not_found",
        "model_put", "model_put_conflict", "group_post", "group_put", "group_delete", "group_not_found", "preview",
        "preview_group", "preview_no_contacts", "not_ready", "build_submit", "model_building", "build_result", "model",
        "outputs", "model_stale", "update_submit", "update_result", "to_wireframe", "check", "check_csv", "check_bad",
        "support", "support_one", "support_f4", "support_mesh", "support_bad", "compare_holes", "compare_bad",
        "from_model_submit", "from_model_result", "from_model_exists", "from_model_bad", "build_failed", "model_error",
        "delete_conflict",
        "delete", "read_only", *PLANNED)  # fmt: skip
MINE = tuple(k for k in KEYS if k not in ("defaults", "bad_param", "refusals"))
SKIP = ("versions", "seconds", "stages", "fp", "sha", "token", "params", "created_at", "updated_at", "at", "job")
LOOSE = (
    "max_residual",
    "cond_max",
    "cond",
    "eta_min",
    "eta_max",
    "h_bar",
    "max_abs_residual",
    "f_model",
    "residual",
    "over",
    "highlight",
    "f_data",
    "size",
)  # a machine may round these otherwise (the size of a DXF: the handles of its entities)


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def job_answer(c: Any, r: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the answer of the route that starts the job, the job at its end as an answer)."""
    assert r.status_code == 200, r.text
    started = r.json()
    job = finish(c, started, timeout=600)
    assert job["status"] == "done", job
    return ({"status": 200, "body": {"source": "project", "id": started["id"]}}, {"status": 200, "body": job["result"]})


def version(c: Any, ident: int) -> int:
    return int(c.get(f"{IMP}/models/{ident}").json()["version"])


def build(c: Any) -> dict[str, Any]:  # noqa: PLR0915 - one scene, told in order
    w = c.w
    out: dict[str, Any] = {}
    out["defaults"] = answer(c.get(f"{IMP}/defaults"))
    bad = body(w, pou={"n_min": 10}, boundary={"box": W.BOX})
    out["bad_param"] = answer(c.post(f"{IMP}/models", json=bad))
    first = c.post(f"{IMP}/models", json=body(w))
    out["model_post"] = answer(first)
    mid = first.json()["id"]
    out["name_taken"] = answer(c.post(f"{IMP}/models", json=body(w)))
    out["model_not_found"] = answer(c.get(f"{IMP}/models/{mid + 99}"))
    put = c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "levels": 2, "kernel": "linear"})
    out["model_put"] = answer(put)
    out["model_put_conflict"] = answer(c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "levels": 1}))
    # the groups: added last, replaced in its place, deleted (the model is as it was, three versions on)
    g = {"name": "Аллювий", "type": "domain", "codes": ["ALV"]}
    out["group_post"] = answer(c.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 2, "group": g}))
    renamed = {**g, "name": "Наносы", "codes": ["ALV", "GR"]}
    out["group_put"] = answer(c.put(f"{IMP}/models/{mid}/groups/4", json={"expected_version": 3, "group": renamed}))
    out["group_not_found"] = answer(c.put(f"{IMP}/models/{mid}/groups/99", json={"expected_version": 4, "group": g}))
    out["group_delete"] = answer(c.delete(f"{IMP}/models/{mid}/groups/4", params={"expected_version": 4}))
    # the preview of the points: a stored group, a group the form has not saved, a group with no contacts
    url = f"{IMP}/models/{mid}/points/preview"
    out["preview"] = answer(c.post(url, json={"group_id": 1, "limit": 12}))
    out["preview_group"] = answer(c.post(url, json={"group": g, "limit": 5}))
    out["preview_no_contacts"] = answer(c.post(url, json={"group": {"name": "Призрак", "type": "domain",
                                                                    "codes": ["НЕТ"]}}))  # fmt: skip
    fault = {"name": "Разлом", "type": "fault", "codes": ["VN"]}
    added = c.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 5, "group": fault})
    out["not_ready"] = answer(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 6}))
    c.delete(f"{IMP}/models/{mid}/groups/{added.json()['group']['id']}", params={"expected_version": 6})
    # the run
    run = c.post(f"{IMP}/models/{mid}/build", json={"expected_version": version(c, mid), "threads": 2})
    out["build_submit"] = {"status": 200, "body": {"source": "project", "id": run.json()["id"]}}
    out["model_building"] = answer(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": version(c, mid)}))
    job = finish(c, run.json(), timeout=600)
    assert job["status"] == "done", job
    out["build_result"] = {"status": 200, "body": job["result"]}
    out["model"] = answer(c.get(f"{IMP}/models/{mid}"))
    out["models"] = answer(c.get(f"{IMP}/models"))
    out["outputs"] = answer(c.get(f"{IMP}/models/{mid}/outputs"))
    ok = c.put(f"{IMP}/models/{mid}", json={"expected_version": version(c, mid), "post": {"v_min": 1.0}})
    assert ok.status_code == 200
    out["model_stale"] = answer(c.get(f"{IMP}/models/{mid}"))
    submit, result = job_answer(c, c.post(f"{IMP}/models/{mid}/update", json={"expected_version": version(c, mid)}))
    out["update_submit"], out["update_result"] = submit, result
    shown = c.get(f"{IMP}/models/{mid}").json()["outputs"][1]["wireframe_id"]
    out["to_wireframe"] = answer(c.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": shown}))
    checks(c, mid, out)
    planned(c, mid, out)
    # a job that fails: the state `error` and what the window shows
    ghost = {"name": "Призрак", "type": "domain", "codes": ["НЕТ"]}
    bad_id = c.post(f"{IMP}/models", json=body(w, groups=[W.domain("INT"), ghost], name="Не построится")).json()["id"]
    failed = finish(c, c.post(f"{IMP}/models/{bad_id}/build", json={"expected_version": 1}).json(), timeout=600)
    assert failed["status"] == "failed", failed
    out["build_failed"] = {"status": 200, "body": {"status": failed["status"], "error": failed["error"]}}
    out["model_error"] = answer(c.get(f"{IMP}/models/{bad_id}"))
    c.delete(f"{IMP}/models/{bad_id}")
    # the deletion: an output that other objects refer to stops it (409 `in_use`, «Где используется»); then it goes
    other = c.post(f"{IMP}/models", json=body(w, "SS", name="Ссылается на выход")).json()["id"]
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.insert(object_refs).values(src_id=other, dst_id=shown, role="boundary"))
    out["delete_conflict"] = answer(c.delete(f"{IMP}/models/{mid}"))
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == other))
    out["delete"] = answer(c.delete(f"{IMP}/models/{mid}"))
    back = read_only(c)
    out["read_only"] = answer(c.post(f"{IMP}/models", json=body(w, name="Только просмотр")))
    back()
    return json.loads(json.dumps(out, ensure_ascii=False))  # type: ignore[no-any-return]


def checks(c: Any, mid: int, out: dict[str, Any]) -> None:
    """The check against the data (Д-32), the support, the comparison with the holes (Д-33), the model on the holes
    (Д-34 ч. 1): the model `mid` is built and its surfaces are written."""
    w = c.w
    out["check"] = answer(c.get(f"{IMP}/models/{mid}/check", params={"sort": "source", "size": 6, "highlight": 0.5}))
    csv = c.get(f"{IMP}/models/{mid}/check.csv", params={"sort": "source"})
    lines = csv.content.decode("utf-8-sig").splitlines()
    out["check_csv"] = {"status": csv.status_code, "body": {"type": csv.headers["content-type"], "bom": csv.content[:3]
                        == b"\xef\xbb\xbf", "lines": len(lines), "head": lines[:3]}}  # fmt: skip
    out["check_bad"] = answer(c.get(f"{IMP}/models/{mid}/check", params={"sort": "no"}))
    allv = c.post(f"{IMP}/models/{mid}/support", json={})
    out["support"] = answer(allv)
    one = allv.json()["surfaces"][0]["wireframe_id"]
    full = c.post(f"{IMP}/models/{mid}/support", json={"wireframe_id": one})
    cut = full.json()
    vals = cut.pop("values")
    out["support_one"] = {"status": full.status_code, "body": {**cut, "values": vals[:12], "values_n": len(vals)}}
    f4 = c.post(f"{IMP}/models/{mid}/support", json={"wireframe_id": one, "format": "f4"})
    head = json.loads(f4.headers["x-bf-support"])
    out["support_f4"] = {
        "status": 200,
        "body": {"type": f4.headers["content-type"], "bytes": len(f4.content), "head": head},
    }
    mesh = c.post(f"{IMP}/models/{mid}/support", json={"wireframe_id": one, "format": "f4mesh"})
    out["support_mesh"] = {
        "status": 200,
        "body": {"type": mesh.headers["content-type"], "bytes": len(mesh.content),
                 "head": json.loads(mesh.headers["x-bf-support"])},
    }  # fmt: skip
    out["support_bad"] = answer(c.post(f"{IMP}/models/{mid}/support", json={"threshold": -1}))
    cmp_url = f"{IMP}/models/{mid}/compare_holes"
    cmp_body = {"set_id": w.set_id, "table": str(w.litho), "field": "CODE", "size": 6}
    out["compare_holes"] = answer(c.post(cmp_url, json=cmp_body))
    out["compare_bad"] = answer(c.post(cmp_url, json={**cmp_body, "table": "нет такой"}))
    url = f"{API}/drillholes/from_model"
    submit, result = job_answer(
        c, c.post(url, json={"set_id": w.set_id, "model_id": mid, "name": "Модель на скважины"})
    )
    out["from_model_submit"], out["from_model_result"] = submit, result
    out["from_model_exists"] = answer(
        c.post(url, json={"set_id": w.set_id, "model_id": mid, "name": "Модель на скважины"})
    )
    out["from_model_bad"] = answer(c.post(url, json={"set_id": w.set_id, "name": "Пусто"}))


def planned(c: Any, mid: int, out: dict[str, Any]) -> None:  # noqa: PLR0915 - one scene, told in order
    """The planned holes (Д-34 ч. 2): the rows, the target mode, the forecast by the bodies of the model `mid`, the
    nearest holes of the set, «План / факт», the files; the routes are `/planned_holes`."""
    w = c.w
    url = f"{API}/planned_holes"
    new = {"name": "ПС-1", "collar": [210.0, 210.0, 330.0], "azimuth": 90.0, "dip": -70.0, "length": 100.0,
           "change": {"azimuth": 1.5, "dip": 0.5}, "set_id": w.set_id}  # fmt: skip
    first = c.post(url, json=new)
    out["planned_post"] = answer(first)
    pid = first.json()["id"]
    aim = c.post(url, json={"name": "ПС-2 (через цель)", "collar": [60.0, 60.0, 330.0], "target": [110.0, 100.0, 270.0],
                            "set_id": w.set_id})  # fmt: skip
    out["planned_target"] = answer(aim)
    out["planned_target_far"] = answer(c.post(url, json={"collar": [0.0, 0.0, 0.0], "target": [300.0, 0.0, 0.0],
                                                         "length": 100.0}))  # fmt: skip
    out["planned_list"] = answer(c.get(url))
    put = c.put(f"{url}/{pid}", json={"expected_version": 1, "length": 120.0, "name": "ПС-1 (длиннее)"})
    out["planned_put"] = answer(put)
    out["planned_conflict"] = answer(c.put(f"{url}/{pid}", json={"expected_version": 1, "length": 10.0}))
    out["planned_bad"] = answer(c.post(url, json={**new, "name": "Плохая", "azimuth": 360.0}))
    out["planned_evaluate"] = answer(c.post(f"{url}/{pid}/evaluate", json={"model_id": mid, "set_id": w.set_id}))
    out["planned_evaluate_set"] = answer(c.post(f"{url}/{aim.json()['id']}/evaluate", json={"set_id": w.set_id}))
    loose = c.post(url, json={"name": "Без набора", "collar": [210.0, 210.0, 330.0], "azimuth": 0.0, "dip": -90.0,
                              "length": 50.0})  # fmt: skip
    out["planned_evaluate_bad"] = answer(c.post(f"{url}/{loose.json()['id']}/evaluate", json={}))  # nothing to ask
    c.delete(f"{url}/{loose.json()['id']}")
    names = c.get(f"/api/v1/viz/sets/{w.set_id}/hole-names").json()["names"]
    out["planned_compare"] = answer(c.post(f"{url}/{pid}/compare", json={"set_id": w.set_id, "hole": names[0]}))
    out["planned_compare_bad"] = answer(c.post(f"{url}/{pid}/compare", json={"set_id": w.set_id, "hole": "нет такой"}))
    with tempfile.TemporaryDirectory() as tmp:
        r = c.post(f"{url}/{pid}/export", json={"format": "csv", "path": tmp})
        out["planned_export_csv"] = {"status": r.status_code, "body": {**r.json(), "files": sorted(Path(f).name for f in
                                     r.json()["files"])}}  # fmt: skip
        out["planned_export_exists"] = answer(c.post(f"{url}/{pid}/export", json={"format": "csv", "path": tmp}))
        r = c.post(f"{url}/{pid}/export", json={"format": "dxf", "path": str(Path(tmp) / "ПС-1.dxf")})
        out["planned_export_dxf"] = {"status": r.status_code, "body": {**r.json(), "files": [Path(f).name for f in
                                     r.json()["files"]]}}  # fmt: skip
    out["planned_delete"] = answer(c.delete(f"{url}/{aim.json()['id']}"))
    back = read_only(c)
    out["planned_read_only"] = answer(c.post(url, json={**new, "name": "Только просмотр"}))
    back()
    c.delete(f"{url}/{pid}")


def norm(x: Any, key: str = "") -> Any:
    """What two runs must agree on: the skipped keys go, the numbers a machine may round are marks, the lines of the
    journal have their numbers cut out."""
    if isinstance(x, dict):
        return {k: norm(v, k) for k, v in x.items() if k not in SKIP}
    if isinstance(x, list):
        return [norm(v, key) for v in x]
    if key in LOOSE and isinstance(x, float | int):
        return "<число>"
    if key == "journal" and isinstance(x, str):
        return re.sub(r"[-+]?\d[\d  .,]*(?:e[-+]?\d+)?", "#", x)
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
    if isinstance(a, float | int) and isinstance(b, float | int) and not isinstance(a, bool) and a is not None:
        return None if math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9) else f"{path}: {a!r} != {b!r}"
    return None if a == b else f"{path}: {str(a)[:100]!r} != {str(b)[:100]!r}"


def test_the_answers_are_what_the_core_answers(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = build(im)
    old = json.loads(DATA.read_text(encoding="utf-8"))
    # `defaults` and `bad_param` are written by `spec.py`: the route says the same
    for key in ("defaults", "bad_param"):
        assert built[key] == old[key], key
    if REGEN:
        merged: dict[str, Any] = {"_note": NOTE}
        for k in KEYS:
            merged[k] = built[k] if k in built else old[k]
        merged.update({k: v for k, v in old.items() if k not in merged})  # the samples of the other substeps
        DATA.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return
    for key in MINE:
        assert key in old, f"{key}: no answer in the file (BF_REGEN_IMPLICIT=1 uv run pytest -q {Path(__file__).name})"
        d = diff(norm(built[key]), norm(old[key]), key)
        assert d is None, d
    for key, got in built.items():  # every refusal has its text, its hint and, where the window shows it, `param`
        if got["status"] >= 400:
            assert got["body"]["code"] and got["body"]["message"] and got["body"]["hint"], key
    assert old["bad_param"]["body"]["param"] == "pou.n_min" and built["name_taken"]["body"]["param"] == "name"
    assert built["build_failed"]["body"]["error"]["code"] == "no_contacts"
    assert built["model_error"]["body"]["state"] == "error" and built["model_error"]["body"]["error"]["group"] == 2
    conflict = built["delete_conflict"]
    assert conflict["status"] == 409 and conflict["body"]["usage"][0]["role"] == "output"
