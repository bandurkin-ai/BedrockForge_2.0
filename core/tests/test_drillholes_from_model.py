"""«Модель на скважины» (подшаг 19.18; ТЗ 7.35 Д-34 ч. 1; ADR 0026 решения 25, 28; e19-api.md §8.4): the bodies of an
implicit model (by the priority of its groups) or closed wireframes cut the holes of a set into a new interval table
`FROM, TO, CODE` — **ТД34-1** a vertical hole through two horizontal layers: the boundaries are the heights of the
contacts (1e−9 m); **ТД34-2** an inclined hole through a sphere R = 50 m by its centre: the interval is 100 m (1e−6);
a gap between the bodies is an empty CODE (never 0); a number field of a block model at the middle of the intervals of
a table; the node of the table in the graph (the sources — the model and the set); a cancel leaves nothing; `.bfggis`;
the refusals {code, message, hint}; the job in a worker through the API."""

from __future__ import annotations

import itertools
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.composites import source
from bf_core.db import dialect
from bf_core.db.schema import dh_tables
from bf_core.drillholes import from_model as fm
from bf_core.drillholes import from_model_spec as fs
from bf_core.drillholes import store as dh
from bf_core.jobs.context import JobCancelled
from bf_core.model import store as ms
from bf_core.wireframes import store as ws
from tests import test_vario_store as vs
from tests.estutil import W, world
from tests.meshutil import cube, icosphere
from tests.test_model_store import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    get,
    make,
    put,
    run,
    write_result,
)

vc = vs.vc  # the core with an open project and the set of `test_vario_store`
KIND = "object:implicit_model"
_ids = itertools.count(70_001)
TOL = 1e-9
G = (1.0 + 5.0**0.5) / 2.0


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- helpers ------------------------------------------------------------------------------------------------------


def new_set(w: W, holes: list[dh.CollarRow], name: str = "На модель") -> int:
    with dialect.write_tx(w.project.engine) as conn:
        set_id = dh.create_set(conn, name)
        dh.replace_collars(conn, set_id, holes)
    return set_id


def vertical(name: str, x: float, y: float, z: float = 10.0, td: float = 120.0) -> dh.CollarRow:
    return dh.CollarRow(name, x=x, y=y, z=z, td=td, azimuth=0.0, dip=-90.0)


def body_of(w: W, ident: int, name: str, group: int | None, mesh: Any, **kw: Any) -> dict[str, Any]:
    """An output of the model: the wireframe `mesh` (vertices, triangles) as a child of the model."""
    v, t = mesh
    staged = ws.stage_wireframe(w.store, v, t)
    try:
        with dialect.write_tx(w.project.engine) as conn:
            return ms.commit_output(conn, w.store, staged, ident, name=name, kind="body", group=group, **kw)
    finally:
        staged.discard()


def model_with(w: W, bodies: list[tuple[str, int | None, Any, dict[str, Any]]], groups: int = 2) -> int:
    """A model of `groups` groups (ids 1…, the first is the youngest) and the bodies `(name, group, mesh, kw)`."""
    zones = ["окисл", "перв", "окисл2", "перв2"]
    spec = [{"name": f"Группа {i + 1}", "type": "domain", "codes": [zones[i]]} for i in range(groups)]
    ident = make(w, groups=spec)["id"]
    write_result(w, ident)
    out = [body_of(w, ident, name, gid, mesh, **kw) for name, gid, mesh, kw in bodies]
    run(w, ident, outputs=out)
    return ident


def wire(w: W, name: str, mesh: Any) -> int:
    with dialect.write_tx(w.project.engine) as conn:
        return ws.put_wireframe(conn, w.store, mesh[0], mesh[1], name=name)


def go(w: W, body: dict[str, Any], cancel_at: int | None = None, seen: list[int] | None = None) -> dict[str, Any]:
    """The job in this process, as the worker runs it; `cancel_at` — the tick that cancels."""
    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return fm.run(w.project.engine, w.project.folder, {"body": body}, job_id=next(_ids), tick=tick)


