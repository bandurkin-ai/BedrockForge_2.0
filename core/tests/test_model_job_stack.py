"""The stack of relations, the faults and the systems of veins in the jobs `implicit.build` / `implicit.update`
(подшаг 19.36; ТЗ Д-24, Д-31, Д-39, Т8.9-13; ADR 0026 решения 15, 19–21, 22–26, 30; e19-api §4, §10) on the fixture
scene of `tests/stack_world.py`: the relations «Эрозия» / «Налегание» / «Интрузия» of the domains, a fault cutting two
of them (the wings, the offset, the space of the wings), a finite fault (the partial fits), a system of two veins that
cross (a body each, the common body, the lines of the crossing as a layer of strings).

What is checked: the outputs and their names, the numbers of `stats` (`stack`, `faults`, `by_group[].offset`,
`vein_pairs`), the files — the fields of the wings and the points they were solved from (the check of the model reads
them back: every field reproduces its data), the same bits with 1, 2, 4, 8 threads, the update equal to the full build
bit for bit (the patches of the wings from the cache), the cancel at every stage leaves nothing (the layers of strings
too), the lines of the crossing replaced by a run and deleted with the model, the errors in Russian
{code, message, hint}. The database tests: SQLite always, PostgreSQL with BF_TEST_PG (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import line_layers, objects
from bf_core.jobs.context import JobCancelled
from bf_core.model import check as ck
from bf_core.model import store as st
from tests.stack_world import (
    FAULT_ID,
    fault_group,
    fault_layers,
    fault_manual,
    make_b,
    small_patches,
    stack_groups,
    vein_system_group,
    veins_world,
)
from tests.test_model_data_load import World
from tests.test_model_job import (
    Cancel,
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    groups_of,
    kids,
    meta_of,
    run,
    shas,
    version,
)

pytestmark = pytest.mark.db


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


@pytest.fixture()
def fscene(world: World):  # type: ignore[no-untyped-def]  # noqa: F811
    """The world and the hand data of a vertical fault x = 200."""
    return world, fault_manual(fault_layers(world))


@pytest.fixture()
def vworld(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    """The world with the lithology that has a second vein VN2 crossing the vein VN."""
    w = veins_world(db_url, tmp_path)
    yield w
    w.engine.dispose()


def faulted(w: World, manual: list[dict[str, Any]], name: str = "Разлом", radius: float | None = None,
            **over: Any) -> dict[str, Any]:  # fmt: skip
    return make_b(w, name, [*stack_groups(), fault_group(radius=radius)], manual, **small_patches(), **over)


def systems(w: World, name: str = "Жилы", **over: Any) -> dict[str, Any]:
    return make_b(w, name, [*groups_of("ALV", "SS"), vein_system_group(7)], **small_patches(), **over)


def fields_of(w: World, mid: int) -> dict[Any, Any]:
    with dialect.read_tx(w.engine) as conn:
        return st.load_fields(conn, w.store, mid)


def checked(w: World, mid: int) -> list[dict[str, Any]]:
    """The summary of the check of the model: a row per field; every field reproduces the data it was solved from."""
    got = ck.load(w.project, mid).table.groups
    assert got and all(g["max_abs_residual"] is not None and g["max_abs_residual"] < 1e-6 for g in got), got
    return got


# --- the stack ------------------------------------------------------------------------------------------------------


def test_a_stack_of_relations_is_built_and_every_field_reproduces_the_data_it_was_solved_from(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = world
    g = groups_of("INT", "ALV", "SS", "SH", "GR")
    g[0]["relation"], g[1]["relation"], g[2]["relation"] = "intrusion", "onlap", "erode"
    m = make_b(w, "Стек", g, **small_patches())
    out = run(w, m["id"])
    stack = out["stats"]["stack"]
    assert stack["relations"] == {"1": "intrusion", "2": "onlap", "3": "erode", "4": "conformable", "5": "conformable"}
    assert sorted(stack["order"]) == [1, 2, 3, 4, 5] and stack["dropped_total"] > 0
    assert {d["relation"] for d in stack["dropped"]} >= {"intrusion", "erode"}
    assert any("Стек отношений" in line for line in out["journal"])
    assert out["stats"]["balance"] < 1e-9 and [(o["kind"], o["group"]) for o in out["outputs"]][-1] == ("body", None)
    assert all(o["wing"] is None for o in out["outputs"])
    fields = fields_of(w, m["id"])
    assert set(fields) == {(i, None) for i in range(1, 6)}
    by = {g["group"]: g for g in out["stats"]["by_group"]}
    for (gid, _wing), gd in fields.items():  # a field is stored with the points it was solved from
        assert len(gd.points["x"]) == by[gid]["points"] and (gd.points["group"] == gid).all()
    for gid in (2, 3, 4, 5):  # what the last source left of a group is what it was solved from
        last = [d for d in stack["dropped"] if d["group"] == gid][-1]
        assert by[gid]["points"] == last["of"] - last["points"]
    assert sum(d["points"] for d in stack["dropped"]) == stack["dropped_total"]
    checked(w, m["id"])  # the check reads every field back: the points of an eroded group are those it was solved from


def test_a_stack_of_conformable_groups_writes_the_table_of_the_load_as_it_always_did(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = world
    m = make_b(w, "Хронология", groups_of("ALV", "SS", "SH"), **small_patches())
    out = run(w, m["id"])
    assert out["stats"]["stack"]["dropped_total"] == 0 and out["stats"]["stack"]["dropped"] == []
    rows = fields_of(w, m["id"])
    assert set(rows) == {(2, None), (3, None), (4, None)}
    assert all(len(r.points["x"]) > 0 and (r.points["wing"] == -1).all() for r in rows.values())
    assert all(g["offset"] is None and g["faults"] == [] for g in out["stats"]["by_group"])


# --- the faults -----------------------------------------------------------------------------------------------------


def test_a_fault_cuts_two_groups_into_wings_and_every_wing_field_reproduces_its_data(fscene) -> None:  # type: ignore[no-untyped-def]
    w, manual = fscene
    m = faulted(w, manual)
    msgs: list[str] = []
    out = run(w, m["id"], threads=2, tick=lambda _p, text: msgs.append(text or ""))
    assert {"Данные", "Участки", "Изоповерхности", "Тела", "Проверки", "Запись"} <= {
        x.split(":", 1)[0] for x in msgs if ":" in x}  # fmt: skip
    by = {(o["kind"], o["group"], o["wing"]): o for o in out["outputs"]}
    assert ("surface", FAULT_ID, None) in by
    assert by[("body", FAULT_ID, 0)]["region"] and by[("body", FAULT_ID, 1)]["region"]
    for gid in (3, 4):  # SS and SH: a surface and a body on every wing
        assert {("surface", gid, 0), ("surface", gid, 1), ("body", gid, 0), ("body", gid, 1)} <= set(by)
    for gid in (1, 2, 5):  # the groups no fault cuts have the plain outputs
        assert ("body", gid, None) in by and ("body", gid, 0) not in by
    assert {("body", None, 0), ("body", None, 1)} <= set(by)  # «Прочее» of every region
    assert "Разлом · SS, крыло 0" in by[("body", 3, 0)]["name"] or by[("body", 3, 0)]["name"].endswith("SS, крыло 0")
    assert by[("body", FAULT_ID, 0)]["name"].endswith("Разлом, крыло 0 (область)")
    assert by[("surface", 3, 1)]["name"].endswith("SS, крыло 1 · поверхность")
    rock = sum(o["volume"] for o in out["outputs"] if o["kind"] == "body" and not o.get("region"))
    boundary = by[("body", FAULT_ID, 0)]["volume"] + by[("body", FAULT_ID, 1)]["volume"]
    assert abs(rock - boundary) / boundary < 1e-9 and out["stats"]["balance"] < 1e-9  # the wings are not rock
    stats = out["stats"]
    cut = [g for g in stats["by_group"] if g["group"] in (3, 4)]
    assert [(g["group"], g["wing"]) for g in cut] == [(3, 0), (3, 1), (4, 0), (4, 1)]
    for g in cut:
        assert g["faults"] == [FAULT_ID] and set(g["offset"]) == {"mean", "max"} and g["offset"]["max"] >= 0.0
    assert all(g["offset"] is None for g in stats["by_group"] if g["group"] not in (3, 4))
    assert stats["faults"][str(FAULT_ID)]["balance"] < 1e-9 and stats["faults"][str(FAULT_ID)]["radius"] is None
    assert any("смещение крыльев" in line for line in out["journal"])
    fields = fields_of(w, m["id"])
    assert set(fields) == {(1, None), (2, None), (3, 0), (3, 1), (4, 0), (4, 1), (5, None), (FAULT_ID, None)}
    assert all((fields[(3, k)].points["wing"] == k).all() and len(fields[(3, k)].points["x"]) > 0 for k in (0, 1))
    summary = checked(w, m["id"])  # the check reads every field back from its stored points
    assert {(g["group"], g["wing"]) for g in summary} == set(fields)
    meta = meta_of(w, m["id"])
    assert [e["wing"] for e in meta["outputs"] if e["group"] == 3 and e["kind"] == "body"] == [0, 1]
    regions = [bool(e.get("region")) for e in meta["outputs"] if e["group"] == FAULT_ID and e["kind"] == "body"]
    assert regions == [True, True]


def test_a_finite_fault_stores_the_partial_fits_of_its_wings_under_numbers_of_their_own(fscene) -> None:  # type: ignore[no-untyped-def]
    w, manual = fscene
    m = faulted(w, manual, name="Конечный", radius=120.0)
    out = run(w, m["id"])
    assert out["stats"]["faults"][str(FAULT_ID)]["radius"] == 120.0
    fields = fields_of(w, m["id"])
    # one finite fault, m = 1: the full fits of the wings are 0 and 1, the fit of the data that are not divided is
    # (0 + 1)·2 + 0 = 2
    assert {(3, 0), (3, 1), (3, 2), (4, 0), (4, 1), (4, 2)} <= set(fields)
    assert len(fields[(3, 2)].points["x"]) > len(fields[(3, 0)].points["x"])  # all the data of the group, not a wing
    checked(w, m["id"])
    assert sum(1 for o in out["outputs"] if o["group"] == 3 and o["kind"] == "body") == 2  # the fits are not outputs


def test_a_fault_with_no_group_to_cut_is_not_used_and_says_so(fscene) -> None:  # type: ignore[no-untyped-def]
    w, manual = fscene
    m = make_b(w, "Лишний", [*groups_of("SS", "SH"), fault_group()], manual, **small_patches())
    out = run(w, m["id"])
    assert all(o["group"] != FAULT_ID for o in out["outputs"]) and out["stats"].get("faults") is None
    assert {(g["group"], g["wing"]) for g in out["stats"]["by_group"]} == {(3, None), (4, None)}


def test_a_fault_that_does_not_cut_the_boundary_is_refused_in_russian(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    w = world
    lay = fault_layers(w, 900.0)  # far outside the box (0…420)
    m = make_b(w, "Вне", [*stack_groups(), fault_group()], fault_manual(lay), **small_patches())
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.status == 422 and e.value.code in ("fault_outside", "no_contacts") and e.value.message
    assert e.value.hint
    meta = meta_of(w, m["id"])
    assert meta["state"] == "error" and meta["error"]["code"] == e.value.code and kids(w, m["id"]) == []


def test_a_missing_fault_is_refused_before_the_job_reads_anything(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from dataclasses import replace

    from bf_core.model import job_stages as js

    w = world
    spec = w.spec([*stack_groups(), fault_group()], fault_manual(fault_layers(w)))
    js.refuse_unready(spec)  # a model the stack can build
    lost = replace(spec, groups=tuple(replace(g, faults=(77,)) if g.name == "SS" else g for g in spec.groups))
    with pytest.raises(ApiError) as e:  # `parse_model` refuses it first: the job meets it in a model edited by hand
        js.refuse_unready(lost)
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.hint


def test_too_many_wings_are_refused_before_the_job_not_at_the_end_of_the_build(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from dataclasses import replace

    from bf_core.model import job_stack as jst
    from bf_core.model import job_stages as js

    assert jst.max_wing([False] * 7) == 127 and jst.max_wing([False] * 8) == 255  # the codes of the wings of 7 / 8
    assert jst.max_wing([True] * 3) == 62 and jst.max_wing([True] * 4) == 254 and jst.max_wing([]) == 0
    w = world
    lay = fault_layers(w)

    def spec_of(finite: list[bool]) -> Any:
        ids = [FAULT_ID + k for k in range(len(finite))]
        faults = [fault_group(i, f"Разлом {i}", 100.0 if f else None) for i, f in zip(ids, finite, strict=True)]
        groups = [*groups_of("SS", "SH"), *faults]
        spec = w.spec(groups, [m for i in ids for m in fault_manual(lay, i)])
        return replace(spec, groups=tuple(replace(g, faults=tuple(ids)) if g.name == "SS" else g for g in spec.groups))

    js.refuse_unready(spec_of([False] * 7))  # 127: the last one the byte holds
    js.refuse_unready(spec_of([True] * 3))
    for finite in ([False] * 8, [True] * 4):
        with pytest.raises(ApiError) as e:
            js.refuse_unready(spec_of(finite))
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", "faults")
        assert "три" not in e.value.hint and e.value.hint


# --- the systems of veins -------------------------------------------------------------------------------------------


def test_a_system_of_veins_gives_a_body_a_vein_the_common_body_and_the_lines_of_the_crossing(vworld) -> None:  # type: ignore[no-untyped-def]
    w = vworld
    m = systems(w)
    out = run(w, m["id"])
    by = {(o["kind"], o["group"], o.get("code"), bool(o.get("common"))): o for o in out["outputs"]}
    assert {("body", 7, "VN", False), ("body", 7, "VN2", False), ("body", 7, None, True)} <= set(by)
    parts = by[("body", 7, "VN", False)]["volume"] + by[("body", 7, "VN2", False)]["volume"]
    assert abs(parts - by[("body", 7, None, True)]["volume"]) <= 1e-6 * parts  # the cut bodies make up the union
    assert by[("body", 7, "VN", False)]["name"].endswith("Жилы · VN") and by[("body", 7, None, True)]["name"].endswith(
        "общее тело")  # fmt: skip
    layer = next(o for o in out["outputs"] if o["kind"] == "line")
    assert "line_layer_id" in layer and "wireframe_id" not in layer and layer["lines"] >= 1 and layer["length"] > 0
    assert layer["name"].endswith("пересечение VN × VN2") and layer["group"] == 7 and layer["wing"] is None
    pairs = out["stats"]["vein_pairs"]
    assert [(p["group"], p["a"], p["b"]) for p in pairs] == [(7, "VN", "VN2")]
    assert pairs[0]["line_length"] == pytest.approx(layer["length"], rel=1e-9) and pairs[0]["volume_common"] > 0
    assert any("общая зона" in line for line in out["journal"])
    with dialect.read_tx(w.engine) as conn:
        got = st.get(conn, m["id"], lines=True)
        plain = st.get(conn, m["id"])
        listed = st.list_rows(conn)
    assert [o["kind"] for o in got["outputs"]].count("line") == 1 and all("wireframe_id" in o for o in plain["outputs"])
    assert plain["outputs"] == [o for o in got["outputs"] if o["kind"] != "line"]  # the wireframes only for the readers
    assert all(o["kind"] != "line" for o in listed[0]["outputs"])
    meta = meta_of(w, m["id"])
    entry = next(e for e in meta["outputs"] if e["kind"] == "line")
    assert entry["line_layer_id"] == layer["line_layer_id"] and entry["pair"] == ["VN", "VN2"]
    with dialect.read_tx(w.engine) as conn:
        row = conn.execute(sa.select(objects.c.parent_id, objects.c.kind, line_layers.c.line_count).join(
            line_layers, line_layers.c.object_id == objects.c.id).where(objects.c.id == layer["line_layer_id"])).one()
        parent = conn.execute(sa.select(objects.c.parent_id, objects.c.kind, objects.c.meta).where(
            objects.c.id == row.parent_id)).one()
    assert row.kind == "line_layer" and parent.kind == "line_file" and parent.parent_id == m["id"]
    assert "model_id" not in parent.meta  # the owner is `parent_id`: a copy would stay stale after `.bfggis`
    assert ["objects.changed", {"kind": "line_layer", "id": layer["line_layer_id"]}] in out["events"]


def test_a_run_writes_the_lines_again_and_the_deletion_of_the_model_takes_them(vworld) -> None:  # type: ignore[no-untyped-def]
    w = vworld
    m = systems(w)
    first = run(w, m["id"])
    old = next(o["line_layer_id"] for o in first["outputs"] if o["kind"] == "line")
    again = run(w, m["id"])
    new = next(o["line_layer_id"] for o in again["outputs"] if o["kind"] == "line")
    # the layer is made again (SQLite may give the freed number back): the events say that one went and one came
    assert ["objects.changed", {"kind": "line_layer", "id": old, "deleted": True}] in again["events"]
    assert ["objects.changed", {"kind": "line_layer", "id": new}] in again["events"]
    with dialect.read_tx(w.engine) as conn:
        files = [int(i) for i in conn.execute(sa.select(objects.c.id).where(
            objects.c.kind == "line_file", objects.c.parent_id == m["id"])).scalars()]  # fmt: skip
        left = [int(i) for i in conn.execute(sa.select(objects.c.id).where(
            objects.c.kind == "line_layer", objects.c.parent_id.in_(files))).scalars()]  # fmt: skip
    assert left == [new] and len(files) == 1  # the old layer and its file are gone, one of them stands
    made = [new, *files, *kids(w, m["id"])]
    with dialect.write_tx(w.engine) as conn:
        gone = st.delete(conn, w.store, m["id"])
    assert gone["line_layers"] == [new] and gone["line_files"] == files
    with dialect.read_tx(w.engine) as conn:  # what the model made went; the hand data of the world stay
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.id.in_(made))).scalar() == 0  # fmt: skip
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind == "line_layer")).scalar() == 3  # fmt: skip


def test_a_system_of_one_vein_or_without_contacts_is_refused_in_russian(vworld) -> None:  # type: ignore[no-untyped-def]
    w = vworld
    bad = vein_system_group(7, codes=("VN", "НЕТ"))
    m = make_b(w, "Пусто", [*groups_of("SS"), bad], **small_patches())
    with pytest.raises(ApiError) as e:
        run(w, m["id"])
    assert e.value.status == 422 and e.value.code == "no_contacts" and e.value.message and e.value.hint
    assert e.value.extra.get("group") == 7
    meta = meta_of(w, m["id"])
    assert meta["state"] == "error" and meta["error"]["group"] == 7 and kids(w, m["id"]) == []


# --- the numbers of the wings and the rows of a field ---------------------------------------------------------------


def test_the_number_of_a_wing_field_is_its_code_and_a_partial_fit_has_one_of_its_own() -> None:
    from bf_core.model.job_stack import wing_id

    assert [wing_id(1, [], 0, (s,)) for s in (0, 1)] == [0, 1]  # an infinite fault: the code of the wing
    assert [wing_id(2, [], 0, key) for key in ((0, 0), (1, 0), (0, 1), (1, 1))] == [0, 1, 2, 3]
    # one finite fault: the full fits are the wings 0 and 1, the fit of the undivided data is the number 2
    assert [wing_id(1, [0], 1, (0,)), wing_id(1, [0], 1, (1,)), wing_id(1, [0], 0, (-1,))] == [0, 1, 2]
    # an infinite fault (bit 0) and a finite one (bit 1): the partial fits are above the codes 0…3
    full = [wing_id(2, [1], 1, (a, b)) for a in (0, 1) for b in (0, 1)]
    part = [wing_id(2, [1], 0, (a, -1)) for a in (0, 1)]
    assert sorted(full) == [0, 1, 2, 3] and part == [4, 5]
    with pytest.raises(ApiError) as e:  # a number that int8 cannot keep
        wing_id(4, [0, 1, 2, 3], 7, (1, 1, 1, 1))
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.extra["param"] == "faults" and e.value.hint


def test_the_rows_of_a_field_are_found_by_their_data_and_a_whole_group_has_none() -> None:
    from types import SimpleNamespace

    from bf_core.model import data as md
    from bf_core.model.job_stack import used_rows

    rng = np.random.default_rng(3)
    n = 40
    xyz, f = rng.normal(size=(n, 3)) * 50.0, np.round(rng.normal(size=n), 1)
    xyz[5], f[5] = xyz[4], f[4] + 1.0  # the same place, another value: two rows
    group = np.where(np.arange(n) < 30, 1, 2).astype(np.int32)
    pts = md.Points(xyz, f, np.zeros(n, np.int8), group, np.full(n, -1, np.int8), np.zeros(n, np.int32),
                    np.zeros((n, 2)), np.full(n, -1, np.int64))  # fmt: skip
    pick = np.array([29, 4, 5, 0, 17])  # in a group, not in the order of the table
    rec = SimpleNamespace(xyz=xyz[pick], f=f[pick])
    assert used_rows(pts, 1, rec).tolist() == pick.tolist()  # type: ignore[arg-type]
    whole = SimpleNamespace(xyz=xyz[:30], f=f[:30])
    assert used_rows(pts, 1, whole) is None  # type: ignore[arg-type]  # every row of the group: no list of rows
    assert used_rows(pts, 1, SimpleNamespace(xyz=None, f=None)) is None  # type: ignore[arg-type]
    other = SimpleNamespace(xyz=xyz[[31, 35]], f=f[[31, 35]])
    assert used_rows(pts, 2, other).tolist() == [31, 35]  # type: ignore[arg-type]
    xyz[11], f[11] = xyz[10], f[10]  # two contacts of two holes at one place with one value
    both = SimpleNamespace(xyz=xyz[[3, 10, 11, 20]], f=f[[3, 10, 11, 20]])
    assert used_rows(pts, 1, both).tolist() == [3, 10, 11, 20]  # type: ignore[arg-type]  # not [3, 10, 10, 20]
    back = SimpleNamespace(xyz=xyz[[11, 3, 10]], f=f[[11, 3, 10]])
    assert used_rows(pts, 1, back).tolist() == [10, 3, 11]  # type: ignore[arg-type]  # the twins in their order


# --- the cancel -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["Данные", "Участки", "Изоповерхности", "Тела", "Проверки", "Запись"])
def test_a_cancel_at_any_stage_leaves_nothing_of_a_faulted_model(fscene, stage: str) -> None:  # type: ignore[no-untyped-def]
    w, manual = fscene
    m = faulted(w, manual, name="Отмена")
    before = meta_of(w, m["id"])
    cancel = Cancel(stage)
    with pytest.raises(JobCancelled):
        run(w, m["id"], tick=cancel)
    assert cancel.fired
    after = meta_of(w, m["id"])
    assert {k: v for k, v in after.items() if k != "state"} == {k: v for k, v in before.items() if k != "state"}
    assert after["state"] == "ready" and shas(w, m["id"]) == {} and kids(w, m["id"]) == []


@pytest.mark.parametrize("stage", ["Тела", "Запись"])
def test_a_cancel_of_a_system_of_veins_leaves_no_layer_of_strings(vworld, stage: str) -> None:  # type: ignore[no-untyped-def]
    w = vworld
    m = systems(w, name="Отмена жил")
    with pytest.raises(JobCancelled):
        run(w, m["id"], tick=Cancel(stage))
    with dialect.read_tx(w.engine) as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind.in_(["line_layer", "line_file", "wireframe"]), objects.c.parent_id == m["id"])).scalar()
    assert n == 0 and shas(w, m["id"]) == {} and meta_of(w, m["id"])["state"] == "ready"
    assert not any("line_layer_id" in e for e in meta_of(w, m["id"])["outputs"])
    assert version(w, m["id"]) == 1
