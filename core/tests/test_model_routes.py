"""The routes of the implicit models without a run (подшаг 19.16; ADR 0026 «API», решение 30; docs/progress/e19-api.md
§1, §3–§5, §7): `GET /defaults`, the rows (CRUD, 404 / 409 / 422 / 423, the events `implicit.changed`), the groups
(POST / PUT / DELETE), the preview of the points (the shape, the counts, `limit`, a group not yet saved, the speed), the
models «только просмотр» and the model that is being built. The run — `test_model_routes_run.py`; the answers the
windows are read against — `test_implicit_contract.py`. The router is added to the app here until the line of the
orchestrator (`tests/modapi_world.py`)."""

from __future__ import annotations

import time
from typing import Any

from bf_core.db import dialect
from bf_core.model import store as st
from tests import modapi_world as W
from tests.estapi_util import ok, read_only, refused
from tests.modapi_world import IMP, body, im, reg  # noqa: F401 - the fixtures
from tests.test_import_job import API


def events(c: Any) -> list[dict[str, Any]]:
    return [d for n, d in c.events if n == "implicit.changed"]


def make(c: Any, *names: str, **over: Any) -> dict[str, Any]:
    return ok(c.post(f"{IMP}/models", json=body(c.w, *names, **over)))  # type: ignore[no-any-return]


def get(c: Any, ident: int) -> dict[str, Any]:
    return ok(c.get(f"{IMP}/models/{ident}"))  # type: ignore[no-any-return]


def building(c: Any, ident: int) -> None:
    with dialect.write_tx(c.project.engine) as conn:
        st.set_state(conn, ident, "building")


# --- the defaults -----------------------------------------------------------------------------------------------------


def test_defaults_need_no_project_and_the_rest_do(reg, pclient) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.model import spec

    got = ok(pclient.get(f"{IMP}/defaults"))
    assert got == {"params": spec.defaults_json(), "ranges": spec.ranges_json()}
    assert got["params"]["kernel"] == "linear" and got["ranges"]["n_min"]["lo"] == 20.0
    refused(pclient.get(f"{IMP}/models"), 409, "no_project")
    refused(pclient.post(f"{IMP}/models", json={}), 409, "no_project")
    assert pclient.post(f"{API}/projects", json={"name": "Пустой"}).status_code == 200
    assert ok(pclient.get(f"{IMP}/models")) == {"models": []}


# --- the rows ---------------------------------------------------------------------------------------------------------