def table(w: Any, set_id: int, table_id: int, fields: tuple[str, ...] = ("CODE",)) -> list[tuple[Any, ...]]:
    """The rows `(hole, from, to, *fields)` of a table, as the sample reader sees them (None — empty)."""
    src = source.read(w.project.engine, set_id, table_id, list(fields))
    out = []
    for i, hole in enumerate(np.repeat(np.array(src.holes, dtype=object), np.diff(src.starts)).tolist()):
        row: list[Any] = [hole, float(src.from_m[i]), float(src.to_m[i])]
        for f in fields:
            if f in src.numbers:
                v = float(src.numbers[f][i])
                row.append(None if math.isnan(v) else v)
            else:
                c = int(src.codes[f][i])
                row.append(None if c < 0 else src.labels[f][c])
        out.append(tuple(row))
    return out


def spans(rows: list[tuple[Any, ...]], hole: str) -> list[tuple[float, float, Any]]:
    return [(r[1], r[2], r[3]) for r in rows if r[0] == hole]


def close(got: list[tuple[float, float, Any]], want: list[tuple[float, float, Any]], tol: float) -> bool:
    return len(got) == len(want) and all(
        g[2] == x[2] and abs(g[0] - x[0]) <= tol and abs(g[1] - x[1]) <= tol for g, x in zip(got, want, strict=True)
    )


def refused_with(fn: Any, status: int, code: str, param: str | None = None) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    err = e.value
    assert (err.status, err.code) == (status, code), (err.status, err.code, err.message)
    assert err.message and err.hint
    if param is not None:
        assert (err.extra or {}).get("param") == param
    return err


def layers() -> tuple[Any, Any]:
    """Two horizontal layers one on the other: z in [−50; 0] and [−100; −50] (they share the face z = −50)."""
    return cube(2, 50.0, (0.0, 0.0, -50.0), seed=None), cube(2, 50.0, (0.0, 0.0, -100.0), seed=None)


def test_the_veins_of_a_system_are_coded_by_their_own_codes_not_by_the_name_of_the_system(w: W) -> None:
    upper, lower = layers()
    veins = [("Жила VN", 1, upper, {"extra": {"code": "VN"}}), ("Жила VN2", 1, lower, {"extra": {"code": "VN2"}})]
    ident = model_with(w, veins, groups=1)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    done = go(w, {"set_id": set_id, "model_id": ident, "name": "Жилы"})
    assert [r[3] for r in table(w, set_id, done["table_id"]) if r[3] is not None] == ["VN", "VN2"]
    renamed = go(w, {"set_id": set_id, "model_id": ident, "name": "Жилы 2", "codes": {"1": "Жильная"}})
    assert {r[3] for r in table(w, set_id, renamed["table_id"])} == {"Жильная", None}  # a code given wins


# --- ТД34-1, ТД34-2 ----------------------------------------------------------------------------------------------


def test_td34_1_vertical_hole_through_two_layers_has_the_contacts_for_boundaries(w: W) -> None:
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0), vertical("V-2", 500.0, 500.0)])
    done = go(w, {"set_id": set_id, "model_id": ident, "name": "Модель"})
    assert (done["rows"], done["holes"], done["no_trajectory"], done["name"]) == (5, 2, 0, "Модель")
    rows = table(w, set_id, done["table_id"])
    # collar at z = 10: the contacts z = 0, −50, −100 are at 10, 60, 110 m; the gaps have no code (None, not 0)
    want = [(0.0, 10.0, None), (10.0, 60.0, "Группа 1"), (60.0, 110.0, "Группа 2"), (110.0, 120.0, None)]
    assert close(spans(rows, "V-1"), want, TOL), spans(rows, "V-1")
    assert spans(rows, "V-1")[0][0] == 0.0 and spans(rows, "V-1")[-1][1] == 120.0  # the collar and the end, bit for bit
    assert spans(rows, "V-2") == [(0.0, 120.0, None)]  # a hole that meets no body: one empty row, never a code
    rep = done["report"]
    assert rep["mode"] == "bodies" and rep["model"]["id"] == ident and rep["stretches"] == 2
    assert [c["code"] for c in rep["codes"]] == ["Группа 1", "Группа 2"]
    assert rep["codes"][0]["length"] == pytest.approx(50)
    assert done["events"][0][0] == "drillholes.changed" and done["events"][0][1]["table"] == str(done["table_id"])


