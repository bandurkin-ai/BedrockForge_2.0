"""The stored implicit models (подшаг 19.11; ADR 0026 решения 22–25; e19-api.md §2, §5, §11): create / read / update /
delete of a model, the checks against the project (404 / 422 with `param`), the reference to the trend (409
`in_use`), the blobs `implicit_points` / `implicit_patches` / `implicit_solution`, the outputs as children, «устарела»
by the inputs and by the parameters, the same on SQLite and PostgreSQL (`-m db`; BF_TEST_PG). The graph —
`test_lineage_implicit.py`, `.bfggis` — `test_model_remap.py`."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db import schema as schema
from bf_core.db.schema import blob_owners, dh_tables, object_refs, objects
from bf_core.geostat import trend_store as ts
from bf_core.model import spec as ms
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model import store_blobs as bl
from bf_core.wireframes import store as ws
from tests.estutil import W, refused, world
from tests.meshutil import cube, grid

BOX = [0.0, 0.0, -300.0, 1000.0, 800.0, 100.0]


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The roles of the model until the line of the orchestrator (`db/schema.py` BLOB_ROLES) is in."""
    if not set(sb.BLOB_ROLES) <= schema.BLOB_ROLES:
        monkeypatch.setattr(schema, "BLOB_ROLES", schema.BLOB_ROLES | set(sb.BLOB_ROLES))


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- helpers (used by the other tests of the model store) -------------------------------------------------------------


def mbody(w: W, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": "Литология", "boundary": {"box": BOX},
        "data": {"set_id": w.set_id, "table": str(w.b), "field": "ZONE"},
        "groups": [{"name": "Окисленные", "type": "domain", "codes": ["окисл"]}]}  # fmt: skip
    out.update(over)
    return out


def make(w: W, points: int | None = None, **over: Any) -> dict[str, Any]:
    spec = ms.parse_model(mbody(w, **over))
    with dialect.write_tx(w.project.engine) as conn:
        return st.create(conn, w.store, w.project, spec, points=points)


def put(w: W, ident: int, version: Any, **raw: Any) -> dict[str, Any]:
    with dialect.write_tx(w.project.engine) as conn:
        return st.update(conn, w.store, w.project, ident, raw, version)


def get(w: W, ident: int) -> dict[str, Any]:
    with dialect.read_tx(w.project.engine) as conn:
        return st.get(conn, ident)


def rd(w: W, fn: Any, *args: Any) -> Any:
    with dialect.read_tx(w.project.engine) as conn:
        return fn(conn, *args)


def refused_make(w: W, status: int, code: str, param: str | None = None, **over: Any) -> Any:
    return refused(lambda: make(w, **over), status, code, param)


def solid(w: W, name: str, side: float = 500.0, offset: Any = (0.0, 0.0, -300.0)) -> int:
    v, t = cube(2, side, offset, seed=None)
    with dialect.write_tx(w.project.engine) as conn:
        return ws.put_wireframe(conn, w.store, v, t, name=name)


def surface(w: W, name: str, z: float = 50.0, offset: Any = (0.0, 0.0, 0.0), *, dtm: bool = False) -> int:
    v, t = grid(4, 4, lambda x, y: np.full_like(x, z), step=250.0, offset=offset, seed=None)
    with dialect.write_tx(w.project.engine) as conn:
        return ws.put_wireframe(conn, w.store, v, t, name=name, dtm=True if dtm else None)


def layer(w: W, name: str = "Контакты") -> int:
    now = dialect.utcnow()
    with dialect.write_tx(w.project.engine) as conn:
        res = conn.execute(sa.insert(objects).values(
            kind="line_layer", name=name, name_key=dialect.name_key(name), sort_key=0.0, state="ready", version=1,
            meta={}, created_at=now, updated_at=now))  # fmt: skip
        return int(res.inserted_primary_key[0])  # type: ignore[index]


