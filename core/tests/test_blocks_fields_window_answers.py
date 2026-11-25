"""The answers of the core the window «Поля блочной модели» is read against (подшаг 17.36; ADR 0023 §7, §9,
decisions 10 and 13; e17-api.md §8, §9, «Допущения» Э17 17.9, 17.26, 17.29): the calculator (`POST /blocks/calc/check`,
`POST /blocks/{id}/calc` with the job `blocks.calc`), the density (`/density`), the categories (`/classify`,
`GET/PUT /blocks/schemes`) and the properties of the fields (`GET /blocks/{id}/fields`, `PATCH`, `DELETE`,
`…/rebuild`) through the real routes; the jobs are followed to their end (`status`, `message`, `result`); the refusals
are the whole body of the answer (422 with `pos` / `param`, 409 `field_exists` / `version_conflict` / `source_lost`,
423).

The file `tests/data/blocks/fields_window_answers.json` is read by vitest (`ui/test/blocks-fields-*.test.ts`).
Regenerate: `BF_REGEN_BLOCKS_FIELDS=1 uv run pytest -q tests/test_blocks_fields_window_answers.py`. What changes from
run to run (the numbers of the jobs, the times, the seconds of the stages) is not stored; the floats are compared to 9
digits."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects, settings
from tests.test_archive import wait_job
from tests.test_blocks_fill_jobs import P, body
from tests.test_blocks_report import API, BL, put, rc  # noqa: F401

DATA = Path(__file__).parent / "data" / "blocks" / "fields_window_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_FIELDS") == "1"
FL = API + "/filters"
SC = BL + "/schemes"
SKIPPED = ("created_at", "updated_at", "size", "at", "stages", "seconds")  # the times, the size of the file, the stages


def answer(r: Any) -> dict[str, Any]:
    """The status and the body of an answer (the body is the whole answer of the core)."""
    return {"status": r.status_code, "body": r.json()}


def version(c: Any, mid: int) -> int:
    return int(c.get(f"{BL}/{mid}").json()["version"])


def job(c: Any, r: Any, request: dict[str, Any]) -> dict[str, Any]:
    """A route that starts a job: the refusal as it is, or the job followed to its end (what the window reads)."""
    if r.status_code != 200:
        return {"request": request, **answer(r)}
    started = r.json()
    assert sorted(started) == ["id", "source"] and isinstance(started["id"], int)
    done = wait_job(c, started["source"], started["id"], timeout=120)
    started_as = {"keys": sorted(started), "source": type(started["source"]).__name__}  # the project id varies
    keys = ("kind", "title", "queue", "status", "message", "error", "result")
    return {"request": request, "status": 200, "started": started_as, "job": {k: done.get(k) for k in keys}}


def calc(c: Any, mid: int, **body: Any) -> dict[str, Any]:
    request = {"expected_version": version(c, mid), **body}
    return job(c, c.post(f"{BL}/{mid}/calc", json=request), request)


def check(c: Any, mid: int, expr: str, **extra: Any) -> dict[str, Any]:
    request = {"model_id": mid, "expr": expr, **extra}
    return {"request": request, **answer(c.post(f"{BL}/calc/check", json=request))}


def density(c: Any, mid: int, **body: Any) -> dict[str, Any]:
    request = {"expected_version": version(c, mid), **body}
    return job(c, c.post(f"{BL}/{mid}/density", json=request), request)


def classify(c: Any, mid: int, **body: Any) -> dict[str, Any]:
    request = {"expected_version": version(c, mid), **body}
    return job(c, c.post(f"{BL}/{mid}/classify", json=request), request)


def model(c: Any) -> int:
    """24 parents (4 × 3 × 2): grades with gaps (a zero, two empty), a category with an empty value, a density with
    gaps, the fields of the example JORC, two fields of the share (`FR`, `FR2`)."""
    n = 24
    i = np.arange(n)
    au = np.array([0.2, 0.5, 1.2, 2.0, np.nan, 0.0, 3.5, 1.1, 0.8, 2.5, 1.9, 0.4,
                   5.0, 0.9, 1.5, np.nan, 2.2, 0.3, 1.0, 4.1, 0.6, 1.7, 2.8, 0.1])  # fmt: skip
    dom = (i % 3).astype(np.int32)
    dom[5] = -1
    sg = 2.2 + 0.05 * i
    sg[[3, 10]] = np.nan
    cols = {
        "AU": au, "CU": np.linspace(0.1, 2.4, n), "DOM": dom, "SG": sg,
        "FR": i / 23.0, "FR2": 1.0 - i / 23.0,
        "PASS": (i % 3 + 1).astype(np.float64), "SLOPE": 0.4 + (i % 6) * 0.15,
        "NH": (i % 5).astype(np.float64), "AD": 10.0 + (i % 4) * 8.0,
    }  # fmt: skip
    fields: list[dict[str, Any]] = [
        {"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
        {"name": "CU", "dtype": "f8", "unit": "%", "role": "grade"},
        {"name": "DOM", "dtype": "cat", "labels": ["Руда", "Порода", "Окисл."]},
        {"name": "SG", "dtype": "f8", "unit": "т/м³"},
        {"name": "FR", "dtype": "f8"},
        {"name": "FR2", "dtype": "f8"},
        {"name": "PASS", "dtype": "f8"},
        {"name": "SLOPE", "dtype": "f8"},
        {"name": "NH", "dtype": "f8"},
        {"name": "AD", "dtype": "f8"},
    ]  # fmt: skip
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [10.0, 10.0, 5.0]}
    return put(c, grid, fields, i.astype(np.uint64), cols, name="Месторождение")


def build(c: Any) -> dict[str, Any]:
    mid = model(c)
    p = P(c.project.engine, c.project.folder)
    flt = answer(c.post(FL, json={"name": "Руда", "rules": {"cond": {"field": "DOM", "op": "=", "value": "Руда"}}}))
    assert flt["status"] == 200, flt
    fid = flt["body"]["id"]
    quick = {"cond": {"field": "CU", "op": ">", "value": 0.5}}
    out: dict[str, Any] = {"ids": {"model": mid, "filter": fid}, "filters": answer(c.get(FL))}
    out["blocks"] = answer(c.get(BL))
    out["model_before"] = answer(c.get(f"{BL}/{mid}"))
    out["fields_before"] = answer(c.get(f"{BL}/{mid}/fields"))

    out["check"] = {
        "number": check(c, mid, "AU * 2 + CU"),
        "bool": check(c, mid, "AU > 1 AND DOM = 'Руда'"),
        "category": check(c, mid, "IF(AU > 1, 'богатая', 'бедная')"),
        "bracket": check(c, mid, "[AU] + 1"),
        "dtype_i2": check(c, mid, "ROUND(AU, 0)", dtype="i2"),
        "syntax": check(c, mid, "AU +"),
        "syntax_import": check(c, mid, "__import__('os')"),
        "syntax_second_line": check(c, mid, "IF(AU > 1,\n AU +, 0)"),
        "field": check(c, mid, "AU + NOPE"),
        "type_text": check(c, mid, "AU + 'a'"),
        "dtype_cat_from_number": check(c, mid, "AU * 2", dtype="cat"),
        "dtype_number_from_text": check(c, mid, "IF(AU > 1, 'a', 'b')", dtype="f8"),
        "rule_ok": check(c, mid, "AU >= 1"),
        "rule_jorc": check(c, mid, "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 25"),
        "rule_field": check(c, mid, "NOPE >= 1"),
        "empty": check(c, mid, "  "),
        "dtype_unknown": check(c, mid, "AU", dtype="f16"),
        "model_missing": {
            "request": {"model_id": 99999, "expr": "AU"},
            **answer(c.post(f"{BL}/calc/check", json={"model_id": 99999, "expr": "AU"})),
        },  # fmt: skip
    }

    out["calc"] = {
        "new": calc(
            c, mid, expr="IF(AU > 1, AU * 2, 0)", field="AU2", dtype="f8", unit="г/т", description="Удвоенное AU"
        ),
        "exists": calc(c, mid, expr="IF(AU > 1, AU * 2, 0)", field="AU2", dtype="f8"),
        "replace": calc(c, mid, expr="IF(AU > 1, AU * 3, 0)", field="AU2", dtype="f8", replace=True),
        "category": calc(c, mid, expr="IF(AU > 1, 'богатая', 'бедная')", field="GRADE", dtype="cat"),
        "bool_i1": calc(c, mid, expr="AU > 1 AND DOM = 'Руда'", field="ORE", dtype="i1"),
        "round_i2": calc(c, mid, expr="ROUND(AU * 10, 0)", field="AU10", dtype="i2"),
        "outside_nan": calc(c, mid, expr="AU + 1", field="AU_N", restrict=quick, outside="nan"),
        "outside_keep": calc(c, mid, expr="AU + 1", field="AU_K", restrict=quick, outside="keep"),
        "with_filter": calc(c, mid, expr="CU * 10", field="CU10", filter_id=fid, restrict=quick, outside="nan"),
        "conflict": {
            "request": {"expected_version": 1, "expr": "AU", "field": "AU_C"},
            **answer(c.post(f"{BL}/{mid}/calc", json={"expected_version": 1, "expr": "AU", "field": "AU_C"})),
        },  # fmt: skip
        "bad_syntax": calc(c, mid, expr="AU +", field="AU_C"),
        "bad_field": calc(c, mid, expr="AU + NOPE", field="AU_C"),
        "bad_type": calc(c, mid, expr="AU * 2", field="AU_C", dtype="cat"),
        "bad_restrict_field": calc(
            c, mid, expr="AU", field="AU_C", restrict={"cond": {"field": "ZZZ", "op": ">", "value": 1}}
        ),
        "bad_filter": calc(c, mid, expr="AU", field="AU_C", filter_id=99999),
        "bad_outside": calc(c, mid, expr="AU", field="AU_C", outside="zero"),
        "bad_name": calc(c, mid, expr="AU", field=" "),
        "bad_dtype": calc(c, mid, expr="AU", field="AU_C", dtype="f16"),
        "model_missing": job(
            c,
            c.post(f"{BL}/99999/calc", json={"expected_version": 1, "expr": "AU", "field": "X"}),
            {"expected_version": 1, "expr": "AU", "field": "X"},
        ),  # fmt: skip
    }

    out["density"] = {
        "constant": density(c, mid, method="constant", value=2.7, default=None, out="DENS", src_field="DENS_SRC"),
        "field": density(c, mid, method="field", field="SG", default=2.5, out="DENS_F", src_field="DENS_F_SRC"),
        "table": density(
            c,
            mid,
            method="table",
            table={
                "field": "DOM",
                "default": 2.4,
                "values": [{"value": "Руда", "density": 3.1}, {"value": "Порода", "density": 2.6}],
            },
            default=None,
            out="DENS_T",
            src_field="DENS_T_SRC",
            restrict=quick,
        ),  # fmt: skip
        "formula": density(
            c, mid, method="formula", expr="2.5 + CU / 10", default=2.5, out="DENS_E", src_field="DENS_E_SRC"
        ),
        "no_default": density(c, mid, method="field", field="SG", default=None, out="DENS_G", src_field="DENS_G_SRC"),
        "exists": density(c, mid, method="constant", value=2.7, out="DENS", src_field="DENS_SRC"),
        "replace": density(c, mid, method="constant", value=2.8, out="DENS", src_field="DENS_SRC", replace=True),
        "invalid": density(c, mid, method="field", field="AU", default=2.5, out="DENS_X", src_field="DENS_X_SRC"),
        "field_missing": density(c, mid, method="field", field="NOPE", out="DENS_X", src_field="DENS_X_SRC"),
        "field_category": density(c, mid, method="field", field="DOM", out="DENS_X", src_field="DENS_X_SRC"),
        "bad_method": density(c, mid, method="weird", out="DENS_X", src_field="DENS_X_SRC"),
        "no_value": density(c, mid, method="constant", out="DENS_X", src_field="DENS_X_SRC"),
        "bad_default": density(c, mid, method="constant", value=2.7, default=-1, out="DENS_X", src_field="DENS_X_SRC"),
        "same_names": density(c, mid, method="constant", value=2.7, out="D", src_field="d"),
        "bad_formula": density(c, mid, method="formula", expr="AU +", out="DENS_X", src_field="DENS_X_SRC"),
        "table_empty": density(
            c, mid, method="table", table={"field": "DOM", "values": []}, out="DENS_X", src_field="DENS_X_SRC"
        ),
    }

    out["schemes"] = {"list": answer(c.get(SC))}
    rules = [{"label": "Руда", "expr": "AU >= 1"}, {"label": "Бедная", "expr": "AU >= 0"}]
    sch = out["schemes"]
    sch["put_new"] = {
        "request": {"name": "Моя", "rules": rules},
        **answer(c.put(f"{SC}/my", json={"name": "Моя", "rules": rules})),
    }
    jorc = next(s for s in sch["list"]["body"]["schemes"] if s["id"] == "jorc")
    changed = [{**r} for r in jorc["rules"]]
    changed[0]["expr"] = "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 30"
    req = {"expected_version": jorc["version"], "name": "JORC", "rules": changed}
    sch["put_jorc_changed"] = {"request": req, **answer(c.put(f"{SC}/jorc", json=req))}
    req = {"expected_version": jorc["version"], "name": "JORC", "rules": jorc["rules"]}
    sch["put_conflict"] = {"request": req, **answer(c.put(f"{SC}/jorc", json=req))}
    cur = next(s for s in c.get(SC).json()["schemes"] if s["id"] == "jorc")
    req = {"expected_version": cur["version"], "name": "JORC", "rules": jorc["rules"]}
    sch["put_jorc_back"] = {"request": req, **answer(c.put(f"{SC}/jorc", json=req))}
    bad: dict[str, tuple[str, dict[str, Any]]] = {
        "bad_id": ("моя схема", {"name": "X", "rules": rules}),
        "no_rules": ("empty", {"name": "X", "rules": []}),
        "syntax": ("syn", {"name": "X", "rules": [{"label": "A", "expr": "AU >="}]}),
        "label_twice": (
            "twice",
            {"name": "X", "rules": [{"label": "A", "expr": "AU > 1"}, {"label": "a", "expr": "AU > 0"}]},
        ),
        "no_version": ("jorc", {"name": "JORC", "rules": jorc["rules"]}),
        "no_name": ("noname", {"name": " ", "rules": rules}),
    }
    for k, (sid, req) in bad.items():
        sch[k] = {"request": req, "id": sid, **answer(c.put(f"{SC}/{sid}", json=req))}
    sch["list_after"] = answer(c.get(SC))

    wid = body(p, (0.0, 0.0, 0.0), (20.0, 30.0, 10.0), name="Зона богатых")
    out["ids"]["wireframe"] = wid
    out["classify"] = {
        "jorc": classify(c, mid, scheme="jorc", estimate="AU", out="CLASS", overrides=[], majority=0),
        "jorc_majority": classify(c, mid, scheme="jorc", estimate="AU", out="CLASS_M", majority=1),
        "custom": classify(c, mid, scheme="my", out="K", filter_id=fid),
        "rules": classify(c, mid, rules=rules, out="K2", estimate="AU", restrict=quick),
        "overrides": classify(c, mid, rules=rules, out="K3", overrides=[{"wireframe_id": wid, "label": "Бедная"}]),
        "exists": classify(c, mid, rules=rules, out="K2"),
        "gkz": classify(c, mid, scheme="gkz", out="G"),
        "scheme_missing": classify(c, mid, scheme="nope", out="G"),
        "neither": classify(c, mid, out="G"),
        "both": classify(c, mid, scheme="my", rules=rules, out="G"),
        "estimate_missing": classify(c, mid, scheme="my", estimate="NOPE", out="G"),
        "rule_field_missing": classify(c, mid, rules=[{"label": "A", "expr": "NOPE > 1"}], out="G"),
        "override_label": classify(
            c, mid, rules=rules, out="G", overrides=[{"wireframe_id": wid, "label": "Нет такой"}]
        ),
        "override_wireframe": classify(
            c, mid, rules=rules, out="G", overrides=[{"wireframe_id": 99999, "label": "Руда"}]
        ),
        "majority_bad": classify(c, mid, rules=rules, out="G", majority=11),
    }

    # the properties of the fields: the list with the sources, then the edits, the stale field, the rebuild
    fl = out["fields"] = {"list": answer(c.get(f"{BL}/{mid}/fields"))}

    def patch(field_name: str, **kw: Any) -> dict[str, Any]:
        req = {"expected_version": version(c, mid), **kw}
        return {"request": req, "name": field_name, **answer(c.patch(f"{BL}/{mid}/fields/{field_name}", json=req))}

    fl["patch_text"] = patch("CU", unit="проц.", description="Медь")
    fl["patch_clear"] = patch("CU", unit=None, description="")
    fl["patch_role"] = patch("FR", role="frac")
    fl["patch_role_density"] = patch("SG", role="density")
    fl["patch_role_clear"] = patch("SG", role=None)
    fl["frac_twice"] = patch("FR2", role="frac")
    fl["role_invalid"] = patch("AU", role="class")
    fl["role_unknown"] = patch("AU", role="weird")
    fl["rename"] = patch("AU10", name="AU_10")
    fl["nothing"] = patch("AU10")
    fl["not_found"] = patch("NOPE", unit="г/т")
    fl["unit_long"] = patch("AU10", unit="я" * 41)
    req = {"expected_version": 1, "unit": "х"}
    fl["conflict"] = {"request": req, "name": "AU10", **answer(c.patch(f"{BL}/{mid}/fields/AU10", json=req))}

    def delete(name: str, v: int | None = None) -> dict[str, Any]:
        v = version(c, mid) if v is None else v
        return {
            "name": name,
            "expected_version": v,
            **answer(c.delete(f"{BL}/{mid}/fields/{name}", params={"expected_version": v})),
        }

    fl["delete_in_use"] = delete("AU")
    fl["delete_required"] = delete("FR")
    fl["delete_conflict"] = delete("AU10", 1)
    fl["delete_not_found"] = delete("NOPE")
    fl["delete_ok"] = delete("AU_K")

    # the views of the project that name a field: «Где используется» lists them, the deletion is refused
    style = {"colour": {"field": "AU10"}, "quick": {"items": [{"field": "AU10", "op": ">", "value": 1}]}}
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.insert(settings).values(scope="view", key="block_styles", value={str(mid): style}, version=1))
        now = dialect.utcnow()
        saved = {"keys": {"block_styles": {str(mid): {"colour": {"field": "AU10"}}}}}
        conn.execute(
            sa.insert(objects).values(
                kind="view", name="Мой вид", name_key="мой вид", meta=saved, sort_key=0.0, state="ready", version=1,
                created_at=now, updated_at=now,
            )
        )  # fmt: skip
    fl["list_views"] = answer(c.get(f"{BL}/{mid}/fields"))
    fl["delete_used_by_view"] = delete("AU10")
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.delete(settings).where(settings.c.key == "block_styles"))
        conn.execute(sa.delete(objects).where(objects.c.kind == "view"))

    # AU is replaced: AU2 (read it) and the others that read it are «устарел»
    out["calc"]["replace_au"] = calc(c, mid, expr="AU + 0", field="AU", dtype="f8", unit="г/т", replace=True)
    fl["list_stale"] = answer(c.get(f"{BL}/{mid}/fields"))

    def rebuild(name: str, v: int | None = None) -> dict[str, Any]:
        req = {"expected_version": version(c, mid) if v is None else v}
        return {"name": name, **job(c, c.post(f"{BL}/{mid}/fields/{name}/rebuild", json=req), req)}

    fl["rebuild"] = rebuild("AU2")
    fl["rebuild_density"] = rebuild("DENS_F")
    fl["rebuild_conflict"] = rebuild("AU2", 1)
    fl["not_rebuildable"] = rebuild("SLOPE")
    fl["rebuild_not_found"] = rebuild("NOPE")
    fl["list_rebuilt"] = answer(c.get(f"{BL}/{mid}/fields"))
    # the filter a field read is deleted: nothing to make it from
    gone = answer(c.delete(f"{FL}/{fid}"))
    fl["filter_delete"] = gone
    fl["rebuild_source_lost"] = rebuild("CU10")

    # a project open for reading: the reads work, every write is 423
    c.project.read_only = True
    try:
        ro = out["read_only"] = {"fields": answer(c.get(f"{BL}/{mid}/fields")), "schemes": answer(c.get(SC)),
                                 "check": check(c, mid, "AU + 1")}  # fmt: skip
        v = version(c, mid)
        post = {"expected_version": v, "expr": "AU", "field": "X"}
        ro["calc"] = answer(c.post(f"{BL}/{mid}/calc", json=post))
        ro["density"] = answer(
            c.post(f"{BL}/{mid}/density", json={"expected_version": v, "method": "constant", "value": 2.7})
        )
        ro["classify"] = answer(c.post(f"{BL}/{mid}/classify", json={"expected_version": v, "scheme": "my"}))
        ro["patch"] = answer(c.patch(f"{BL}/{mid}/fields/AU", json={"expected_version": v, "unit": "г/т"}))
        ro["delete"] = answer(c.delete(f"{BL}/{mid}/fields/AU10", params={"expected_version": v}))
        ro["rebuild"] = answer(c.post(f"{BL}/{mid}/fields/AU2/rebuild", json={"expected_version": v}))
        ro["scheme"] = answer(c.put(f"{SC}/my", json={"expected_version": 2, "name": "Моя", "rules": rules}))
    finally:
        c.project.read_only = False
    out["model_after"] = answer(c.get(f"{BL}/{mid}"))
    out["rules"] = "ответы настоящего ядра через маршруты блочных моделей (test_blocks_fields_window_answers.py)"
    return out


def masked(x: Any) -> Any:
    """The floats to 9 significant digits (the same run gives the same bits; another machine may differ in the last)."""
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items() if k not in SKIPPED}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    if isinstance(x, str) and x.startswith("fld:"):
        return "fld:"  # the file of a field is named by its sha (the times of the file vary)
    return x


def first_diff(a: Any, b: Any, path: str = "") -> str | None:
    """The path of the first difference of two answers (for the message of a failed comparison)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            d = first_diff(a.get(k), b.get(k), f"{path}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return next(
            (d for i, (x, y) in enumerate(zip(a, b, strict=True)) if (d := first_diff(x, y, f"{path}/{i}"))), None
        )
    return None if a == b else f"{path}: {str(a)[:120]!r} != {str(b)[:120]!r}"


