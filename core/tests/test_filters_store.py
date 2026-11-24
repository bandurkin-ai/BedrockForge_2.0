"""The library of filters (подшаг 17.7; ТЗ 7.35 Д-02, 8.7.5; ADR 0023 §8, decisions 11–12): the rule tree and its
text of 8.7.4, storage with a version (`PUT` without it — 409), the users (`object_refs`, the view settings of the
block models), 422 `filter_in_use` with the list, the refs to wireframes, the view key `block_styles.filter_id`,
the cycle `.bfggis` (ids of wireframes, of consumers and of the view renumbered), `-m db` (SQLite and PostgreSQL)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import store as bm
from bf_core.blocks.filters import remap, routes
from bf_core.blocks.filters import schema as fs
from bf_core.blocks.filters import store as st
from bf_core.calc import parse
from bf_core.db import dialect, migrate
from bf_core.db.schema import object_refs, objects
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viewstate import keys as vk
from tests.dbutil import make_engine
from tests.test_archive import export, wait_job

API = "/api/v1"
FL = API + "/filters"
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [5.0, 5.0, 2.5]}
FIELDS = [{"name": "AU", "kind": "number", "unit": "g/t"}, {"name": "DOMAIN", "kind": "number", "unit": None}]


def cond(field: str, op: str, value: Any = None, **extra: Any) -> dict[str, Any]:
    c: dict[str, Any] = {"field": field, "op": op, **extra}
    if value is not None:
        c["value"] = value
    return {"cond": c}


def group(op: str, *items: dict[str, Any]) -> dict[str, Any]:
    return {"op": op, "items": list(items)}


def wire(oid: int | None, side: str = "inside", **extra: Any) -> dict[str, Any]:
    return {"wireframe": {"id": oid, "side": side, **extra}}


def refused(rules: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fs.normalize_rules(rules)
    return e.value


# --- the tree and its text --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "text"),
    [
        (group("and", cond("AU", ">", 1), cond("DOMAIN", "=", 2)), "AU > 1 AND DOMAIN = 2"),
        (group("and", cond("DOMAIN", "=", 2), cond("AU", ">", 1)), "DOMAIN = 2 AND AU > 1"),  # the reverse
        (
            group("and", group("or", cond("AU", ">", 1), cond("CU", "<=", "0,5")), cond("DOMAIN", "=", 2)),
            "(AU > 1 OR CU <= 0.5) AND DOMAIN = 2",
        ),  # fmt: skip
        (
            group("or", group("and", cond("A", ">", 1), cond("B", "<", 2)), cond("C", "<>", 3)),
            "A > 1 AND B < 2 OR C <> 3",
        ),  # fmt: skip
        (group("not", cond("AU", ">=", "2.5")), "NOT AU >= 2.5"),
        (group("not", group("or", cond("A", "=", 1), cond("B", "=", 2))), "NOT (A = 1 OR B = 2)"),
        (cond("AU", "empty"), "ISNULL(AU)"),
        (cond("AU", "notempty"), "NOT ISNULL(AU)"),
        (cond("ROCK", "in", values=["A", "O'Z", 3, "4,5"]), "ROCK IN ('A', 'O''Z', 3, 4.5)"),
        (cond("Au ppm", "=", "Руда"), "[Au ppm] = 'Руда'"),
        (cond("AU", ">", -1.25), "AU > -1.25"),
        ({"expr": "if(au > 1, 1, 0) = 1 and domain in (1, 2)"}, "IF(au > 1, 1, 0) = 1 AND domain IN (1, 2)"),
        (group("and", {"expr": "A > 1 or B > 1"}, cond("C", "=", 2)), "(A > 1 OR B > 1) AND C = 2"),
        (group("and", cond("AU", ">", 1)), "AU > 1"),  # one item: the item itself
    ],
)
def test_text_is_the_canonical_expression_of_8_7_4(rules: dict[str, Any], text: str) -> None:
    ok = fs.normalize_rules(rules)
    assert fs.to_text(ok) == text
    assert parse(text).to_text() == text  # and it reads back
    assert fs.to_text(fs.normalize_rules(ok)) == text  # normalizing twice changes nothing


def test_text_is_null_with_a_wireframe_or_a_surface_and_the_title_always_there() -> None:
    rules = fs.normalize_rules(group("and", cond("AU", ">", 1), wire(7, by="centre"),
                                     {"surface": {"id": 8, "side": "below"}}))  # fmt: skip
    assert fs.to_text(rules) is None
    assert rules["items"][1] == {"wireframe": {"id": 7, "side": "inside", "by": "centre", "m": 4}}
    assert rules["items"][2] == {"surface": {"id": 8, "side": "below", "by": "centre"}}
    names = {7: "Рудное тело 1", 8: "Рельеф"}
    assert fs.title_of(rules, names) == (
        "AU > 1 И внутри каркаса «Рудное тело 1» (по центру) И ниже поверхности «Рельеф» (по центру)"
    )
    assert fs.title_of(fs.normalize_rules(wire(None, "outside")), {}) == "снаружи каркаса (недоступен) (по доле)"
    assert fs.fields_of(rules) == ["AU"]
    assert fs.object_ids(rules) == [7, 8]


def test_title_in_russian_with_parentheses_where_needed() -> None:
    rules = fs.normalize_rules(group("and", group("or", cond("A", "empty"), cond("B", "in", values=[1, "x"])),
                                     group("not", group("and", cond("C", "notempty"), cond("D", "<", 3)))))  # fmt: skip
    assert fs.title_of(rules) == "(A — пусто ИЛИ B в (1, 'x')) И НЕ (C — не пусто И D < 3)"


def test_fields_in_order_of_first_use_without_case() -> None:
    rules = fs.normalize_rules(group("and", cond("AU", ">", 1), {"expr": "au * cu > 1 and [Мощность] < 3"},
                                     cond("DOMAIN", "in", values=[1])))  # fmt: skip
    assert fs.fields_of(rules) == ["AU", "cu", "Мощность", "DOMAIN"]


@pytest.mark.parametrize(
    ("rules", "param"),
    [
        ("AU > 1", "rules"),
        ({}, "rules"),
        ({"op": "xor", "items": [cond("A", "=", 1)]}, "rules.op"),
        ({"op": "and", "items": []}, "rules.items"),
        ({"op": "not", "items": [cond("A", "=", 1), cond("B", "=", 1)]}, "rules.items"),
        ({"op": "and", "items": [cond("A", "=", 1)], "x": 1}, "rules"),
        (group("and", cond("A", "=", 1), cond("B", "~", 1)), "rules.items.1.cond.op"),
        (group("and", cond("A", "=", 1), cond("", "=", 1)), "rules.items.1.cond.field"),
        (group("and", cond("A", "=", 1), cond("B]", "=", 1)), "rules.items.1.cond.field"),
        (cond("A", ">"), "rules.cond.value"),
        (cond("A", ">", "много"), "rules.cond.value"),
        (cond("A", "<", True), "rules.cond.value"),
        (cond("A", "=", float("nan")), "rules.cond.value"),
        (cond("A", "in", values=[]), "rules.cond.values"),
        (cond("A", "in", values=[1, None]), "rules.cond.values.1"),
        (cond("A", "in", values=list(range(1001))), "rules.cond.values"),
        ({"cond": {"field": "A", "op": "=", "value": 1, "extra": 1}}, "rules.cond"),
        ({"cond": {"field": "A", "op": "=", "value": 1}, "expr": "A = 1"}, "rules"),
        (wire(1, "left"), "rules.wireframe.side"),
        (wire(1, by="half"), "rules.wireframe.by"),
        (wire(1, m=17), "rules.wireframe.m"),
        (wire(0), "rules.wireframe.id"),
        (wire("7"), "rules.wireframe.id"),
        ({"surface": {"id": 1, "side": "inside"}}, "rules.surface.side"),
        ({"surface": {"id": 1, "side": "above", "m": 4}}, "rules.surface"),
        (group("and", cond("A", "=", 1), group("or", wire(1), wire(2, "up"))), "rules.items.1.items.1.wireframe.side"),
    ],
)
def test_wrong_rules_are_422_with_the_path_of_the_place(rules: Any, param: str) -> None:
    e = refused(rules)
    assert (e.status, e.code, e.extra["param"]) == (422, "filter_rules", param)
    assert e.message and e.hint  # in Russian, with a hint


def test_expression_errors_keep_the_place_in_the_text() -> None:
    e = refused(group("and", cond("A", "=", 1), {"expr": "AU > (1"}))
    assert (e.status, e.code, e.extra["param"]) == (422, "expr_syntax", "rules.items.1.expr")
    assert e.extra["pos"] == 5 and e.extra["reason"] == "unclosed_paren" and e.hint
    for text in ("__import__('os')", "", "AU >", "a" * 4001):
        bad = refused({"expr": text})
        assert (bad.code, bad.extra["param"]) == ("expr_syntax", "rules.expr"), text
    assert refused({"expr": 5}).code == "filter_rules"
    with pytest.raises(ApiError) as e2:  # the `expr` of a body is checked too
        fs.rules_of_body({"expr": "AU >> 1"}, required=True)
    assert (e2.value.code, e2.value.extra["param"]) == ("expr_syntax", "expr")


def test_limits_of_the_tree_depth_32_nodes_500() -> None:
    deep: dict[str, Any] = cond("A", "=", 1)
    for _ in range(31):
        deep = group("not", deep)  # 32 levels with the leaf
    assert fs.normalize_rules(deep)
    e = refused(group("not", deep))
    assert (e.code, e.extra["param"].count(".items.")) == ("filter_rules", 32)
    assert fs.normalize_rules(group("and", *[cond("A", "=", i) for i in range(499)]))  # 499 + the group = 500
    e = refused(group("and", *[cond("A", "=", i) for i in range(500)]))
    assert e.code == "filter_rules" and "500" in e.message
    long = group("or", *[{"expr": "A > 1 AND B > 2 AND C > 3 AND D > 4 AND E > 5 AND F > 6 AND G > 7"}] * 70)
    assert fs.to_text(fs.normalize_rules(long)) is None  # more than the parser takes: the text is null, not a refusal


def test_body_takes_rules_or_expr_not_both() -> None:
    assert fs.rules_of_body({"expr": "AU > 1"}, required=True) == {"expr": "AU > 1"}
    assert fs.rules_of_body({}, required=False) is None
    for body in ({}, {"rules": cond("A", "=", 1), "expr": "A = 1"}):
        with pytest.raises(ApiError) as e:
            fs.rules_of_body(body, required=True)
        assert (e.value.code, e.value.extra["param"]) == ("filter_rules", "rules")


# --- the library in a project -----------------------------------------------------------------------------------------


@pytest.fixture()
def fc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with the project of the demo (wireframes «Рельеф», «Рудное тело 1», «Рудное тело 2») and the router of
    the filters (until `api/modules.py` names it); `events` — what the core published."""
    if not any(str(getattr(r, "path", "")).startswith(FL) for r in pclient.app.routes):
        pclient.app.include_router(routes.router, prefix=API)
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    events: list[tuple[str, dict[str, Any]]] = []
    publish = pclient.core.events.publish
    monkeypatch.setattr(pclient.core.events, "publish", lambda e, d: (events.append((e, d)), publish(e, d))[1])
    pclient.events = events
    pclient.wf = {w["name"]: w["id"] for w in pclient.get(f"{API}/wireframes").json()["wireframes"]}
    return pclient


