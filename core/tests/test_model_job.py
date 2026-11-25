"""The jobs `implicit.build` / `implicit.update` (подшаг 19.12; ТЗ 8.9.3; ADR 0026 решения 22–26; e19-api §2, §6, §7):
a model of the fixture scene (`tests/fixtures/implicit` through `test_model_data_load.World`) built into its files and
its outputs, the same bits with 1, 2, 4, 8 threads, the cancel at every stage leaves nothing (no file, no wireframe, no
row), an error puts the model into `error` and writes nothing, the outputs of the last run are replaced in place, the
refusals of Э19б, the shells, the ids of the files for `.bfggis`, the laziness of the module. The update is
`test_model_update.py`; the job in a worker — `test_model_job_worker.py`. The database tests: SQLite always,
PostgreSQL with BF_TEST_PG (`-m db`)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db import schema as schema
from bf_core.db.schema import blob_owners, objects
from bf_core.jobs.context import JobCancelled
from bf_core.model import job
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_model
from bf_core.wireframes import store as wfs
from tests.test_model_data_load import World, domain

pytestmark = pytest.mark.db
CORE = Path(__file__).resolve().parents[1]
BOX = [0.0, 0.0, 120.0, 420.0, 420.0, 330.0]
LAYERS = (("INT", 1), ("ALV", 2), ("SS", 3), ("SH", 4), ("GR", 5))


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The roles of the files of the model until the line of the orchestrator (`db/schema.py` BLOB_ROLES) is in."""
    if not set(sb.BLOB_ROLES) <= schema.BLOB_ROLES:
        monkeypatch.setattr(schema, "BLOB_ROLES", schema.BLOB_ROLES | set(sb.BLOB_ROLES))


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


def groups_of(*names: str) -> list[dict[str, Any]]:
    gid = dict(LAYERS)
    return [domain(gid[n], n, n) for n in names]


def make(w: World, name: str, groups: list[dict[str, Any]], *, table: Any = None, h: float = 8.0,
         **over: Any) -> dict[str, Any]:  # fmt: skip
    """A model of the scene with `groups` (the youngest first), the step `h` (coarse, for the speed of the tests)."""
    spec = w.spec(groups, **({"table": str(table)} if table is not None else {}))
    body = {**spec.canonical(), "boundary": {"box": BOX}, "h": h, "name": name, **over}
    with dialect.write_tx(w.engine) as conn:
        return st.create(conn, w.store, w.project, parse_model(body))


def version(w: World, mid: int) -> int:
    with dialect.read_tx(w.engine) as conn:
        return int(sb.stored(conn, mid).version)


def run(w: World, mid: int, *, update: bool = False, tick: Any = None, threads: int = 4,
        **params: Any) -> dict[str, Any]:  # fmt: skip
    p = {"model_id": mid, "expected_version": version(w, mid), "threads": threads, **params}
    return job.run_build(w.engine, w.project.folder, p, tick or (lambda _p, _m: None), update=update)


def meta_of(w: World, mid: int) -> dict[str, Any]:
    with dialect.read_tx(w.engine) as conn:
        return dict(sb.stored(conn, mid).meta)


def state_of(w: World, mid: int) -> str:
    with dialect.read_tx(w.engine) as conn:
        return str(sb.stored(conn, mid).state)


def kids(w: World, mid: int) -> list[int]:
    with dialect.read_tx(w.engine) as conn:
        return [int(i) for i in conn.execute(sa.select(objects.c.id).where(
            objects.c.parent_id == mid, objects.c.kind == "wireframe").order_by(objects.c.id)).scalars()]  # fmt: skip


def shas(w: World, mid: int) -> dict[str, str]:
    with dialect.read_tx(w.engine) as conn:
        return {str(r.role): str(r.sha256) for r in conn.execute(sa.select(blob_owners).where(
            blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == mid))}  # fmt: skip


def geometry(w: World, mid: int) -> list[tuple[str, bytes, bytes]]:
    """(name, vertices, triangles) of every output, as bytes."""
    with dialect.read_tx(w.engine) as conn:
        outs = st.outputs_of(conn, sb.stored(conn, mid).meta)
        got = [(o["name"], *wfs.read_wireframe(conn, w.store, o["wireframe_id"])) for o in outs]
    return [(n.split(" · ", 1)[1], v.tobytes(), t.tobytes()) for n, v, t in got]  # the model's name is not geometry


def stage_names(msgs: list[str]) -> set[str]:
    return {m.split(":", 1)[0] for m in msgs if ":" in m}