def staged_result(w: W, groups: tuple[int, ...] = (1, 2), shift: float = 0.0) -> dict[str, Any]:
    """The three files of a tiny built model: two groups, three points and one patch each."""
    n = 3 * len(groups)
    gid = np.repeat(np.array(groups, dtype=np.int32), 3)
    pts = {"x": np.arange(n, dtype=float) + shift, "y": np.arange(n) * 2.0, "z": np.arange(n) * -3.0,
           "f": np.where(np.arange(n) % 2 == 0, 0.0, 1.0), "kind": np.zeros(n, dtype=np.int8), "group": gid,
           "wing": np.full(n, -1, dtype=np.int8), "src": np.full(n, -1, dtype=np.int32),
           "from": np.full(n, np.nan), "to": np.full(n, np.nan), "vertex": np.full(n, -1, dtype=np.int64)}  # fmt: skip
    m = len(groups)
    patches = {"group": np.array(groups, dtype=np.int32), "wing": np.full(m, -1, dtype=np.int8),
               "j": np.zeros(m, dtype=np.int32), "cx": np.arange(m) * 1.0, "cy": np.zeros(m), "cz": np.zeros(m),
               "r": np.full(m, 50.0), "n": np.full(m, 3, dtype=np.int32),
               "offset": np.arange(m, dtype=np.int64) * 7, "hash": np.array([bytes([i + 1]) * 32 for i in range(m)]),
               "eta": np.zeros(m), "cond": np.full(m, 1e3)}  # fmt: skip
    solution = np.arange(7 * m, dtype=float) + shift
    return {sb.ROLE_POINTS: bl.stage_points(w.store, pts, groups=list(groups), sources=["A-1 0,0–2,0 м"]),
            sb.ROLE_PATCHES: bl.stage_patches(w.store, patches), sb.ROLE_SOLUTION: bl.stage_solution(w.store, solution)}


def write_result(w: W, ident: int, **kw: Any) -> dict[str, str]:
    staged = staged_result(w, **kw)
    try:
        with dialect.write_tx(w.project.engine) as conn:
            return bl.commit(conn, w.store, ident, staged)
    finally:
        bl.discard(staged)


def run(w: W, ident: int, outputs: list[dict[str, Any]] | None = None, **kw: Any) -> None:
    """What the job does at its end: `record_run` with the parameters the model has now."""
    meta = get(w, ident)["meta"]
    with dialect.write_tx(w.project.engine) as conn:
        st.record_run(conn, ident, sb.load_spec(meta), {"at": "2026-11-25T10:00:00", "job": 1, "mode": "build"},
                      meta["inputs"], outputs=outputs or [], stats={"points": 6, "patches": 2, "seconds": 0.5}, **kw)


def output(w: W, ident: int, name: str, group: int | None, kind: str = "body", **kw: Any) -> dict[str, Any]:
    v, t = cube(2, 100.0, (0.0, 0.0, -100.0), seed=None)
    staged = ws.stage_wireframe(w.store, v, t)
    try:
        with dialect.write_tx(w.project.engine) as conn:
            return st.commit_output(conn, w.store, staged, ident, name=name, kind=kind, group=group, **kw)
    finally:
        staged.discard()


# --- the model: create, read, list ------------------------------------------------------------------------------------