def inclined_through_sphere(w: W, level: int = 2) -> tuple[int, int, Any]:
    """A hole along the diagonal of the icosahedron (vertex to the opposite vertex) through the centre of a sphere
    of R = 50 m: its chord is 2R whatever the faces between the vertices are."""
    centre = np.array([200.0, 300.0, -40.0])
    u = np.array([G, 0.0, -1.0]) / math.sqrt(G * G + 1.0)  # towards the vertex (g, 0, −1): east, down
    collar = centre - 80.0 * u
    dip = -math.degrees(math.asin(-u[2]))
    hole = dh.CollarRow("I-1", x=float(collar[0]), y=float(collar[1]), z=float(collar[2]), td=200.0, azimuth=90.0,
                        dip=dip)  # fmt: skip
    set_id = new_set(w, [hole], "Наклонные")
    mesh = icosphere(level, 50.0, centre)
    return set_id, wire(w, "Шар", mesh), mesh


def test_td34_2_inclined_hole_through_a_sphere_by_its_centre_is_100_m_long(w: W) -> None:
    set_id, ball, _mesh = inclined_through_sphere(w)
    done = go(w, {"set_id": set_id, "mesh_ids": [ball], "name": "Шар на скважине"})
    rows = spans(table(w, set_id, done["table_id"]), "I-1")
    assert [r[2] for r in rows] == [None, "Шар", None]
    assert rows[1][1] - rows[1][0] == pytest.approx(100.0, abs=1e-6)
    assert rows[1][0] == pytest.approx(30.0, abs=1e-6) and rows[2][1] == pytest.approx(200.0, abs=1e-6)


def test_the_priority_is_the_order_of_the_groups_and_a_shell_is_inner_first(w: W) -> None:
    big = cube(2, 100.0, (0.0, 0.0, -100.0), seed=None)  # x, y in [0; 100], z in [−100; 0]
    mid = cube(2, 60.0, (20.0, 20.0, -80.0), seed=None)  # inside the big one: z in [−80; −20]
    small = cube(2, 20.0, (40.0, 40.0, -60.0), seed=None)  # inside the mid one: z in [−60; −40]
    set_id = new_set(w, [vertical("P-1", 50.0, 50.0, 0.0, 120.0)])
    # the younger group (the first) is the small cube: it wins where the cubes overlap; the shells of the second group
    # are nested (a higher threshold lies inside a lower one) and the inner one is coded first
    ident = model_with(w, [("Мелкий", 1, small, {}), ("Оболочка 0.5", 2, big, {"threshold": 0.5}),
                           ("Оболочка 1", 2, mid, {"threshold": 1.0})])  # fmt: skip
    rows = spans(table(w, set_id, go(w, {"set_id": set_id, "model_id": ident})["table_id"]), "P-1")
    want = [(0.0, 20.0, "Группа 2 ≥ 0.5"), (20.0, 40.0, "Группа 2 ≥ 1"), (40.0, 60.0, "Группа 1"),
            (60.0, 80.0, "Группа 2 ≥ 1"), (80.0, 100.0, "Группа 2 ≥ 0.5"), (100.0, 120.0, None)]  # fmt: skip
    assert close(rows, want, 1e-7), rows


def test_codes_rename_the_bodies_and_the_bodies_of_one_code_are_one_row(w: W) -> None:
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    body = {"set_id": set_id, "model_id": ident, "codes": {"1": "Руда", "2": "Руда"}}
    rows = spans(table(w, set_id, go(w, {**body, "name": "Руда"})["table_id"]), "V-1")
    assert close(rows, [(0.0, 10.0, None), (10.0, 110.0, "Руда"), (110.0, 120.0, None)], TOL), rows
    refused_with(lambda: go(w, {**body, "codes": {"77": "X"}, "name": "Д"}), 422, "bad_param", "codes")
    refused_with(lambda: go(w, {**body, "codes": {"1": " "}, "name": "Д"}), 422, "bad_param", "codes")