def test_row_crud_and_events(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    made = make(im)
    assert made == {"id": made["id"], "name": "Литология", "version": 1}
    assert events(im) == [{"kind": "implicit_model", "id": made["id"]}]
    got = get(im, made["id"])
    assert got["state"] == "ready" and got["error"] is None and got["outputs"] == []
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (False, None, False)
    meta = got["meta"]
    assert meta["v"] == 1 and meta["algo"] == "rbf-1" and meta["extent"] == W.BOX and meta["h"] == 8.0
    assert [(g["id"], g["name"], g["codes"]) for g in meta["groups"]] == [(1, "INT", ["INT"]), (3, "SS", ["SS"])]
    assert [i["kind"] for i in meta["inputs"]] == ["dh_set", "dh_table"]
    rows = ok(im.get(f"{IMP}/models"))["models"]
    assert [(r["id"], r["name"], r["version"], r["state"], r["stats"]) for r in rows] == [
        (made["id"], "Литология", 1, "ready", None)]  # fmt: skip
    assert rows[0]["groups"] == [{"id": 1, "name": "INT", "type": "domain"}, {"id": 3, "name": "SS", "type": "domain"}]
    # PUT over the version read: the keys replace, the version moves, the event is published
    put = ok(im.put(f"{IMP}/models/{made['id']}", json={"expected_version": 1, "h": 6, "levels": 1, "name": "Моя"}))
    assert put == {"id": made["id"], "name": "Моя", "version": 2}
    meta = get(im, made["id"])["meta"]
    assert (meta["h"], meta["levels"], meta["kernel"], len(meta["groups"])) == (6.0, 1, "linear", 2)
    assert events(im)[-1] == {"kind": "implicit_model", "id": made["id"]}
    stale = im.put(f"{IMP}/models/{made['id']}", json={"expected_version": 1, "h": 5})
    assert refused(stale, 409, "version_conflict")["version"] == 2
    # `groups` is the whole list: the order, the deletion
    ok(im.put(f"{IMP}/models/{made['id']}", json={"expected_version": 2, "groups": [meta["groups"][1]]}))
    assert [g["name"] for g in get(im, made["id"])["meta"]["groups"]] == ["SS"]
    assert ok(im.delete(f"{IMP}/models/{made['id']}")) == {"deleted": made["id"], "wireframes": []}
    assert events(im)[-1] == {"kind": "implicit_model", "id": made["id"], "deleted": True}
    refused(im.get(f"{IMP}/models/{made['id']}"), 404, "model_not_found")
    refused(im.delete(f"{IMP}/models/{made['id']}"), 404, "model_not_found")
    assert ok(im.get(f"{IMP}/models")) == {"models": []}


def test_row_refusals(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = im.w
    made = make(im)
    refused(im.post(f"{IMP}/models", json=body(w, name="Литология")), 409, "name_taken", "name")
    refused(im.post(f"{IMP}/models", json=body(w, pou={"n_min": 10})), 422, "bad_param", "pou.n_min")
    refused(im.post(f"{IMP}/models", json=body(w, h="много")), 422, "bad_param", "h")
    refused(im.post(f"{IMP}/models", json=body(w, kernel="gauss", name="Другая")), 422, "bad_param", "kernel")
    refused(im.post(f"{IMP}/models", json=body(w, boundary={"box": [0, 0, 0, 0, 5, 5]})), 422, "boundary_empty",
            "boundary.box.3")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, groups=[{"name": "Х", "type": "domain", "codes": ["QQ"]}],
                                               data=None)), 422, "bad_param", "data")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, groups=[W.domain("INT"), W.domain("INT", gid=9)])), 422, "bad_param",
            "groups.1.name")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, groups=[W.domain("INT"), W.domain("SS", codes=("INT",))])), 422,
            "bad_param", "groups.1.codes.0")  # fmt: skip
    data = body(w)["data"]
    refused(im.post(f"{IMP}/models", json=body(w, data={**data, "set_id": w.set_id + 99})), 404, "set_not_found",
            "data.set_id")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, data={**data, "table": "99999"})), 404, "table_not_found",
            "data.table")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, data={**data, "field": "НЕТ"})), 422, "field_not_found",
            "data.field")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, boundary={"wireframe_id": 99999})), 404, "wireframe_not_found",
            "boundary.wireframe_id")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=body(w, trend_id=99999)), 404, "trend_not_found", "trend_id")
    refused(im.post(f"{IMP}/models", json=body(w, manual=[{"layer_id": 99999, "group": 1}])), 404,
            "layer_not_found", "manual.0.layer_id")  # fmt: skip
    refused(im.post(f"{IMP}/models", json=[1]), 422, "bad_request")
    refused(im.put(f"{IMP}/models/{made['id']}", json={"h": 5}), 422, "bad_param", "expected_version")
    refused(im.put(f"{IMP}/models/{made['id']}", json={"expected_version": 0}), 422, "bad_param", "expected_version")
    refused(im.put(f"{IMP}/models/{made['id'] + 99}", json={"expected_version": 1}), 404, "model_not_found")
    refused(im.put(f"{IMP}/models/{made['id']}", json={"expected_version": 1, "levels": 9}), 422, "bad_param",
            "levels")  # fmt: skip
    other = make(im, "ALV", name="Другая")
    refused(im.put(f"{IMP}/models/{other['id']}", json={"expected_version": 1, "name": "Литология"}), 409,
            "name_taken", "name")  # fmt: skip
    assert get(im, made["id"])["version"] == 1  # the refused edits changed nothing
    assert len(ok(im.get(f"{IMP}/models"))["models"]) == 2


# --- the groups -------------------------------------------------------------------------------------------------------