# --- the build ------------------------------------------------------------------------------------------------------


def test_a_build_writes_the_files_the_outputs_and_the_run(world: World) -> None:
    w = world
    m = make(w, "Слои", groups_of("ALV", "SS", "SH"))
    msgs: list[str] = []
    out = run(w, m["id"], tick=lambda _p, text: msgs.append(text or ""))
    assert {"Данные", "Участки", "Изоповерхности", "Тела", "Проверки", "Запись"} <= stage_names(msgs)
    assert out["state"] == "ready" and out["mode"] == "build" and out["reused"] == {"patches": 0, "bricks": 0}
    assert out["events"][0] == ["implicit.changed", {"kind": "implicit_model", "id": m["id"]}]
    kinds = [(o["kind"], o["group"]) for o in out["outputs"]]
    assert kinds == [("surface", 2), ("body", 2), ("surface", 3), ("body", 3), ("surface", 4), ("body", 4),
                     ("body", None)]  # fmt: skip
    assert [o["name"] for o in out["outputs"]] == ["Слои · ALV · поверхность", "Слои · ALV", "Слои · SS · поверхность",
                                                  "Слои · SS", "Слои · SH · поверхность", "Слои · SH",
                                                  "Слои · Прочее"]  # fmt: skip
    assert all(o["triangles"] > 0 and o["area"] > 0 for o in out["outputs"])
    assert all(o["volume"] > 0 for o in out["outputs"] if o["kind"] == "body")
    meta = meta_of(w, m["id"])
    assert meta["state"] == "ready" and meta["error"] is None and state_of(w, m["id"]) == "ready"
    assert meta["last_run"]["mode"] == "build" and meta["last_run"]["params"] == sb.params_hash(sb.load_spec(meta))
    assert [o["wireframe_id"] for o in meta["outputs"]] == [o["wireframe_id"] for o in out["outputs"]]
    assert sorted(o["wireframe_id"] for o in out["outputs"]) == kids(w, m["id"])
    assert set(shas(w, m["id"])) == set(sb.BLOB_ROLES)
    s = meta["stats"]
    assert s["points"] > 0 and s["patches"] == sum(g["patches"] for g in s["by_group"]) > 0
    assert [g["group"] for g in s["by_group"]] == [2, 3, 4] and s["max_residual"] < 1e-6 and s["cond_max"] > 0
    assert s["reused"] == {"patches": 0, "bricks": 0} and out["stats"]["balance"] < 1e-9
    assert all(set(g) >= {"group", "wing", "points", "patches", "h_bar", "max_residual", "cond_max", "eta_min",
                          "eta_max", "support_share"} for g in s["by_group"])  # fmt: skip
    with dialect.read_tx(w.engine) as conn:
        fields = st.load_fields(conn, w.store, m["id"])
        listed = st.get(conn, m["id"])
        wf = conn.execute(sa.select(objects.c.meta, objects.c.parent_id).where(
            objects.c.id == out["outputs"][1]["wireframe_id"])).one()  # fmt: skip
    assert set(fields) == {(2, None), (3, None), (4, None)}
    counts = [g["patches"] for g in s["by_group"]]
    assert counts == [len(f.lam) for f in fields.values()] == [len(f.patches["j"]) for f in fields.values()]
    assert listed["stale"] is False and [o["wireframe_id"] for o in listed["outputs"]] == sorted(kids(w, m["id"]))
    assert wf.parent_id == m["id"] and wf.meta["source"] == {"op": "implicit", "model_id": m["id"], "group": 2}
    pts = fields[(2, None)].points
    assert set(pts) >= {"x", "y", "z", "f", "kind", "group", "wing", "src"} and (pts["group"] == 2).all()


def test_the_files_hold_the_patches_and_the_solutions_of_the_field(world: World) -> None:
    """The three files fit each other (`load_fields`): a row per patch, its λ and c in the solution, η = 0 of a domain
    and a hash of its own."""
    w = world
    m = make(w, "Данные", groups_of("SS"))
    run(w, m["id"])
    with dialect.read_tx(w.engine) as conn:
        f = st.load_fields(conn, w.store, m["id"])[(3, None)]
    assert f.patches["n"].sum() == sum(len(x) for x in f.lam) and np.isfinite(np.concatenate(f.lam)).all()
    assert (f.patches["cond"] > 0).all() and (f.patches["eta"] == 0).all() and f.c.shape == (len(f.lam), 4)
    raw = f.patches["hash"].tobytes()
    assert len({raw[i : i + 32] for i in range(0, len(f.lam) * 32, 32)}) == len(f.lam)  # a hash of a patch each


