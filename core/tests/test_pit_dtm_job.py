"""The jobs `pit.surfaces` and `pit.solid` (подшаг 21.12; ТЗ 8.11.4 «Выходы»; ADR 0028 решения 25, 26, 28, 30;
docs/progress/e21-api.md §9, §10): the DTMs of the shells (wireframes with `wireframes.dtm`, «‹сценарий› — RF 0,85»,
the folder of the scenario, the scenario their input in the graph, a second run replaces them in place) and the solid of
the pit (closed, its volume against Σ V of the mined parents, two pits, the fallback, `pit_solid_failed`). The z-grids
are made by `surfaces.z_grids` from a set of mined parents on a model with a known grid (also turned about Z); the
tables of the run are made by hand (`tests/pitutil.py`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.blocks import meta as block_meta
from bf_core.blocks import schema as block_schema
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import objects, wireframes
from bf_core.geo import dz_kernels
from bf_core.geo import solids as gs
from bf_core.jobs import registry
from bf_core.jobs.context import JobCancelled
from bf_core.mine import dtm_job as dj
from bf_core.mine import solid as so
from bf_core.mine import spec, surfaces
from bf_core.mine import store as st
from bf_core.mine import store_data as sd
from bf_core.mine.fields import ShellMap
from bf_core.wireframes import store as ws
from tests.estutil import W, world
from tests.pitutil import (
    AU_EST,
    BASE,
    BENCHES,
    CONTROL,
    ELEMENTS,
    GRID,
    ROCKS_EST,
    TEMPLATE,
    counts,
    meta_shells,
    refused,
    shells_of,
)
from tests.test_blocks_fields_store import add_fields, make_model
from tests.test_lineage_resources import EST, edit_table

NODE = "object:pit_scenario"
NZ = 6
BLOCK = 5.0 * 5.0 * 2.5  # the volume of a parent of the model of the tests (m³)


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


# --- a model, a relief, a scenario ----------------------------------------------------------------------------------


def full_model(w: W, rotation: float = 30.0, name: str = "Полная") -> int:
    """A model of 10 × 8 × 6 parents, every cell present (the rows are in the order of P)."""
    return make_model(w.project, w.store, n=480, name=name, grid={**GRID, "rotation": rotation})


def bowl(shells: dict[int, int], cx: int = 4, cy: int = 3) -> dict[tuple[int, int, int], int]:
    """The nested pits round the column (cx, cy): the shell n has `shells[n]` levels in the column of the centre and one
    level less with every step of the Chebyshev distance; a parent holds the smallest shell that has it."""
    out: dict[tuple[int, int, int], int] = {}
    for n in sorted(shells):
        for i in range(10):
            for j in range(8):
                depth = shells[n] - max(abs(i - cx), abs(j - cy))
                for k in range(NZ - max(depth, 0), NZ):
                    out.setdefault((i, j, k), n)
    return out


def terrain(w: W, z: float = -285.0, x_max: float | None = None, name: str = "Рельеф") -> int:
    """A flat relief over the plan of the model (`x_max` — it ends there: the east is outside of its plan)."""
    with dialect.read_tx(w.project.engine) as conn:
        mid = int(conn.execute(sa.select(objects.c.id).where(objects.c.name == "Полная")).scalar_one())
        xy = surfaces.centres(Model.open(conn, w.store, mid).grid)
    lo, hi = xy.min(axis=0) - 60.0, xy.max(axis=0) + 60.0
    if x_max is not None:
        hi[0] = x_max
    v = np.array([[lo[0], lo[1], z], [hi[0], lo[1], z], [hi[0], hi[1], z], [lo[0], hi[1], z]])
    with dialect.write_tx(w.project.engine) as conn:
        return ws.put_wireframe(conn, w.store, v, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), name=name)


def scenario(w: W, model_id: int, mined: dict[tuple[int, int, int], int], *, terrain_id: int | None = None,
             name: str = "Сценарий карьера", pit: bool = True, over: dict[str, Any] | None = None,
             total: int = 3, sid: int | None = None) -> int:  # fmt: skip
    """A scenario with a result whose z-grids are those of `mined` (and the field PIT of the model — `pit`)."""
    over = {**(over or {}), "terrain_id": terrain_id}
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, model_id)
        relief = None
        if terrain_id is not None:
            relief = dz_kernels.surface_index(*ws.read_wireframe(conn, w.store, terrain_id))
    grid = model.grid
    smap = ShellMap.from_parents(
        np.array([i + 10 * (j + 8 * k) for (i, j, k) in mined], dtype=np.uint64), np.array(list(mined.values()))
    )
    zg = surfaces.z_grids(grid, smap, relief, n_shells=total)
    nx, ny, _ = grid.counts
    c = np.arange(nx * ny)
    z_range = (grid.corner[2], grid.corner[2] + grid.counts[2] * 2.5)
    params = spec.parse_params({"model_id": model_id, **BASE, **over}).resolved(model.fields, z_range).canonical()
    rows = shells_of(total)
    staged = st.stage(w.store, sd.shells_table(rows, ELEMENTS), sd.benches_table(BENCHES),
                      sd.zgrid_table(c % nx, c // nx, zg.z), TEMPLATE, CONTROL)  # fmt: skip
    from bf_core.mine import store_graph as sg

    built = st.Built(params, {"id": model_id, "name": "Полная", "grid": GRID}, sg.read_fields(params), staged,
                     meta_shells(rows), {"ore_t": 6.0e6, "waste_t": 1.2e7, "strip": 2.0, "cf": 1.0e8}, q=0.01,
                     objective_error=12.5, outputs={"fields": ["PIT", "ORE"], "wireframes": []}, solver="hpf",
                     seconds={"total": 1.5}, versions={"numpy": "2"}, job_id=3)  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        ident = sid if sid is not None else int(st.create(conn, name, {"model_id": model_id, **BASE, **over})["id"])
        st.write_result(conn, w.store, ident, built)
    if pit and sid is None:
        pit_field(w, model_id, ident, mined, model.rows)
    return ident


def pit_field(w: W, model_id: int, ident: int, mined: dict[tuple[int, int, int], int], rows: int) -> None:
    """The field PIT as the run writes it: the smallest shell of every parent (0 — in none), `source.op = "pit"`."""
    arr = np.zeros(rows, dtype=np.int16)
    for (i, j, k), n in mined.items():
        arr[i + 10 * (j + 8 * k)] = n  # the model is full: the row of a parent is its P
    src = {"op": "pit", "params": {}, "inputs": [], "algo": "pit-1", "scenario_id": ident}
    add_fields(w.project, w.store, model_id, [{"name": "PIT", "dtype": "i2"}], {"PIT": arr}, source=src)


def run_surfaces(w: W, sid: int, shells: Any = None, **kw: Any) -> dict[str, Any]:
    return dj.run_surfaces(w.project.engine, w.project.folder, sid, shells, **kw)


def run_solid(w: W, sid: int, shell: Any = 2, name: str | None = None, **kw: Any) -> dict[str, Any]:
    return dj.run_solid(w.project.engine, w.project.folder, sid, shell, name, **kw)


def rd(w: W, fn: Any, *args: Any, **kw: Any) -> Any:
    with dialect.read_tx(w.project.engine) as conn:
        return fn(conn, *args, **kw)


def row_of(w: W, wid: int) -> Any:
    def one(conn: sa.Connection) -> Any:
        return conn.execute(sa.select(objects, wireframes).join(wireframes, wireframes.c.object_id == objects.c.id)
                            .where(objects.c.id == wid)).one()  # fmt: skip

    return rd(w, one)


def all_wireframes(w: W) -> list[int]:
    return rd(w, lambda c: list(c.execute(sa.select(objects.c.id).where(objects.c.kind == "wireframe")).scalars()))


# --- pit.surfaces ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0.0, 30.0])
def test_the_dtms_are_the_z_grids_over_the_centres_of_the_columns(w: W, rotation: float) -> None:
    mid = full_model(w, rotation)
    mined = bowl({1: 1, 2: 2, 3: 3})
    rel = terrain(w)
    sid = scenario(w, mid, mined, terrain_id=rel)
    out = run_surfaces(w, sid)
    assert out["scenario_id"] == sid and out["folder"] == "Карьер/Сценарий карьера" and out["skipped"] == []
    assert [x["shell"] for x in out["wireframes"]] == [1, 2, 3]
    assert [x["name"] for x in out["wireframes"]] == [f"Сценарий карьера — RF {t}" for t in ("0,8", "1,0", "1,2")]
    grid = rd(w, lambda c: Model.open(c, w.store, mid)).grid
    xy = surfaces.centres(grid)
    smap = ShellMap.from_parents(np.array([i + 10 * (j + 8 * k) for (i, j, k) in mined], dtype=np.uint64),
                                 np.array(list(mined.values())))  # fmt: skip
    zg = surfaces.z_grids(grid, smap, dz_kernels.surface_index(*rd(w, ws.read_wireframe, w.store, rel)), n_shells=3)
    for item in out["wireframes"]:
        assert (item["nodes"], item["triangles"], item["replaced"]) == (80, 2 * 9 * 7, False)
        row = row_of(w, item["wireframe_id"])
        assert row.dtm is not None and row.folder == out["folder"] and row.kind == "wireframe"  # a DTM, in the folder
        assert row.meta["source"] == {"op": "pit_surface", "scenario_id": sid, "shell": item["shell"], "rf": item["rf"],
                                      "version": 2, "scenario": "Сценарий карьера"}  # fmt: skip
        xyz, tri = rd(w, ws.read_wireframe, w.store, item["wireframe_id"])
        assert len(xyz) == 80 and len(tri) == 126 and not np.isnan(xyz).any()
        assert np.array_equal(xyz[:, :2], xy)  # the nodes are the centres of the columns in the axes of the project
        # the surface of the shell: the relief outside the pit, the bottom of the lowest parent of the column in it
        assert np.array_equal(xyz[:, 2], zg.of(item["shell"]))
        assert xyz[:, 2].max() == -285.0 and xyz[:, 2].min() == -285.0 - 2.5 * item["shell"]  # the centre goes down
        a, b, c = (xyz[tri[:, k]] for k in range(3))
        cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        assert (cross > 0).all()  # a surface: the triangles look up
    assert [e[0] for e in out["events"]] == ["objects.changed"] * 3
    assert {e[1]["id"] for e in out["events"]} == {x["wireframe_id"] for x in out["wireframes"]}


def test_the_scenario_is_the_input_of_every_dtm_in_the_graph(w: W) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w))
    out = run_surfaces(w, sid, [3, 1])  # the order of the request does not matter
    assert [x["shell"] for x in out["wireframes"]] == [1, 3]
    for item in out["wireframes"]:
        node = lineage.Node("wireframe", item["wireframe_id"])
        refs = rd(w, lineage.store.inputs_of, [node])[node]
        assert [(r.node, r.name) for r in refs] == [(lineage.Node(NODE, sid), "Сценарий карьера")]
        assert rd(w, lineage.status, [node])[node].state == "current"


def test_a_second_run_replaces_the_dtms_in_place(w: W) -> None:
    mid = full_model(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=(rel := terrain(w)))
    first = run_surfaces(w, sid)
    ids = [x["wireframe_id"] for x in first["wireframes"]]
    before = (counts(w), len(all_wireframes(w)))
    again = run_surfaces(w, sid, [2])
    assert again["wireframes"][0]["wireframe_id"] == ids[1] and again["wireframes"][0]["replaced"] is True
    assert (counts(w), len(all_wireframes(w))) == before  # no second copy of the shell, no blob nobody owns
    assert row_of(w, ids[1]).version == 2 and row_of(w, ids[0]).version == 1
    # the scenario is run again with a bigger pit: the geometry of the same objects is new
    with dialect.read_tx(w.project.engine) as conn:
        old_z = ws.read_wireframe(conn, w.store, ids[1])[0][:, 2].copy()
    sid2 = scenario(w, mid, bowl({1: 2, 2: 3, 3: 4}), terrain_id=rel, sid=sid, pit=False)
    assert sid2 == sid
    third = run_surfaces(w, sid)
    assert [x["wireframe_id"] for x in third["wireframes"]] == ids and all(x["replaced"] for x in third["wireframes"])
    assert len(all_wireframes(w)) == before[1]
    with dialect.read_tx(w.project.engine) as conn:
        new_z = ws.read_wireframe(conn, w.store, ids[1])[0][:, 2]
    assert not np.array_equal(old_z, new_z) and new_z.min() < old_z.min()
    assert row_of(w, ids[1]).meta["source"]["version"] == 3  # the scenario of the new result
    node = lineage.Node("wireframe", ids[1])
    assert rd(w, lineage.status, [node])[node].state == "current"  # recorded against the new result


def test_a_node_outside_the_plan_of_the_relief_is_not_built(w: W) -> None:
    mid = full_model(w)
    # the relief ends at the middle of the plan: the east is outside, there are no mined parents there
    mined = bowl({1: 1, 2: 2, 3: 3}, cx=2, cy=3)
    with dialect.read_tx(w.project.engine) as conn:
        xy = surfaces.centres(Model.open(conn, w.store, mid).grid)
    sid = scenario(w, mid, mined, terrain_id=terrain(w, x_max=float(np.median(xy[:, 0]))))
    out = run_surfaces(w, sid, [3])
    item = out["wireframes"][0]
    assert 0 < item["nodes"] < 80 and 0 < item["triangles"] < 126
    xyz, tri = rd(w, ws.read_wireframe, w.store, item["wireframe_id"])
    assert not np.isnan(xyz).any() and len(xyz) == item["nodes"]  # a gap is not built, not a node at 0


def test_the_checks_of_the_request(w: W) -> None:
    mid = full_model(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}))
    for bad, param in (([0], "shells.0"), ([1, 4], "shells.1"), ([True], "shells.0"), (["2"], "shells.0")):
        e = refused(lambda bad=bad: rd(w, dj.check_surfaces, sid, bad), 422, "bad_param")  # type: ignore[misc]
        assert e.extra["param"] == param
    assert rd(w, dj.check_surfaces, sid, [3, 1, 3]) == [1, 3] and rd(w, dj.check_surfaces, sid) == [1, 2, 3]
    assert refused(lambda: rd(w, dj.check_solid, sid, 9), 422, "bad_param").extra["param"] == "shell"
    refused(lambda: rd(w, dj.check_solid, sid, None), 422, "bad_param")
    assert rd(w, dj.check_solid, sid, 2) == 2
    refused(lambda: rd(w, dj.check_surfaces, 99_999), 404, "pit_scenario_not_found")
    with dialect.write_tx(w.project.engine) as conn:
        fresh = int(st.create(conn, "Новый", {"model_id": mid, **BASE})["id"])
    for call in (lambda: run_surfaces(w, fresh), lambda: run_solid(w, fresh, 1)):
        refused(call, 409, "pit_no_result")
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_running(conn, sid, job_id=None)
    refused(lambda: run_surfaces(w, sid), 409, "pit_running")
    assert len(all_wireframes(w)) == 0  # nothing was written by a refusal


def test_a_cancel_keeps_the_shells_that_are_done_and_tells_the_tree(w: W) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w))
    base = len(all_wireframes(w))  # the relief
    seen: list[str] = []

    def tick(_f: float | None = None, message: str | None = None) -> None:
        seen.append(message or "")
        if message == "Оболочки 3/3":
            raise JobCancelled()

    with pytest.raises(JobCancelled) as e:
        run_surfaces(w, sid, tick=tick)
    assert len(e.value.events or []) == 2 and all(ev[0] == "objects.changed" for ev in e.value.events or [])
    assert len(all_wireframes(w)) == base + 2 and "Оболочки 1/3" in seen


# --- pit.solid ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0.0, 30.0])
def test_the_solid_is_closed_and_its_volume_is_that_of_the_mined_parents(w: W, rotation: float) -> None:
    mid = full_model(w, rotation)
    mined = bowl({1: 1, 2: 2, 3: 3})
    sid = scenario(w, mid, mined, terrain_id=terrain(w))
    out = run_solid(w, sid, 2)
    mined_2 = sum(1 for n in mined.values() if n <= 2)
    assert out["volume_blocks"] == pytest.approx(mined_2 * BLOCK, rel=1e-12)  # Σ V of the parents with 1 ≤ PIT ≤ 2
    assert out["volume"] == pytest.approx(out["volume_blocks"], rel=1e-9) and out["volume_diff_pct"] == 0.0
    assert (out["parts"], out["closed"], out["method"], out["replaced"]) == (1, True, "between", False)
    assert (out["shell"], out["rf"], out["name"]) == (2, 1.0, "Сценарий карьера — карьер RF 1,00")
    assert out["folder"] == "Карьер/Сценарий карьера" and out["warnings"] == []
    assert out["events"] == [["objects.changed", {"kind": "wireframe", "id": out["wireframe_id"]}]]
    row = row_of(w, out["wireframe_id"])
    assert row.dtm is None and row.folder == out["folder"]  # a solid, not a DTM
    assert row.stats["closed"] is True and row.stats["volume"] == pytest.approx(out["volume"], rel=1e-9)
    assert row.stats["volume_blocks"] == out["volume_blocks"] and row.stats["parts"] == 1
    assert row.meta["source"]["op"] == "pit_solid" and row.meta["source"]["shell"] == 2
    xyz, tri = rd(w, ws.read_wireframe, w.store, out["wireframe_id"])
    assert gs.is_closed_solid(xyz, tri)[0]
    assert xyz[:, 2].max() == pytest.approx(-285.0) and xyz[:, 2].min() == pytest.approx(-285.0 - 2.5 * 2)
    node = lineage.Node("wireframe", out["wireframe_id"])
    assert [r.node for r in rd(w, lineage.store.inputs_of, [node])[node]] == [lineage.Node(NODE, sid)]


def test_the_default_name_the_given_name_and_a_second_run(w: W) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w))
    first = run_solid(w, sid, 3, "Карьер в конце")
    assert first["name"] == "Карьер в конце" and first["replaced"] is False
    before = (counts(w), len(all_wireframes(w)))
    second = run_solid(w, sid, 3)  # the same shell: the same object, the name is the default one now
    assert second["wireframe_id"] == first["wireframe_id"] and second["replaced"] is True
    assert second["name"] == "Сценарий карьера — карьер RF 1,20" and second["volume"] == first["volume"]
    assert (counts(w), len(all_wireframes(w))) == before and row_of(w, first["wireframe_id"]).version == 2
    other = run_solid(w, sid, 2)  # another shell is another solid
    assert other["wireframe_id"] != first["wireframe_id"] and len(all_wireframes(w)) == before[1] + 1


def test_two_pits_are_two_parts_and_their_volumes_add_up(w: W) -> None:
    mid = full_model(w)
    mined = {
        **bowl({1: 1, 2: 1, 3: 2}, cx=2, cy=2),
        **dict.fromkeys(bowl({1: 2}, cx=7, cy=5), 3),
    }  # the second: shell 3
    sid = scenario(w, mid, mined, terrain_id=terrain(w))
    out = run_solid(w, sid, 3)
    assert out["parts"] == 2 and out["method"] == "between" and out["closed"] is True
    assert out["volume"] == pytest.approx(out["volume_blocks"], rel=1e-9)
    assert run_solid(w, sid, 1)["parts"] == 1  # the second pit is not in the shell 1


def test_two_columns_that_touch_by_a_corner_are_two_parts(w: W) -> None:
    mid = full_model(w)
    mined = {(4, 3, NZ - 1): 1, (3, 4, NZ - 1): 1, (7, 2, NZ - 1): 2}  # the first two touch by a corner of the plan
    sid = scenario(w, mid, mined, terrain_id=terrain(w))
    out = run_solid(w, sid, 1)
    assert out["parts"] == 2 and out["method"] == "between" and out["closed"] is True
    assert out["volume"] == pytest.approx(2 * BLOCK, rel=1e-9) and out["volume_diff_pct"] == 0.0
    both = run_solid(w, sid, 2)
    assert both["parts"] == 3 and both["volume"] == pytest.approx(3 * BLOCK, rel=1e-9)


def test_a_notch_goes_to_variant_a_and_the_volume_is_the_same(w: W) -> None:
    """Two columns not mined in the middle of a pit, diagonal to each other: the weld of variant Б would give their edge
    four triangles — manifold3d takes the difference of the two solids under the surfaces."""
    mid = full_model(w)
    mined = {(i, j, k): 1 for i in range(1, 9) for j in range(1, 7) for k in range(NZ - 2, NZ)}
    for k in range(NZ - 2, NZ):
        del mined[(4, 3, k)], mined[(5, 4, k)]
    sid = scenario(w, mid, mined, terrain_id=terrain(w))
    out = run_solid(w, sid, 1)
    assert out["method"] == "difference" and out["closed"] is True and out["parts"] == 1
    assert out["volume"] == pytest.approx(out["volume_blocks"], rel=1e-9)
    assert gs.is_closed_solid(*rd(w, ws.read_wireframe, w.store, out["wireframe_id"]))[0]


def test_the_fallback_gives_the_same_body(w: W) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w))
    a = run_solid(w, sid, 3)
    b = run_solid(w, sid, 3, force_difference=True)
    assert (a["method"], b["method"]) == ("between", "difference") and b["wireframe_id"] == a["wireframe_id"]
    assert b["volume"] == pytest.approx(a["volume"], rel=1e-9) and b["parts"] == a["parts"] == 1


def test_a_shell_without_a_pit_and_a_solid_that_cannot_be_closed_are_422_with_a_hint(
    w: W, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid = full_model(w)
    sid = scenario(w, mid, {k: 2 for k in bowl({2: 2})}, terrain_id=terrain(w))  # nothing is mined in the shell 1
    before = (counts(w), len(all_wireframes(w)))
    e = refused(lambda: run_solid(w, sid, 1), 422, "pit_solid_failed")
    assert "нет объёма" in e.message

    def broken(*_a: Any, **_k: Any) -> Any:
        raise gs.SolidError("wireframe_open", "Тело карьера не замкнуто: открытых рёбер — 4.", gs.FIX_HINT)

    monkeypatch.setattr(so, "build", broken)
    e = refused(lambda: run_solid(w, sid, 2), 422, "pit_solid_failed")
    assert "не замкнуто" in e.message and "оболочк" in e.hint
    assert (counts(w), len(all_wireframes(w))) == before  # nothing is written


def test_the_difference_is_shown_not_refused_and_a_field_of_another_scenario_says_so(w: W) -> None:
    mid = full_model(w)
    mined = bowl({1: 1, 2: 2, 3: 3}, cx=0, cy=0)  # the pit at the edge of the grid: the solid spans centre to centre
    sid = scenario(w, mid, mined, terrain_id=terrain(w))
    out = run_solid(w, sid, 3)
    assert out["closed"] is True and out["volume"] < out["volume_blocks"] and out["volume_diff_pct"] > 1.0
    shown = abs(out["volume"] - out["volume_blocks"]) / out["volume_blocks"] * 100
    assert out["volume_diff_pct"] == pytest.approx(shown, abs=0.006)
    # another scenario on the model: the field PIT there is that of the first one, it is not this scenario's parents
    lone = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w, name="Рельеф 2"), name="Чужое поле",
                    pit=False)  # fmt: skip
    no_field = run_solid(w, lone, 2)
    assert no_field["volume_blocks"] is None and no_field["volume_diff_pct"] is None
    assert any("PIT" in x for x in no_field["warnings"]) and no_field["closed"] is True


def test_the_relief_of_the_scenario_is_the_top_and_without_it_the_top_of_the_grid(w: W) -> None:
    mid = full_model(w)
    mined = bowl({1: 1, 2: 2, 3: 3})
    flat = scenario(w, mid, mined, terrain_id=None, name="Без рельефа")  # the top of the grid, z = −285
    out = run_solid(w, flat, 2)
    assert out["volume"] == pytest.approx(out["volume_blocks"], rel=1e-9)
    xyz, _ = rd(w, ws.read_wireframe, w.store, out["wireframe_id"])
    assert xyz[:, 2].max() == pytest.approx(-285.0)
    low = scenario(w, mid, mined, terrain_id=terrain(w, z=-287.5, name="Ниже"), name="Рельеф ниже", pit=False)
    out2 = run_solid(w, low, 2)  # the relief is one level under the top: the pit is one level less deep
    assert out2["volume"] < out["volume"] and out2["closed"] is True
    xyz2, _ = rd(w, ws.read_wireframe, w.store, out2["wireframe_id"])
    assert xyz2[:, 2].max() == pytest.approx(-287.5)


def test_a_relief_that_is_gone_is_404(w: W) -> None:
    mid = full_model(w)
    rel = terrain(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=rel)
    with dialect.write_tx(w.project.engine) as conn:
        ws.delete_wireframe(conn, rel)
    e = refused(lambda: run_solid(w, sid, 2), 404, "wireframe_not_found")
    assert "рельеф" in e.message and e.hint


def test_the_model_changed_under_the_scenario_is_409(w: W) -> None:
    mid = full_model(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}))
    other = make_model(w.project, w.store, n=480, name="Другая", grid={**GRID, "counts": [11, 8, 6]})
    with dialect.write_tx(w.project.engine) as conn:
        row = conn.execute(sa.select(objects.c.meta).where(objects.c.id == sid)).scalar_one()
        meta = {**row, "params": {**row["params"], "model_id": other}}
        conn.execute(sa.update(objects).where(objects.c.id == sid).values(meta=meta))
    for call in (lambda: run_surfaces(w, sid), lambda: run_solid(w, sid, 1)):
        refused(call, 409, "model_changed")


# --- the graph ------------------------------------------------------------------------------------------------------


def test_td35_1_the_dtm_and_the_solid_go_stale_after_the_scenario(w: W) -> None:
    """A table of samples is edited → the field of the estimation is «устарел» → the scenario → its DTMs, its solid."""
    mid = full_model(w)
    est = {"AU_EST": np.full(480, 1.5)}
    add_fields(w.project, w.store, mid, [{"name": "AU_EST", "dtype": "f8", "unit": "г/т"}], est,
               source=EST)  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        lineage.record(conn, lineage.Node("bm_field", mid, "au_est"), [lineage.Node("dh_table", w.a)])
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w),
                   over={"elements": AU_EST, "rocks": ROCKS_EST})  # fmt: skip
    dtm = run_surfaces(w, sid, [2])["wireframes"][0]["wireframe_id"]
    solid = run_solid(w, sid, 2)["wireframe_id"]
    nodes = [lineage.Node("wireframe", dtm), lineage.Node("wireframe", solid), lineage.Node(NODE, sid)]
    assert {n.id: s.state for n, s in rd(w, lineage.status, nodes, store=w.store).items()} == {
        dtm: "current", solid: "current", sid: "current"}  # fmt: skip
    edit_table(w)
    got = rd(w, lineage.status, nodes, store=w.store)
    assert got[nodes[2]].state == "stale" and "таблица «Пробы»" in str(got[nodes[2]].reason)
    for n in nodes[:2]:
        assert got[n].state == "stale" and "сценарий карьера «Сценарий карьера»" in str(got[n].reason), got[n].reason


# --- the jobs -------------------------------------------------------------------------------------------------------


def test_the_handlers_are_registered_as_quick_jobs() -> None:
    for kind in ("pit.surfaces", "pit.solid"):
        job = registry.KINDS[kind]
        assert job.queue == "quick" and job.max_attempts == 1 and job.title