def model(c: Any, name: str = "Модель") -> int:
    idx = np.arange(0, 24, 2, dtype=np.uint64)
    values = {"AU": np.linspace(0.1, 5.0, len(idx)), "DOMAIN": (np.arange(len(idx)) % 3).astype(np.float64)}
    with dialect.write_tx(c.project.engine) as conn:
        return bm.put_block_model(conn, BlobStore(c.project.folder), GRID, FIELDS, idx, values, name=name)


def consumer(c: Any, filter_id: int, name: str = "Поле AU2", role: str = st.ROLE_USE) -> int:
    """An object of a later step (a calculator field, a report…) that names the filter: `object_refs` role `filter`."""
    now = dialect.utcnow()
    with dialect.write_tx(c.project.engine) as conn:
        oid = int(conn.execute(sa.insert(objects).values(
            kind="report", name=name, name_key=dialect.name_key(name), parent_id=None, sort_key=0.0, state="ready",
            version=1, meta={}, created_at=now, updated_at=now)).inserted_primary_key[0])  # type: ignore[index]  # fmt: skip
        conn.execute(sa.insert(object_refs).values(src_id=oid, dst_id=filter_id, role=role))
    return oid


def make(c: Any, name: str = "Руда", **body: Any) -> dict[str, Any]:
    if "rules" not in body and "expr" not in body:
        body["rules"] = group("and", cond("AU", ">", 1), cond("DOMAIN", "=", 2))
    r = c.post(FL, json={"name": name, **body})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_create_list_get_with_text_title_and_fields(fc) -> None:  # type: ignore[no-untyped-def]
    f = make(fc, note="  Борт 1 г/т  ", folder="Отчёты\\ Борт")
    assert (f["name"], f["version"], f["folder"], f["note"]) == ("Руда", 1, "Отчёты/Борт", "Борт 1 г/т")
    assert (f["text"], f["title"], f["fields"]) == ("AU > 1 AND DOMAIN = 2", "AU > 1 И DOMAIN = 2", ["AU", "DOMAIN"])
    assert f["wireframes"] == [] and f["rules"]["op"] == "and" and f["created_at"] and f["updated_at"]
    assert fc.events[-1] == ("objects.changed", {"kind": "filter", "id": f["id"]})
    e = make(fc, "Выражение", expr="IF(AU > 1, 1, 0) = 1")
    assert e["rules"] == {"expr": "IF(AU > 1, 1, 0) = 1"} and e["text"] == "IF(AU > 1, 1, 0) = 1"
    listed = fc.get(FL).json()["filters"]
    assert [x["name"] for x in listed] == ["Выражение", "Руда"] and "rules" not in listed[0]
    assert {k: listed[1][k] for k in ("id", "text", "title", "fields", "note", "version")} == {
        k: f[k] for k in ("id", "text", "title", "fields", "note", "version")}  # fmt: skip
    assert fc.get(f"{FL}/{f['id']}").json() == f
    assert fc.get(f"{FL}/999").json()["code"] == "filter_not_found"