@pytest.mark.parametrize("threads", [2, 8])
def test_the_same_bits_with_1_2_4_8_threads(world: World, threads: int) -> None:
    w = world
    names = groups_of("ALV", "SS")
    a = make(w, "Один", names)
    b = make(w, "Много", names)
    run(w, a["id"], threads=1)
    out = run(w, b["id"], threads=threads)
    assert geometry(w, a["id"]) == geometry(w, b["id"]) and len(out["outputs"]) == 5
    assert shas(w, a["id"]) == shas(w, b["id"])  # the points, the patches with their hashes, the solution: bit for bit


def test_the_relations_of_e19b_are_refused_not_ready(world: World) -> None:
    w = world
    m = make(w, "Разлом", groups_of("SS"))
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, m["id"], {"groups": [
            {**groups_of("SS")[0], "relation": "erode"}]}, 1)  # fmt: skip
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.status == 422 and e.value.code == "not_ready" and e.value.hint and "SS" in e.value.message
    assert state_of(w, m["id"]) == "ready" and meta_of(w, m["id"])["state"] == "error"  # the reason is in the model
    assert meta_of(w, m["id"])["error"]["code"] == "not_ready" and kids(w, m["id"]) == []


# --- the cancel and the errors --------------------------------------------------------------------------------------


class Cancel:
    """A tick that raises at the first message of a stage."""

    def __init__(self, stage: str) -> None:
        self.stage, self.fired = stage + ":", False

    def __call__(self, _p: float | None, text: str | None) -> None:
        if text and text.startswith(self.stage) and not self.fired:
            self.fired = True
            raise JobCancelled


@pytest.mark.parametrize("stage", ["Данные", "Участки", "Изоповерхности", "Тела", "Проверки", "Запись"])
def test_a_cancel_at_any_stage_leaves_nothing(world: World, stage: str) -> None:
    w = world
    m = make(w, "Отмена", groups_of("SS", "SH"))
    before = meta_of(w, m["id"])
    cancel = Cancel(stage)
    with pytest.raises(JobCancelled) as e:
        run(w, m["id"], tick=cancel)
    assert cancel.fired and e.value.events == [["implicit.changed", {"kind": "implicit_model", "id": m["id"]}]]
    after = meta_of(w, m["id"])
    assert state_of(w, m["id"]) == "ready" and after["state"] == "ready" and after["error"] is None
    assert {k: v for k, v in after.items() if k != "state"} == {k: v for k, v in before.items() if k != "state"}
    assert shas(w, m["id"]) == {} and kids(w, m["id"]) == []
    with dialect.read_tx(w.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind == "wireframe")).scalar() == 0  # fmt: skip
    assert not list(w.store.tmp_dir.glob("*")) if w.store.tmp_dir.exists() else True  # no temp file of a staged blob
    out = run(w, m["id"])  # the model is as it was: a run after the cancel is a plain build
    assert out["state"] == "ready" and len(out["outputs"]) == 5


def test_a_cancel_of_a_rebuild_keeps_the_last_run(world: World) -> None:
    w = world
    m = make(w, "Прежний", groups_of("SS"))
    first = run(w, m["id"])
    with pytest.raises(JobCancelled):
        run(w, m["id"], tick=Cancel("Запись"))
    meta = meta_of(w, m["id"])
    assert meta["state"] == "ready" and [o["wireframe_id"] for o in meta["outputs"]] == [
        o["wireframe_id"] for o in first["outputs"]]  # fmt: skip
    assert sorted(kids(w, m["id"])) == sorted(o["wireframe_id"] for o in first["outputs"])


def test_an_error_puts_the_model_into_error_and_writes_nothing(world: World) -> None:
    w = world
    groups = groups_of("SS")
    m = make(w, "Ошибка", groups)
    first = run(w, m["id"])
    ids, files = sorted(kids(w, m["id"])), shas(w, m["id"])
    with dialect.write_tx(w.engine) as conn:
        st.update(conn, w.store, w.project, m["id"], {"groups": [*groups, domain(9, "Пусто", "XX")]},
                  version(w, m["id"]))  # fmt: skip
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.status == 422 and e.value.code == "no_contacts" and e.value.hint
    meta = meta_of(w, m["id"])
    assert meta["state"] == "error" and meta["error"]["code"] == "no_contacts" and meta["error"]["group"] == 9
    assert meta["error"]["message"] and meta["error"]["hint"] and state_of(w, m["id"]) == "ready"
    assert sorted(kids(w, m["id"])) == ids and shas(w, m["id"]) == files  # the last run stays
    assert [o["wireframe_id"] for o in meta["outputs"]] == [o["wireframe_id"] for o in first["outputs"]]
    with dialect.read_tx(w.engine) as conn:
        assert st.get(conn, m["id"])["state"] == "error"
    again = make(w, "Новая", [domain(9, "Пусто", "XX")])  # a model that never was built: no wireframe at all
    with pytest.raises(ApiError):
        run(w, again["id"])
    assert kids(w, again["id"]) == [] and shas(w, again["id"]) == {}