def test_create_checks_the_project_and_keeps_the_meta(w: W) -> None:
    made = make(w, name=None)
    assert made == {"id": made["id"], "name": "Неявная модель", "version": 1}
    assert make(w, name=None)["name"] == "Неявная модель (2)"
    got = get(w, made["id"])
    meta = got["meta"]
    assert (got["name"], got["version"], got["state"], got["error"], got["stale"], got["source_lost"]) == (
        "Неявная модель", 1, "ready", None, False, False)  # fmt: skip
    assert meta["v"] == 1 and meta["algo"] == "rbf-1" and meta["last_run"] is None and meta["stats"] is None
    assert set(meta["versions"]) == {"numpy", "scipy", "numba", "skimage", "manifold3d"} and meta["created_at"]
    assert (meta["outputs"], meta["extent"], meta["name"]) == ([], BOX, got["name"])
    diag = math.sqrt(1000.0**2 + 800.0**2 + 400.0**2)
    assert meta["h"] is None  # the user gave none: «авто» stays so, the step in use is `h_used`
    assert meta["h_used"] == pytest.approx(ms.resolve_h(diag))  # resolved: diagonal / 200 within the range
    assert meta["data"]["table"] == str(w.b) and meta["data"]["field"] == "ZONE"
    assert [(i["kind"], i["id"]) for i in meta["inputs"]] == [("dh_set", w.set_id), ("dh_table", w.b)]
    assert all(i["fp"] and i["name"] for i in meta["inputs"])
    row = rd(w, lambda c: c.execute(sa.select(objects).where(objects.c.id == made["id"])).first())
    assert (row.kind, row.parent_id, row.state, row.extent) == ("implicit_model", None, "ready", BOX)


def test_the_table_and_the_field_are_taken_as_the_project_has_them(w: W) -> None:
    made = make(w, data={"set_id": w.set_id, "table": "литология", "field": "zone"})
    data = get(w, made["id"])["meta"]["data"]
    assert (data["table"], data["field"]) == (str(w.b), "ZONE")


def test_inputs_follow_the_table_of_the_contract(w: W) -> None:
    wire, expand, dtm, lay = solid(w, "Граница"), solid(w, "Расширение", 100.0), surface(w, "ЦТМ", dtm=True), layer(w)
    made = make(w, boundary={"wireframe_id": wire, "dtm_id": dtm}, trend_id=w.trend,
                groups=[{"name": "Окисленные", "type": "domain", "codes": ["окисл"]},
                        {"name": "Ручная", "type": "domain", "id": 7},
                        {"name": "Рост", "type": "expand", "params": {"wireframe_id": expand, "delta": 5}}],
                manual=[{"layer_id": lay, "role": "contact", "group": 7}])  # fmt: skip
    meta = get(w, made["id"])["meta"]
    assert [(i["kind"], i["id"]) for i in meta["inputs"]] == [
        ("dh_set", w.set_id), ("dh_table", w.b), ("object:line_layer", lay), ("object:trend", w.trend),
        ("wireframe", wire), ("wireframe", expand), ("wireframe", dtm)]  # fmt: skip
    # the extent of the boundary: the cube (0..500 × 0..500 × −300..200) cut by the DTM at z = 50 from above
    assert meta["extent"] == [0.0, 0.0, -300.0, 500.0, 500.0, 50.0]
    assert rd(w, lambda c: c.execute(sa.select(objects.c.extent).where(objects.c.id == made["id"])).scalar()) == (
        meta["extent"])  # fmt: skip


def test_list_rows_by_name_with_the_state_of_each(w: W) -> None:
    b = make(w, name="Б")["id"]
    a = make(w, name="А", groups=[])["id"]
    wire = output(w, a, "Тело А", None)["wireframe_id"]
    run(w, a, outputs=[{"wireframe_id": wire, "kind": "body", "group": None, "wing": None, "name": "Тело А"}])
    rows = rd(w, st.list_rows)
    assert [r["name"] for r in rows] == ["А", "Б"] and [r["id"] for r in rows] == [a, b]
    first = rows[0]
    assert (first["state"], first["stale"], first["groups"], first["stats"]) == (
        "ready", False, [], {"points": 6, "patches": 2, "seconds": 0.5})  # fmt: skip
    assert [(o["wireframe_id"], o["kind"], o["name"]) for o in first["outputs"]] == [(wire, "body", "Тело А")]
    assert rows[1]["groups"] == [{"id": 1, "name": "Окисленные", "type": "domain"}] and rows[1]["stats"] is None
    assert {"created_at", "updated_at"} <= set(first)


# --- refusals ---------------------------------------------------------------------------------------------------------