def test_names_refusals_and_a_read_only_project(fc) -> None:  # type: ignore[no-untyped-def]
    make(fc)
    r = fc.post(FL, json={"name": "руда ", "expr": "AU > 2"})  # the same name without case and spaces
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (409, "name_taken", "name")
    for name in ("", "   ", None):
        r = fc.post(FL, json={"name": name, "expr": "AU > 2"})
        assert (r.status_code, r.json()["code"]) == (422, "name_empty")
    assert fc.post(FL, json={"name": "Пусто"}).json()["param"] == "rules"
    assert fc.post(FL, json={"name": "Плохо", "expr": "AU >"}).json()["code"] == "expr_syntax"
    assert fc.post(FL, json={"name": "Плохо", "rules": cond("A", "~", 1)}).json()["param"] == "rules.cond.op"
    assert fc.post(FL, json={"name": "Плохо", "expr": "A > 1", "note": 5}).json()["param"] == "note"
    assert fc.post(FL, json=[1]).json()["code"] == "bad_request"
    assert [x["name"] for x in fc.get(FL).json()["filters"]] == ["Руда"]  # the refusals left nothing
    f = fc.get(FL).json()["filters"][0]
    fc.project.read_only = True  # opened in another window (ТЗ 5.7): reading works, writing is 423
    try:
        for r in (fc.post(FL, json={"name": "Новая", "expr": "AU > 2"}),
                  fc.put(f"{FL}/{f['id']}", json={"expected_version": 1, "name": "Иначе"}),
                  fc.delete(f"{FL}/{f['id']}")):  # fmt: skip
            assert (r.status_code, r.json()["code"]) == (423, "project_read_only"), r.text
        assert fc.get(f"{FL}/{f['id']}").status_code == 200 and fc.get(f"{FL}/{f['id']}/usage").status_code == 200
    finally:
        fc.project.read_only = False