def test_wireframes_by_the_order_of_the_list_and_a_name_for_the_code(w: W) -> None:
    upper, lower = layers()
    a, b = wire(w, "Нижний слой", lower), wire(w, "Верхний слой", upper)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    rows = spans(table(w, set_id, go(w, {"set_id": set_id, "mesh_ids": [a, b], "name": "Слои"})["table_id"]), "V-1")
    assert [r[2] for r in rows] == [None, "Верхний слой", "Нижний слой", None]  # by depth
    done = go(w, {"set_id": set_id, "mesh_ids": [a, b], "codes": {str(a): "Н"}, "name": "Слои 2"})
    assert [r[2] for r in spans(table(w, set_id, done["table_id"]), "V-1")] == [None, "Верхний слой", "Н", None]


# --- the holes ----------------------------------------------------------------------------------------------------


def test_holes_that_start_inside_end_inside_miss_the_body_or_have_no_trajectory(w: W) -> None:
    body = cube(2, 100.0, (0.0, 0.0, -100.0), seed=None)  # z in [−100; 0]
    wid = wire(w, "Тело", body)
    holes = [vertical("A-start", 50.0, 50.0, -20.0, 50.0),  # from the collar inside the body to the end (z = −70)
             vertical("B-end", 50.0, 50.0, 20.0, 60.0),  # from 20 m above, ends at z = −40 inside the body
             vertical("C-miss", 500.0, 500.0, 0.0, 30.0),
             vertical("D-above", 50.0, 50.0, 80.0, 30.0),  # ends above the body
             dh.CollarRow("E-nocollar")]  # fmt: skip
    set_id = new_set(w, holes)
    with dialect.write_tx(w.project.engine) as conn:  # E-nocollar is in the set but has no coordinates
        assert dh.hole_ids(conn, set_id)["E-nocollar"]
    done = go(w, {"set_id": set_id, "mesh_ids": [wid], "name": "Края"})
    rows = table(w, set_id, done["table_id"])
    assert close(spans(rows, "A-start"), [(0.0, 50.0, "Тело")], TOL)
    assert close(spans(rows, "B-end"), [(0.0, 20.0, None), (20.0, 60.0, "Тело")], TOL)
    assert spans(rows, "C-miss") == [(0.0, 30.0, None)] and spans(rows, "D-above") == [(0.0, 30.0, None)]
    assert spans(rows, "E-nocollar") == []  # no path: no rows, counted, never a code or a zero
    assert (done["holes"], done["no_trajectory"]) == (4, 1)
    assert any("без траектории" in line for line in done["journal"])
    assert all(r[3] is None or isinstance(r[3], str) for r in rows)


def test_whole_holes_samples_and_the_gaps_cover_every_hole_to_the_end() -> None:
    from bf_core.composites import coding
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey

    c = np.array([[0.0, 0.0, 0.0, 100.0], [50.0, 0.0, 0.0, 0.0], [90.0, 0.0, 0.0, 40.0]])
    traj = desurvey(Collars(np.arange(3), c[:, 0], c[:, 1], c[:, 2], c[:, 3]),
                    Surveys(np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0), np.zeros(0)), n_holes=3)  # fmt: skip
    samples, used = fm.whole_samples(traj, None, np.array([11, 12, 13]))
    assert used.tolist() == [0, 2] and samples.holes.tolist() == [11, 13]  # the hole of zero length has no path
    assert samples.frm.tolist() == [0.0, 0.0] and samples.to.tolist() == [100.0, 40.0]
    st = coding.Stretches(
        lines=samples.__class__(np.array([13]), np.array([0, 1]), np.array([5.0]), np.array([40.0])),
        code=np.array([0], dtype=np.int32), names=["a"], known=np.ones(2, dtype=bool), ends=samples.to, report={},
        journal=[])  # fmt: skip
    rows = fm.fill_rows(st, samples.holes, samples.to, [0])
    assert rows.hole.tolist() == [0, 1, 1] and rows.code.tolist() == [-1, -1, 0]
    assert rows.frm.tolist() == [0.0, 0.0, 5.0] and rows.to.tolist() == [100.0, 5.0, 40.0]