def test_the_references_are_checked_with_the_param(w: W) -> None:
    refused_make(w, 404, "wireframe_not_found", "boundary.wireframe_id", boundary={"wireframe_id": 999_999})
    refused_make(w, 404, "dtm_not_found", "boundary.dtm_id", boundary={"box": BOX, "dtm_id": 999_999})
    refused_make(w, 404, "dtm_not_found", "boundary.dtm_id",
                 boundary={"box": BOX, "dtm_id": solid(w, "Не ЦТМ")})  # a wireframe that is not a DTM
    refused_make(w, 404, "set_not_found", "data.set_id", data={"set_id": 999_999, "table": "1", "field": "ZONE"})
    refused_make(w, 404, "table_not_found", "data.table", data={"set_id": w.set_id, "table": "нет", "field": "ZONE"})
    refused_make(w, 422, "field_not_found", "data.field", data={"set_id": w.set_id, "table": str(w.b), "field": "X"})
    refused_make(w, 404, "trend_not_found", "trend_id", trend_id=999_999)
    refused_make(w, 404, "layer_not_found", "manual.0.layer_id",
                 groups=[{"name": "Р", "type": "domain", "id": 1}],
                 manual=[{"layer_id": 999_999, "role": "contact", "group": 1}])  # fmt: skip
    refused_make(w, 404, "wireframe_not_found", "groups.1.params.wireframe_id",
                 groups=[{"name": "Р", "type": "domain", "codes": ["окисл"]},
                         {"name": "Рост", "type": "expand",
                          "params": {"wireframe_id": 999_999, "delta": 1}}])  # fmt: skip
    refused_make(w, 404, "result_not_found", "groups.0.params.result_id", groups=[
        {"name": "Au", "type": "shell", "params": {"source": "composites", "result_id": 999_999, "field": "AU",
                                                   "thresholds": [0.5]}}])  # fmt: skip
    refused_make(w, 422, "field_not_found", "groups.0.params.field", groups=[
        {"name": "Au", "type": "shell", "params": {"source": "table", "table": str(w.a), "field": "нет",
                                                   "thresholds": [0.5]}}])  # fmt: skip
    refused_make(w, 404, "wireframe_not_found", "post.clip_id", post={"clip": "wireframe", "clip_id": 999_999})
    refused_make(w, 404, "dtm_not_found", "post.clip_id", post={"clip": "dtm", "clip_id": solid(w, "Куб-обрезка")})
    assert rd(w, st.list_rows) == []  # nothing was written


def test_open_wireframes_and_an_empty_boundary(w: W) -> None:
    open_one = surface(w, "Поверхность")
    err = refused_make(w, 422, "wireframe_open", "boundary.wireframe_id", boundary={"wireframe_id": open_one})
    assert err.extra["wireframe_id"] == open_one and "Поверхность" in err.message
    refused_make(w, 422, "wireframe_open", "groups.1.params.wireframe_id",
                 groups=[{"name": "Р", "type": "domain", "codes": ["окисл"]},
                         {"name": "Рост", "type": "expand",
                          "params": {"wireframe_id": open_one, "delta": 1}}])  # fmt: skip
    refused_make(w, 422, "wireframe_open", "post.clip_id", post={"clip": "wireframe", "clip_id": open_one})
    far = surface(w, "Далёкая ЦТМ", offset=(50_000.0, 50_000.0, 0.0), dtm=True)  # no common plan with the box
    refused_make(w, 422, "boundary_empty", "boundary.dtm_id", boundary={"box": BOX, "dtm_id": far})
    low = surface(w, "Низкая ЦТМ", z=-900.0, dtm=True)  # the whole box is above it
    refused_make(w, 422, "boundary_empty", "boundary.dtm_id", boundary={"box": BOX, "dtm_id": low})
    refused_make(w, 422, "boundary_empty", "boundary.box.3", boundary={"box": [0, 0, 0, 0, 1, 1]})  # the parser's


def test_too_many_points_and_a_taken_name(w: W) -> None:
    err = refused_make(w, 422, "too_many_points", points=3_000_000)
    assert err.extra["points"] == 3_000_000
    assert make(w, points=2_000_000)["id"]
    refused(lambda: make(w), 409, "name_taken", "name")
    refused(lambda: rd(w, st.get, 999_999), 404, "model_not_found")