def test_put_needs_the_version_409_without_it_and_with_a_stale_one(fc) -> None:  # type: ignore[no-untyped-def]
    f = make(fc)
    url = f"{FL}/{f['id']}"
    for body in ({"name": "Другое"}, {"expected_version": None, "name": "Другое"}, {"expected_version": "1"},
                 {"expected_version": 7, "name": "Другое"}, {"expected_version": True}):  # fmt: skip
        r = fc.put(url, json=body)
        assert (r.status_code, r.json()["code"], r.json()["version"]) == (409, "version_conflict", 1), body
        assert r.json()["message"] and r.json()["hint"]
    assert fc.get(url).json()["name"] == "Руда"  # nothing written
    r = fc.put(url, json={"expected_version": 1, "name": "Руда 1 г/т", "expr": "AU > 1", "note": "про борт"})
    assert r.status_code == 200, r.text
    g = r.json()
    assert (g["version"], g["name"], g["text"], g["rules"], g["note"]) == (
        2, "Руда 1 г/т", "AU > 1", {"expr": "AU > 1"}, "про борт")  # fmt: skip
    assert fc.events[-1] == ("objects.changed", {"kind": "filter", "id": f["id"]})
    r = fc.put(url, json={"expected_version": 1, "name": "Опять"})  # the window read version 1
    assert (r.status_code, r.json()["version"]) == (409, 2)
    only_note = fc.put(url, json={"expected_version": 2, "note": None}).json()  # the rest stays
    got = (only_note["version"], only_note["text"], only_note["note"], only_note["name"])
    assert got == (3, "AU > 1", None, "Руда 1 г/т")
    other = make(fc, "Вторая", expr="AU > 3")
    r = fc.put(url, json={"expected_version": 3, "name": " вторая"})
    assert (r.status_code, r.json()["code"]) == (409, "name_taken") and other["id"] != f["id"]
    assert fc.put(url, json={"expected_version": 3, "rules": cond("A", "~", 1)}).json()["param"] == "rules.cond.op"
    assert fc.put(f"{FL}/999", json={"expected_version": 1}).json()["code"] == "filter_not_found"
    assert fc.get(url).json()["version"] == 3  # the refusals wrote nothing


