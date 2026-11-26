"""The provider `object:pit_scenario` of the graph (подшаг 21.10; ADR 0025; ADR 0028 решение 23; ТЗ Д-35 —
**ТД35-1 up to the scenario and the field PIT**): the fingerprint is the SHA-256 of `data` and ALGO, the inputs are the
model, the fields it reads, the filter and the wireframes (the terrain, the restriction, the rock rules); a table of
samples edited → the field of the estimation is «устарел» → the scenario and the field PIT built from it say so with the
chain; «Закрепить»; «источник удалён»; «Перестроить» (the maker of the kind and the owner of a field `op = "pit"`).
The lines of the orchestrator (`providers.py`, `rebuild.py`, `blocks/schema.py`) are not in this subshag: the kind is
registered by importing the adapter, the maker and the owner of the field are put in place here, `FIELD_OPS` is
patched."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

import bf_core.lineage.adapt_pit as adapt  # registers the kind until `lineage/providers.py` imports it
from bf_core import lineage
from bf_core.blocks import meta as block_meta
from bf_core.blocks import schema as block_schema
from bf_core.blocks.filters import store as fst
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.lineage import providers, rebuild
from bf_core.mine import store as st
from bf_core.wireframes import store as ws
from tests.estutil import W, world
from tests.pitutil import make, new, of_estimate, params_of, pit_field, rd, refused, shells_of
from tests.test_lineage_resources import edit_table, est_field
from tests.test_model_store import solid

NODE = "object:pit_scenario"


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


@pytest.fixture(autouse=True)
def pit_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """`FIELD_OPS` += "pit" is a line of the orchestrator (`blocks/schema.py`): until it is there, the test puts it."""
    for mod in (block_schema, block_meta):
        if "pit" not in mod.FIELD_OPS:
            monkeypatch.setattr(mod, "FIELD_OPS", (*mod.FIELD_OPS, "pit"))


def state(w: W, ident: int, store: Any = None, kind: str = NODE, sub: str = "") -> lineage.NodeStatus:
    node = lineage.Node(kind, ident, sub)
    with dialect.read_tx(w.project.engine) as conn:
        return lineage.status(conn, [node], store=store)[node]


def fp(w: W, ident: int) -> str | None:
    return rd(w, lineage.fingerprint, lineage.Node(NODE, ident))


# --- the provider ---------------------------------------------------------------------------------------------------


def test_the_kind_has_a_provider_with_a_russian_label() -> None:
    assert NODE in providers.registered()
    assert providers.label_of(NODE) == "сценарий карьера"
    assert st.NODE == NODE and adapt.JOB_KIND == st.JOB_KIND == "pit.run"


def test_a_scenario_is_current_and_its_inputs_are_the_model_and_the_fields(w: W) -> None:
    ident = make(w)
    assert state(w, ident).state == "current" and fp(w, ident)
    node = lineage.Node(NODE, ident)
    with dialect.read_tx(w.project.engine) as conn:
        refs = lineage.store.inputs_of(conn, [node])[node]
    assert [(r.node.kind, r.node.id, r.node.sub) for r in refs] == [("object:block_model", w.model, ""),
                                                                    ("bm_field", w.model, "au")]  # fmt: skip
    assert all(r.fp for r in refs) and refs[1].name == "Модель.AU"


def test_the_fingerprint_is_the_data_and_the_algo_not_the_name_the_calendar_or_the_draft(w: W) -> None:
    ident = make(w, "Карьер")
    first = fp(w, ident)
    assert first is not None and first.startswith("p:") and fp(w, ident) == first
    with dialect.write_tx(w.project.engine) as conn:  # a rename, a calendar, parameters not run yet: the same numbers
        st.rename(conn, ident, "Другое имя", 2)
        st.update_params(conn, ident, 3, calendar={"q_ore": 1.0e6})
        st.update_params(conn, ident, 4, params=params_of(w, mining={"c_m0": 9.0}))
    assert fp(w, ident) == first and state(w, ident).state == "current"
    with dialect.write_tx(w.project.engine) as conn:  # a run again with the same tables: version + 1, same fingerprint
        st.write_result(conn, w.store, ident, _built(w))
    assert rd(w, st.info, ident)["version"] == 6 and fp(w, ident) == first
    with dialect.write_tx(w.project.engine) as conn:  # other numbers: another state
        st.write_result(conn, w.store, ident, _built(w, shells_of(2)))
    assert fp(w, ident) != first
    assert st.fingerprint("ab" * 32, "pit-1") != st.fingerprint("ab" * 32, "pit-2")


def _built(w: W, shells: Any = None) -> st.Built:
    from tests.pitutil import built

    return built(w, shells)


def test_a_scenario_without_a_result_is_current_and_says_so(w: W) -> None:
    ident = new(w, "Новый")
    assert fp(w, ident) == "нет результата" and state(w, ident).state == "current"
    edit_table(w)
    assert state(w, ident, w.store).state == "current"  # no inputs yet: nothing is stale
    node = lineage.Node(NODE, ident)
    with dialect.read_tx(w.project.engine) as conn:
        assert lineage.store.inputs_of(conn, [node]).get(node, []) == []


def test_a_scenario_that_runs_is_building(w: W) -> None:
    ident = make(w)
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident)
    assert state(w, ident).state == "building"
    with dialect.write_tx(w.project.engine) as conn:
        st.settle(conn, ident)
    assert state(w, ident).state == "current"


# --- ТД35-1 up to the scenario and the field PIT ---------------------------------------------------------------------


def test_td35_1_a_table_of_samples_edited_makes_the_scenario_and_its_pit_stale_with_the_chain(w: W) -> None:
    ident = of_estimate(w)
    pit_field(w, ident)
    assert state(w, ident, w.store).state == "current"
    assert state(w, w.model, w.store, "bm_field", "pit").state == "current"
    edit_table(w)
    got = state(w, ident, w.store)
    assert got.state == "stale"
    # scenario ← the field of the model ← the table: the whole way, as the window «Происхождение» shows it
    assert got.reason == "поле блочной модели «Модель.AU_EST» ← изменено: таблица «Пробы»", got.reason
    info = rd(w, st.info, ident, store=w.store)
    assert info["graph"]["stale"] is True and "таблица «Пробы»" in info["graph"]["stale_reason"]
    assert rd(w, st.list_scenarios)[0]["graph"]["stale"] is True
    pit = state(w, w.model, w.store, "bm_field", "pit")  # PIT ← the scenario ← the field ← the table
    assert pit.state == "stale" and "сценарий карьера «Сценарий ЮЗ»" in str(pit.reason), pit.reason
    assert "таблица «Пробы»" in str(pit.reason), pit.reason
    with dialect.read_tx(w.project.engine) as conn:
        origin = lineage.origin(conn, lineage.Node(NODE, ident))
    by_kind = {(o["kind"], o["sub"]): o["state"] for o in origin}
    assert by_kind[("bm_field", "au_est")] == "stale" and by_kind[("object:block_model", "")] == "current"
    # the estimation is run again: the field is a new file, the scenario says «изменено: поле»
    est_field(w, value=2.5, replace=True)
    again = state(w, ident, w.store)
    assert again.state == "stale" and again.reason == "изменено: поле блочной модели «Модель.AU_EST»", again.reason
    # «Перестроить»: the scenario is written again from the new field and is current; the field PIT follows its run
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, _built(w, shells_of(2)))
    assert state(w, ident, w.store).state == "current"
    refreshed = rd(w, st.info, ident)
    assert refreshed["version"] == 3 and refreshed["graph"]["stale"] is False
    assert state(w, w.model, w.store, "bm_field", "pit").state == "stale"  # the field is of the old run


def test_td35_1_the_others_fields_do_not_make_it_stale(w: W) -> None:
    """A scenario reads AU: a new field of the model (written by an estimation) does not touch it."""
    ident = make(w)
    est_field(w)
    edit_table(w)
    assert state(w, ident, w.store).state == "current"
    with dialect.read_tx(w.project.engine) as conn:  # but impact names it for the field it reads
        impact = lineage.impact(conn, lineage.Node("bm_field", w.model, "au_est"))
    assert impact["total"] == 0


def test_impact_of_a_field_names_the_scenario_and_what_is_built_from_it(w: W) -> None:
    ident = of_estimate(w)
    pit_field(w, ident)
    with dialect.read_tx(w.project.engine) as conn:
        impact = lineage.impact(conn, lineage.Node("bm_field", w.model, "au_est"))
        assert impact["by_kind"] == {NODE: 1, "bm_field": 1} and impact["total"] == 2
        assert {n["name"] for n in impact["nodes"]} == {"Сценарий ЮЗ", "Модель.PIT"}
        everything = lineage.impact(conn, lineage.Node("object:block_model", w.model))
    assert ident in [n["id"] for n in everything["nodes"] if n["kind"] == NODE]


def test_a_pinned_scenario_is_not_stale_but_says_it_would_be(w: W) -> None:
    ident = of_estimate(w)
    node = lineage.Node(NODE, ident)
    edit_table(w)
    with dialect.write_tx(w.project.engine) as conn:
        pinned = lineage.pin(conn, node, True)  # «Закрепить»: a scenario that went out
    assert (pinned.state, pinned.changed) == ("pinned", True) and "таблица «Пробы»" in str(pinned.reason)
    got = rd(w, st.list_scenarios)[0]["graph"]
    assert got["pinned"] is True and got["stale"] is True  # the window shows both: pinned, and the inputs changed
    with dialect.write_tx(w.project.engine) as conn:
        st.rename(conn, ident, "Выпущен", 2)  # the pin survives a rename
    assert state(w, ident).state == "pinned"
    with dialect.write_tx(w.project.engine) as conn:  # «Снять»
        assert lineage.pin(conn, node, False).state == "stale"
        refused(lambda: lineage.pin(conn, lineage.Node(NODE, 99_999), True), 404, "lineage_node_not_found")
        lineage.pin(conn, node, True)
        st.delete(conn, ident)
    with dialect.read_tx(w.project.engine) as conn:  # a deleted scenario takes its pin with it
        assert lineage.store.pins_of(conn, [node]) == set()


def test_a_deleted_scenario_leaves_its_fields_as_a_lost_source(w: W) -> None:
    ident = make(w, "Карьер")
    pit_field(w, ident)
    assert state(w, w.model, w.store, "bm_field", "pit").state == "current"
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, ident)  # the field PIT stays (docs/progress/e21-api.md §15.10), the link says «источник удалён»
    lost = state(w, w.model, w.store, "bm_field", "pit")
    assert lost.state == lineage.LOST and "источник удалён" in str(lost.reason), lost.reason


def test_a_deleted_filter_is_a_lost_source_and_the_filter_is_an_input(w: W) -> None:
    ident = make(w, "Карьер", filter_id=w.flt)
    node = lineage.Node(NODE, ident)
    with dialect.read_tx(w.project.engine) as conn:
        kinds = [r.node.kind for r in lineage.store.inputs_of(conn, [node])[node]]
    assert "object:filter" in kinds and "bm_field" in kinds
    assert [i["kind"] for i in rd(w, st.info, ident)["meta"]["inputs"]].count("object:filter") == 1
    with dialect.write_tx(w.project.engine) as conn:
        fst.update(conn, w.flt, {"expr": "AU > 0.5", "expected_version": 1})
    moved = state(w, ident)
    assert moved.state == "stale" and moved.reason.startswith("изменено: ") and "«Руда»" in moved.reason, moved.reason  # type: ignore[union-attr]
    with dialect.write_tx(w.project.engine) as conn:
        fst.delete(conn, w.flt)
    lost = state(w, ident)
    assert lost.state == lineage.LOST and "источник удалён" in str(lost.reason)
    assert rd(w, st.list_scenarios)[0]["graph"]["source_lost"] is True


def test_the_wireframes_of_the_terrain_the_restriction_and_the_rules_are_inputs(w: W) -> None:
    terrain, pit, cut, zone = solid(w, "Рельеф"), solid(w, "Карьер"), solid(w, "Зона", 200.0), solid(w, "Рудная зона")
    with dialect.write_tx(w.project.engine) as conn:
        flt = fst.create(conn, {"name": "В зоне", "rules": {"wireframe": {"id": cut, "side": "inside", "by": "frac"}}})
    restrict = {"op": "and", "items": [{"wireframe": {"id": pit, "side": "inside", "by": "frac"}},
                                       {"expr": "N > 10"}]}  # fmt: skip
    rocks = {"by": {"kind": "rules", "rules": [{"name": "Зона", "rules": {"wireframe": {"id": zone, "side": "inside",
                                                                                      "by": "frac"}}}]},
             "types": [{"name": "Зона", "c_p": 20.0, "rec": {"AU": 0.9}}]}  # fmt: skip
    params = params_of(w, terrain_id=terrain, filter_id=flt, restrict=restrict, rocks=rocks)
    from bf_core.mine import store_graph as sg

    with dialect.read_tx(w.project.engine) as conn:
        nodes = sg.input_nodes(conn, params, sg.read_fields(params))
    got = [(n.kind, n.id, n.sub) for n in nodes]
    assert got == [("object:block_model", w.model, ""), ("bm_field", w.model, "au"), ("bm_field", w.model, "n"),
                   ("object:filter", flt, ""), ("wireframe", terrain, ""), ("wireframe", pit, ""),
                   ("wireframe", cut, ""), ("wireframe", zone, "")]  # fmt: skip
    ident = make(w, "Карьер", terrain_id=terrain, filter_id=flt, restrict=restrict, rocks=rocks)
    assert state(w, ident).state == "current"
    with dialect.write_tx(w.project.engine) as conn:  # the terrain is deleted: «источник удалён»
        ws.delete_wireframe(conn, terrain)
    gone = state(w, ident)
    assert gone.state == lineage.LOST and "каркас «Рельеф»" in str(gone.reason), gone.reason
    # what is not there any more is left out of the nodes (the graph of the scenario is what it read)
    with dialect.read_tx(w.project.engine) as conn:
        assert [n.kind for n in sg.input_nodes(conn, {"model_id": w.model, "terrain_id": terrain}, ["NOPE"])] == [
            "object:block_model"]  # fmt: skip
        assert sg.input_nodes(conn, {"model_id": None}, ["AU"]) == []


def test_the_state_of_many_scenarios_is_one_batch(w: W) -> None:
    ids = [make(w, f"Вариант {i}") for i in range(6)]
    seen: list[str] = []

    def count(rows: int) -> int:
        seen.clear()
        nodes = [lineage.Node(NODE, i) for i in ids[:rows]]
        with dialect.read_tx(w.project.engine) as conn:
            sa.event.listen(conn, "before_cursor_execute", lambda *a: seen.append(a[2]))
            got = lineage.status(conn, nodes, store=w.store)
            assert all(s.state == "current" for s in got.values())
        return len(seen)

    one, six = count(1), count(6)
    assert six <= one + 2, (one, six)  # no query per scenario: the providers look up in batches


# --- «Перестроить»: the maker of the kind and the owner of the field ----------------------------------------


def test_the_maker_puts_the_job_with_the_parameters_of_the_result(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.jobs.registry as reg

    monkeypatch.setitem(rebuild.MAKERS, NODE, rebuild.Maker(adapt.owner, adapt.prepare_rebuild))
    monkeypatch.setitem(reg.KINDS, adapt.JOB_KIND, SimpleNamespace())  # until `jobs/registry.py` registers it
    ident = of_estimate(w)
    edit_table(w)
    svc = SimpleNamespace(require_current=lambda: w.project)
    node = lineage.Node(NODE, ident)
    step = rebuild.prepare_step(svc, node, {})
    assert step.job_kind == "pit.run" and step.title == "Перестроить сценарий карьера «Сценарий ЮЗ»"
    assert step.params == rd(w, st.rebuild_params, ident) and step.params["scenario_id"] == ident
    assert step.params["params"] == rd(w, st.result_params, ident) and step.params["expected_version"] == 2
    with dialect.read_tx(w.project.engine) as conn:
        plan = rebuild.make_plan(conn, node, with_dependents=False)
    assert [(i.node, i.owner, i.state) for i in plan.items] == [(node, node, "stale")]
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, ident)
    refused(lambda: rebuild.prepare_step(svc, node, {}), 409, "pit_running")
    refused(lambda: rebuild.prepare_step(svc, lineage.Node(NODE, 99_999), {}), 404, "pit_scenario_not_found")
    plain = new(w, "Новый")
    refused(lambda: rebuild.prepare_step(svc, lineage.Node(NODE, plain), {}), 409, "pit_no_result")


def test_the_field_pit_is_rebuilt_by_its_scenario(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    """The line of the orchestrator in `rebuild._field_owner`: a field with `op = "pit"` is made by its scenario."""
    ident = of_estimate(w)
    pit_field(w, ident)
    meta = rd(w, lambda c: c.execute(sa.select(objects.c.meta).where(objects.c.id == w.model)).scalar())
    assert adapt.pit_field_owner(meta, "pit") == lineage.Node(NODE, ident)
    assert adapt.pit_field_owner(meta, "au") is None and adapt.pit_field_owner(meta, "nope") is None
    assert adapt.pit_field_owner(None, "pit") is None
    assert adapt.pit_field_owner({"fields": [{"name": "PIT"}]}, "pit") is None
    original = rebuild._field_owner

    def owner(conn: sa.Connection, node: lineage.Node) -> lineage.Node | None:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == node.id)).scalar()
        return original(conn, node) or adapt.pit_field_owner(meta, node.sub)

    monkeypatch.setitem(rebuild.MAKERS, "bm_field", rebuild.Maker(owner, rebuild.MAKERS["bm_field"].prepare))
    monkeypatch.setitem(rebuild.MAKERS, NODE, rebuild.Maker(adapt.owner, adapt.prepare_rebuild))
    edit_table(w)
    head = lineage.Node("bm_field", w.model, "pit")
    with dialect.read_tx(w.project.engine) as conn:
        plan = rebuild.make_plan(conn, head, with_dependents=False, blobs=w.store)
    assert [(i.node.kind, i.owner) for i in plan.items] == [("bm_field", lineage.Node(NODE, ident))]
    assert plan.items[0].state == "stale" and "сценарий карьера" in str(plan.items[0].reason)