def test_random_holes_tile_the_path_and_the_codes_agree_with_the_boxes_at_the_middles(w: W) -> None:
    """Hard data: 150 inclined holes through 6 overlapping boxes (faces, edges and corners shared by construction):
    the rows tile [0, length] with no gap or overlap and the code of every row is that of the first box that holds the
    point of the path at its middle (found independently, with the coordinates of the path itself)."""
    rng = np.random.default_rng(11)
    n, m = 150, 6
    lows = rng.integers(0, 6, size=(m, 3)) * 20.0 - np.array([0.0, 0.0, 100.0])
    sides = rng.integers(2, 5, size=m) * 20.0
    ids = [wire(w, f"Коробка {k}", cube(2, float(sides[k]), tuple(lows[k]), seed=None)) for k in range(m)]
    # the collars are off the planes of the faces (a hole that runs in a face or starts on an edge of a body has no
    # single answer: the middle of a row would be a tie), the directions are those of the faces and the diagonals
    holes = [dh.CollarRow(f"R-{i:03d}", x=float(rng.integers(0, 12) * 10.0 + 0.37),
                          y=float(rng.integers(0, 12) * 10.0 + 0.53), z=float(rng.integers(0, 3) * 20.0 + 0.11),
                          td=float(rng.uniform(60.0, 160.0)),
                          azimuth=float(rng.choice([0.0, 45.0, 90.0, 200.0])),
                          dip=float(rng.choice([-90.0, -60.0, -45.0, -30.0]))) for i in range(n)]  # fmt: skip
    set_id = new_set(w, holes, "Случайные")
    done = go(w, {"set_id": set_id, "mesh_ids": ids, "name": "Случайные на скважинах"})
    rows = table(w, set_id, done["table_id"])
    traj = source.trajectories(w.project, set_id)
    wrong = 0
    for i, hole in enumerate(holes):
        mine = spans(rows, hole.name)
        assert mine[0][0] == 0.0 and mine[-1][1] == traj.traj.lengths()[traj.index[traj.hole_ids[i]]]
        assert all(a[1] == b[0] for a, b in zip(mine[:-1], mine[1:], strict=True)), mine  # no gap, no overlap
        assert all(r[0] < r[1] for r in mine)
        dense = np.full(len(mine), i)
        xyz = traj.traj.positions(dense, np.array([0.5 * (r[0] + r[1]) for r in mine]))
        for r, p in zip(mine, xyz, strict=True):
            want = next((f"Коробка {k}" for k in range(m) if np.all(p >= lows[k]) and np.all(p <= lows[k] + sides[k])),
                        None)  # fmt: skip
            wrong += want != r[2]
    assert wrong == 0
    assert sum(r[2] is not None for r in rows) > 40  # the boxes are met: the check is not about empty rows


# --- the field of a block model -----------------------------------------------------------------------------------


def test_a_field_of_a_block_model_at_the_middle_of_the_intervals(w: W) -> None:
    from bf_core.blocks.model import Model

    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, w.model)
    au = model.columns(["AU"])["AU"]
    centres = model.centres()
    k = int(np.flatnonzero(np.isfinite(au))[0])
    c = centres[k]
    # a hole through the centre of one block: collar 10 m above it, vertical, the interval round the block's middle
    hole = vertical("B-1", float(c[0]), float(c[1]), float(c[2] + 10.0), 40.0)
    out = vertical("B-2", float(c[0] + 1e6), float(c[1]), float(c[2] + 10.0), 40.0)  # far outside the grid
    set_id = new_set(w, [hole, out], "Для поля")
    tid = dh.write_table(w.project.engine, set_id, dh.TableData(
        name="Интервалы", kind="interval", holes=["B-1", "B-1", "B-2", "ZZ-9"], from_m=[5.0, 20.0, 5.0, 0.0],
        to_m=[15.0, 30.0, 15.0, 5.0], columns=[dh.ColumnSpec("N", "number")], values=[[1.0, 2.0, 3.0, 4.0]]),
        job_id=None, tick=lambda _p, _m: None)["table_id"]  # fmt: skip
    done = go(w, {"set_id": set_id, "block_model_id": w.model, "field": "au", "table": str(tid), "name": "AU на скв."})
    rows = table(w, set_id, done["table_id"], ("AU",))
    hit = [r for r in rows if r[0] == "B-1" and r[1] == 5.0]
    assert hit and hit[0][3] == pytest.approx(float(au[k]), abs=0.0)  # the middle (10 m) is the centre of the block
    assert [r[3] for r in rows if r[0] == "B-2"] == [None]  # outside the model: empty, never 0
    assert [r[3] for r in rows if r[0] == "ZZ-9"] == [None]  # a hole without a trajectory: empty
    assert done["report"]["mode"] == "field" and done["report"]["empty"] >= 2 and done["rows"] == 4
    with dialect.read_tx(w.project.engine) as conn:
        refs = lineage.store.inputs_of(conn, [lineage.Node("dh_table", done["table_id"])])
    got = [(r.node.kind, r.node.id, r.node.sub) for r in refs[lineage.Node("dh_table", done["table_id"])]]
    assert got == [("bm_field", w.model, "au"), ("dh_table", tid, ""), ("dh_set", set_id, "")]
    refused_with(lambda: go(w, {"set_id": set_id, "block_model_id": w.model, "field": "DOM", "table": str(tid),
                                "name": "К"}), 422, "bad_param", "field")  # fmt: skip
    refused_with(lambda: go(w, {"set_id": set_id, "block_model_id": w.model, "field": "AU", "table": "нет",
                                "name": "Т"}), 404, "table_not_found")  # fmt: skip


