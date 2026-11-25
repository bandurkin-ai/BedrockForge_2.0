"""The routes of the estimation of grades (подшаг 18.13; ADR 0024 §11; docs/progress/e18-api.md §3–§7): the rows and the
plans (CRUD, 404 / 409 / 422 / 423, the order of the checks, the events `geostat.changed`), `GET /estimate/defaults`,
`POST /estimate/run` (refused before the job: `field_exists`, `version_conflict`, `source_lost`; then the real job),
`POST /estimate/preview` (= the kernel, ≤ 100 ms, overtaken requests). The checks — `test_estimate_api_checks.py`.
The routers are included here until the lines of the orchestrator (`tests/estapi_util.py`)."""

from __future__ import annotations

import statistics
import time
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from tests import estapi_util as U
from tests.estapi_util import API, GS, finish, make_row, ok, read_only, refused, row_body

reg, ea = U.reg, U.ea  # the fixtures

E = f"{GS}/estimations"
P = f"{GS}/estimation-plans"


def events(c: Any, kind: str | None = None) -> list[dict[str, Any]]:
    return [d for n, d in c.events if n == "geostat.changed" and (kind is None or d["kind"] == kind)]


# --- the rows --------------------------------------------------------------------------------------------------------


def test_row_crud_and_events(ea) -> None:  # type: ignore[no-untyped-def]
    made = make_row(ea)
    assert made == {"id": made["id"], "name": "AU · OK · окисл", "version": 1}
    assert events(ea, "estimation") == [{"kind": "estimation", "id": made["id"]}]
    got = ok(ea.get(f"{E}/{made['id']}"))
    assert got["meta"]["method"]["kind"] == "ok" and got["meta"]["origin"] and got["stale"] is False
    assert [c["name"] for c in got["columns"]][:2] == ["AU_OK_STATUS", "AU_OK_EST"] and got["fields_exist"] == []
    listed = ok(ea.get(E))["estimations"]
    assert [(r["id"], r["method"], r["prefix"], r["label"], r["plans"]) for r in listed] == [
        (made["id"], "ok", "AU_OK_", "окисл", [])]  # fmt: skip
    assert ok(ea.get(E, params={"model_id": ea.w.model + 1}))["estimations"] == []
    # PUT over the version read: the keys replace, the version moves, the event is published
    put = ok(ea.put(f"{E}/{made['id']}", json={"expected_version": 1, "disc": [3, 3, 1], "name": "Мой OK"}))
    assert put == {"id": made["id"], "name": "Мой OK", "version": 2}
    assert ok(ea.get(f"{E}/{made['id']}"))["meta"]["disc"] == [3, 3, 1]
    assert events(ea, "estimation")[-1] == {"kind": "estimation", "id": made["id"]}
    stale = ea.put(f"{E}/{made['id']}", json={"expected_version": 1, "disc": [1, 1, 1]})
    assert refused(stale, 409, "version_conflict")["version"] == 2
    # the plan holds the row; deleting the row takes it out of the plan and publishes both
    plan = ok(ea.post(P, json={"model_id": ea.w.model, "items": [made["id"]]}))
    assert ok(ea.get(f"{E}/{made['id']}"))["plans"] == [plan["id"]]
    assert ok(ea.delete(f"{E}/{made['id']}")) == {"deleted": made["id"], "plans": [plan["id"]]}
    assert {"kind": "estimation", "id": made["id"], "deleted": True} in events(ea)
    assert {"kind": "estimation_plan", "id": plan["id"]} in events(ea)
    assert ok(ea.get(f"{P}/{plan['id']}"))["items"] == []
    refused(ea.get(f"{E}/{made['id']}"), 404, "estimation_not_found")
    refused(ea.delete(f"{E}/{made['id']}"), 404, "estimation_not_found")