def test_the_job_checks_the_version_and_the_building_state(world: World) -> None:
    w = world
    m = make(w, "Версия", groups_of("SS"))
    with pytest.raises(ApiError) as e:
        job.run_build(w.engine, w.project.folder, {"model_id": m["id"], "expected_version": 7}, lambda *_: None)
    assert e.value.status == 409 and e.value.code == "version_conflict" and e.value.extra["version"] == 1
    with dialect.write_tx(w.engine) as conn:
        st.begin(conn, m["id"], 1, job_id=111)  # another job has it
    with pytest.raises(ApiError) as e2:
        job.run_build(w.engine, w.project.folder, {"model_id": m["id"], "expected_version": 1}, lambda *_: None,
                      job_id=222)  # fmt: skip
    assert e2.value.status == 409 and e2.value.code == "model_building"
    assert meta_of(w, m["id"])["state"] == "building"  # the job that has it is not disturbed
    with pytest.raises(ApiError) as e3:
        job.run_build(w.engine, w.project.folder, {"model_id": 999_999}, lambda *_: None)
    assert e3.value.status == 404 and e3.value.code == "model_not_found"


def test_the_route_may_have_marked_the_model_and_a_cancel_restores_what_it_says(world: World) -> None:
    w = world
    m = make(w, "Маршрут", groups_of("SS"))
    with dialect.write_tx(w.engine) as conn:
        st.begin(conn, m["id"], 1)  # the route (19.16) marks the model before it queues the job
    err = {"code": "iso_check", "message": "было", "hint": "раньше"}
    with pytest.raises(JobCancelled):
        params = {"model_id": m["id"], "expected_version": 1, "previous_state": "error", "previous_error": err}
        job.run_build(w.engine, w.project.folder, params, Cancel("Участки"), job_id=5)
    meta = meta_of(w, m["id"])
    assert meta["state"] == "error" and meta["error"] == err and state_of(w, m["id"]) == "ready"


def test_a_model_edited_while_it_was_built_is_not_overwritten(world: World) -> None:
    w = world
    m = make(w, "Правка", groups_of("SS"))

    def edit(_p: float | None, text: str | None) -> None:
        if text and text.startswith("Запись") and not edit.done:  # type: ignore[attr-defined]
            edit.done = True  # type: ignore[attr-defined]
            with dialect.write_tx(w.engine) as conn:  # a window saves the model (the route would refuse: building)
                conn.execute(sa.update(objects).where(objects.c.id == m["id"]).values(version=5))

    edit.done = False  # type: ignore[attr-defined]
    with pytest.raises(ApiError) as e:
        run(w, m["id"], tick=edit)
    assert e.value.status == 409 and e.value.code == "version_conflict"
    assert shas(w, m["id"]) == {} and kids(w, m["id"]) == []


# --- the outputs of the last run are replaced in place -------------------------------------------------------------


def test_a_second_build_replaces_the_outputs_in_place_and_deletes_the_ones_that_are_no_more(world: World) -> None:
    w = world
    m = make(w, "Заново", groups_of("ALV", "SS", "SH"))
    first = run(w, m["id"])
    ids = {(o["kind"], o["group"]): o["wireframe_id"] for o in first["outputs"]}
    with dialect.read_tx(w.engine) as conn:
        versions = {i: int(conn.execute(sa.select(objects.c.version).where(objects.c.id == i)).scalar_one())
                    for i in kids(w, m["id"])}  # fmt: skip
    with dialect.write_tx(w.engine) as conn:  # the group SH is gone, the model is edited
        st.update(conn, w.store, w.project, m["id"], {"groups": groups_of("ALV", "SS")}, 1)
    second = run(w, m["id"])
    got = {(o["kind"], o["group"]): o["wireframe_id"] for o in second["outputs"]}
    assert set(got) == {("surface", 2), ("body", 2), ("surface", 3), ("body", 3), ("body", None)}
    assert all(got[k] == ids[k] for k in got)  # the same ids: what refers to them keeps its reference
    gone = [ids[("surface", 4)], ids[("body", 4)]]
    assert sorted(kids(w, m["id"])) == sorted(got.values()) and not set(gone) & set(kids(w, m["id"]))
    with dialect.read_tx(w.engine) as conn:
        for i in got.values():
            now = int(conn.execute(sa.select(objects.c.version).where(objects.c.id == i)).scalar_one())
            assert now == versions[i] + 1
    assert ["objects.changed", {"kind": "wireframe", "id": gone[0], "deleted": True}] in second["events"]
    assert shas(w, m["id"]).keys() == set(sb.BLOB_ROLES)