# --- the trend: object_refs and 409 in_use ----------------------------------------------------------------------------


def test_a_trend_of_a_model_is_not_deleted(w: W) -> None:
    ident = make(w, trend_id=w.trend)["id"]
    refs = rd(w, lambda c: c.execute(sa.select(object_refs.c.dst_id, object_refs.c.role).where(
        object_refs.c.src_id == ident)).all())  # fmt: skip
    assert [(r.dst_id, r.role) for r in refs] == [(w.trend, "trend")]
    with dialect.write_tx(w.project.engine) as conn:
        err = refused(lambda: ts.delete(conn, w.trend), 409, "in_use")
    assert [(u["id"], u["kind"], u["role"]) for u in err.extra["usage"]] == [(ident, "implicit_model", "trend")]
    assert [(u["id"], u["name"]) for u in rd(w, ts.usage, w.trend)] == [(ident, "Литология")]
    # a model that takes the trend off frees it; one that names another trend moves the reference
    put(w, ident, 1, trend_id=None)
    with dialect.write_tx(w.project.engine) as conn:
        other = ts.create(conn, {"name": "Другой", "ang1": 10, "size": 50})["id"]
    put(w, ident, 2, trend_id=other)
    assert [(u["id"], u["name"]) for u in rd(w, ts.usage, other)] == [(ident, "Литология")]
    assert rd(w, ts.usage, w.trend) == []
    with dialect.write_tx(w.project.engine) as conn:
        ts.delete(conn, w.trend)
        refused(lambda: ts.delete(conn, other), 409, "in_use")
        st.delete(conn, w.store, ident)
        ts.delete(conn, other)  # the model is gone: so is its reference


# --- update -----------------------------------------------------------------------------------------------------------


def test_update_replaces_the_keys_and_keeps_the_rest(w: W) -> None:
    ident = make(w)["id"]
    out = put(w, ident, 1, levels=3, boundary={"box": [0, 0, -300, 500, 400, 100]}, h=None)
    assert out == {"id": ident, "name": "Литология", "version": 2}
    meta = get(w, ident)["meta"]
    assert (meta["levels"], meta["extent"], meta["name"]) == (3, [0.0, 0.0, -300.0, 500.0, 400.0, 100.0], "Литология")
    assert meta["h"] is None
    assert meta["h_used"] == pytest.approx(ms.resolve_h(math.sqrt(500.0**2 + 400.0**2 + 400.0**2)))  # h null — again
    assert meta["groups"][0]["codes"] == ["окисл"] and meta["created_at"]  # the others stay
    assert put(w, ident, 2, name="Новое имя")["name"] == "Новое имя"
    other = make(w, name="Другая")["id"]
    refused(lambda: put(w, other, 1, name="Новое имя"), 409, "name_taken", "name")
    refused(lambda: put(w, ident, 2), 409, "version_conflict")
    assert refused(lambda: put(w, ident, 2), 409, "version_conflict").extra == {"version": 3}
    for bad in (None, 0, "1", True, 1.5):
        refused(lambda bad=bad: put(w, ident, bad), 422, "bad_param", "expected_version")  # type: ignore[misc]
    refused(lambda: put(w, ident, 3, levels=9), 422, "bad_param", "levels")
    refused(lambda: put(w, 999_999, 1), 404, "model_not_found")
    assert get(w, ident)["version"] == 3  # a refused PUT changes nothing