def test_delete_of_a_filter_in_use_is_422_with_the_list_of_users(fc) -> None:  # type: ignore[no-untyped-def]
    f = make(fc)
    fid = f["id"]
    free = make(fc, "Свободный", expr="AU > 3")
    assert fc.delete(f"{FL}/{free['id']}").json() == {"deleted": free["id"]}
    assert fc.events[-1] == ("objects.changed", {"kind": "filter", "id": free["id"], "deleted": True})
    assert fc.get(f"{FL}/{free['id']}").status_code == 404 and fc.get(f"{FL}/{fid}/usage").json() == {"usage": []}
    one = consumer(fc, fid, "Поле AU2")
    mid = model(fc, "Модель Б")
    r = fc.put(API + "/view/block_styles", json={"value": {str(mid): {"visible": True, "filter_id": fid}},
                                                  "expected_version": 0})  # fmt: skip
    assert r.status_code == 200, r.text
    camera = {"position": [0, 0, 10], "target": [0, 0, 0], "up": [0, 1, 0], "zoom": 1.0, "ortho": True, "fov": 45.0}
    saved = fc.post(API + "/views", json={"camera": camera, "name": "Мой вид"})
    assert saved.status_code == 200, saved.text
    with dialect.write_tx(fc.project.engine) as conn:  # a saved view that names the filter in its keys
        view = int(conn.execute(sa.select(objects.c.id).where(objects.c.kind == "view")).scalar_one())
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == view)).scalar_one()
        meta["keys"]["block_styles"] = {str(mid): {"filter_id": fid}}
        conn.execute(sa.update(objects).where(objects.c.id == view).values(meta=meta))
    expect = [{"kind": "object", "id": one, "name": "Поле AU2", "role": "filter"},
              {"kind": "view", "id": mid, "name": "Вид проекта · Модель Б", "role": "block_style"},
              {"kind": "view", "id": view, "name": "Мой вид", "role": "block_style"}]  # fmt: skip
    assert fc.get(f"{FL}/{fid}/usage").json()["usage"] == expect
    r = fc.delete(f"{FL}/{fid}")
    assert r.status_code == 422, r.text
    body = r.json()
    assert (body["code"], body["usage"]) == ("filter_in_use", expect)
    assert "«Поле AU2»" in body["message"] and "«Вид проекта · Модель Б»" in body["message"] and body["hint"]
    assert fc.get(f"{FL}/{fid}").status_code == 200  # still there
    # the users go one by one; the filter goes with the last one
    fc.put(API + "/view/block_styles", json={"value": {str(mid): {"visible": True, "filter_id": None}},
                                              "expected_version": 1})  # fmt: skip
    assert [u["id"] for u in fc.get(f"{FL}/{fid}/usage").json()["usage"]] == [one, view]
    with dialect.write_tx(fc.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == view))
        conn.execute(sa.delete(object_refs).where(object_refs.c.dst_id == fid))
    assert fc.delete(f"{FL}/{fid}").json() == {"deleted": fid}
    assert fc.get(FL).json()["filters"] == []