# --- the graph ----------------------------------------------------------------------------------------------------


def status(w: W, *nodes: lineage.Node) -> list[lineage.NodeStatus]:
    with dialect.read_tx(w.project.engine) as conn:
        got = lineage.status(conn, list(nodes))
    return [got[n] for n in nodes]


def test_the_table_is_a_node_built_from_the_model_and_the_set(w: W) -> None:
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    done = go(w, {"set_id": set_id, "model_id": ident, "name": "Литология модели"})
    node = lineage.Node("dh_table", done["table_id"])
    with dialect.read_tx(w.project.engine) as conn:
        refs = lineage.store.inputs_of(conn, [node])[node]
        impact = lineage.impact(conn, lineage.Node(KIND, ident))
    assert [(r.node.kind, r.node.id) for r in refs] == [(KIND, ident), ("dh_set", set_id)]
    assert [r.name for r in refs][0] == get(w, ident)["name"] and all(r.fp for r in refs)
    assert (done["table_id"]) in [n["id"] for n in impact["nodes"] if n["kind"] == "dh_table"]
    assert [s.state for s in status(w, node)] == ["current"]
    # the parameters of the model change: the table is «устарел» and names the model
    put(w, ident, get(w, ident)["version"], levels=3)
    stale = status(w, node)[0]
    assert stale.state == "stale" and "неявная модель" in str(stale.reason)
    # the model is deleted: the table stays with its rows, its source is told gone
    with dialect.write_tx(w.project.engine) as conn:
        ms.delete(conn, w.store, ident)
    lost = status(w, node)[0]
    assert lost.state == lineage.LOST and len(table(w, set_id, done["table_id"])) == 4


def test_wireframes_are_the_sources_when_the_request_names_them(w: W) -> None:
    upper, _lower = layers()
    a = wire(w, "Слой", upper)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    done = go(w, {"set_id": set_id, "mesh_ids": [a], "name": "Слой на скважине"})
    node = lineage.Node("dh_table", done["table_id"])
    with dialect.read_tx(w.project.engine) as conn:
        refs = lineage.store.inputs_of(conn, [node])[node]
    assert [(r.node.kind, r.node.id) for r in refs] == [("wireframe", a), ("dh_set", set_id)]
    assert status(w, node)[0].state == "current"
    with dialect.write_tx(w.project.engine) as conn:  # the geometry of the body changes: the table is «устарел»
        ws.delete_wireframe(conn, a)
    assert status(w, node)[0].state == lineage.LOST


# --- the request, the refusals ------------------------------------------------------------------------------------