def test_a_model_that_is_being_built_is_not_written(w: W) -> None:
    ident = make(w)["id"]
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.begin(conn, ident, 9), 409, "version_conflict")
        st.begin(conn, ident, 1, job_id=None)
    row = rd(w, lambda c: c.execute(sa.select(objects.c.state, objects.c.job_id).where(objects.c.id == ident)).first())
    assert tuple(row) == ("ready", None)  # `building` lives in `meta`: the clean-ups of the jobs drop `building` rows
    assert get(w, ident)["state"] == "building"
    refused(lambda: put(w, ident, 1, levels=3), 409, "model_building")
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.delete(conn, w.store, ident), 409, "model_building")
        refused(lambda: st.begin(conn, ident, 1), 409, "model_building")
        st.set_state(conn, ident, "error", error={"code": "iso_check", "message": "м", "hint": "п", "group": 1})
    got = get(w, ident)
    assert (got["state"], got["error"]["code"], got["version"]) == ("error", "iso_check", 1)
    assert rd(w, lambda c: c.execute(sa.select(objects.c.state).where(objects.c.id == ident)).scalar()) == "ready"
    assert put(w, ident, 1, levels=3)["version"] == 2


# --- «устарела» -------------------------------------------------------------------------------------------------------


def test_stale_by_the_inputs_and_by_the_parameters(w: W) -> None:
    wire, lay = solid(w, "Граница"), layer(w)
    ident = make(w, boundary={"wireframe_id": wire}, trend_id=w.trend,
                 groups=[{"name": "Р", "type": "domain", "id": 1}],
                 manual=[{"layer_id": lay, "role": "contact", "group": 1}])["id"]  # fmt: skip
    run(w, ident)
    fresh = {"stale": False, "stale_reason": None, "source_lost": False}
    assert rd(w, lambda c: st.freshness(c, get(w, ident)["meta"])) == fresh
    with dialect.write_tx(w.project.engine) as conn:  # the table of the codes is rewritten
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
    assert get(w, ident)["stale_reason"] == "изменена таблица «Литология»"
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev - 1))
        conn.execute(sa.update(objects).where(objects.c.id == lay).values(version=objects.c.version + 1))
    assert get(w, ident)["stale_reason"] == "изменён слой «Контакты»"
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == lay).values(version=objects.c.version - 1))
        conn.execute(sa.update(objects).where(objects.c.id == w.trend).values(version=objects.c.version + 1))
    assert get(w, ident)["stale_reason"] == "изменён тренд «Тренд СВ»"
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == w.trend).values(version=objects.c.version - 1))
    assert get(w, ident)["stale"] is False
    put(w, ident, 1, levels=3)  # the parameters differ from those of the last run
    assert (get(w, ident)["stale"], get(w, ident)["stale_reason"]) == (True, "изменены параметры")
    run(w, ident)
    assert get(w, ident)["stale"] is False
    put(w, ident, 2, levels=2)
    assert get(w, ident)["stale_reason"] == "изменены параметры"


def test_a_deleted_input_is_a_lost_source(w: W) -> None:
    wire, dtm = solid(w, "Граница"), surface(w, "ЦТМ", dtm=True)
    ident = make(w, boundary={"wireframe_id": wire, "dtm_id": dtm})["id"]
    with dialect.write_tx(w.project.engine) as conn:  # the version of an object is not its geometry
        conn.execute(sa.update(objects).where(objects.c.id == dtm).values(version=objects.c.version + 1))
    assert get(w, ident)["stale"] is False
    meta = get(w, ident)["meta"]  # a DTM and a wireframe are told apart in the reason
    for who, reason in ((wire, "изменён каркас «Граница»"), (dtm, "изменена ЦТМ «ЦТМ»")):
        one = [{**i, "fp": "other"} for i in meta["inputs"] if i["kind"] == "wireframe" and i["id"] == who]
        assert rd(w, st.freshness, {**meta, "inputs": one})["stale_reason"] == reason
    with dialect.write_tx(w.project.engine) as conn:
        ws.delete_wireframe(conn, wire)
    got = get(w, ident)
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (True, "источник удалён", True)
    assert rd(w, st.list_rows)[0]["source_lost"] is True
    assert got["meta"]["groups"]  # the parameters still read


# --- the blobs --------------------------------------------------------------------------------------------------------