def numbers(x: Any) -> Any:
    if isinstance(x, dict):
        for v in x.values():
            yield from numbers(v)
    elif isinstance(x, list):
        for v in x:
            yield from numbers(v)
    elif isinstance(x, float):
        yield x


def refused(case: dict[str, Any], status: int, code: str, param: str | None = None) -> dict[str, Any]:
    """A refusal before the job (the status and the body) or the end of a job that failed (`job.error`; status 0)."""
    if case["status"] == 200:
        assert case["job"]["status"] == "failed", case
        e = case["job"]["error"]
        assert status == 0 and e["code"] == code, case
    else:
        assert (case["status"], case["body"]["code"]) == (status, code), case
        e = case["body"]
    if param is not None:
        assert e.get("param") == param, case
    assert e["message"] and e["hint"] and any("а" <= ch.lower() <= "я" for ch in e["message"]), e
    return dict(e)


def done(case: dict[str, Any]) -> dict[str, Any]:
    assert case["status"] == 200 and case["job"]["status"] == "done", case
    return dict(case["job"]["result"])


def test_fields_window_answers_are_what_the_core_answers(rc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps(build(rc), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    a = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(a) == masked(built), first_diff(masked(a), masked(built))
    # what the window relies on: the calculator
    ck = a["check"]
    assert ck["number"]["body"] == {
        "text": "AU * 2 + CU",
        "fields_used": ["AU", "CU"],
        "out_kind": "number",
        "dtype": "f8",
    }
    assert ck["bool"]["body"]["out_kind"] == "bool" and ck["bool"]["body"]["dtype"] == "i1"
    assert ck["category"]["body"]["out_kind"] == "category" and ck["category"]["body"]["dtype"] == "cat"
    assert ck["dtype_i2"]["body"]["dtype"] == "i2"
    assert ck["rule_ok"]["body"]["out_kind"] == "bool" and ck["rule_jorc"]["body"]["out_kind"] == "bool"
    assert ck["rule_field"]["body"]["code"] == "expr_field"  # the rules of a scheme are checked as expressions
    for k, code in (("syntax", "expr_syntax"), ("syntax_import", "expr_syntax"), ("syntax_second_line", "expr_syntax"),
                    ("field", "expr_field"), ("type_text", "expr_type"), ("dtype_cat_from_number", "expr_type"),
                    ("dtype_number_from_text", "expr_type")):  # fmt: skip
        e = refused(ck[k], 422, code)
        assert isinstance(e["pos"], int) and e["param"] == "expr", (k, e)
    assert ck["field"]["body"]["field"] == "NOPE" and ck["syntax_second_line"]["body"]["line"] == 2
    refused(ck["empty"], 422, "bad_param", "expr")
    refused(ck["dtype_unknown"], 422, "bad_param", "dtype")
    refused(ck["model_missing"], 404, "block_model_not_found")
    c = a["calc"]
    new = done(c["new"])
    assert new["field"] == "AU2" and new["rows"] == 24 and new["dtype"] == "f8" and "stages" in new
    assert refused(c["exists"], 409, "field_exists")["field"] == "AU2"
    assert done(c["replace"])["field"] == "AU2" and done(c["category"])["dtype"] == "cat"
    assert done(c["bool_i1"])["dtype"] == "i1" and done(c["round_i2"])["dtype"] == "i2"
    assert done(c["outside_nan"])["passed"] < 24 and done(c["outside_keep"])["passed"] < 24
    assert done(c["with_filter"])["missing"] >= 0
    assert refused(c["conflict"], 409, "version_conflict")["version"] > 1
    for k, code in (("bad_syntax", "expr_syntax"), ("bad_field", "expr_field"), ("bad_type", "expr_type")):
        assert isinstance(refused(c[k], 422, code)["pos"], int)
    refused(c["bad_restrict_field"], 422, "filter_field_missing")
    refused(c["bad_filter"], 404, "filter_not_found")
    refused(c["bad_outside"], 422, "bad_param", "outside")
    refused(c["bad_name"], 422, "bad_param", "field")
    refused(c["bad_dtype"], 422, "bad_param", "dtype")
    refused(c["model_missing"], 404, "block_model_not_found")
    # the density
    d = a["density"]
    for k in ("constant", "field", "table", "formula", "no_default", "replace"):
        r = done(d[k])
        assert r["fields"] == [d[k]["request"]["out"], d[k]["request"]["src_field"]] and r["rows"] == 24, k
    assert done(d["field"])["defaulted"] == 2 and done(d["no_default"])["warnings"]
    assert refused(d["exists"], 409, "field_exists")["field"] == "DENS"
    refused(d["invalid"], 0, "density_invalid")
    refused(d["field_missing"], 422, "density_field_missing", "field")
    refused(d["field_category"], 422, "bad_param", "field")
    refused(d["bad_method"], 422, "bad_param", "method")
    refused(d["no_value"], 422, "bad_param", "value")
    refused(d["bad_default"], 422, "bad_param", "default")
    refused(d["same_names"], 422, "bad_param", "src_field")
    refused(d["bad_formula"], 422, "expr_syntax")
    refused(d["table_empty"], 422, "bad_param", "table.values")
    # the categories and the schemes
    s = a["schemes"]
    assert [x["id"] for x in s["list"]["body"]["schemes"]][:2] == ["jorc", "gkz"]
    assert s["put_new"]["status"] == 200 and s["put_new"]["body"]["id"] == "my"
    assert s["put_jorc_changed"]["status"] == 200 and s["put_jorc_changed"]["body"]["version"] == 2
    assert refused(s["put_conflict"], 409, "version_conflict")["version"] == 2
    assert s["put_jorc_back"]["status"] == 200
    refused(s["bad_id"], 422, "bad_param", "id")
    refused(s["no_rules"], 422, "bad_param", "rules")
    assert refused(s["syntax"], 422, "expr_syntax")["param"] == "rules.0.expr"
    refused(s["label_twice"], 422, "bad_param")
    refused(s["no_version"], 409, "version_conflict")
    refused(s["no_name"], 422, "bad_param", "name")
    k = a["classify"]
    jr = done(k["jorc"])
    assert jr["field"] == "CLASS" and sum(jr["counts"].values()) + jr["unclassified"] + jr["no_estimate"] == 24
    assert (
        done(k["jorc_majority"])["field"] == "CLASS_M"
        and done(k["custom"])["counts"]
        and done(k["rules"])["field"] == "K2"
    )
    assert done(k["overrides"])["field"] == "K3"
    assert refused(k["exists"], 409, "field_exists")["field"] == "K2"
    refused(k["gkz"], 422, "scheme_incomplete", "rules.0.expr")
    refused(k["scheme_missing"], 404, "scheme_not_found")
    refused(k["neither"], 422, "bad_param", "rules")
    refused(k["both"], 422, "bad_param", "rules")
    refused(k["estimate_missing"], 422, "classify_field_missing")
    refused(k["rule_field_missing"], 422, "classify_field_missing")
    refused(k["override_label"], 422, "bad_param", "overrides.0.label")
    refused(k["override_wireframe"], 404, "wireframe_not_found", "overrides.0.wireframe_id")
    refused(k["majority_bad"], 422, "bad_param", "majority")
    # the fields
    f = a["fields"]
    listed = {x["name"]: x for x in f["list"]["body"]["fields"]}
    assert listed["AU2"]["source"]["op"] == "calc" and listed["AU2"]["rebuildable"] and not listed["AU"]["rebuildable"]
    assert (
        listed["CLASS"]["role"] == "class"
        and listed["DENS"]["role"] == "density"
        and listed["DENS"]["source"]["op"] == "density"
    )
    assert listed["AU"]["usage"] and listed["AU"]["deletable"] is False
    assert f["patch_text"]["status"] == 200 and f["patch_clear"]["body"]["field"]["unit"] is None
    assert f["patch_role"]["body"]["field"]["role"] == "frac" and f["patch_role_clear"]["body"]["field"]["role"] is None
    refused(f["frac_twice"], 422, "frac_exists")
    refused(f["role_invalid"], 422, "role_invalid")
    refused(f["rename"], 422, "field_rename_unsupported")
    refused(f["nothing"], 422, "bad_param")
    refused(f["not_found"], 404, "block_field_not_found")
    refused(f["unit_long"], 422, "bad_param")
    refused(f["conflict"], 409, "version_conflict")
    assert refused(f["delete_in_use"], 422, "field_in_use")["usage"]
    refused(f["delete_required"], 422, "field_required")
    refused(f["delete_conflict"], 409, "version_conflict")
    refused(f["delete_not_found"], 404, "block_field_not_found")
    assert f["delete_ok"]["status"] == 200 and f["delete_ok"]["body"]["deleted"] == "AU_K"
    views = {x["name"]: x for x in f["list_views"]["body"]["fields"]}["AU10"]["usage"]
    assert {(u["kind"], u["name"], u["role"]) for u in views} == {
        ("view", "Вид проекта · Месторождение", "colour"),
        ("view", "Вид проекта · Месторождение", "restrictor"),
        ("view", "Мой вид", "colour"),
    }
    assert {u["name"] for u in refused(f["delete_used_by_view"], 422, "field_in_use")["usage"]} == {
        "Вид проекта · Месторождение",
        "Мой вид",
    }
    stale = {x["name"]: x for x in f["list_stale"]["body"]["fields"]}
    assert stale["AU2"]["stale"] and stale["AU2"]["stale_reason"] and not stale["CU10"]["stale"]
    assert done(f["rebuild"])["field"] == "AU2" and done(f["rebuild_density"])["fields"] == ["DENS_F", "DENS_F_SRC"]
    refused(f["rebuild_conflict"], 409, "version_conflict")
    refused(f["not_rebuildable"], 422, "not_rebuildable")
    refused(f["rebuild_not_found"], 404, "block_field_not_found")
    assert not {x["name"]: x for x in f["list_rebuilt"]["body"]["fields"]}["AU2"]["stale"]
    assert f["filter_delete"]["status"] == 200
    refused(f["rebuild_source_lost"], 409, "source_lost")
    ro = a["read_only"]
    assert ro["fields"]["status"] == 200 and ro["schemes"]["status"] == 200 and ro["check"]["status"] == 200
    for key in ("calc", "density", "classify", "patch", "delete", "rebuild", "scheme"):
        refused(ro[key], 423, "project_read_only")
    assert all(math.isfinite(v) for v in numbers(a))