def test_an_output_other_objects_refer_to_cannot_go_and_nothing_is_written(world: World) -> None:
    w = world
    m = make(w, "Занят", groups_of("ALV", "SS"))
    first = run(w, m["id"])
    victim = next(o["wireframe_id"] for o in first["outputs"] if (o["kind"], o["group"]) == ("body", 3))
    with dialect.write_tx(w.engine) as conn:
        now = dialect.utcnow()
        other = int(conn.execute(sa.insert(objects).values(
            kind="note", name="Ссылка", name_key="ссылка", sort_key=0.0, state="ready", version=1, meta={},
            created_at=now, updated_at=now)).inserted_primary_key[0])  # type: ignore[index]
        conn.execute(sa.insert(schema.object_refs).values(src_id=other, dst_id=victim, role="use"))
        st.update(conn, w.store, w.project, m["id"], {"groups": groups_of("ALV")}, 1)
    before = shas(w, m["id"])
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.code == "wireframe_in_use" and e.value.hint
    assert shas(w, m["id"]) == before
    assert sorted(kids(w, m["id"])) == sorted(o["wireframe_id"] for o in first["outputs"])


# --- the shells and the other groups ---------------------------------------------------------------------------------


def shell(gid: int, w: World, **params: Any) -> dict[str, Any]:
    given = {"source": "table", "table": str(w.assays), "field": "AU", "thresholds": [0.3, 1.0], **params}
    return {"id": gid, "name": f"Au {gid}", "type": "shell", "codes": [], "params": given}


def test_a_shell_group_builds_the_nested_bodies_and_its_fields_are_in_the_files(world: World) -> None:
    w = world
    m = make(w, "Оболочки", [*groups_of("SS"), shell(6, w)])
    out = run(w, m["id"], threads=4)
    bodies = [o for o in out["outputs"] if o["group"] == 6]
    assert [o["threshold"] for o in bodies] == [0.3, 1.0] and all(o["kind"] == "body" for o in bodies)
    assert bodies[1]["volume"] <= bodies[0]["volume"] and all(o["volume"] > 0 for o in bodies)  # k is inside k − 1
    assert [o["name"] for o in bodies] == ["Оболочки · Au 6 · 0,3", "Оболочки · Au 6 · 1"]
    with dialect.read_tx(w.engine) as conn:
        fields = st.load_fields(conn, w.store, m["id"])
    assert set(fields) == {(3, None), (6, None)} and len(fields[(6, None)].lam) > 0
    again = run(w, m["id"], update=True)
    assert again["reused"]["patches"] == out["stats"]["patches"] > 0  # nothing changed: every patch is in the cache
    assert [(o["name"], o["volume"]) for o in again["outputs"]] == [(o["name"], o["volume"]) for o in out["outputs"]]


def test_an_expansion_and_a_vein_are_built_in_their_order(world: World) -> None:
    from bf_core.wireframes import store as ws
    from tests.meshutil import cube

    w = world
    v, t = cube(2, 80.0, (150.0, 150.0, 180.0), seed=None)
    with dialect.write_tx(w.engine) as conn:
        wid = ws.put_wireframe(conn, w.store, v, t, name="Куб")
    expand = {"id": 7, "name": "Куб +5", "type": "expand", "codes": [], "params": {"wireframe_id": wid, "delta": 5.0}}
    m = make(w, "Расширение", [*groups_of("SS"), expand])
    out = run(w, m["id"])
    ex = [o for o in out["outputs"] if o["group"] == 7]
    assert len(ex) == 1 and ex[0]["kind"] == "body" and ex[0]["name"] == "Расширение · Куб +5"
    # a cube of 80 m grown by 5 m: 90³ with the rounded edges and corners, less than the square cube of 90 m
    assert 80.0**3 < ex[0]["volume"] < 90.0**3
    again = run(w, m["id"])  # the outputs of the last run are replaced in place
    assert [o["wireframe_id"] for o in again["outputs"]] == [o["wireframe_id"] for o in out["outputs"]]
    vein = {"id": 8, "name": "Жила", "type": "vein", "codes": ["VN"], "params": {"bound": "hull", "offset": 0.0}}
    mv = make(w, "Жилы", [*groups_of("SS"), vein])
    outv = run(w, mv["id"])
    jv = [o for o in outv["outputs"] if o["group"] == 8]
    assert len(jv) == 1 and jv[0]["volume"] > 0 and jv[0]["kind"] == "body"
    assert any("Жила" in line or "жила" in line for line in outv["journal"])