def test_the_three_files_round_trip_and_are_replaced(w: W) -> None:
    ident = make(w)["id"]
    with pytest.raises(Exception) as e:  # not built yet
        rd(w, bl.load_fields, w.store, ident)
    assert getattr(e.value, "code", "") == "not_built"
    shas = write_result(w, ident)
    assert set(shas) == set(sb.BLOB_ROLES) and rd(w, bl.shas_of, ident) == shas
    got = rd(w, bl.load_fields, w.store, ident)
    assert sorted(got) == [(1, None), (2, None)]
    g2 = got[(2, None)]
    assert (g2.group, g2.wing) == (2, None) and list(g2.points["x"]) == [3.0, 4.0, 5.0]
    assert g2.points["kind"].dtype == np.int8 and g2.points["f"].dtype == np.float64
    assert np.isnan(g2.points["from"]).all() and list(g2.points["vertex"]) == [-1, -1, -1]
    assert list(g2.patches["n"]) == [3] and g2.patches["hash"][0] == bytes([2]) * 32
    assert [list(v) for v in g2.lam] == [[7.0, 8.0, 9.0]] and g2.c.shape == (1, 4)
    assert list(g2.c[0]) == [10.0, 11.0, 12.0, 13.0]  # c follows λ at `offset + n`
    _cols, meta = bl.read_points(w.store, shas[sb.ROLE_POINTS])
    assert meta == {"v": 1, "groups": [1, 2], "sources": ["A-1 0,0–2,0 м"]}
    # a second result replaces the first: the roles are the same, the old files lose their owner
    again = write_result(w, ident, shift=1.0)
    assert again[sb.ROLE_POINTS] != shas[sb.ROLE_POINTS] and rd(w, bl.shas_of, ident) == again
    n = rd(w, lambda c: c.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
        blob_owners.c.owner_id == ident)).scalar())  # fmt: skip
    assert n == 3
    # the files that do not fit each other are not read
    with dialect.write_tx(w.project.engine) as conn:
        staged = {sb.ROLE_SOLUTION: bl.stage_solution(w.store, np.zeros(5))}
        bl.commit(conn, w.store, ident, staged)
    with pytest.raises(Exception) as e2:
        rd(w, bl.load_fields, w.store, ident)
    assert getattr(e2.value, "code", "") == "not_built"
    with pytest.raises(ValueError):
        bl.stage_points(w.store, {"x": [1.0]}, groups=[1], sources=[])


# --- the outputs and delete -------------------------------------------------------------------------------------------


