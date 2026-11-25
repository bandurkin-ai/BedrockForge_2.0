"""The run of the implicit models through the routes (подшаг 19.16; ADR 0026 «API», решения 22–26, 30; docs/progress/
e19-api.md §2, §5–§7): `POST …/build` and `…/update` refused before the job (409, 422, 423), the job in a worker, the
state `building` → `ready`, «устарела» after a changed parameter and its `update` from the cache, `…/outputs`,
«В каркас» (a copy without the parent that stays), the project archive with the built model, the deletion (the outputs,
the files and the cache go; 409 `in_use` when other objects refer to an output), a job that fails puts the model into
`error`. The router is added to the app here until the line of the orchestrator (`tests/modapi_world.py`)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects
from bf_core.projects.service import projects_of
from tests import modapi_world as W
from tests.estapi_util import finish, ok, refused
from tests.modapi_world import IMP, body, im, reg  # noqa: F401 - the fixtures
from tests.test_import_job import API, wait_job


def get(c: Any, ident: int) -> dict[str, Any]:
    return ok(c.get(f"{IMP}/models/{ident}"))  # type: ignore[no-any-return]


def start(c: Any, ident: int, what: str, **over: Any) -> dict[str, Any]:
    version = get(c, ident)["version"]
    r = c.post(f"{IMP}/models/{ident}/{what}", json={"expected_version": version, **over})
    return ok(r)  # type: ignore[no-any-return]


def wait_state(c: Any, ident: int, state: str, timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while get(c, ident)["state"] != state:
        assert time.monotonic() < end, f"the model did not become {state}"
        time.sleep(0.05)


def kids(c: Any, ident: int) -> list[int]:
    with dialect.read_tx(c.project.engine) as conn:
        return [int(i) for i in conn.execute(sa.select(objects.c.id).where(
            objects.c.parent_id == ident, objects.c.kind == "wireframe").order_by(objects.c.id)).scalars()]  # fmt: skip


def alive(c: Any, ident: int) -> bool:
    with dialect.read_tx(c.project.engine) as conn:
        return conn.execute(sa.select(objects.c.id).where(objects.c.id == ident)).first() is not None


def cache_dir(c: Any, ident: int) -> Path:
    return Path(c.project.folder) / "cache" / "implicit" / str(ident)


def test_refused_before_the_job(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = ok(c.post(f"{IMP}/models", json=body(c.w)))["id"]
    for what in ("build", "update"):
        url = f"{IMP}/models/{mid}/{what}"
        refused(c.post(url, json={"expected_version": 9}), 409, "version_conflict")
        refused(c.post(url, json={}), 422, "bad_param", "expected_version")
        refused(c.post(url, json={"expected_version": 1, "threads": 0}), 422, "bad_param", "threads")
        refused(c.post(url, json={"expected_version": 1, "threads": 65}), 422, "bad_param", "threads")
        refused(c.post(url, json=[1]), 422, "bad_request")
        refused(c.post(f"{IMP}/models/{mid + 99}/{what}", json={"expected_version": 1}), 404, "model_not_found")
    assert get(c, mid)["state"] == "ready" and get(c, mid)["version"] == 1  # nothing of it marked the model
    # a group of Э19б (a fault) and a relation other than «Согласно» are `not_ready` until 19.32–19.36
    fault = {"name": "Разлом", "type": "fault", "codes": ["VN"]}
    ok(c.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 1, "group": fault}))
    refused(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 2}), 422, "not_ready")
    assert get(c, mid)["state"] == "ready"
    ok(c.delete(f"{IMP}/models/{mid}/groups/4", params={"expected_version": 2}))
    onlap = {**W.domain("ALV"), "relation": "onlap"}
    ok(c.post(f"{IMP}/models/{mid}/groups", json={"expected_version": 3, "group": onlap}))
    refused(c.post(f"{IMP}/models/{mid}/update", json={"expected_version": 4}), 422, "not_ready")
    # a model whose reference went: the row stays readable, the run says what is missing
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == c.w.set_id))
    assert get(c, mid)["source_lost"] is True
    refused(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 4}), 404, "set_not_found", "data.set_id")


def test_a_queue_that_refuses_the_job_leaves_the_model_as_it_was(im, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """The route does not mark the model: nothing to take back when `submit` fails (or the job is cancelled in the
    queue, or the core stops between the two)."""
    from bf_core.api.errors import ApiError
    from bf_core.jobs.scheduler import Scheduler

    c = im
    mid = ok(c.post(f"{IMP}/models", json=body(c.w)))["id"]
    seen: list[str] = []

    def refuse(_self: Any, *_a: Any, **_k: Any) -> Any:
        seen.append(get(c, mid)["state"])  # what the model is while the job is being queued
        raise ApiError(503, "queue_full", "Очередь заданий переполнена.", "Повторите позже.")

    monkeypatch.setattr(Scheduler, "submit", refuse)
    refused(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 1}), 503, "queue_full")
    assert seen == ["ready"] and get(c, mid)["state"] == "ready"
    with dialect.read_tx(c.project.engine) as conn:  # `objects.state` is never `building` for a model
        row = conn.execute(sa.select(objects.c.state, objects.c.job_id).where(objects.c.id == mid)).first()
    assert tuple(row) == ("ready", None)
    ok(c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "levels": 3}))  # not stuck: no 409 `model_building`


def test_a_second_build_while_the_first_job_is_queued_or_running_is_refused(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = ok(c.post(f"{IMP}/models", json=body(c.w)))["id"]
    started = start(c, mid, "build", threads=2)
    for what in ("build", "update"):  # at once: the job may still be in the queue, the model is «идёт построение»
        refused(c.post(f"{IMP}/models/{mid}/{what}", json={"expected_version": 1}), 409, "model_building")
    refused(c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "levels": 3}), 409, "model_building")
    refused(c.delete(f"{IMP}/models/{mid}"), 409, "model_building")
    assert get(c, mid)["state"] == "building"
    assert finish(c, started, timeout=600)["status"] == "done"
    assert get(c, mid)["state"] == "ready"
    ok(c.put(f"{IMP}/models/{mid}", json={"expected_version": get(c, mid)["version"], "levels": 3}))


def test_a_job_that_fails_puts_the_model_into_error(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    ghost = {"name": "Призрак", "type": "domain", "codes": ["НЕТ"]}
    mid = ok(c.post(f"{IMP}/models", json=body(c.w, groups=[W.domain("INT"), ghost])))["id"]
    started = start(c, mid, "update")  # no earlier result: the same as the full build
    assert sorted(started) == ["id", "source"]
    job = finish(c, started)
    assert job["status"] == "failed", job
    assert job["error"]["code"] == "no_contacts" and job["error"]["message"] and job["error"]["hint"]
    got = get(c, mid)
    assert got["state"] == "error" and got["outputs"] == [] and got["version"] == 1
    assert got["error"]["code"] == "no_contacts" and got["error"]["group"] == 2 and got["error"]["hint"]
    assert ok(c.get(f"{IMP}/models"))["models"][0]["state"] == "error"
    assert kids(c, mid) == [] and not cache_dir(c, mid).exists()  # nothing was written
    # the model can be built again once its group is fixed (the state `error` is no obstacle)
    ok(c.put(f"{IMP}/models/{mid}/groups/2", json={"expected_version": 1, "group": {
        "name": "Призрак", "type": "domain", "codes": ["SS"]}}))  # fmt: skip
    assert get(c, mid)["state"] == "error"  # the edit does not hide the last failure
    ok(c.delete(f"{IMP}/models/{mid}"))


def test_build_update_copy_archive_delete(im, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = ok(c.post(f"{IMP}/models", json=body(c.w)))["id"]
    started = start(c, mid, "build", threads=2)
    assert sorted(started) == ["id", "source"]
    wait_state(c, mid, "building")  # the job marks the model when it starts, not the route when it queues it
    refused(c.post(f"{IMP}/models/{mid}/build", json={"expected_version": 1}), 409, "model_building")
    refused(c.post(f"{IMP}/models/{mid}/update", json={"expected_version": 1}), 409, "model_building")
    refused(c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "h": 6}), 409, "model_building")
    refused(c.delete(f"{IMP}/models/{mid}"), 409, "model_building")
    job = finish(c, started, timeout=600)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["state"] == "ready" and res["mode"] == "build" and res["reused"] == {"patches": 0, "bricks": 0}
    assert [(o["kind"], o["group"], o["name"]) for o in res["outputs"]] == [
        ("surface", 1, "Литология · INT · поверхность"), ("body", 1, "Литология · INT"),
        ("surface", 3, "Литология · SS · поверхность"), ("body", 3, "Литология · SS"),
        ("body", None, "Литология · Прочее")]  # fmt: skip
    got = get(c, mid)
    assert (got["state"], got["error"], got["stale"], got["source_lost"]) == ("ready", None, False, False)
    assert [o["wireframe_id"] for o in got["outputs"]] == [o["wireframe_id"] for o in res["outputs"]] == kids(c, mid)
    assert all(o["triangles"] > 0 and o["area"] > 0 for o in got["outputs"])
    assert ok(c.get(f"{IMP}/models/{mid}/outputs")) == {"outputs": got["outputs"]}
    assert got["meta"]["stats"]["points"] == res["stats"]["points"] > 1000 and got["meta"]["last_run"]["job"]
    assert ok(c.get(f"{IMP}/models"))["models"][0]["stats"]["patches"] == res["stats"]["patches"]
    assert cache_dir(c, mid).is_dir() and any(cache_dir(c, mid).iterdir())  # the bricks for «Обновить»
    names = [e for e in c.events if e[0] == "implicit.changed"]
    assert names[0][1] == {"kind": "implicit_model", "id": mid} and len(names) >= 2  # created, started
    # a changed parameter makes the model «устарела»; the same value again — not
    ok(c.put(f"{IMP}/models/{mid}", json={"expected_version": 1, "post": {"v_min": 1.0}}))
    stale = get(c, mid)
    assert (stale["stale"], stale["stale_reason"], stale["state"]) == (True, "изменены параметры", "ready")
    assert [o["wireframe_id"] for o in stale["outputs"]] == [o["wireframe_id"] for o in got["outputs"]]  # kept
    ok(c.put(f"{IMP}/models/{mid}", json={"expected_version": 2, "post": {"v_min": 0}}))
    assert get(c, mid)["stale"] is False
    ok(c.put(f"{IMP}/models/{mid}", json={"expected_version": 3, "post": {"v_min": 1.0}}))
    # «Обновить» takes the patches and the bricks of the run; the outputs are written in place (the same numbers)
    t = time.perf_counter()
    upd = finish(c, start(c, mid, "update"), timeout=600)
    assert upd["status"] == "done", upd
    assert upd["result"]["mode"] == "update" and upd["result"]["reused"]["patches"] == res["stats"]["patches"] > 0
    assert time.perf_counter() - t < 60
    after = get(c, mid)
    assert (after["stale"], after["stale_reason"], after["state"]) == (False, None, "ready")
    assert [o["wireframe_id"] for o in after["outputs"]] == [o["wireframe_id"] for o in got["outputs"]]
    # «В каркас»: a copy with no parent, the geometry of the output, the model as its input
    out = got["outputs"][1]
    copy = ok(c.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": out["wireframe_id"]}))
    assert copy["name"] == "Литология · INT (копия)" and copy["wireframe_id"] not in kids(c, mid)
    again = ok(c.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": out["wireframe_id"],
                                                                "name": "Дайка для разреза"}))  # fmt: skip
    assert again["name"] == "Дайка для разреза"
    twice = ok(c.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": out["wireframe_id"]}))
    assert twice["name"] == "Литология · INT (копия) (2)"
    wf = ok(c.get(f"{API}/wireframes/{copy['wireframe_id']}"))
    assert (wf["triangle_count"], wf["name"]) == (out["triangles"], copy["name"])
    assert wf["stats"]["volume"] == pytest.approx(out["volume"], rel=1e-12)
    with dialect.read_tx(c.project.engine) as conn:
        row = conn.execute(sa.select(objects.c.parent_id, objects.c.meta).where(
            objects.c.id == copy["wireframe_id"])).one()  # fmt: skip
        assert row.parent_id is None and row.meta["source"]["model_id"] == mid
        from bf_core import lineage

        who = lineage.impact(conn, lineage.Node("object:implicit_model", mid))["nodes"]
        assert {(n["kind"], n["id"]) for n in who} >= {("wireframe", copy["wireframe_id"]),
                                                       ("wireframe", again["wireframe_id"])}  # fmt: skip
    assert ("objects.changed", {"kind": "wireframe", "id": copy["wireframe_id"]}) in c.events
    refused(c.post(f"{IMP}/models/{mid}/to_wireframe", json={"wireframe_id": copy["wireframe_id"]}), 404,
            "wireframe_not_found", "wireframe_id")  # not an output of the model
    refused(c.post(f"{IMP}/models/{mid}/to_wireframe", json={}), 422, "bad_param", "wireframe_id")
    refused(c.post(f"{IMP}/models/{mid + 99}/to_wireframe", json={"wireframe_id": 1}), 404, "model_not_found")
    # the project archive: the built model comes back fresh, its outputs are the children of the new number
    exp = c.post(f"{API}/projects/export", json={"path": str(tmp_path / "Модель")}).json()
    done = wait_job(c, exp["source"], exp["id"])
    assert done["status"] == "done", done
    imp = c.post(f"{API}/projects/import", json={"path": done["result"]["path"], "mode": "copy"}).json()
    done = wait_job(c, imp["source"], imp["id"])
    assert done["status"] == "done", done
    uid, maps = done["result"]["uid"], {int(k): int(v) for k, v in done["result"]["id_maps"]["objects"].items()}
    # the deletion: the outputs go, the copies stay, the files and the cache are gone
    shas_before = _blobs(c, mid)
    assert shas_before
    ids = kids(c, mid)
    gone = ok(c.delete(f"{IMP}/models/{mid}"))
    assert gone == {"deleted": mid, "wireframes": ids}
    assert not alive(c, mid) and not any(alive(c, i) for i in ids) and not cache_dir(c, mid).exists()
    assert alive(c, copy["wireframe_id"]) and alive(c, again["wireframe_id"]) and _blobs(c, mid) == {}
    assert {"kind": "implicit_model", "id": mid, "deleted": True} in [d for n, d in c.events if n == "implicit.changed"]
    assert all(("objects.changed", {"kind": "wireframe", "id": i, "deleted": True}) in c.events for i in ids)
    assert ok(c.get(f"{IMP}/models")) == {"models": []}
    # … and the copy of the project has the model as it was
    assert c.post(f"{API}/projects/{uid}/open").status_code == 200
    c.project = projects_of(c.core).require_current()
    models = ok(c.get(f"{IMP}/models"))["models"]
    assert [(m["name"], m["state"], m["stale"], m["source_lost"]) for m in models] == [
        ("Литология", "ready", False, False)]  # fmt: skip
    new = models[0]["id"]
    assert new == maps[mid] and [o["wireframe_id"] for o in models[0]["outputs"]] == [maps[i] for i in ids]
    meta = get(c, new)["meta"]
    assert meta["data"]["set_id"] == maps[c.w.set_id] and meta["post"]["v_min"] == 1.0
    assert all(o["triangles"] > 0 for o in get(c, new)["outputs"]) and ok(c.delete(f"{IMP}/models/{new}"))["deleted"]


def _blobs(c: Any, ident: int) -> dict[str, str]:
    with dialect.read_tx(c.project.engine) as conn:
        return {str(r.role): str(r.sha256) for r in conn.execute(sa.select(blob_owners).where(
            blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == ident,
            blob_owners.c.role.like("implicit_%")))}  # fmt: skip


def test_an_output_other_objects_refer_to_stops_the_deletion(im) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c = im
    mid = ok(c.post(f"{IMP}/models", json=body(c.w, "INT")))["id"]
    job = finish(c, start(c, mid, "build"), timeout=600)
    assert job["status"] == "done", job
    out = get(c, mid)["outputs"][0]["wireframe_id"]
    other = ok(c.post(f"{IMP}/models", json=body(c.w, "SS", name="Ссылка")))["id"]
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.insert(object_refs).values(src_id=other, dst_id=out, role="boundary"))
    err = refused(c.delete(f"{IMP}/models/{mid}"), 409, "in_use")
    assert err["usage"] == [{"id": other, "kind": "implicit_model", "name": "Ссылка", "role": "output"}]
    assert alive(c, mid) and alive(c, out) and len(kids(c, mid)) == 3  # nothing was deleted
    assert cache_dir(c, mid).is_dir() and get(c, mid)["state"] == "ready"
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == other))
    assert ok(c.delete(f"{IMP}/models/{mid}"))["deleted"] == mid
