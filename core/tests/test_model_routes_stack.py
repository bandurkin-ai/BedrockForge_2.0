"""The routes of the stack, the faults and the systems of veins (подшаг 19.36; ТЗ Д-24, Д-31, Д-39; e19-api §10):
`PUT /models/{id}/relations`, `POST /models/{id}/faults`, `PUT /models/{id}/groups/{gid}/faults`, `POST
/models/{id}/vein_system` — the answers, the versions, the refusals with their `param` (404, 409 `version_conflict` /
`model_building`, 422 `bad_param`, 423), then the run through the routes: a model with the relations and a fault gives
the wings, a model with a system of veins gives the bodies and the layer of strings, and the deletion takes the layer.
The router is added to the app here until the line of the orchestrator (`tests/modapi_world.py`)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from tests.estapi_util import finish, ok, read_only, refused
from tests.modapi_world import IMP, body, im, reg  # noqa: F401 - the fixtures
from tests.stack_world import fault_layers, fault_manual, veins_lithology


def get(c: Any, ident: int) -> dict[str, Any]:
    return ok(c.get(f"{IMP}/models/{ident}"))  # type: ignore[no-any-return]


def model(c: Any, *names: str, **over: Any) -> int:
    over.setdefault("h", 10.0)
    over.setdefault("pou", {"n_patch": 300, "n_min": 100, "delta": 0.25})
    return int(ok(c.post(f"{IMP}/models", json=body(c.w, *names, **over)))["id"])


def fault_body(c: Any, mid: int, **over: Any) -> dict[str, Any]:
    manual = [{"layer_id": x["layer_id"], "role": x["role"]} for x in fault_manual(fault_layers(c.w))]
    group = {"name": "Разлом", "type": "fault", "params": {"radius": None}}
    return {"expected_version": get(c, mid)["version"], "group": group, "manual": manual, **over}


# --- the relations ----------------------------------------------------------------------------------------------------


def test_the_relations_and_the_order_of_the_stack_are_one_edit_of_the_model(im) -> None:  # type: ignore[no-untyped-def] # noqa: F811
    c = im
    mid = model(c, "INT", "ALV", "SS", "SH")
    url = f"{IMP}/models/{mid}/relations"
    r = ok(c.put(url, json={"expected_version": 1, "order": [2, 3, 1, 4],
                            "relations": {"3": "erode", "1": "intrusion", "4": "onlap"}}))  # fmt: skip
    assert r == {"id": mid, "version": 2}
    groups = get(c, mid)["meta"]["groups"]
    assert [g["id"] for g in groups] == [2, 3, 1, 4]
    assert {g["id"]: g["relation"] for g in groups} == {2: "conformable", 3: "erode", 1: "intrusion", 4: "onlap"}
    only = ok(c.put(url, json={"expected_version": 2, "relations": {"2": "erode"}}))  # the order stays as it is
    assert only["version"] == 3 and [g["id"] for g in get(c, mid)["meta"]["groups"]] == [2, 3, 1, 4]
    refused(c.put(url, json={"expected_version": 1, "relations": {}}), 409, "version_conflict")
    refused(c.put(url, json={"expected_version": 3, "order": [1, 2, 3]}), 422, "bad_param", "order")
    refused(c.put(url, json={"expected_version": 3, "order": [1, 2, 3, 4, 4]}), 422, "bad_param")
    refused(c.put(url, json={"expected_version": 3, "relations": {"99": "erode"}}), 422, "bad_param", "relations.99")
    refused(c.put(url, json={"expected_version": 3, "relations": {"1": "sideways"}}), 422, "bad_param", "relations.1")
    refused(c.put(url, json={"relations": {}}), 422, "bad_param", "expected_version")
    refused(c.put(f"{IMP}/models/{mid + 99}/relations", json={"expected_version": 1}), 404, "model_not_found")
    assert get(c, mid)["version"] == 3  # no refused call moved the version
    back = read_only(c)
    refused(c.put(url, json={"expected_version": 3, "relations": {}}), 423, "project_read_only")
    back()


# --- the faults -------------------------------------------------------------------------------------------------------


def test_a_fault_is_added_with_its_hand_data_and_cuts_the_groups_it_is_given(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = model(c, "SS", "SH")
    got = ok(c.post(f"{IMP}/models/{mid}/faults", json=fault_body(c, mid)))
    fid = got["group"]["id"]
    assert got["version"] == 2 and got["id"] == mid and fid == 5
    assert got["group"] == {"id": fid, "name": "Разлом", "type": "fault", "codes": [], "relation": "conformable",
                            "faults": [], "params": {"radius": None}}  # fmt: skip
    meta = get(c, mid)["meta"]
    assert [m["group"] for m in meta["manual"]] == [fid, fid, fid]  # the layers are given the number of the new group
    assert [m["role"] for m in meta["manual"]] == ["contact", "inside", "outside"]
    cut = ok(c.put(f"{IMP}/models/{mid}/groups/3/faults", json={"expected_version": 2, "fault_ids": [fid]}))
    assert cut == {"id": mid, "version": 3}
    assert {g["id"]: g["faults"] for g in get(c, mid)["meta"]["groups"]} == {3: [fid], 4: [], fid: []}
    ok(c.put(f"{IMP}/models/{mid}/groups/3/faults", json={"expected_version": 3, "fault_ids": []}))  # no more
    assert get(c, mid)["meta"]["groups"][0]["faults"] == []
    finite = {"expected_version": 4, "group": {"name": "Конечный", "params": {"radius": 150}}, "manual": []}
    refused(c.post(f"{IMP}/models/{mid}/faults", json=finite), 422, "bad_param")  # no data: no codes, no layers
    again = fault_body(c, mid, group={"name": "Конечный", "codes": ["VN"], "params": {"radius": "150,5"}})
    two = ok(c.post(f"{IMP}/models/{mid}/faults", json=again))
    assert two["group"]["params"]["radius"] == 150.5 and two["group"]["codes"] == ["VN"] and two["group"]["id"] == 6


def test_the_refusals_of_the_faults_name_the_parameter(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = model(c, "SS", "SH")
    url = f"{IMP}/models/{mid}/faults"
    refused(c.post(url, json=fault_body(c, mid, group={"name": "Р", "type": "domain", "codes": ["VN"]})), 422,
            "bad_param", "group.type")  # fmt: skip
    refused(c.post(url, json=fault_body(c, mid, group={"name": "Р", "params": {"radius": -5}, "codes": ["VN"]})),
            422, "bad_param", "group.params.radius")  # fmt: skip
    refused(c.post(url, json=fault_body(c, mid, group={"name": "SS", "codes": ["VN"]})), 422, "bad_param")
    refused(c.post(url, json=fault_body(c, mid, manual=[{"role": "contact"}])), 422, "bad_param", "manual.0.layer_id")
    refused(c.post(url, json=fault_body(c, mid, manual="x")), 422, "bad_param", "manual")
    refused(c.post(url, json={**fault_body(c, mid), "expected_version": 9}), 409, "version_conflict")
    refused(c.post(f"{IMP}/models/{mid + 99}/faults", json=fault_body(c, mid)), 404, "model_not_found")
    refused(c.post(url, json={"group": {"name": "Р", "codes": ["VN"]}}), 422, "bad_param", "expected_version")
    fid = ok(c.post(url, json=fault_body(c, mid)))["group"]["id"]
    put = f"{IMP}/models/{mid}/groups/3/faults"
    v = get(c, mid)["version"]
    refused(c.put(put, json={"expected_version": v, "fault_ids": [4]}), 422, "bad_param", "fault_ids.0")  # not a fault
    refused(c.put(put, json={"expected_version": v, "fault_ids": [fid, fid]}), 422, "bad_param")
    refused(c.put(put, json={"expected_version": v, "fault_ids": "1"}), 422, "bad_param", "fault_ids")
    refused(c.put(f"{IMP}/models/{mid}/groups/99/faults", json={"expected_version": v, "fault_ids": []}), 404,
            "group_not_found")  # fmt: skip
    refused(c.put(f"{IMP}/models/{mid}/groups/{fid}/faults", json={"expected_version": v, "fault_ids": [fid]}), 422,
            "bad_param", "fault_ids")  # a fault is not cut by a fault
    refused(c.put(put, json={"expected_version": v - 1, "fault_ids": []}), 409, "version_conflict")
    assert get(c, mid)["version"] == v
    back = read_only(c)
    refused(c.post(url, json=fault_body(c, mid)), 423, "project_read_only")
    refused(c.put(put, json={"expected_version": v, "fault_ids": []}), 423, "project_read_only")
    back()


# --- the systems of veins ---------------------------------------------------------------------------------------------


def test_a_system_of_veins_is_added_with_the_codes_of_its_veins(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = model(c, "SS")
    veins = [{"code": "VN", "name": "Главная"}, {"code": "VN2", "bound": "hull", "offset": 2, "t_min": 0.5}]
    group = {"name": "Жилы", "type": "vein_system", "params": {"veins": veins}}
    r = ok(c.post(f"{IMP}/models/{mid}/vein_system", json={"expected_version": 1, "group": group}))
    g = r["group"]
    assert r["version"] == 2 and g["type"] == "vein_system" and g["codes"] == ["VN", "VN2"] and g["id"] == 4
    assert [v["name"] for v in g["params"]["veins"]] == ["Главная", "VN2"]  # the name of a vein is its code by default
    assert g["params"]["veins"][1]["offset"] == 2.0 and g["params"]["veins"][1]["t_min"] == 0.5
    url = f"{IMP}/models/{mid}/vein_system"

    def post(group: dict[str, Any], version: int = 2) -> Any:
        return c.post(url, json={"expected_version": version, "group": group})

    refused(post({"name": "Одна", "params": {"veins": veins[:1]}}), 422, "bad_param", "group.params.veins")
    refused(post({"name": "Повтор", "params": {"veins": [veins[0], veins[0]]}}), 422, "bad_param",
            "group.params.veins.1.code")  # fmt: skip
    refused(post({"name": "Вид", "type": "vein", "params": {"veins": veins}}), 422, "bad_param", "group.type")
    refused(post({"name": "Пусто"}), 422, "bad_param", "group.params.veins")
    refused(post({"name": "Жилы", "params": {"veins": veins}}), 422, "bad_param")  # the name is taken
    taken = {"name": "Другая", "params": {"veins": [{"code": "VN"}, {"code": "XX"}]}}
    refused(post(taken), 422, "bad_param")  # the code VN is the code of the first system
    two = {"name": "Версия", "params": {"veins": [{"code": "A"}, {"code": "B"}]}}
    refused(post(two, 1), 409, "version_conflict")
    refused(post({"name": "Много", "params": {"veins": [{"code": f"C{i}"} for i in range(21)]}}), 422, "bad_param")
    back = read_only(c)
    refused(post({"name": "Просмотр", "params": {"veins": [{"code": "A"}, {"code": "B"}]}}), 423, "project_read_only")
    back()


# --- the run through the routes ---------------------------------------------------------------------------------------


def test_the_run_of_a_faulted_model_and_of_a_system_of_veins_through_the_routes(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = model(c, "INT", "ALV", "SS", "SH")
    ok(c.put(f"{IMP}/models/{mid}/relations", json={"expected_version": 1, "relations": {"1": "intrusion"}}))
    fid = ok(c.post(f"{IMP}/models/{mid}/faults", json=fault_body(c, mid)))["group"]["id"]
    ok(c.put(f"{IMP}/models/{mid}/groups/3/faults", json={"expected_version": 3, "fault_ids": [fid]}))
    run = ok(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 4, "threads": 2}))
    job = finish(c, run, timeout=600)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["state"] == "ready" and res["stats"]["stack"]["relations"]["1"] == "intrusion"
    wings = {(o["group"], o["wing"]) for o in res["outputs"] if o["kind"] == "body"}
    assert wings >= {(3, 0), (3, 1), (fid, 0), (None, 1)}
    shown = get(c, mid)
    assert [o["wing"] for o in shown["outputs"] if o["group"] == 3 and o["kind"] == "body"] == [0, 1]
    assert get(c, mid)["stale"] is False
    # the system of veins: its codes are in the second table of the project (the lithology with VN2)
    table = c.w._table("Литология жил", "interval", veins_lithology(), "CODE", "text")  # noqa: SLF001
    data = {"set_id": c.w.set_id, "table": str(table), "field": "CODE", "d_max": 30, "step": 5}
    vid = model(c, "SS", name="Жилы", data=data)
    group = {"name": "Жилы", "type": "vein_system", "params": {"veins": [{"code": "VN"}, {"code": "VN2"}]}}
    added = ok(c.post(f"{IMP}/models/{vid}/vein_system", json={"expected_version": 1, "group": group}))
    done = finish(c, ok(c.post(f"{IMP}/models/{vid}/build", json={"expected_version": added["version"]})), timeout=600)
    assert done["status"] == "done", done
    lines = [o for o in done["result"]["outputs"] if o["kind"] == "line"]
    assert len(lines) == 1 and lines[0]["lines"] >= 1 and lines[0]["length"] > 0
    assert [(p["a"], p["b"]) for p in done["result"]["stats"]["vein_pairs"]] == [("VN", "VN2")]
    listed = ok(c.get(f"{IMP}/models/{vid}/outputs"))["outputs"]
    assert [o["kind"] for o in listed].count("line") == 1 and get(c, vid)["outputs"] == listed
    row = next(m for m in ok(c.get(f"{IMP}/models"))["models"] if m["id"] == vid)
    assert all(o["kind"] != "line" for o in row["outputs"])  # the tree lists the wireframes of the model only
    layer = lines[0]["line_layer_id"]
    c.events.clear()
    deleted = ok(c.delete(f"{IMP}/models/{vid}"))
    assert deleted["line_layers"] == [layer]
    assert ("objects.changed", {"kind": "line_layer", "id": layer, "deleted": True}) in c.events
    with dialect.read_tx(c.project.engine) as conn:
        assert conn.execute(sa.select(objects.c.id).where(objects.c.id == layer)).first() is None


def test_a_model_that_is_being_built_refuses_every_edit_of_the_stack(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.model import store as st
    from tests.test_model_job import live_job

    c = im
    mid = model(c, "SS", "SH")
    alive = live_job(c.w)  # a job that is alive has the model
    with dialect.write_tx(c.project.engine) as conn:
        st.begin(conn, mid, 1, job_id=alive)
    base = f"{IMP}/models/{mid}"
    group = {"name": "Жилы", "params": {"veins": [{"code": "A"}, {"code": "B"}]}}
    refused(c.put(f"{base}/relations", json={"expected_version": 1, "relations": {}}), 409, "model_building")
    refused(c.post(f"{base}/faults", json=fault_body(c, mid)), 409, "model_building")
    refused(c.put(f"{base}/groups/3/faults", json={"expected_version": 1, "fault_ids": []}), 409, "model_building")
    refused(c.post(f"{base}/vein_system", json={"expected_version": 1, "group": group}), 409, "model_building")
    assert get(c, mid)["state"] == "building"  # nothing of them touched the model


# --- .bfggis ----------------------------------------------------------------------------------------------------------


def test_a_bfggis_keeps_the_wings_the_layer_of_strings_and_the_fresh_state(im, tmp_path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.projects.service import projects_of
    from tests.test_import_job import API, wait_job

    c = im
    table = c.w._table("Литология жил", "interval", veins_lithology(), "CODE", "text")  # noqa: SLF001
    mid = model(c, "INT", "ALV", "SS", "SH", name="Разлом в архиве")  # (a new table moves the key of the set)
    ok(c.put(f"{IMP}/models/{mid}/relations", json={"expected_version": 1, "relations": {"1": "intrusion"}}))
    fid = ok(c.post(f"{IMP}/models/{mid}/faults", json=fault_body(c, mid)))["group"]["id"]
    ok(c.put(f"{IMP}/models/{mid}/groups/3/faults", json={"expected_version": 3, "fault_ids": [fid]}))
    built = finish(c, ok(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 4})), timeout=600)
    assert built["status"] == "done", built
    data = {"set_id": c.w.set_id, "table": str(table), "field": "CODE", "d_max": 30, "step": 5}
    vid = model(c, "SS", name="Жилы в архиве", data=data)
    group = {"name": "Жилы", "type": "vein_system", "params": {"veins": [{"code": "VN"}, {"code": "VN2"}]}}
    ok(c.post(f"{IMP}/models/{vid}/vein_system", json={"expected_version": 1, "group": group}))
    done = finish(c, ok(c.post(f"{IMP}/models/{vid}/build", json={"expected_version": 2})), timeout=600)
    assert done["status"] == "done", done
    old = get(c, vid)
    layer = next(o["line_layer_id"] for o in old["outputs"] if o["kind"] == "line")
    exp = c.post(f"{API}/projects/export", json={"path": str(tmp_path / "Разломы")}).json()
    packed = wait_job(c, exp["source"], exp["id"])
    assert packed["status"] == "done", packed
    imp = c.post(f"{API}/projects/import", json={"path": packed["result"]["path"], "mode": "copy"}).json()
    got = wait_job(c, imp["source"], imp["id"])
    assert got["status"] == "done", got
    maps = {int(k): int(v) for k, v in got["result"]["id_maps"]["objects"].items()}
    assert c.post(f"{API}/projects/{got['result']['uid']}/open").status_code == 200
    c.project = projects_of(c.core).require_current()
    new = get(c, maps[vid])
    assert (new["stale"], new["source_lost"], new["state"]) == (False, False, "ready")
    moved = next(o for o in new["outputs"] if o["kind"] == "line")
    assert moved["line_layer_id"] == maps[layer] and moved["length"] > 0 and moved["lines"] >= 1
    with dialect.read_tx(c.project.engine) as conn:  # the layer is in a file whose parent is the model it belongs to
        parent = conn.execute(sa.select(objects.c.parent_id).where(
            objects.c.id == conn.execute(sa.select(objects.c.parent_id).where(
                objects.c.id == moved["line_layer_id"])).scalar_one())).scalar_one()  # fmt: skip
    assert parent == maps[vid]
    faulted = get(c, maps[mid])
    assert (faulted["stale"], faulted["state"], faulted["stale_reason"]) == (False, "ready", None)
    assert [(o["group"], o["wing"], o.get("region", False)) for o in faulted["outputs"] if o["kind"] == "body"][:3] == [
        (fid, 0, True), (fid, 1, True), (1, None, False)]  # fmt: skip
    check = ok(c.get(f"{IMP}/models/{maps[mid]}/check", params={"size": 5}))  # the fields of the wings are read back
    assert check["rows"] and {(g["group"], g["wing"]) for g in check["groups"]} >= {(3, 0), (3, 1), (fid, None)}
    assert all(g["max_abs_residual"] < 1e-6 for g in check["groups"])