def test_wireframe_refs_unknown_wireframes_and_a_leaf_that_became_unavailable(fc) -> None:  # type: ignore[no-untyped-def]
    one, two, relief = fc.wf["Рудное тело 1"], fc.wf["Рудное тело 2"], fc.wf["Рельеф"]
    rules = group("or", group("and", cond("AU", ">", 1), wire(one, by="centre")), wire(two, "outside", m=2),
                  {"surface": {"id": relief, "side": "above"}})  # fmt: skip
    f = make(fc, "Каркасы", rules=rules)
    assert f["text"] is None and f["fields"] == ["AU"]
    assert f["title"] == ("AU > 1 И внутри каркаса «Рудное тело 1» (по центру) ИЛИ снаружи каркаса «Рудное тело 2» "
                          "(по доле) ИЛИ выше поверхности «Рельеф» (по центру)")  # fmt: skip
    assert f["wireframes"] == [{"id": one, "name": "Рудное тело 1", "available": True},
                               {"id": two, "name": "Рудное тело 2", "available": True},
                               {"id": relief, "name": "Рельеф", "available": True}]  # fmt: skip
    refs = lambda: sorted(  # noqa: E731
        (int(r.dst_id), r.role) for r in fc.project.engine.connect().execute(
            sa.select(object_refs).where(object_refs.c.src_id == f["id"])))  # fmt: skip
    assert refs() == sorted([(one, "filter_src"), (two, "filter_src"), (relief, "filter_src")])
    r = fc.delete(f"{API}/wireframes/{two}")  # the wireframe a filter names stays
    assert r.status_code == 422 and "«Каркасы»" in r.json()["message"], r.text
    bad = fc.post(FL, json={"name": "Нет каркаса", "rules": group("and", wire(one), wire(99999, "outside"))})
    assert (bad.status_code, bad.json()["code"], bad.json()["param"]) == (
        422, "wireframe_not_found", "rules.items.1.wireframe.id")  # fmt: skip
    g = fc.put(f"{FL}/{f['id']}", json={"expected_version": 1, "rules": wire(one)}).json()  # fewer wireframes
    assert [w["id"] for w in g["wireframes"]] == [one] and refs() == [(one, "filter_src")]
    assert fc.delete(f"{API}/wireframes/{two}").status_code == 200  # free now
    assert fc.delete(f"{FL}/{f['id']}").json() == {"deleted": f["id"]}
    assert refs() == []  # the refs went with the filter
    # a wireframe the project has lost since (an import without it): «недоступен», and it may stay on a PUT
    h = make(fc, "Потеря", rules=group("and", cond("AU", ">", 1), wire(one)))
    with dialect.write_tx(fc.project.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == h["id"])).scalar_one()
        meta["rules"]["items"][1]["wireframe"]["id"] = 424242
        conn.execute(sa.update(objects).where(objects.c.id == h["id"]).values(meta=meta))
        conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == h["id"]))
    lost = fc.get(f"{FL}/{h['id']}").json()
    assert lost["wireframes"] == [{"id": 424242, "name": None, "available": False}]
    assert lost["title"] == "AU > 1 И внутри каркаса (недоступен) (по доле)"
    kept = fc.put(f"{FL}/{h['id']}", json={"expected_version": 1, "note": "осталась без каркаса",
                                           "rules": lost["rules"]})  # fmt: skip
    assert kept.status_code == 200, kept.text
    new = fc.put(f"{FL}/{h['id']}", json={"expected_version": 2, "rules": wire(515151)})
    assert (new.status_code, new.json()["code"]) == (422, "wireframe_not_found")  # a new one must exist


def test_test_routes_check_the_model_and_wait_for_the_restrictor(fc) -> None:  # type: ignore[no-untyped-def]
    f = make(fc)
    assert fc.post(f"{FL}/{f['id']}/test", json={}).json()["param"] == "model_id"
    assert fc.post(f"{FL}/{f['id']}/test", json={"model_id": 99999}).json()["code"] == "block_model_not_found"
    assert fc.post(f"{FL}/999/test", json={"model_id": 1}).json()["code"] == "filter_not_found"
    assert fc.post(f"{FL}/test", json={"model_id": 1}).json()["param"] == "rules"
    assert fc.post(f"{FL}/test", json={"model_id": 1, "expr": "AU >"}).json()["code"] == "expr_syntax"
    mid = model(fc)
    if importlib.util.find_spec("bf_core.blocks.restrict") is None:
        asked = ((f"{FL}/{f['id']}/test", {"model_id": mid}), (f"{FL}/test", {"model_id": mid, "expr": "AU > 1"}))
        for url, body in asked:
            r = fc.post(url, json=body)
            assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "not_ready", "model_id")


# --- the view key `block_styles` --------------------------------------------------------------------------------------


def test_view_key_block_styles_names_a_filter() -> None:
    styles = {"3": {"visible": True, "filter_id": 11}, "4": {"visible": False}, "5": {"filter_id": None}}
    ok = vk.normalize("block_styles", styles, strict=True)
    assert ok == styles
    for junk in ("a", 0, -1, True, 1.5):
        assert vk.normalize("block_styles", {"3": {"filter_id": junk}}, strict=False) == {"3": {"filter_id": None}}
        with pytest.raises(vk.BadValue):
            vk.normalize("block_styles", {"3": {"filter_id": junk}}, strict=True)
    assert sorted(vk.ids_in("block_styles", ok)) == [3, 4, 5, 11]
    alive = {3, 4, 5}.__contains__  # the filter 11 is gone: the model shows all blocks again
    assert vk.drop_missing("block_styles", ok, alive) == {"3": {"visible": True, "filter_id": None},
                                                         "4": {"visible": False}, "5": {"filter_id": None}}  # fmt: skip
    assert vk.drop_missing("block_styles", ok, {3, 11}.__contains__) == {"3": styles["3"]}  # models 4, 5 gone
    assert vk.map_ids("block_styles", ok, {3: 30, 11: 110, 4: 40}.get) == {
        "30": {"visible": True, "filter_id": 110}, "40": {"visible": False}}  # fmt: skip
    assert vk.map_ids("block_styles", ok, {3: 30}.get) == {"30": {"visible": True, "filter_id": None}}
    assert vk.normalize("cloud_styles", {"3": {"filter_id": "a"}}, strict=True) == {"3": {"filter_id": "a"}}  # others


