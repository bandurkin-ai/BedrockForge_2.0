"""The review of 19.29б-B (core of the implicit models): the state «идёт построение» is in `meta`, lives only with its
job (the clean-ups of the jobs and of the project snapshot delete the `building` rows of `objects`, a model with its
outputs among them; a job cancelled in the queue or killed leaves no mark behind), an edit of a model that was built
keeps the fingerprints of its inputs («устарела» stays), `h` is what the user gave, the extent follows the run, a copy
«В каркас» is renumbered by an import, the check as a file does not run a formula."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.archive import export
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.jobs import store as jobs_store
from bf_core.model import remap, spec_parts
from bf_core.model import store as st
from bf_core.model import store_base as sb
from tests.estutil import W, refused, world
from tests.test_model_store import (
    BOX,
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    get,
    make,
    output,
    put,
    rd,
    run,
    write_result,
)

KIND = "object:implicit_model"


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def job_row(w: W, status: str = "running") -> int:
    ident = jobs_store.insert(w.project.engine, instance=False, queue="heavy", kind="implicit.build", title="t",
                              params={}, secret=None, max_attempts=1, memory_mb=1)  # fmt: skip
    jobs_store.update(w.project.engine, False, ident, status=status)
    return ident


def built(w: W, **over: Any) -> tuple[int, dict[str, Any]]:
    ident = make(w, **over)["id"]
    write_result(w, ident)
    out = output(w, ident, "Тело", 1)
    run(w, ident, outputs=[out])
    return ident, out


def alive(w: W, ident: int) -> bool:
    return rd(w, lambda c: c.execute(sa.select(objects.c.id).where(objects.c.id == ident)).first()) is not None


# --- 1: «идёт построение» does not make the model a row that the clean-ups delete ------------------------------------


def test_a_model_that_is_being_built_survives_the_clean_ups_of_the_jobs_and_of_the_snapshot(w: W) -> None:
    ident, out = built(w)
    jid = job_row(w)
    with dialect.write_tx(w.project.engine) as conn:
        st.begin(conn, ident, 1, job_id=jid)
    assert get(w, ident)["state"] == "building"
    row = rd(w, lambda c: tuple(c.execute(sa.select(objects.c.state, objects.c.job_id).where(
        objects.c.id == ident)).first()))  # fmt: skip
    assert row == ("ready", None)
    with dialect.write_tx(w.project.engine) as conn:
        assert jobs_store.drop_partial(conn, jid) == 0  # the cancel / the crash of the job
    assert alive(w, ident) and alive(w, out["wireframe_id"])
    snap = export.snapshot(w.project.engine, Path(w.project.folder))
    engine = dialect.create_engine(dialect.sqlite_url(str(snap)))
    try:
        with dialect.read_tx(engine) as conn:
            kept = {int(i) for i in conn.execute(sa.select(objects.c.id)).scalars()}
    finally:
        engine.dispose()
    assert {ident, out["wireframe_id"]} <= kept  # the snapshot of the export has the model with its outputs


def test_an_import_does_not_carry_the_mark_of_another_projects_job(w: W) -> None:
    ident, _out = built(w)
    jid = job_row(w)
    with dialect.write_tx(w.project.engine) as conn:
        st.begin(conn, ident, 1, job_id=jid)
    meta = get(w, ident)["meta"]
    assert meta["state"] == "building" and "job_id" in meta
    new = remap.remap_meta(meta, remap.Remap({}, {}, {}))
    assert (new["state"], new["error"]) == ("ready", None) and "job_id" not in new and "back" not in new
    assert {"job_id", "back", "h_used"} <= set(spec_parts.META_ONLY)  # not parameters: `load_spec` skips them


# --- 2: a mark without a live job is not «идёт построение» -----------------------------------------------------------


def test_a_mark_without_a_live_job_is_not_building_and_the_model_is_not_stuck(w: W) -> None:
    ident = make(w)["id"]
    err = {"code": "iso_check", "message": "м", "hint": "п", "group": 1}
    with dialect.write_tx(w.project.engine) as conn:
        st.set_state(conn, ident, "error", error=err)
        st.set_state(conn, ident, "building", job_id=987_654)  # no such job: it was cancelled in the queue, killed…
    got = get(w, ident)
    assert (got["state"], got["error"]) == ("error", err)  # what the model was before the mark
    assert rd(w, st.list_rows)[0]["state"] == "error"
    assert put(w, ident, 1, levels=3)["version"] == 2  # an edit, no 409 `model_building`
    meta = get(w, ident)["meta"]
    assert (meta["state"], meta["error"]) == ("error", err) and "job_id" not in meta and "back" not in meta
    with dialect.write_tx(w.project.engine) as conn:
        st.begin(conn, ident, 2, job_id=987_654)
        st.set_state(conn, ident, "ready")
        st.begin(conn, ident, 2)  # a mark of the job that is gone is taken by a new one


def test_a_mark_with_a_live_job_refuses_and_ends_with_the_job(w: W) -> None:
    ident = make(w)["id"]
    jid = job_row(w, "queued")
    with dialect.write_tx(w.project.engine) as conn:
        st.set_state(conn, ident, "building", job_id=jid)
    assert get(w, ident)["state"] == "building"
    refused(lambda: put(w, ident, 1, levels=3), 409, "model_building")
    with dialect.write_tx(w.project.engine) as conn:
        refused(lambda: st.delete(conn, w.store, ident), 409, "model_building")
    jobs_store.update(w.project.engine, False, jid, status="cancelled")  # the job ends without a word to the model
    assert get(w, ident)["state"] == "ready"
    assert put(w, ident, 1, levels=3)["version"] == 2
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, w.store, ident)


def test_the_first_mark_keeps_what_the_model_goes_back_to(w: W) -> None:
    ident = make(w)["id"]
    err = {"code": "no_contacts", "message": "м", "hint": "п"}
    first, second = job_row(w), job_row(w)
    with dialect.write_tx(w.project.engine) as conn:
        st.set_state(conn, ident, "error", error=err)
        st.set_state(conn, ident, "building", job_id=first)
        st.set_state(conn, ident, "building", job_id=second)  # a second mark does not forget the first state
    meta = get(w, ident)["meta"]
    assert meta["back"] == {"state": "error", "error": err} and meta["job_id"] == second
    assert sb.back_of(meta) == ("error", err)


# --- 3: an edit does not make a changed input look unchanged --------------------------------------------------------


def test_a_rename_of_a_stale_model_keeps_it_stale_and_so_does_the_graph(w: W) -> None:
    ident, _out = built(w)
    before = get(w, ident)["meta"]["inputs"]
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
    assert get(w, ident)["stale"] is True
    put(w, ident, 1, name="Другое имя")
    got = get(w, ident)
    assert got["stale"] is True and got["stale_reason"] == "изменена таблица «Литология»"
    assert [i["fp"] for i in got["meta"]["inputs"]] == [i["fp"] for i in before]
    node = lineage.Node(KIND, ident)
    with dialect.read_tx(w.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "stale"
    put(w, ident, 2, levels=3)  # a group or any other key: the same
    assert get(w, ident)["stale"] is True
    with dialect.read_tx(w.project.engine) as conn:
        assert lineage.status(conn, [node])[node].state == "stale"


def test_a_new_input_gets_its_fingerprint_as_it_is_now_a_model_never_built_follows_its_inputs(w: W) -> None:
    ident, _out = built(w)
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
    put(w, ident, 1, trend_id=w.trend)  # a new input: the trend
    kinds = {i["kind"]: i for i in get(w, ident)["meta"]["inputs"]}
    assert "object:trend" in kinds and kinds["object:trend"]["fp"]
    assert get(w, ident)["stale"] is True  # the table is still what the last run did not see
    fresh = make(w, name="Не строилась")["id"]
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
    put(w, fresh, 1, name="Новое")
    assert get(w, fresh)["stale"] is False  # no run to compare with: the inputs are as they are now


# --- 5д: h as the user gave it; the extent of the run ----------------------------------------------------------------


def test_h_is_what_the_user_gave_and_the_boundary_without_h_takes_the_step_again(w: W) -> None:
    auto = make(w)["id"]
    meta = get(w, auto)["meta"]
    assert meta["h"] is None and meta["h_used"] > 0
    put(w, auto, 1, boundary={"box": [0, 0, -30, 50, 40, 10]})  # a smaller box, no h: the step follows it
    small = get(w, auto)["meta"]
    assert small["h"] is None and small["h_used"] < meta["h_used"]
    given = make(w, name="Заданный", h=4.0)["id"]
    put(w, given, 1, boundary={"box": [0, 0, -30, 50, 40, 10]})
    assert get(w, given)["meta"]["h"] == 4.0 and get(w, given)["meta"]["h_used"] == 4.0
    put(w, given, 2, h=None)
    assert get(w, given)["meta"]["h"] is None


def test_a_model_built_with_an_auto_step_is_not_stale_by_its_parameters(w: W) -> None:
    ident, _out = built(w)
    assert get(w, ident)["stale"] is False  # `last_run.params` is of the parameters as given (h None)


def test_the_run_writes_the_extent_it_read(w: W) -> None:
    ident = make(w)["id"]
    write_result(w, ident)
    meta = get(w, ident)["meta"]
    moved = [1.0, 2.0, -3.0, 40.0, 50.0, 6.0]
    with dialect.write_tx(w.project.engine) as conn:
        st.record_run(conn, ident, sb.load_spec(meta), {"at": "2026-11-25T10:00:00", "job": 1, "mode": "build"},
                      meta["inputs"], outputs=[], stats={"points": 1}, extent=moved)  # fmt: skip
    assert get(w, ident)["meta"]["extent"] == moved and BOX != moved
    row = rd(w, lambda c: c.execute(sa.select(objects.c.extent).where(objects.c.id == ident)).scalar())
    assert [float(v) for v in row] == moved


# --- 5е: the copy «В каркас» is renumbered by an import ---------------------------------------------------------------


def test_the_source_of_a_copy_is_renumbered_by_an_import(w: W) -> None:
    ident, out = built(w)
    now = dialect.utcnow()
    with dialect.write_tx(w.project.engine) as conn:
        copy = int(conn.execute(sa.insert(objects).values(
            kind="wireframe", name="Копия", name_key="копия", parent_id=None, sort_key=0.0, state="ready", version=1,
            meta={"source": {"op": "implicit_copy", "model_id": ident, "wireframe_id": out["wireframe_id"]}},
            created_at=now, updated_at=now)).inserted_primary_key[0])  # fmt: skip
        remap.remap_implicit(conn, {ident: 501, out["wireframe_id"]: 502, copy: copy}, {})
        source = conn.execute(sa.select(objects.c.meta).where(objects.c.id == copy)).scalar_one()["source"]
    assert source == {"op": "implicit_copy", "model_id": 501, "wireframe_id": 502}