def test_row_refusals(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    made = make_row(ea)
    named = ea.post(E, json=row_body(w, name="AU · OK · окисл"))
    refused(named, 409, "name_taken", "name")
    refused(ea.post(E, json=row_body(w, disc=[1, 1, 11])), 422, "bad_param", "disc.2")
    refused(ea.post(E, json=row_body(w, method={"kind": "ik"})), 422, "bad_param", "method.ik.thresholds")
    refused(ea.post(E, json=row_body(w, model_id=w.model + 99)), 404, "block_model_not_found", "model_id")
    refused(ea.post(E, json=row_body(w, target={"domain_field": "НЕТ", "label": "x"})), 422, "field_not_found",
            "target.domain_field")  # fmt: skip
    refused(ea.post(E, json=row_body(w, target={"domain_field": "ZONE", "label": "золото"})), 422, "domain_unmapped",
            "target.label")  # fmt: skip
    refused(ea.post(E, json=row_body(w, variogram={"model_id": w.vz + 99})), 404, "model_not_found",
            "variogram.model_id")  # fmt: skip
    refused(ea.post(E, json=[1]), 422, "bad_request")
    refused(ea.put(f"{E}/{made['id']}", json={"disc": [1, 1, 1]}), 422, "bad_param", "expected_version")
    refused(ea.put(f"{E}/{made['id'] + 99}", json={"expected_version": 1}), 404, "estimation_not_found")
    refused(ea.put(f"{E}/{made['id']}", json={"expected_version": 1, "disc": [0, 1, 1]}), 422, "bad_param", "disc.0")
    assert ok(ea.get(f"{E}/{made['id']}"))["version"] == 1  # nothing of the refused was written


def test_order_of_the_checks_and_a_read_only_project(ea) -> None:  # type: ignore[no-untyped-def]
    made = make_row(ea)
    back = read_only(ea)
    try:
        # the shape of the body, then the project, then the parameters, then the names and the versions
        refused(ea.post(E, json=[1]), 422, "bad_request")
        refused(ea.post(E, json=row_body(ea.w, disc=[1, 1, 11])), 423, "project_read_only")
        refused(ea.put(f"{E}/{made['id']}", json={"expected_version": 9}), 423, "project_read_only")
        refused(ea.delete(f"{E}/{made['id']}"), 423, "project_read_only")
        refused(ea.post(P, json={"model_id": ea.w.model, "items": []}), 423, "project_read_only")
        refused(ea.post(f"{GS}/estimate/run", json={"estimation_ids": [made["id"]], "expected_version": 1}), 423,
                "project_read_only")  # fmt: skip
        refused(ea.post(f"{GS}/estimate/xval", json={"estimation_id": made["id"]}), 423, "project_read_only")
        # what only reads works
        assert ok(ea.get(E))["estimations"] and ok(ea.get(f"{E}/{made['id']}"))["version"] == 1
        assert ok(ea.get(P))["plans"] == []
        ok(ea.post(f"{GS}/estimate/preview", json={"estimation_id": made["id"], "at": {"ijk": [4, 4, 7]}}))
    finally:
        back()
    assert events(ea, "estimation") == [{"kind": "estimation", "id": made["id"]}]  # nothing was published


def test_no_project_is_409(reg, pclient) -> None:  # type: ignore[no-untyped-def]  # noqa: ARG001
    for r in (pclient.get(E), pclient.get(P), pclient.post(E, json={}), pclient.get(f"{GS}/estimate/defaults"),
              pclient.post(f"{GS}/estimate/preview", json={})):  # fmt: skip
        refused(r, 409, "no_project")


# --- the plans -------------------------------------------------------------------------------------------------------


def test_plans(ea) -> None:  # type: ignore[no-untyped-def]
    a = make_row(ea, "окисл")["id"]
    b = make_row(ea, "перех", samples={"set_id": ea.w.set_id, "table": str(ea.w.a), "field": "AU", "domain": {
        "table": str(ea.w.b), "field": "ZONE", "values": ["перех"]}})["id"]  # fmt: skip
    c = make_row(ea, "перв", name="Другой префикс", outputs={"prefix": "ALT_"}, samples={
        "set_id": ea.w.set_id, "table": str(ea.w.a), "field": "AU", "domain": {
            "table": str(ea.w.b), "field": "ZONE", "values": ["перв"]}})["id"]  # fmt: skip
    plan = ok(ea.post(P, json={"model_id": ea.w.model, "items": [b, a]}))
    assert plan == {"id": plan["id"], "name": "План · Модель оценки", "version": 1}
    got = ok(ea.get(f"{P}/{plan['id']}"))
    assert [(i["id"], i["label"], i["method"], i["prefix"]) for i in got["items"]] == [
        (b, "перех", "ok", "AU_OK_"), (a, "окисл", "ok", "AU_OK_")]  # fmt: skip
    listed = ok(ea.get(P))["plans"]
    assert listed[0]["items"] == [b, a] and ok(ea.get(P, params={"model_id": 0}))["plans"] == []
    put = ok(ea.put(f"{P}/{plan['id']}", json={"expected_version": 1, "items": [a, b, c], "name": "Три домена"}))
    assert put == {"id": plan["id"], "name": "Три домена", "version": 2}
    assert events(ea, "estimation_plan")[-1] == {"kind": "estimation_plan", "id": plan["id"]}
    assert refused(ea.put(f"{P}/{plan['id']}", json={"expected_version": 1}), 409, "version_conflict")["version"] == 2
    refused(ea.put(f"{P}/{plan['id']}", json={"expected_version": 2, "items": [a, a]}), 422, "bad_param", "items.1")
    refused(ea.put(f"{P}/{plan['id']}", json={"expected_version": 2, "items": [a, 9999]}), 404,
            "estimation_not_found", "items.1")  # fmt: skip
    refused(ea.post(P, json={"model_id": ea.w.model, "items": [a], "name": "Три домена"}), 409, "name_taken", "name")
    refused(ea.post(P, json={"model_id": ea.w.model + 9, "items": []}), 404, "block_model_not_found", "model_id")
    refused(ea.post(P, json={"items": []}), 422, "bad_param", "model_id")
    same = ea.post(f"{E}", json=row_body(ea.w, name="Тот же домен"))  # the same prefix and the same domain as `a`
    other = ok(same)["id"]
    refused(ea.post(P, json={"model_id": ea.w.model, "items": [a, other]}), 422, "bad_param", "items.1")
    refused(ea.get(f"{P}/9999"), 404, "plan_not_found")
    assert ok(ea.delete(f"{P}/{plan['id']}")) == {"deleted": plan["id"]}
    assert events(ea, "estimation_plan")[-1] == {"kind": "estimation_plan", "id": plan["id"], "deleted": True}
    assert len(ok(ea.get(E))["estimations"]) == 4  # the rows stay
    refused(ea.delete(f"{P}/{plan['id']}"), 404, "plan_not_found")


# --- the defaults of the wizard -------------------------------------------------------------------------------------


def defaults(c: Any, **q: Any) -> Any:
    w = c.w
    query = {"model_id": w.model, "set_id": w.set_id, "table": str(w.a), "field": "AU", **q}
    return c.get(f"{GS}/estimate/defaults", params=query)


def test_defaults_of_the_wizard(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    d = ok(defaults(ea, block_domain="ZONE", domain_table=str(w.b), domain_field="ZONE", domain_values="окисл;перех",
                    variogram_id=w.vz))  # fmt: skip
    m = d["model"]
    assert (m["id"], m["name"], m["rows"], m["parents"], m["sub"], m["regular"], m["size"]) == (
        w.model, "Модель оценки", 800, 800, False, True, [10.0, 10.0, 5.0])  # fmt: skip
    assert m["domain_fields"] == [{"name": "ZONE", "kind": "category", "labels": ["окисл", "перех", "перв"]}]
    s = d["samples"]
    assert (s["field"], s["unit"], s["weights"], s["table_name"], s["table"]) == ("AU", "г/т", "DECL_W", "Композиты",
                                                                                     str(w.a))  # fmt: skip
    c = s["counts"]
    assert c["rows"] == 500 and c["rows"] == c["missing"] + c["no_position"] + c["outside_domain"] + c["used"]
    assert c["missing"] > 0 and c["no_position"] == 0
    assert [v["value"] for v in s["domain"]["values"]] == ["окисл", "перв", "перех"]
    assert sum(v["n"] for v in s["domain"]["values"]) == 500 - c["missing"]
    chosen = [v for v in s["domain"]["values"] if v["value"] in ("окисл", "перех")]
    assert c["used"] == sum(v["n"] for v in chosen) and s["mean"]["naive"] and s["mean"]["declus"]
    assert s["domain"]["table"] == str(w.b) and all(v["naive"] and v["declus"] for v in chosen)
    assert d["mapping"] == {"окисл": ["окисл"], "перех": ["перех"], "перв": ["перв"]}
    v = d["variogram"]
    assert (v["id"], v["name"], v["scale"], v["share"], v["warnings"]) == (w.vz, "AU · окисл", "z", None, [])
    assert v["snapshot"]["structures"][0]["a1"] == 60.0 and len(v["sha"]) == 64
    assert d["search"] == {"from": "model", "ang1": 0.0, "ang2": 0.0, "ang3": 0.0, "r1": 60.0, "r2": 60.0, "r3": 30.0}
    assert d["params"]["disc"] == [4, 4, 2] and d["params"]["search"]["passes"][0]["n_max"] == 24
    assert set(d["ranges"]) and all({"lo", "hi", "lo_open", "integer", "unit"} <= set(r) for r in d["ranges"].values())
    # no domain, no variogram: the whole table, no mapping, nothing of the model
    plain = ok(defaults(ea, weights=""))
    assert plain["samples"]["weights"] is None and plain["samples"]["domain"] is None and plain["mapping"] is None
    assert plain["variogram"] is None and plain["search"] is None
    assert plain["samples"]["counts"]["used"] == 500 - plain["samples"]["counts"]["missing"]
    assert plain["samples"]["mean"]["declus"] == plain["samples"]["mean"]["naive"]  # without weights: the naive one


def test_defaults_refusals(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    refused(ea.get(f"{GS}/estimate/defaults"), 422, "bad_param", "model_id")
    refused(defaults(ea, model_id=w.model + 9), 404, "block_model_not_found", "model_id")
    refused(defaults(ea, set_id=w.set_id + 9), 404, "set_not_found")
    refused(defaults(ea, field="НЕТ"), 422, "field_not_found")
    refused(defaults(ea, variogram_id=w.vz + 99), 404, "model_not_found", "variogram_id")
    refused(defaults(ea, method="кригинг"), 422, "bad_param", "method")
    refused(defaults(ea, domain_table=str(w.b), domain_field="ZONE", domain_values="окисл", block_domain="НЕТ"), 422,
            "bad_param",
            "block_domain")  # fmt: skip
    refused(defaults(ea, channel="c" * 65), 422, "bad_param", "channel")


def test_defaults_of_a_variogram_model_in_normal_scores(ea) -> None:  # type: ignore[no-untyped-def]
    from tests.estutil import BACK, vmodel

    w = ea.w
    y = vmodel(w, "AU · Y", transform="nscore")  # no back transform: refused (Д-38)
    err = refused(defaults(ea, variogram_id=y), 422, "variogram_scale", "variogram_id")
    assert err["message"] == "Модель построена в нормальных оценках"
    z = vmodel(w, "AU · Y назад", transform="nscore", back=BACK)
    v = ok(defaults(ea, variogram_id=z))["variogram"]
    assert v["scale"] == "back" and v["share"] == 0.97 and v["warnings"] == []
    low = vmodel(w, "AU · Y 0,9", transform="nscore", back={**BACK, "share": 0.9})
    assert ok(defaults(ea, variogram_id=low))["variogram"]["warnings"] == [
        "Доля Эрмита 0,9 < 0,95 — обратное преобразование приближённое."]  # fmt: skip
    # the model of another domain
    other = ok(defaults(ea, variogram_id=w.vz, domain_table=str(w.b), domain_field="ZONE", domain_values="перв"))
    assert other["variogram"]["warnings"] in ([], ["Модель построена по другому домену."])


def test_defaults_are_overtaken_by_a_newer_request(ea) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks import viz_spec as sp

    tok = sp.GATE.enter("wizard", 9)  # a newer request of the channel is running
    try:
        refused(defaults(ea, channel="wizard", seq=1), 409, "superseded")
        assert ok(defaults(ea, channel="other", seq=1))["model"]["rows"] == 800
    finally:
        sp.GATE.leave(tok)


# --- the preview ----------------------------------------------------------------------------------------------------


def preview(c: Any, **body: Any) -> Any:
    return c.post(f"{GS}/estimate/preview", json=body)


def test_preview_is_the_kernel_and_is_fast(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    row = make_row(ea)["id"]
    body = row_body(w)
    one = ok(preview(ea, estimation=body, at={"ijk": [4, 4, 7]}))
    assert one["at"]["ijk"] == [4, 4, 7] and one["label"] == "окисл" and one["status"] == 0 and one["pass"] == 1
    assert one["values"]["EST"] > 0 and one["values"]["KV"] >= 0 and one["values"]["NS"] > 0 and one["mu"] is not None
    assert one["counts"]["selected"] == one["values"]["NS"] <= one["counts"]["candidates"]
    assert one["size"] == [10.0, 10.0, 5.0] and one["ellipsoid"]["centre"] == one["centre"]
    n_sel = one["counts"]["selected"]
    assert one["ellipsoid"]["r1"] == 60.0 and one["passes"][0] == {"pass": 1, "ok": True, "n": n_sel, "reason": None}
    picked = [s for s in one["samples"] if s["reason"] == "selected"]
    assert len(picked) == one["counts"]["selected"] and all(s["lambda"] is not None for s in picked)
    assert abs(sum(s["lambda"] for s in picked) - 1.0) < 1e-9  # ordinary kriging: the weights sum to 1
    assert all(s["lambda"] is None for s in one["samples"] if s["reason"] != "selected")
    # the saved row gives the same block; the same block by its row and by a point
    saved = ok(preview(ea, estimation_id=row, at={"ijk": [4, 4, 7]}))
    assert saved["values"] == one["values"] and saved["origin"] == one["origin"]
    by_row = ok(preview(ea, estimation_id=row, at={"row": one["at"]["row"]}))
    by_xyz = ok(preview(ea, estimation_id=row, at={"xyz": one["centre"]}))
    assert by_row["values"] == one["values"] == by_xyz["values"] and by_xyz["at"]["row"] == one["at"]["row"]
    # a block of another domain is outside (Д-27): STATUS 1, nothing of the values is 0
    out = ok(preview(ea, estimation_id=row, at={"ijk": [4, 4, 0]}))
    assert out["status"] == 1 and out["pass"] == 0 and set(out["values"].values()) == {None} and out["samples"] == []
    # the kernel itself, bit for bit
    from bf_core.geostat.estimate import kernel as kn
    from bf_core.geostat.estimate import preview as pv
    from bf_core.geostat.estimate import samples as sm
    from bf_core.geostat.estimate.kna_blocks import Blocks
    from bf_core.storage.blobs import BlobStore

    with dialect.read_tx(ea.project.engine) as conn:
        spec, origin = pv.row_of(conn, row)
        bl = Blocks.open(conn, BlobStore(ea.project.folder), spec)
    rk = kn.compile_row(spec, sm.load(ea.project, spec, origin=origin), rotation_deg=bl.rotation)
    b = bl.place({"ijk": [4, 4, 7]})
    d = kn.estimate_block(rk, b.centre, b.size, active=b.active)
    assert one["values"]["EST"] == d.values["EST"] and one["values"]["KV"] == d.values["KV"]
    assert np.allclose(np.sort([s["lambda"] for s in picked]), np.sort(d.lam), rtol=0, atol=0)
    # ≤ 100 мс at the next block of the same parameters (the cache of the compiled row)
    times = []
    for k in range(12):
        t0 = time.perf_counter()
        ok(preview(ea, estimation_id=row, at={"ijk": [k % 10, (k * 3) % 10, 7 - k % 2]}))
        times.append(time.perf_counter() - t0)
    assert statistics.median(times) <= 0.1, times
    print(f"preview median {statistics.median(times) * 1000:.1f} ms, max {max(times) * 1000:.1f} ms")


def test_preview_refusals(ea) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks import viz_spec as sp

    row = make_row(ea)["id"]
    at = {"ijk": [1, 1, 7]}
    refused(preview(ea, at=at), 422, "bad_param", "estimation")
    refused(preview(ea, estimation=row_body(ea.w), estimation_id=row, at=at), 422, "bad_param", "estimation")
    refused(preview(ea, estimation_id=row), 422, "bad_param", "at")
    refused(preview(ea, estimation_id=row, at={"ijk": [1, 1]}), 422, "bad_param", "at.ijk")
    refused(preview(ea, estimation_id=row, at={"ijk": [1, 1, 99]}), 422, "bad_param", "at.ijk.2")
    refused(preview(ea, estimation_id=row, at={"row": 10_000}), 422, "bad_param", "at.row")
    refused(preview(ea, estimation_id=row, at={"xyz": [0.0, 0.0, 0.0]}), 422, "kna_block_missing", "at.xyz")
    refused(preview(ea, estimation_id=row + 9, at=at), 404, "estimation_not_found")
    refused(preview(ea, estimation=row_body(ea.w, disc=[1, 1, 11]), at=at), 422, "bad_param", "disc.2")
    refused(preview(ea, estimation=row_body(ea.w, model_id=0), at=at), 422, "bad_param", "model_id")
    refused(preview(ea, estimation_id=row, at=at, seq=-1), 422, "bad_param", "seq")
    tok = sp.GATE.enter("wizard", 5)
    try:
        refused(preview(ea, estimation_id=row, at=at, channel="wizard", seq=2), 409, "superseded")
    finally:
        sp.GATE.leave(tok)
    # a source that is gone: the row reads «источник удалён»
    with dialect.write_tx(ea.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == ea.w.vz))
        conn.execute(sa.delete(objects).where(objects.c.id == ea.w.model))
    refused(preview(ea, estimation_id=row, at=at), 409, "source_lost", "estimation_id")
    assert ok(ea.get(f"{E}/{row}"))["source_lost"] is True


# --- the run --------------------------------------------------------------------------------------------------------


def run_body(c: Any, ids: list[int], **extra: Any) -> dict[str, Any]:
    version = ok(c.get(f"{API}/blocks/{c.w.model}"))["version"]
    return {"estimation_ids": ids, "expected_version": version, **extra}


def test_run_is_refused_before_the_job(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    a = make_row(ea)["id"]
    R = f"{GS}/estimate/run"
    refused(ea.post(R, json=[1]), 422, "bad_request")
    refused(ea.post(R, json={"expected_version": 1}), 422, "bad_param", "plan_id")  # neither
    refused(ea.post(R, json={"plan_id": 1, "estimation_ids": [a], "expected_version": 1}), 422, "bad_param", "plan_id")
    refused(ea.post(R, json={"estimation_ids": [a]}), 422, "bad_param", "expected_version")
    refused(ea.post(R, json={"estimation_ids": [a], "expected_version": 1, "replace": "да"}), 422, "bad_param",
            "replace")  # fmt: skip
    refused(ea.post(R, json={"estimation_ids": [a + 99], "expected_version": 1}), 404, "estimation_not_found",
            "estimation_ids.0")  # fmt: skip
    refused(ea.post(R, json={"plan_id": 99, "expected_version": 1}), 404, "plan_not_found")
    empty = ok(ea.post(P, json={"model_id": w.model, "items": []}))
    refused(ea.post(R, json={"plan_id": empty["id"], "expected_version": 1}), 422, "bad_param", "plan_id")
    stale = refused(ea.post(R, json={"estimation_ids": [a], "expected_version": 99}), 409, "version_conflict")
    assert stale["version"] == ok(ea.get(f"{API}/blocks/{w.model}"))["version"]


def test_run_the_real_job_and_the_fields_exist(ea) -> None:  # type: ignore[no-untyped-def]
    w = ea.w
    a = make_row(ea, "окисл")["id"]
    R = f"{GS}/estimate/run"
    started = ok(ea.post(R, json=run_body(ea, [a])))
    job = finish(ea, started)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["model_id"] == w.model and res["plan_id"] is None and res["fields"][:2] == ["AU_OK_STATUS", "AU_OK_EST"]
    assert res["rows"][0]["estimation_id"] == a and res["rows"][0]["counts"]["by_status"][0] > 0
    assert ["geostat.changed", {"kind": "estimation", "id": a}] in res["events"]
    info = ok(ea.get(f"{API}/blocks/{w.model}"))
    assert info["version"] == res["version"] and "AU_OK_EST" in [f["name"] for f in info["fields"]]
    got = ok(ea.get(f"{E}/{a}"))
    assert got["fields_exist"][:2] == ["AU_OK_STATUS", "AU_OK_EST"] and got["stale"] is False
    # the fields exist now: refused with the name, until `replace`; the model moved: the old version conflicts
    again = refused(ea.post(R, json=run_body(ea, [a])), 409, "field_exists")
    assert again["field"] == "AU_OK_STATUS"
    assert refused(ea.post(R, json={"estimation_ids": [a], "expected_version": 1, "replace": True}), 409,
                   "version_conflict")["version"] == res["version"]  # fmt: skip
    plan = ok(ea.post(P, json={"model_id": w.model, "items": [a]}))
    body = {**run_body(ea, []), "plan_id": plan["id"], "replace": True}
    body.pop("estimation_ids")
    second = finish(ea, ok(ea.post(R, json=body)))
    assert second["status"] == "done" and second["result"]["plan_id"] == plan["id"], second
    # the source of a row gone: 409 `source_lost` with the place of the row
    with dialect.write_tx(ea.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == w.model))
    lost = refused(ea.post(R, json={"estimation_ids": [a], "expected_version": 1}), 404, "block_model_not_found")
    assert lost["message"]