def test_a_structural_trend_has_no_global_matrix_and_is_not_refused(world: World) -> None:
    """19.12б: the matrices of the patches go to the solver and to the field (`test_model_job_structural.py`); the
    model has no global A for such a trend and says which trend it is."""
    from bf_core.model import job_stages

    w = world
    m = make(w, "Тренд", groups_of("SS"))
    spec = sb.load_spec(meta_of(w, m["id"]))
    with dialect.read_tx(w.engine) as conn:
        assert job_stages.anisotropy_of(conn, spec) == (None, None)  # no trend, no anisotropy: nothing to turn
        assert job_stages.global_matrix(conn, spec) is None


# --- the grid the bricks are keyed by, the files for .bfggis, the handlers ------------------------------------------


def test_the_grid_of_the_bricks_is_the_grid_of_the_isosurface(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """`BrickFiles` hashes the token of the grid it builds itself from the boundary: it must be the grid `isosurface`
    makes of the same box (else the keys name other bricks)."""
    from bf_core.geo import iso

    real, seen = iso.isosurface, []

    def spy(*args: Any, **kw: Any) -> Any:
        mesh = real(*args, **kw)
        seen.append((kw["cache"], mesh.grid.token()))
        return mesh

    monkeypatch.setattr(iso, "isosurface", spy)
    w = world
    m = make(w, "Сетка", groups_of("SS", "SH"), levels=3, h=9.0)
    run(w, m["id"])
    assert len(seen) == 2 and all(c._token.decode().split("|")[0] == tok for c, tok in seen)  # noqa: SLF001


def test_the_ids_of_the_outputs_are_what_a_bfggis_renumbers(world: World) -> None:
    from bf_core.model import remap

    w = world
    m = make(w, "Архив", groups_of("ALV", "SS"))
    run(w, m["id"])
    meta = meta_of(w, m["id"])
    old = [o["wireframe_id"] for o in meta["outputs"]]
    ids = remap.Remap(objects={i: i + 1000 for i in [*old, m["id"], w.set_id]}, tables={w.litho: w.litho + 50})
    new = remap.remap_meta(meta, ids)
    assert [o["wireframe_id"] for o in new["outputs"]] == [i + 1000 for i in old]
    assert new["data"]["set_id"] == w.set_id + 1000 and new["data"]["table"] == str(w.litho + 50)
    assert new["stats"]["points"] == meta["stats"]["points"] and new["last_run"]["mode"] == "build"
    with dialect.read_tx(w.engine) as conn:  # the children of the model name it as their source (renumbered with it)
        src = [conn.execute(sa.select(objects.c.meta).where(objects.c.id == i)).scalar_one()["source"] for i in old]
    assert all(s["model_id"] == m["id"] and s["op"] == "implicit" for s in src)


def test_the_handlers_and_the_memory_and_the_laziness() -> None:
    import bf_core.model.job as j  # noqa: F401 - registers the handlers
    from bf_core.jobs.registry import KINDS

    assert {"implicit.build", "implicit.update"} <= set(KINDS)
    assert all(KINDS[k].queue == "heavy" and KINDS[k].max_attempts == 1 for k in ("implicit.build", "implicit.update"))
    assert job.memory_mb({"threads": 8, "points": 2_000_000}) > job.memory_mb({"threads": 1, "points": 10_000}) > 400
    assert job.memory_mb({}) > 0
    code = ("import bf_core.model.job, sys; bad = [m for m in ('numba', 'manifold3d', 'pyfqmr') if m in sys.modules]; "
            "raise SystemExit(3 if bad else 0)")
    env = {**os.environ, "PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1"}
    run_ = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120, env=env)
    assert run_.returncode == 0, run_.stderr[-400:]