# --- `.bfggis` --------------------------------------------------------------------------------------------------------


def test_bfggis_roundtrip_renumbers_wireframes_consumers_and_the_view(fc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.archive import importer

    one, two = fc.wf["Рудное тело 1"], fc.wf["Рудное тело 2"]
    rules = group("and", cond("AU", ">", "0,5"), group("or", wire(two, by="centre"), {"expr": "DOMAIN in (1, 2)"}),
                  {"surface": {"id": fc.wf["Рельеф"], "side": "below", "by": "frac"}})  # fmt: skip
    a = make(fc, "С каркасами", rules=rules, note="первый")
    b = make(fc, "Простой", expr="AU > 1 and DOMAIN = 2")
    user = consumer(fc, a["id"], "Отчёт по рудным телам")
    mid = model(fc, "Модель Ц")
    gone = make(fc, "Не попадёт в стиль", expr="AU > 9")
    styles = {str(mid): {"visible": True, "filter_id": b["id"], "mode": "shell"}}
    assert fc.put(API + "/view/block_styles", json={"value": styles, "expected_version": 0}).status_code == 200
    assert fc.delete(f"{API}/wireframes/{fc.wf['Рельеф']}").status_code == 422  # a filter names the surface
    # the ids shift: «Рудное тело 1» (before «Рудное тело 2») is deleted, so the old number of the body 2 names
    # another row of the new project
    assert fc.delete(f"{API}/wireframes/{one}").status_code == 200
    before = {f["name"]: f for f in fc.get(FL).json()["filters"]}
    assert fc.delete(f"{FL}/{gone['id']}").status_code == 200
    job = export(fc, tmp_path / "Фильтры")
    r = fc.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    done = wait_job(fc, r["source"], r["id"])
    assert done["status"] == "done", done
    maps = {int(k): int(v) for k, v in done["result"]["id_maps"]["objects"].items()}
    assert fc.post(f"{API}/projects/{done['result']['uid']}/open").status_code == 200
    project = projects_of(fc.core).require_current()
    if not hasattr(importer, "remap_filters"):  # until the line of the orchestrator in `importer._restore` is in
        with dialect.write_tx(project.engine) as conn:
            remap.remap_filters(conn, maps)
    after = {f["name"]: f for f in fc.get(FL).json()["filters"]}
    assert set(after) == {"С каркасами", "Простой"}
    new_a, new_b = after["С каркасами"], after["Простой"]
    assert new_a["id"] == maps[a["id"]] and new_b["id"] == maps[b["id"]]
    for new, old in ((new_a, before["С каркасами"]), (new_b, before["Простой"])):
        for k in ("text", "fields", "note", "version"):
            assert new[k] == old[k], k
    full = fc.get(f"{FL}/{new_a['id']}").json()
    names = {w["name"]: w["id"] for w in fc.get(f"{API}/wireframes").json()["wireframes"]}
    assert full["rules"]["items"][1]["items"][0] == {"wireframe": {"id": names["Рудное тело 2"], "side": "inside",
                                                                   "by": "centre", "m": 4}}  # fmt: skip
    assert full["rules"]["items"][2]["surface"]["id"] == names["Рельеф"] and full["rules"]["items"][2]["surface"][
        "by"] == "frac"  # fmt: skip
    assert [(w["id"], w["name"], w["available"]) for w in full["wireframes"]] == [
        (names["Рудное тело 2"], "Рудное тело 2", True), (names["Рельеф"], "Рельеф", True)]  # fmt: skip
    assert full["title"] == ("AU > 0.5 И (внутри каркаса «Рудное тело 2» (по центру) ИЛИ DOMAIN IN (1, 2)) И "
                             "ниже поверхности «Рельеф» (по доле)")  # fmt: skip
    with dialect.read_tx(project.engine) as conn:  # the refs `filter_src` came with the archive: the new ids
        refs = sorted((int(r.dst_id), r.role) for r in conn.execute(
            sa.select(object_refs).where(object_refs.c.src_id == new_a["id"])))  # fmt: skip
    assert refs == sorted([(names["Рудное тело 2"], "filter_src"), (names["Рельеф"], "filter_src")])
    assert maps[two] == names["Рудное тело 2"] != two  # the old number of the body 2 is not the new one
    usage = fc.get(f"{FL}/{new_a['id']}/usage").json()["usage"]
    assert usage == [{"kind": "object", "id": maps[user], "name": "Отчёт по рудным телам", "role": "filter"}]
    new_model = maps[mid]
    shown = fc.get(API + "/view").json()["values"]["block_styles"]["value"]
    assert shown == {str(new_model): {"visible": True, "filter_id": new_b["id"], "mode": "shell"}}
    assert fc.get(f"{FL}/{new_b['id']}/usage").json()["usage"] == [
        {"kind": "view", "id": new_model, "name": "Вид проекта · Модель Ц", "role": "block_style"}]  # fmt: skip
    assert fc.delete(f"{FL}/{new_b['id']}").json()["code"] == "filter_in_use"
    assert fc.delete(f"{FL}/{new_a['id']}").json()["code"] == "filter_in_use"


def test_remap_of_the_meta_of_a_filter() -> None:
    surface = group("not", {"surface": {"id": 5, "side": "above"}})
    rules = fs.normalize_rules(group("and", cond("AU", ">", 1), wire(4), surface, wire(None)))
    meta = {"v": 1, "rules": rules, "text": None, "fields": ["AU"], "note": "n"}
    new = remap.filter_meta(meta, {4: 40, 5: 50}.get)
    assert [fs.object_ids(new["rules"])] == [[40, 50]] and new["rules"]["items"][3]["wireframe"]["id"] is None
    assert {k: new[k] for k in ("v", "text", "fields", "note")} == {"v": 1, "text": None, "fields": ["AU"], "note": "n"}
    lost = remap.filter_meta(meta, {4: 40}.get)  # the surface is not in the archive: the leaf stays, «недоступен»
    assert lost["rules"]["items"][2]["items"][0]["surface"] == {"id": None, "side": "above", "by": "centre"}
    assert meta["rules"]["items"][1]["wireframe"]["id"] == 4  # the argument is not changed
    assert remap.filter_meta({"v": 1}, {}.get) == {"v": 1}


# --- both databases ---------------------------------------------------------------------------------------------------


@pytest.mark.db
def test_store_on_both_databases(db_url: str, tmp_path: Path) -> None:
    """Create, version, the users, deleting and the refs to a wireframe on SQLite and (BF_TEST_PG) PostgreSQL."""
    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        now = dialect.utcnow()
        with dialect.write_tx(engine) as conn:
            wf = int(conn.execute(sa.insert(objects).values(
                kind="wireframe", name="Тело", name_key=dialect.name_key("Тело"), parent_id=None, sort_key=0.0,
                state="ready", version=1, meta={}, created_at=now, updated_at=now)).inserted_primary_key[0])  # type: ignore[index]  # fmt: skip
            fid = st.create(conn, {"name": "Руда", "rules": group("and", cond("AU", ">", 1), wire(wf))})
            other = st.create(conn, {"name": "Вторая", "expr": "AU > 2"})
            assert st.get(conn, fid)["wireframes"] == [{"id": wf, "name": "Тело", "available": True}]
            with pytest.raises(ApiError) as e:
                st.create(conn, {"name": "РУДА", "expr": "AU > 2"})
            assert (e.value.status, e.value.code) == (409, "name_taken")
        with dialect.write_tx(engine) as conn:
            with pytest.raises(ApiError) as e:
                st.update(conn, fid, {"name": "Нет версии"})
            assert (e.value.status, e.value.code, e.value.extra["version"]) == (409, "version_conflict", 1)
            assert st.update(conn, fid, {"expected_version": 1, "expr": "AU > 5"}) == 2
            assert st.get(conn, fid)["text"] == "AU > 5" and st.get(conn, fid)["wireframes"] == []
            assert conn.execute(sa.select(object_refs).where(object_refs.c.src_id == fid)).first() is None
            conn.execute(sa.insert(object_refs).values(src_id=wf, dst_id=other, role=st.ROLE_USE))
            with pytest.raises(ApiError) as e:
                st.delete(conn, other)
            assert (e.value.status, e.value.code) == (422, "filter_in_use")
            assert e.value.extra["usage"] == [{"kind": "object", "id": wf, "name": "Тело", "role": "filter"}]
            conn.execute(sa.delete(object_refs).where(object_refs.c.dst_id == other))
            st.delete(conn, other)
            assert [f["id"] for f in st.list_filters(conn)] == [fid]
            assert st.rules_of(conn, fid) == {"expr": "AU > 5"}
            with pytest.raises(ApiError) as e:
                st.rules_of(conn, other)
            assert e.value.status == 404
    finally:
        engine.dispose()