def test_refusals_come_before_any_work_with_code_message_and_hint(w: W) -> None:
    upper, _lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {})])
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    ok = {"set_id": set_id, "model_id": ident, "name": "Т"}
    refused_with(lambda: go(w, {**ok, "set_id": 99999}), 404, "set_not_found")
    refused_with(lambda: go(w, {**ok, "model_id": 99999}), 404, "model_not_found")
    refused_with(lambda: go(w, {"set_id": set_id, "name": "Т"}), 422, "bad_param", "model_id")
    refused_with(lambda: go(w, {**ok, "mesh_ids": [1]}), 422, "bad_param", "model_id")  # two forms at once
    refused_with(lambda: go(w, {**ok, "name": "я" * 201}), 422, "bad_param", "name")
    refused_with(lambda: go(w, {"set_id": set_id, "mesh_ids": [99999]}), 422, "wireframe_not_found", "wireframe_ids")
    empty = make(w, name="Пустая", groups=[])["id"]
    refused_with(lambda: go(w, {**ok, "model_id": empty}), 422, "no_bodies", "model_id")
    surface = wire(w, "Поверхность", _plane())
    err = refused_with(lambda: go(w, {"set_id": set_id, "mesh_ids": [surface], "name": "О"}), 422, "wireframe_open")
    assert "Каркас" in err.message
    done = go(w, ok)
    taken = refused_with(lambda: go(w, ok), 409, "table_exists", None)
    assert (taken.extra or {})["name"] == "Т" and (taken.extra or {})["suggested"] == "Т (2)"
    assert table(w, set_id, done["table_id"])  # the table that was there is not replaced
    with dialect.read_tx(w.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state == "building")).scalar() == 0


def _plane() -> Any:
    v = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    return v, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)


def test_a_model_that_is_building_and_a_model_without_the_set_are_refused(w: W) -> None:
    upper, _lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {})])
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    with dialect.write_tx(w.project.engine) as conn:
        ms.begin(conn, ident, get(w, ident)["version"])
    refused_with(lambda: go(w, {"set_id": set_id, "model_id": ident}), 409, "model_building")


# --- a cancel -----------------------------------------------------------------------------------------------------


def test_a_cancel_at_any_tick_leaves_nothing(w: W) -> None:
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])
    set_id = new_set(w, [vertical(f"V-{i}", 25.0, 25.0) for i in range(30)])
    seen = [0]
    first = go(w, {"set_id": set_id, "model_id": ident, "name": "Первая"}, seen=seen)
    ticks = seen[0]
    assert ticks >= 8, "the job must ask for a cancel often"
    with dialect.read_tx(w.project.engine) as conn:
        before = lineage.store.count(conn)
        tables = conn.execute(sa.select(sa.func.count()).select_from(dh_tables)).scalar()
    for at in range(1, ticks + 1):
        with pytest.raises(JobCancelled):
            go(w, {"set_id": set_id, "model_id": ident, "name": f"Отмена {at}"}, cancel_at=at)
        with dialect.read_tx(w.project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_tables)).scalar() == tables, at
            assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state == "building")).scalar() == 0
            assert lineage.store.count(conn) == before, at  # the graph got nothing either
    assert len(table(w, set_id, first["table_id"])) == first["rows"]


# --- the imports --------------------------------------------------------------------------------------------------