def test_groups_crud(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mid = make(im)["id"]
    url = f"{IMP}/models/{mid}/groups"
    added = ok(im.post(url, json={"expected_version": 1, "group": {"name": "Аллювий", "type": "domain",
                                                                    "codes": ["ALV"]}}))  # fmt: skip
    assert added["version"] == 2 and added["id"] == mid
    assert added["group"] == {"id": 4, "name": "Аллювий", "type": "domain", "codes": ["ALV"],
                              "relation": "conformable", "faults": [], "params": {}}  # fmt: skip
    assert [g["id"] for g in get(im, mid)["meta"]["groups"]] == [1, 3, 4]  # the new one is the oldest: last
    assert events(im)[-1] == {"kind": "implicit_model", "id": mid}
    refused(im.post(url, json={"expected_version": 1, "group": {"name": "Х", "type": "domain", "codes": ["GR"]}}),
            409, "version_conflict")  # fmt: skip
    refused(im.post(url, json={"expected_version": 2, "group": {"id": 3, "name": "Х", "type": "domain",
                                                                 "codes": ["GR"]}}), 422, "bad_param",
            "group.id")  # fmt: skip
    refused(im.post(url, json={"expected_version": 2, "group": {"name": "INT", "type": "domain", "codes": ["GR"]}}),
            422, "bad_param", "groups.3.name")  # fmt: skip
    refused(im.post(url, json={"expected_version": 2, "group": {"name": "Х", "type": "domain", "codes": ["SS"]}}),
            422, "bad_param", "groups.3.codes.0")  # fmt: skip
    refused(im.post(url, json={"expected_version": 2, "group": {"name": "Х", "type": "ядро", "codes": ["GR"]}}),
            422, "bad_param", "group.type")  # fmt: skip
    refused(im.post(url, json={"expected_version": 2}), 422, "bad_param", "group.name")
    refused(im.post(url, json={"group": {"name": "Х", "type": "domain", "codes": ["GR"]}}), 422, "bad_param",
            "expected_version")  # fmt: skip
    put = ok(im.put(f"{url}/4", json={"expected_version": 2, "group": {"id": 77, "name": "Глина", "type": "domain",
                                                                         "codes": ["ALV", "GR"]}}))  # fmt: skip
    assert put["version"] == 3 and put["group"]["id"] == 4 and put["group"]["codes"] == ["ALV", "GR"]  # id is the URL's
    assert [(g["id"], g["name"]) for g in get(im, mid)["meta"]["groups"]] == [(1, "INT"), (3, "SS"), (4, "Глина")]
    refused(im.put(f"{url}/99", json={"expected_version": 3, "group": {"name": "Х", "type": "domain",
                                                                        "codes": ["GR"]}}), 404, "group_not_found")
    refused(im.put(f"{url}/4", json={"expected_version": 2, "group": {"name": "Х", "type": "domain", "codes": ["GR"]}}),
            409, "version_conflict")  # fmt: skip
    # the delete takes the version from the query or from the body; the manual layers of the group go with it
    layer = next(iter(im.w.layers.values()))
    ok(im.put(f"{IMP}/models/{mid}", json={"expected_version": 3, "manual": [{"layer_id": layer, "role": "contact",
                                                                              "group": 4}]}))  # fmt: skip
    assert get(im, mid)["meta"]["manual"] == [{"layer_id": layer, "role": "contact", "group": 4}]
    gone = ok(im.delete(f"{url}/4", params={"expected_version": 4}))
    assert gone == {"id": mid, "version": 5, "deleted": 4}
    meta = get(im, mid)["meta"]
    assert [g["id"] for g in meta["groups"]] == [1, 3] and meta["manual"] == []
    gone = ok(im.request("DELETE", f"{url}/3", json={"expected_version": 5}))
    assert gone["deleted"] == 3 and [g["id"] for g in get(im, mid)["meta"]["groups"]] == [1]
    refused(im.delete(f"{url}/3", params={"expected_version": 6}), 404, "group_not_found")
    refused(im.delete(f"{url}/1"), 422, "bad_param", "expected_version")
    refused(im.delete(f"{url}/1", params={"expected_version": 1}), 409, "version_conflict")
    refused(im.post(f"{IMP}/models/{mid + 99}/groups", json={"expected_version": 1, "group": {
        "name": "Х", "type": "domain", "codes": ["GR"]}}), 404, "model_not_found")  # fmt: skip


# --- the preview of the points ----------------------------------------------------------------------------------------


def test_points_preview_shape_counts_and_limit(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mid = make(im)["id"]
    url = f"{IMP}/models/{mid}/points/preview"
    got = ok(im.post(url, json={"group_id": 1}))
    assert set(got) == {"group", "n", "truncated", "counts", "notes", "points"} and got["group"] == 1
    cols = got["points"]
    assert set(cols) == {"x", "y", "z", "f", "kind"} and {len(v) for v in cols.values()} == {got["n"]}
    assert got["truncated"] is False and got["n"] > 100
    c = got["counts"]
    assert c["contact"] > 0 and c["inside"] > 0 and c["outside"] > 0 and (c["manual"], c["value"]) == (0, 0)
    assert c["contact"] + c["inside"] + c["outside"] == got["n"]
    pts = list(zip(cols["x"], cols["y"], cols["z"], cols["f"], cols["kind"], strict=True))
    contact = [p for p in pts if p[4] == 0]
    assert contact and all(p[3] == 0.0 for p in contact)  # a contact has f = 0
    assert all(p[3] < 0 for p in pts if p[4] == 1) and all(p[3] > 0 for p in pts if p[4] == 2)  # inside < 0 < outside
    assert all(-100 < p[0] < 520 and 0 < p[2] < 400 for p in pts)  # the coordinates of the project, not shifted
    cut = ok(im.post(url, json={"group_id": 1, "limit": 50}))
    assert cut["n"] == got["n"] and cut["truncated"] is True and len(cut["points"]["x"]) == 50
    assert cut["points"]["x"] == cols["x"][:50] and cut["counts"] == c  # the first ones, the same order
    # a group the form has not saved yet: by its codes; the same number replaces the stored group
    new = ok(im.post(url, json={"group": {"name": "Аллювий", "type": "domain", "codes": ["ALV"]}}))
    assert new["group"] == 4 and new["n"] > 0
    same = ok(im.post(url, json={"group": {"id": 1, "name": "INT", "type": "domain", "codes": ["INT"]}}))
    assert same["points"] == got["points"]
    wide = ok(im.post(url, json={"group_id": 1}))
    assert wide == got  # the second request is the same answer


def test_points_preview_refusals(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mid = make(im)["id"]
    url = f"{IMP}/models/{mid}/points/preview"
    refused(im.post(url, json={}), 422, "bad_param", "group_id")
    refused(im.post(url, json={"group_id": 1, "group": {"name": "Х", "type": "domain", "codes": ["GR"]}}), 422,
            "bad_param", "group_id")  # fmt: skip
    refused(im.post(url, json={"group_id": 1, "limit": 0}), 422, "bad_param", "limit")
    refused(im.post(url, json={"group_id": 1, "limit": 3_000_000}), 422, "bad_param", "limit")
    refused(im.post(url, json={"group_id": 99}), 404, "group_not_found")
    refused(im.post(f"{IMP}/models/{mid + 99}/points/preview", json={"group_id": 1}), 404, "model_not_found")
    refused(im.post(url, json={"group": {"name": "Пустая", "type": "domain", "codes": ["НЕТ"]}}), 422, "no_contacts")
    refused(im.post(url, json={"group": {"name": "Х", "type": "domain", "codes": ["SS"]}}), 422, "bad_param",
            "groups.2.codes.0")  # fmt: skip
    refused(im.post(url, json=[1]), 422, "bad_request")
    refused(im.post(url, json={"group_id": 1, "channel": 5}), 422, "bad_param")
    # a vein has no points of this kind: an empty answer with the reason, not an error
    fine = ok(im.post(url, json={"group": {"name": "Жила", "type": "vein", "codes": ["VN"]}}))
    assert fine["n"] == 0 and fine["notes"] and fine["points"]["x"] == []


def test_points_preview_is_fast_once_the_data_are_read(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.model import routes_points

    mid = make(im)["id"]
    url = f"{IMP}/models/{mid}/points/preview"
    routes_points.forget()
    ok(im.post(url, json={"group_id": 3}))  # the data are read from the project
    best = []
    for _ in range(5):
        t = time.perf_counter()
        got = ok(im.post(url, json={"group_id": 3, "limit": 100_000}))
        best.append(time.perf_counter() - t)
    assert got["n"] > 1000 and min(best) <= 0.2, best  # 8.9.2 «≤ 200 мс»: the selection of the columns only
    again = [time.perf_counter()]
    ok(im.post(url, json={"group_id": 3, "limit": 100_000}))  # the same request: the kept text of the answer
    again.append(time.perf_counter())
    assert again[1] - again[0] <= 0.2
    # the answer follows the data: another group definition is read again, the kept load is not taken for it
    routes_points.forget()
    assert ok(im.post(url, json={"group_id": 3}))["n"] == got["n"]
    ok(im.put(f"{IMP}/models/{mid}/groups/3", json={"expected_version": 1, "group": {
        "name": "SS", "type": "domain", "codes": ["SS", "SH"]}}))  # fmt: skip
    assert ok(im.post(url, json={"group_id": 3}))["n"] != got["n"]


# --- «только просмотр» and a model that is being built ----------------------------------------------------------------


def test_read_only_refuses_the_writes_and_reads_the_rest(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mid = make(im)["id"]
    back = read_only(im)
    try:
        group = {"name": "Х", "type": "domain", "codes": ["GR"]}
        for r in (im.post(f"{IMP}/models", json=body(im.w, name="Новая")),
                  im.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "h": 5}),
                  im.delete(f"{IMP}/models/{mid}"),
                  im.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 1, "group": group}),
                  im.put(f"{IMP}/models/{mid}/groups/1", json={"expected_version": 1, "group": group}),
                  im.delete(f"{IMP}/models/{mid}/groups/1", params={"expected_version": 1}),
                  im.post(f"{IMP}/models/{mid}/build", json={"expected_version": 1}),
                  im.post(f"{IMP}/models/{mid}/update", json={"expected_version": 1}),
                  im.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": 1})):  # fmt: skip
            refused(r, 423, "project_read_only")
        assert ok(im.get(f"{IMP}/defaults"))["params"]["levels"] == 2
        assert len(ok(im.get(f"{IMP}/models"))["models"]) == 1 and get(im, mid)["version"] == 1
        assert ok(im.get(f"{IMP}/models/{mid}/outputs")) == {"outputs": []}
        assert ok(im.post(f"{IMP}/models/{mid}/points/preview", json={"group_id": 1}))["n"] > 0
    finally:
        back()


def test_a_model_being_built_refuses_every_write(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    mid = make(im)["id"]
    building(im, mid)
    group = {"name": "Х", "type": "domain", "codes": ["GR"]}
    for r in (im.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "h": 5}),
              im.delete(f"{IMP}/models/{mid}"),
              im.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 1, "group": group}),
              im.put(f"{IMP}/models/{mid}/groups/1", json={"expected_version": 1, "group": group}),
              im.delete(f"{IMP}/models/{mid}/groups/1", params={"expected_version": 1}),
              im.post(f"{IMP}/models/{mid}/build", json={"expected_version": 1}),
              im.post(f"{IMP}/models/{mid}/update", json={"expected_version": 1}),
              im.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": 1})):  # fmt: skip
        refused(r, 409, "model_building")
    got = get(im, mid)
    assert got["state"] == "building" and got["version"] == 1 and len(got["meta"]["groups"]) == 2
    assert ok(im.get(f"{IMP}/models"))["models"][0]["state"] == "building"
    assert ok(im.post(f"{IMP}/models/{mid}/points/preview", json={"group_id": 1}))["n"] > 0  # reading is allowed
