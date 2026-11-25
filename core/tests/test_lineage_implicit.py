"""The provider `object:implicit_model` of the graph (подшаг 19.11; ADR 0025; ADR 0026 решение 25; e19-api.md §2.3):
the fingerprint (the parameters without the numbers of the objects, the files `implicit_points` and
`implicit_solution`, `algo`), the inputs the store records, «устарела» through the graph, the outputs that follow the
model, impact and deletion. `tests/test_structure.py` requires the provider of every computed object kind."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

import bf_core.lineage.adapt_implicit as adapt  # registers the kind until `lineage/providers.py` imports it
from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.lineage import providers
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.wireframes import store as ws
from tests.estutil import W, world
from tests.test_model_store import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    get,
    make,
    output,
    put,
    rd,
    run,
    solid,
    write_result,
)

KIND = "object:implicit_model"


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def status(w: W, *nodes: lineage.Node) -> list[lineage.NodeStatus]:
    with dialect.read_tx(w.project.engine) as conn:
        got = lineage.status(conn, list(nodes))
    return [got[n] for n in nodes]


def fp(w: W, ident: int) -> str | None:
    return rd(w, lineage.fingerprint, lineage.Node(KIND, ident))


def built(w: W, **over: Any) -> tuple[int, dict[str, Any]]:
    """A model with two files and one output, as the job leaves it."""
    ident = make(w, **over)["id"]
    write_result(w, ident)
    out = output(w, ident, "Тело", 1)
    run(w, ident, outputs=[out])
    return ident, out


# --- the provider ---------------------------------------------------------------------------------------------------


def test_the_kind_has_a_provider_with_a_russian_label() -> None:
    assert KIND in providers.registered()
    assert providers.label_of(KIND) == "неявная модель"
    assert sb.LINEAGE_KIND == KIND


def test_a_model_is_current_and_follows_its_inputs(w: W) -> None:
    ident, out = built(w, trend_id=w.trend)
    node, child = lineage.Node(KIND, ident), lineage.Node("wireframe", out["wireframe_id"])
    assert [(s.state, s.reason) for s in status(w, node, child)] == [("current", None), ("current", None)]
    with dialect.read_tx(w.project.engine) as conn:  # the inputs the store recorded (ADR 0025: the table `lineage`)
        refs = lineage.store.inputs_of(conn, [node, child])
    assert [(r.node.kind, r.node.id) for r in refs[node]] == [
        ("dh_set", w.set_id), ("dh_table", w.b), ("object:trend", w.trend)]  # fmt: skip
    assert [r.name for r in refs[node]][1] == "Литология" and all(r.fp for r in refs[node])
    assert [r.node for r in refs[child]] == [node]
    # a table of the codes is rewritten: the model is «устарела» and says which; the output says through the model
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev + 1))
    model, kid = status(w, node, child)
    assert (model.state, model.reason) == ("stale", "изменена таблица «Литология»")
    assert kid.state == "stale" and "неявная модель «Литология»" in str(kid.reason)
    # the trend changes: «изменено: тренд» comes from the edge of the graph
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.b).values(rev=dh_tables.c.rev - 1))
        conn.execute(sa.update(objects).where(objects.c.id == w.trend).values(version=objects.c.version + 1))
    assert "тренд «Тренд СВ»" in str(status(w, node)[0].reason)


def test_a_model_that_is_being_built_is_building_and_a_lost_input_is_lost(w: W) -> None:
    wire = solid(w, "Граница")
    ident, _out = built(w, boundary={"wireframe_id": wire})
    node = lineage.Node(KIND, ident)
    with dialect.write_tx(w.project.engine) as conn:
        st.begin(conn, ident, 1)
    assert status(w, node)[0].state == "building"
    with dialect.write_tx(w.project.engine) as conn:
        st.set_state(conn, ident, "ready")
        ws.delete_wireframe(conn, wire)
    lost = status(w, node)[0]
    assert (lost.state, lost.reason) == (lineage.LOST, "источник удалён")


# --- the fingerprint ------------------------------------------------------------------------------------------------


def test_the_fingerprint_is_the_parameters_and_the_two_files(w: W) -> None:
    ident = make(w)["id"]
    empty = fp(w, ident)
    assert empty is not None and fp(w, ident) == empty
    write_result(w, ident)
    with_files = fp(w, ident)
    assert with_files != empty  # the files are in it
    put(w, ident, 1, name="Другое имя")
    assert fp(w, ident) == with_files  # the name is not
    version = get(w, ident)["version"]
    put(w, ident, version, name="Другое имя")  # an edit that changes nothing in the parameters: only the version
    assert get(w, ident)["version"] == version + 1 and fp(w, ident) == with_files
    put(w, ident, version + 1, levels=3)
    changed = fp(w, ident)
    assert changed not in (empty, with_files)  # the parameters are
    write_result(w, ident, shift=1.0)  # other points and other solution
    assert fp(w, ident) not in (empty, with_files, changed)


def test_the_fingerprint_does_not_hold_the_numbers_of_the_objects(w: W) -> None:
    a = make(w, name="А", boundary={"wireframe_id": solid(w, "Г1")}, trend_id=w.trend)["id"]
    b = make(w, name="Б", boundary={"wireframe_id": solid(w, "Г2")}, trend_id=w.trend)["id"]
    write_result(w, a)
    write_result(w, b)
    assert get(w, a)["meta"]["boundary"] != get(w, b)["meta"]["boundary"]
    assert fp(w, a) == fp(w, b)  # the same parameters but the name and the numbers, the same files


def test_the_provider_fingerprint_is_the_helper_of_the_module(w: W) -> None:
    ident = make(w)["id"]
    shas = write_result(w, ident)
    meta = get(w, ident)["meta"]
    got = adapt.fingerprint(meta, 1, {sb.ROLE_POINTS: shas[sb.ROLE_POINTS], sb.ROLE_SOLUTION: shas[sb.ROLE_SOLUTION]})
    assert got == fp(w, ident) and got.startswith("m:")
    assert adapt.fingerprint({"broken": True}, 7, {}) == "7"  # a model that does not parse: its version


# --- the outputs ----------------------------------------------------------------------------------------------------


def test_outputs_go_stale_with_the_parameters_and_current_again_after_a_run(w: W) -> None:
    ident, out = built(w)
    child = lineage.Node("wireframe", out["wireframe_id"])
    assert status(w, child)[0].state == "current"
    put(w, ident, 1, name="Новое имя")
    assert status(w, child)[0].state == "current"  # a rename is not a change
    put(w, ident, 2, levels=3)
    kid = status(w, child)[0]
    assert kid.state == "stale" and "неявная модель" in str(kid.reason)
    run(w, ident, outputs=[out])  # the job ends with the parameters it has: the model and its output are «актуален»
    assert [s.state for s in status(w, lineage.Node(KIND, ident), child)] == ["current", "current"]


def test_impact_and_deletion(w: W) -> None:
    ident, out = built(w, trend_id=w.trend, manual=[], groups=[])
    with dialect.read_tx(w.project.engine) as conn:
        hit = lineage.impact(conn, lineage.Node("object:trend", w.trend))
        assert [(n["kind"], n["id"]) for n in hit["nodes"]] == [(KIND, ident), ("wireframe", out["wireframe_id"])]
        assert hit["by_kind"] == {KIND: 1, "wireframe": 1}
        again = lineage.impact(conn, lineage.Node("dh_table", w.b))
        assert KIND in again["by_kind"]
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, w.store, ident)
    child = lineage.Node("wireframe", out["wireframe_id"])
    assert [s.state for s in status(w, lineage.Node(KIND, ident), child)] == ["deleted", "deleted"]
    with dialect.read_tx(w.project.engine) as conn:
        assert lineage.store.inputs_of(conn, [lineage.Node(KIND, ident)]).get(lineage.Node(KIND, ident), []) == []
        assert lineage.sweep(conn) == 0  # nothing of the model is left behind