def test_outputs_are_children_and_go_with_the_model(w: W) -> None:
    ident = make(w, trend_id=w.trend)["id"]
    keep = make(w, name="Соседняя")["id"]
    first = output(w, ident, "Литология · Окисленные", 1, threshold=None)
    second = output(w, ident, "Литология · Окисленные", None, kind="body", wing=1, threshold=0.5)
    assert first["name"] == "Литология · Окисленные" and second["name"] == "Литология · Окисленные (2)"
    assert (second["wing"], second["threshold"]) == (1, 0.5) and "threshold" not in first
    other = output(w, keep, "Соседний", 1)["wireframe_id"]
    write_result(w, ident)
    run(w, ident, outputs=[first, second])
    kids = rd(w, lambda c: c.execute(sa.select(objects.c.id, objects.c.parent_id, objects.c.meta).where(
        objects.c.kind == "wireframe", objects.c.parent_id == ident).order_by(objects.c.id)).all())  # fmt: skip
    assert [k.id for k in kids] == [first["wireframe_id"], second["wireframe_id"]]
    assert kids[1].meta["source"] == {"op": "implicit", "model_id": ident, "group": None, "wing": 1,
                                      "threshold": 0.5}  # fmt: skip
    assert kids[0].meta["source"] == {"op": "implicit", "model_id": ident, "group": 1}
    got = get(w, ident)["outputs"]
    assert [(o["wireframe_id"], o["kind"], o["group"], o["wing"], o["threshold"], o["triangles"]) for o in got] == [
        (first["wireframe_id"], "body", 1, None, None, 48), (second["wireframe_id"], "body", None, 1, 0.5, 48)]
    assert got[0]["volume"] == pytest.approx(100.0**3) and got[0]["area"] == pytest.approx(6 * 100.0**2)
    # the graph: an output has the model as its input
    from bf_core import lineage

    with dialect.read_tx(w.project.engine) as conn:
        node = lineage.Node("wireframe", first["wireframe_id"])
        refs = lineage.store.inputs_of(conn, [node])[node]
        assert [r.node for r in refs] == [lineage.Node("object:implicit_model", ident)]
    with dialect.write_tx(w.project.engine) as conn:
        gone = st.delete(conn, w.store, ident)
    assert gone["deleted"] == ident and gone["wireframes"] == [first["wireframe_id"], second["wireframe_id"]]
    assert [r["id"] for r in gone["removed"]] == gone["wireframes"] and all(r["shas"] for r in gone["removed"])
    with dialect.read_tx(w.project.engine) as conn:
        left = conn.execute(sa.select(objects.c.id).where(objects.c.kind.in_(("implicit_model", "wireframe")))
                            .order_by(objects.c.id)).scalars().all()  # fmt: skip
        assert left == sorted([keep, other])  # the other model and its output stay
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
            blob_owners.c.owner_id == ident, blob_owners.c.owner_kind == "object")).scalar() == 0  # fmt: skip
        assert conn.execute(sa.select(sa.func.count()).select_from(object_refs).where(
            object_refs.c.src_id == ident)).scalar() == 0  # fmt: skip
        from bf_core.db.schema import lineage as lt

        assert conn.execute(sa.select(sa.func.count()).select_from(lt).where(
            lt.c.dst_id.in_([ident, first["wireframe_id"], second["wireframe_id"]]),
            lt.c.dst_kind.in_(("object:implicit_model", "wireframe")))).scalar() == 0  # fmt: skip
    refused(lambda: rd(w, st.get, ident), 404, "model_not_found")
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.delete(conn, w.store, ident), 404, "model_not_found")
        ts.delete(conn, w.trend)  # the reference went with the model


def test_an_output_in_use_stops_the_deletion(w: W) -> None:
    ident = make(w)["id"]
    out = output(w, ident, "Тело", 1)
    other = make(w, name="Друг")["id"]
    with dialect.write_tx(w.project.engine) as conn:  # something refers to the output
        conn.execute(sa.insert(object_refs).values(src_id=other, dst_id=out["wireframe_id"], role="drape"))
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.delete(conn, w.store, ident), 422, "wireframe_in_use")
    assert get(w, ident)["id"] == ident  # the transaction was rolled back: nothing of the model went


# --- the same on SQLite and PostgreSQL --------------------------------------------------------------------------------


@pytest.mark.db
def test_store_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The store without the API on SQLite and PostgreSQL: create, update with the version, the reference to the
    trend and 409 `in_use`, the blobs, the outputs, «устарела», delete with the outputs (`-m db`; BF_TEST_PG)."""
    w = world(tmp_path, db_url)
    try:
        ident = make(w, trend_id=w.trend)["id"]
        assert put(w, ident, 1, levels=3)["version"] == 2
        refused(lambda: put(w, ident, 1), 409, "version_conflict")
        with dialect.write_tx(w.project.engine) as conn:
            refused(lambda: ts.delete(conn, w.trend), 409, "in_use")
        first = output(w, ident, "Тело", 1)
        write_result(w, ident)
        run(w, ident, outputs=[first])
        assert get(w, ident)["stale"] is False and len(rd(w, bl.load_fields, w.store, ident)) == 2
        with dialect.write_tx(w.project.engine) as conn:
            conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
        assert get(w, ident)["stale_reason"] == "изменена таблица «Литология»"
        with dialect.write_tx(w.project.engine) as conn:
            assert st.delete(conn, w.store, ident)["wireframes"] == [first["wireframe_id"]]
            ts.delete(conn, w.trend)
        assert rd(w, st.list_rows) == []
    finally:
        w.project.engine.dispose()