def test_the_modules_do_not_load_numba_with_their_import() -> None:
    """The API process registers the handler and includes the router: numba comes with the first job."""
    code = (
        "import sys, bf_core.drillholes.from_model, bf_core.drillholes.from_model_spec, "
        "bf_core.drillholes.router\n"
        "bad = [m for m in ('numba', 'manifold3d') if m in sys.modules]\n"
        "raise SystemExit(3 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]


def test_the_kind_is_the_one_the_registry_will_name() -> None:
    from bf_core.jobs.registry import KINDS

    assert fm.KIND == "drillholes.from_model" and KINDS[fm.KIND].queue == "heavy"
    assert fm.memory_mb({"triangles": 10_000_000}) > fm.memory_mb({})
    assert fs.NAME_MAX == 200


# --- the route and the job in a worker ----------------------------------------------------------------------------

API = "/api/v1"


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.projects.service import projects_of

    if fm.__name__ not in HANDLER_MODULES:  # until the line of the orchestrator (`jobs/registry.py`)
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", fm.__name__)
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    pclient.wires = {x["name"]: x["id"] for x in pclient.get(f"{API}/wireframes").json()["wireframes"]}
    return pclient


def test_the_route_makes_the_job_and_the_table_with_the_ore_body_of_the_demo(demo) -> None:  # type: ignore[no-untyped-def]
    from tests.test_import_job import wait_job

    c = demo
    ore = c.wires["Рудное тело 1"]
    body = {"set_id": c.set_id, "mesh_ids": [ore], "name": "Рудное тело на скважинах"}
    r = c.post(f"{API}/drillholes/from_model", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert job["status"] == "done", job
    done = job["result"]
    assert set(done) >= {"set_id", "table_id", "name", "rows", "holes", "no_trajectory", "report", "events"}
    tables = {t["name"]: t for t in c.get(f"{API}/drillholes/sets/{c.set_id}/tables").json()["tables"]}
    mine = tables["Рудное тело на скважинах"]
    assert (mine["kind"], mine["rows"]) == ("interval", done["rows"]) and done["report"]["stretches"] > 0
    got = c.get(f"{API}/drillholes/sets/{c.set_id}/tables/{mine['table']}?limit=1000").json()
    key = {col["name"]: col["key"] for col in got["columns"]}
    codes = {row[key["CODE"]] for row in got["rows"]}
    assert codes == {"Рудное тело 1", None}  # the gaps are empty, never 0
    first: dict[str, Any] = {}
    for row in got["rows"]:
        first.setdefault(row["hole"], row)
    assert all(row["from"] == 0.0 for row in first.values())  # every hole is covered from its collar
    r = c.post(f"{API}/drillholes/from_model", json=body)  # the same name again
    assert r.status_code == 409 and r.json()["code"] == "table_exists" and r.json()["hint"], r.text
    assert r.json()["suggested"] == "Рудное тело на скважинах (2)"
    r = c.post(f"{API}/drillholes/from_model", json={**body, "name": "Разлом", "mesh_ids": [c.wires["Разлом"]]})
    assert r.status_code == 422 and r.json()["code"] == "wireframe_open" and r.json()["hint"], r.text
    r = c.post(f"{API}/drillholes/from_model", json={"set_id": c.set_id})
    assert r.status_code == 422 and r.json()["param"] == "model_id" and r.json()["hint"], r.text
    c.project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        r = c.post(f"{API}/drillholes/from_model", json={**body, "name": "Ещё"})
        assert r.status_code == 423 and r.json()["hint"], r.text
    finally:
        c.project.read_only = False


# --- .bfggis ------------------------------------------------------------------------------------------------------


def test_bfggis_keeps_the_table_and_renumbers_its_sources(tmp_path: Path, vc) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from bf_core.projects.service import projects_of
    from bf_core.storage.blobs import BlobStore
    from tests.test_import_job import wait_job
    from tests.test_vario_store import API as VAPI

    project = vc.project
    # the three holes of the set stand at z = 100, td = 20: a cube round the first one holds its top 15 m
    mesh = cube(2, 20.0, (-10.0, -10.0, 85.0), seed=None)
    with dialect.write_tx(project.engine) as conn:
        body = ws.put_wireframe(conn, BlobStore(project.folder), mesh[0], mesh[1], name="Куб")
    request = {"set_id": vc.set_id, "mesh_ids": [body], "name": "Куб на скв."}
    done = fm.run(project.engine, project.folder, {"body": request}, job_id=next(_ids), tick=lambda _p, _m: None)
    before = spans(table(SimpleNamespace(project=project), vc.set_id, done["table_id"]), "A-1")
    assert [r[2] for r in before] == ["Куб", None] and abs(before[0][1] - 15.0) < 1e-9
    r = vc.post(VAPI + "/projects/export", json={"path": str(tmp_path / "Модель на скважины")}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = vc.post(VAPI + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    objs = {int(k): int(v) for k, v in job["result"]["id_maps"]["objects"].items()}
    tabs = {int(k): int(v) for k, v in job["result"]["id_maps"]["dh_tables"].items()}
    assert vc.post(f"{VAPI}/projects/{job['result']['uid']}/open").status_code == 200
    opened = projects_of(vc.core).require_current()
    new_set, new_table = objs[vc.set_id], tabs[done["table_id"]]
    after = spans(table(SimpleNamespace(project=opened), new_set, new_table), "A-1")
    assert after == before  # the rows came bit for bit
    node = lineage.Node("dh_table", new_table)
    with dialect.read_tx(opened.engine) as conn:
        refs = lineage.store.inputs_of(conn, [node])[node]
        state = lineage.status(conn, [node])[node]
    assert [(r.node.kind, r.node.id) for r in refs] == [("wireframe", objs[body]), ("dh_set", new_set)]
    assert state.state == "current", (state.state, state.reason)

