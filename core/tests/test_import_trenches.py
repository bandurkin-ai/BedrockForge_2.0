"""Job `import.trenches` (ТЗ 7.35 Д-16; ADR 0011 §3, §5): polylines of STR, DXF and vertex tables → trenches of a
drillhole set. Names from the string or from a field, «Обратить направление», samples by 3D length, an existing
set, cancellation at every step, the operation log, the job through the scheduler and the audit after it."""

from __future__ import annotations

import itertools
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_holes, dh_rows, dh_surveys, dh_tables, import_log, objects
from bf_core.drillholes import store as dh
from bf_core.formats.lineset import from_lines, from_points
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

# UTM coordinates: one float64 step of Y is 9.3e-10 m.
T1 = np.array(
    [
        [512_345.678, 5_912_345.123, 1234.56],
        [512_371.913, 5_912_352.871, 1231.07],
        [512_380.004, 5_912_390.335, 1236.92],
        [512_355.217, 5_912_401.668, 1229.40],
    ]
)
T2 = np.array([[512_500.25, 5_912_100.5, 1200.0], [512_530.25, 5_912_140.5, 1200.0], [512_530.25, 5_912_140.5, 1188.0]])
T3 = np.array([[512_600.5, 5_912_000.25, 1210.0], [512_640.5, 5_912_030.25, 1210.0]])
_ids = itertools.count(100)


def chainage(v: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(np.sqrt((np.diff(v, axis=0) ** 2).sum(axis=1)))])


class Ctx(JobContext):
    """The context of a worker, run in the test process; `cancel_at` — the tick at which «Отменить» arrives."""

    def __init__(self, folder: Path, params: dict[str, Any], job_id: int, cancel_at: int | None = None) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.ticks = 0
        self.cancel_at = cancel_at
        super().__init__({"id": job_id, "params": params, "project_folder": str(folder)}, self.sent.append,
                         threading.Event())  # fmt: skip

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        self.ticks += 1
        if self.cancel_at is not None and self.ticks >= self.cancel_at:
            self._cancelled.set()
        super().tick(progress, message)

    @property
    def lines(self) -> list[str]:
        return [str(m[3]) for m in self.sent if m[0] == "log"]


def run_job(folder: Path, params: dict[str, Any], *, job_id: int | None = None, cancel_at: int | None = None) -> Ctx:
    """What the worker and the scheduler do with a job: run the handler; drop what a stopped job was building."""
    from bf_core.importing.trenches import import_trenches
    from bf_core.jobs.store import finish_cleanup

    ctx = Ctx(folder, params, next(_ids) if job_id is None else job_id, cancel_at)
    try:
        ctx.result = import_trenches(ctx)  # type: ignore[attr-defined]
    except (JobCancelled, JobError):
        finish_cleanup(ctx.engine(), False, ctx.id)
        raise
    finally:
        ctx.close()
    return ctx


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Канавы", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine, uid=f"trenches-{next(_ids)}")  # the key of cached paths
    engine.dispose()


@pytest.fixture()
def strings(tmp_path: Path) -> Path:
    """A Micromine STR: every trench is a string of its own (STRING = the name)."""
    from bf_core.formats.str_micromine import write_str

    path = tmp_path / "trenches.str"
    write_str(path, [from_lines("TR-1", [T1]), from_lines("TR-2", [T2]), from_lines("TR-3", [T3])])
    return path


def holes_of(engine: sa.Engine, set_id: int) -> dict[str, Any]:
    with dialect.read_tx(engine) as conn:
        return {r.name: r for r in conn.execute(sa.select(dh_holes).where(dh_holes.c.set_id == set_id))}


def stations_of(engine: sa.Engine, hole_id: int) -> tuple[np.ndarray, np.ndarray]:
    """(depth, xyz) of a hole in the order of the rows; its stations have no azimuth and dip."""
    with dialect.read_tx(engine) as conn:
        own = dh_surveys.c.hole_id == hole_id
        rows = conn.execute(sa.select(dh_surveys).where(own).order_by(dh_surveys.c.id)).all()
    assert all(r.azimuth is None and r.dip is None for r in rows)
    return np.array([r.depth for r in rows]), np.array([[r.x, r.y, r.z] for r in rows])


def state_of(engine: sa.Engine) -> dict[str, Any]:
    """Everything an import may leave behind."""
    sv = dh_surveys.c
    with dialect.read_tx(engine) as conn:
        return {
            "objects": [tuple(r) for r in conn.execute(sa.select(objects.c.name, objects.c.state, objects.c.version))],
            "holes": sorted(tuple(r) for r in conn.execute(sa.select(dh_holes.c.name, dh_holes.c.x, dh_holes.c.td))),
            "surveys": [tuple(r) for r in conn.execute(sa.select(sv.hole_id, sv.depth, sv.x).order_by(sv.id))],
            "tables": [tuple(r) for r in conn.execute(sa.select(dh_tables.c.name, dh_tables.c.state))],
            "rows": conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one(),
            "log": conn.execute(sa.select(sa.func.count()).select_from(import_log)).scalar_one(),
            "blobs": conn.execute(sa.select(sa.func.count()).select_from(blob_owners)).scalar_one(),
        }


def test_strings_become_trenches_named_by_string(project, strings: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.router import hole_type
    from bf_core.drillholes.trajectory import trajectories

    ctx = run_job(project.folder, {"set_name": "Канавы 2026", "lines": {"path": str(strings)}})
    res = ctx.result
    assert res["new_set"] and res["trenches"] == 3 and res["vertices"] == 9 and res["skipped"] == 0
    assert res["length"] == pytest.approx(chainage(T1)[-1] + 62.0 + 50.0, abs=1e-9)
    assert res["files"][0]["file"] == "trenches.str" and res["files"][0]["trenches"] == 3
    holes = holes_of(project.engine, res["set_id"])
    assert sorted(holes) == ["TR-1", "TR-2", "TR-3"]
    for name, v in (("TR-1", T1), ("TR-2", T2), ("TR-3", T3)):
        h = holes[name]
        depth, xyz = stations_of(project.engine, h.id)
        assert (xyz == v).all() and [h.x, h.y, h.z] == v[0].tolist()  # the float64 of the file, bit for bit
        assert np.abs(depth - chainage(v)).max() <= 1e-12 and h.td == depth[-1]  # 3D length from the first vertex
        assert h.has_collar and h.azimuth is None and h.dip is None
        assert h.attrs == {"TYPE": "Канава"} and hole_type(h.attrs)["key"] == "trench"
    assert stations_of(project.engine, holes["TR-2"].id)[0].tolist() == [0.0, 50.0, 62.0]  # not 50 m in plan
    with dialect.read_tx(project.engine) as conn:
        obj = conn.execute(sa.select(objects)).one()
        log = conn.execute(sa.select(import_log)).one()
        owners = conn.execute(sa.select(blob_owners.c.owner_kind, blob_owners.c.role)).all()
    assert (obj.name, obj.state, obj.kind, obj.job_id) == ("Канавы 2026", "ready", "drillhole_set", None)
    assert obj.version == 2
    assert (log.target, log.format, log.job_id) == ("trenches", "micromine_str", ctx.id) and log.sha256
    assert log.counts["trenches"] == 3 and log.mapping["name_from"] == "string"
    assert [tuple(o) for o in owners] == [("import", "source")]  # the source file is kept in the project
    assert any("Канав: 3" in line for line in ctx.lines), ctx.lines
    # the trajectories of the set go through the vertices exactly
    st = trajectories(project, res["set_id"])
    i = st.index[holes["TR-1"].id]
    assert st.skipped == [] and (st.traj.positions(np.full(4, i), chainage(T1)) == T1).all()


def test_names_from_a_field_from_join_and_numbered(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.str_micromine import write_str

    path = tmp_path / "one_string.str"
    attrs = {"JOIN": [11.0, 12.0, 13.0], "NAME": ["К-1", "К-2", "К-3"], "YEAR": [2024.0] * 3}
    write_str(path, [from_lines("TRENCH", [T1, T2, T3], attrs=attrs)])
    lines = {"path": str(path), "name_from": "field"}
    res = run_job(project.folder, {"set_name": "A", "lines": {**lines, "name_field": "name"}}).result
    holes = holes_of(project.engine, res["set_id"])
    assert sorted(holes) == ["К-1", "К-2", "К-3"] and holes["К-2"].x == T2[0, 0]
    res = run_job(project.folder, {"set_name": "B", "lines": {**lines, "name_field": "JOIN"}}).result
    assert sorted(holes_of(project.engine, res["set_id"])) == ["11", "12", "13"]  # numbers as they are written
    # the name of the string: three lines share it, so they are numbered in the order of the file — and it is said
    ctx = run_job(project.folder, {"set_name": "C", "lines": {"path": str(path)}})
    holes = holes_of(project.engine, ctx.result["set_id"])
    assert sorted(holes) == ["TRENCH-1", "TRENCH-2", "TRENCH-3"] and holes["TRENCH-3"].y == T3[0, 1]
    assert any("«TRENCH»" in line and "по порядку в файле" in line for line in ctx.lines), ctx.lines
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"set_name": "D", "lines": {**lines, "name_field": "CODE"}})
    assert err.value.code == "no_name_field" and "NAME" in err.value.hint and "JOIN" in err.value.hint
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"set_name": "D", "lines": {**lines, "name_field": "YEAR"}})
    assert err.value.code == "trench_name_repeats" and "2024" in err.value.message
    assert [o[0] for o in state_of(project.engine)["objects"]] == ["A", "B", "C"]  # the failed imports left nothing


def test_reverse_direction(project, strings: Path) -> None:  # type: ignore[no-untyped-def]
    res = run_job(project.folder, {"set_name": "R", "lines": {"path": str(strings), "reverse": True}}).result
    holes = holes_of(project.engine, res["set_id"])
    for name, v in (("TR-1", T1), ("TR-2", T2), ("TR-3", T3)):
        depth, xyz = stations_of(project.engine, holes[name].id)
        assert (xyz == v[::-1]).all() and [holes[name].x, holes[name].y, holes[name].z] == v[-1].tolist()
        assert np.abs(depth - chainage(v[::-1])).max() <= 1e-12
    assert stations_of(project.engine, holes["TR-2"].id)[0].tolist() == [0.0, 12.0, 62.0]
    # by name: only the trenches listed
    res = run_job(project.folder, {"set_name": "R2", "lines": {"path": str(strings), "reverse": ["TR-3"]}}).result
    holes = holes_of(project.engine, res["set_id"])
    assert holes["TR-1"].x == T1[0, 0] and holes["TR-3"].x == T3[-1, 0]
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"set_name": "R3", "lines": {"path": str(strings), "reverse": ["TR-9"]}})
    assert err.value.code == "unknown_trench" and "TR-9" in err.value.message


def _samples(tmp_path: Path, rows: list[str], name: str = "samples.csv") -> dict[str, Any]:
    path = tmp_path / name
    path.write_text("TRENCH;FROM;TO;AU_PPM;SAMPLE\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "mapping": {"hole_id": "TRENCH", "from": "FROM", "to": "TO", "sample_id": "SAMPLE"},
        "components": [{"column": "AU_PPM", "unit": "ppm"}],
    }


def test_samples_from_to_by_3d_length(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import interval_xyz, trajectories

    samples = _samples(tmp_path, ["TR-2;0;20;0,5;S1", "TR-2;45;55;1,2;S2", "TR-2;55;62;0,1;S3", "TR-1;0;10;2;S4"])
    params = {"set_name": "Канавы", "lines": {"path": str(strings)}, "samples": samples}
    ctx = run_job(project.folder, params)
    res = ctx.result
    assert res["samples"] == 4 and [f["file"] for f in res["files"]] == ["trenches.str", "samples.csv"]
    holes = holes_of(project.engine, res["set_id"])
    with dialect.read_tx(project.engine) as conn:
        c = dh_tables.c
        tables = conn.execute(sa.select(c.name, c.kind, c.state, c.row_count)).all()
        targets = sorted(conn.execute(sa.select(import_log.c.target)).scalars())
    assert [tuple(t) for t in tables] == [("Пробы (Assay)", "assay", "ready", 4)] and targets == ["assay", "trenches"]
    st = trajectories(project, res["set_id"])
    hole = np.full(3, holes["TR-2"].id)
    a, b = interval_xyz(st, hole, np.array([0.0, 45.0, 55.0]), np.array([20.0, 55.0, 62.0]))
    step = (T2[1] - T2[0]) / 50.0
    # «от/до» run along the polyline: 45–55 m passes the vertex at 50 m and ends 5 m down the vertical segment
    assert np.abs(a - [T2[0], T2[0] + 45.0 * step, T2[1] - [0, 0, 5.0]]).max() <= 1e-9
    assert np.abs(b - [T2[0] + 20.0 * step, T2[1] - [0, 0, 5.0], T2[2]]).max() <= 1e-9
    assert (b[2] == T2[2]).all()  # the end of the last sample is the last vertex itself


def test_table_of_vertices(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """CSV «канава, X, Y, Z»: Windows-1251, a decimal comma, numbers as names, rows of two trenches mixed."""
    rows = [("007", T2[0]), ("12", T3[0]), ("007", T2[1]), ("12", T3[1]), ("007", T2[2])]
    text = "Канава;X, м;Y, м;Z, м\n" + "".join(
        f"{n};{';'.join(repr(float(c)).replace('.', ',') for c in v)}\n" for n, v in rows
    )
    path = tmp_path / "вершины.csv"
    path.write_bytes(text.encode("cp1251"))
    ctx = run_job(project.folder, {"set_name": "CSV", "lines": {"path": str(path)}})
    holes = holes_of(project.engine, ctx.result["set_id"])
    assert sorted(holes) == ["007", "12"]  # the leading zeros of a name are kept
    for name, v in (("007", T2), ("12", T3)):
        depth, xyz = stations_of(project.engine, holes[name].id)
        assert (xyz == v).all() and depth.tolist() == chainage(v).tolist()
    assert ctx.result["files"][0]["format"] == "points"
    # the column of the trench is named explicitly; a trench without Z is not loaded and the log says which
    text = "ID;NAME;X;Y;Z\n1;A;10;20;5\n2;A;13;24;5\n3;B;50;60;\n4;B;51;61;7\n5;C;1;1;1\n"
    path = tmp_path / "named.csv"
    path.write_text(text, encoding="utf-8")
    ctx = run_job(project.folder, {"set_name": "CSV 2", "lines": {"path": str(path), "name_field": "NAME"}})
    assert ctx.result["trenches"] == 1 and ctx.result["skipped"] == 2
    assert stations_of(project.engine, holes_of(project.engine, ctx.result["set_id"])["A"].id)[0].tolist() == [0.0, 5.0]
    assert any("Z" in line and "«B»" in line for line in ctx.lines), ctx.lines
    assert any("меньше двух" in line and "«C»" in line for line in ctx.lines), ctx.lines


def test_surpac_and_dxf(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.dxf_write import write_dxf
    from bf_core.formats.str_surpac import write_surpac

    path = tmp_path / "surpac.str"
    five = from_lines("Стринг 5", [T1], attrs={"D1": ["К-5"]})
    write_surpac(path, [five, from_lines("Стринг 8", [T2], attrs={"D1": ["К-8"]})])
    res = run_job(project.folder, {"set_name": "S1", "lines": {"path": str(path)}}).result
    assert res["files"][0]["format"] == "surpac_str"
    holes = holes_of(project.engine, res["set_id"])
    assert sorted(holes) == ["Стринг 5", "Стринг 8"]
    assert (stations_of(project.engine, holes["Стринг 5"].id)[1] == T1).all()
    lines = {"path": str(path), "name_from": "field", "name_field": "D1"}
    res = run_job(project.folder, {"set_name": "S2", "lines": lines}).result
    assert sorted(holes_of(project.engine, res["set_id"])) == ["К-5", "К-8"]

    path = tmp_path / "trenches.dxf"
    layers = [from_lines("TR-A", [T1]), from_lines("КАНАВЫ", [T2, T3]), from_points("ПИКЕТЫ", T1[:2])]
    write_dxf(path, layers)
    ctx = run_job(project.folder, {"set_name": "D1", "lines": {"path": str(path)}})
    holes = holes_of(project.engine, ctx.result["set_id"])
    assert sorted(holes) == ["TR-A", "КАНАВЫ-1", "КАНАВЫ-2"] and ctx.result["files"][0]["format"] == "dxf"
    assert (stations_of(project.engine, holes["КАНАВЫ-2"].id)[1] == T3).all()
    assert any("ПИКЕТЫ" in line for line in ctx.lines)  # a layer of points holds no trenches
    # only the chosen layers
    ctx = run_job(project.folder, {"set_name": "D2", "lines": {"path": str(path), "layers": ["TR-A"]}})
    assert sorted(holes_of(project.engine, ctx.result["set_id"])) == ["TR-A"]


def test_degenerate_geometry(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.dxf_write import write_dxf

    twice = np.vstack([T1[0], T1[0], T1[1], T1[1], T1[1], T1[2]])  # vertices repeated in the file
    ring = np.vstack([T1, T1[0]])  # a closed line: the trench comes back to its start
    path = tmp_path / "hard.dxf"
    write_dxf(path, [from_lines("A", [twice]), from_lines("B", [ring]), from_lines("C", [np.vstack([T3[0], T3[0]])])])
    ctx = run_job(project.folder, {"set_name": "G", "lines": {"path": str(path)}})
    assert ctx.result["trenches"] == 2 and ctx.result["skipped"] == 1 and ctx.result["vertices"] == 8
    holes = holes_of(project.engine, ctx.result["set_id"])
    depth, xyz = stations_of(project.engine, holes["A"].id)
    assert (xyz == T1[:3]).all() and (np.diff(depth) > 0).all()  # no station of zero length
    depth, xyz = stations_of(project.engine, holes["B"].id)
    assert (xyz[-1] == T1[0]).all() and holes["B"].td == pytest.approx(chainage(ring)[-1], abs=1e-9)
    assert any("«C»" in line for line in ctx.lines) and any("замкнут" in line.lower() for line in ctx.lines), ctx.lines


def _hole_set(project) -> int:  # type: ignore[no-untyped-def]
    """A set with a drillhole: a collar, two readings and two samples."""
    with dialect.write_tx(project.engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow("H1", 512_000.0, 5_912_000.0, 1200.0, 80.0, 90.0, -60.0,
                                                       attrs={"Тип": "Скважина", "STATUS": "Пробурена"})])  # fmt: skip
        dh.replace_surveys_in(conn, set_id, [("H1", 0.0, 90.0, -60.0), ("H1", 40.0, 92.0, -61.0)], lambda _f, _m: None)
    data = dh.TableData("Пробы", "assay", ["H1", "H1"], [0.0, 2.0], [2.0, 4.0],
                        [dh.ColumnSpec("AU", "number", "g/t", "component")], [[0.3, 0.9]])  # fmt: skip
    dh.write_table(project.engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    return set_id


def test_trenches_are_added_to_an_existing_set(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import trajectories
    from bf_core.formats.str_micromine import write_str

    set_id = _hole_set(project)
    before = holes_of(project.engine, set_id)["H1"]
    res = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)}}).result
    assert res["set_id"] == set_id and not res["new_set"]
    holes = holes_of(project.engine, set_id)
    assert sorted(holes) == ["H1", "TR-1", "TR-2", "TR-3"] and tuple(holes["H1"]) == tuple(before)
    assert holes["TR-1"].attrs == {"Тип": "Канава"}  # the attribute of the type the set already has
    with dialect.read_tx(project.engine) as conn:
        h1 = conn.execute(sa.select(dh_surveys.c.depth, dh_surveys.c.azimuth, dh_surveys.c.x)
                          .where(dh_surveys.c.hole_id == before.id)).all()  # fmt: skip
        version = conn.execute(sa.select(objects.c.version)).scalar_one()
        rows = conn.execute(sa.select(sa.func.count()).select_from(dh_rows)).scalar_one()
    assert [tuple(r) for r in h1] == [(0.0, 90.0, None), (40.0, 92.0, None)] and version == 2 and rows == 2
    st = trajectories(project, set_id)
    assert st.skipped == [] and st.traj.lengths()[st.index[before.id]] == 80.0

    # the same trench again with another geometry: its stations are replaced, nothing is doubled
    moved = T3 + [5.0, 0.0, 1.0]
    again = tmp_path / "again.str"
    write_str(again, [from_lines("TR-3", [np.vstack([moved, moved[-1] + [0.0, 30.0, 0.0]])])])
    run_job(project.folder, {"set_id": set_id, "lines": {"path": str(again)}})
    holes = holes_of(project.engine, set_id)
    depth, xyz = stations_of(project.engine, holes["TR-3"].id)
    assert len(holes) == 4 and depth.tolist() == [0.0, 50.0, 80.0] and (xyz[:2] == moved).all()
    assert holes["TR-3"].td == 80.0 and holes["TR-3"].x == moved[0, 0]
    assert len(stations_of(project.engine, holes["TR-1"].id)[0]) == 4  # the other trenches stay

    # samples of the trenches would replace the samples of the set: only on request, or as a table of their own
    samples = _samples(tmp_path, ["TR-2;0;20;0,5;S1"])
    was = state_of(project.engine)
    with pytest.raises(JobError) as err:
        run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)}, "samples": samples})
    assert err.value.code == "assay_exists" and "Пробы" in err.value.message and state_of(project.engine) == was
    own = {**samples, "kind": "interval", "table_name": "Пробы канав", "components": [],
           "attributes": ["AU_PPM"], "mapping": {"hole_id": "TRENCH", "from": "FROM", "to": "TO"}}  # fmt: skip
    run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)}, "samples": own})
    assert sorted(t[0] for t in state_of(project.engine)["tables"]) == ["Пробы", "Пробы канав"]
    res = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)},
                                   "samples": {**samples, "replace": True}}).result  # fmt: skip
    assert res["samples"] == 1
    assert sorted(t[0] for t in state_of(project.engine)["tables"]) == ["Пробы (Assay)", "Пробы канав"]


def test_cancel_at_any_step_leaves_nothing(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    samples = _samples(tmp_path, ["TR-2;0;20;0,5;S1", "TR-1;0;10;2;S4"])
    set_id = _hole_set(project)
    for target in ({"set_name": "Новые канавы"}, {"set_id": set_id}):
        params = {**target, "lines": {"path": str(strings)}, "samples": {**samples, "replace": True}}
        was = state_of(project.engine)
        ticks = 0
        for at in range(1, 200):
            try:
                ctx = run_job(project.folder, params, cancel_at=at)
            except JobCancelled:
                ticks = at
                assert state_of(project.engine) == was, f"cancelled at tick {at}"
                continue
            break
        else:
            raise AssertionError("the job never finished")
        assert ticks >= 8, ticks  # the job reports its progress at every stage: reading, lines, samples, writing
        assert ctx.result["trenches"] == 3 and state_of(project.engine) != was


def test_errors_are_told_in_russian_and_leave_nothing(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.formats.str_micromine import write_str

    was = state_of(project.engine)

    def fails(params: dict[str, Any]) -> JobError:
        with pytest.raises(JobError) as err:
            run_job(project.folder, params)
        assert err.value.message and err.value.hint and state_of(project.engine) == was
        return err.value

    assert fails({"set_name": "X", "lines": {"path": str(tmp_path / "нет.str")}}).code == "file_unreadable"
    (tmp_path / "plan.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    assert fails({"set_name": "X", "lines": {"path": str(tmp_path / "plan.jpg")}}).code == "not_lines"
    (tmp_path / "junk.str").write_bytes(b"\x00\x01\x02 not a string file")
    assert fails({"set_name": "X", "lines": {"path": str(tmp_path / "junk.str")}}).code == "unsupported"
    flat = tmp_path / "no_z.str"
    write_str(flat, [from_lines("P", [np.column_stack([T1[:, :2], np.full(4, np.nan)])])])
    e = fails({"set_name": "X", "lines": {"path": str(flat)}})
    assert e.code == "no_trenches" and "Z" in e.message
    twins = tmp_path / "twins.str"
    write_str(twins, [from_lines("L", [T1, T2], attrs={"NAME": ["К-1", "К-1"]})])
    e = fails({"set_name": "X", "lines": {"path": str(twins), "name_from": "field", "name_field": "NAME"}})
    assert e.code == "trench_name_repeats" and "К-1" in e.message
    assert fails({"set_id": 999, "lines": {"path": str(strings)}}).code == "set_not_found"
    assert fails({"lines": {"path": str(strings)}}).code == "name_empty"
    bad = _samples(tmp_path, ["TR-1;0;x;1;S1"])
    assert fails({"set_name": "X", "lines": {"path": str(strings)}, "samples": bad}).code == "import_check_failed"
    run_job(project.folder, {"set_name": "X", "lines": {"path": str(strings)}})
    was = state_of(project.engine)
    assert fails({"set_name": "x", "lines": {"path": str(strings)}}).code == "name_taken"


def test_a_retry_after_the_commit_changes_nothing(project, strings: Path) -> None:  # type: ignore[no-untyped-def]
    """The worker died after the final transaction: the second attempt finds the import done."""
    params = {"set_name": "Канавы", "lines": {"path": str(strings)}}
    first = run_job(project.folder, params, job_id=7)
    was = state_of(project.engine)
    second = run_job(project.folder, params, job_id=7)
    assert second.result == first.result and state_of(project.engine) == was
    assert any("уже записаны" in line for line in second.lines)


def test_preview_for_the_wizard(strings: Path, tmp_path: Path) -> None:
    from bf_core.importing.trenches import preview

    out = preview({"path": str(strings), "reverse": ["TR-2"]})
    assert out["format"] == "micromine_str" and out["trenches"] == 3 and out["vertices"] == 9
    assert out["fields"] == ["JOIN"] and out["layers"] == [{"name": n, "lines": 1} for n in ("TR-1", "TR-2", "TR-3")]
    assert [t["name"] for t in out["preview"]] == ["TR-1", "TR-2", "TR-3"]
    x, y, z = T2[2].tolist()  # the direction of TR-2 is reversed: it starts at its last vertex
    assert out["preview"][1] == {"name": "TR-2", "vertices": 3, "length": 62.0, "x": x, "y": y, "z": z}


def test_through_the_scheduler_then_the_audit(pclient, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The handler is registered for the worker process; the audit of the trenches finds no survey errors."""
    from bf_core.jobs.service import jobs_of

    pclient.post(f"{API}/projects", json={"name": "Канавы"})
    samples = _samples(tmp_path, ["TR-2;0;20;0,5;S1", "TR-2;20;62;1,2;S2", "TR-1;0;10;2;S3", "TR-3;0;50;0,2;S4"])
    params = {"set_name": "Канавы", "lines": {"path": str(strings)}, "samples": samples}
    key, job_id = jobs_of(pclient.core).submit("import.trenches", params, title="Импорт канав")
    job = wait_job(pclient, key, job_id)
    assert job["status"] == "done" and job["result"]["trenches"] == 3, job
    log = pclient.get(f"{API}/jobs/{key}/{job_id}/log").json()
    assert "Канав: 3" in str(log)
    sets = pclient.get(f"{API}/drillholes/sets").json()["sets"]
    assert [(s["name"], s["holes"]) for s in sets] == [("Канавы", 3)]
    set_id = sets[0]["id"]
    traj = pclient.get(f"{API}/drillholes/sets/{set_id}/trajectories").json()
    by_name = {h["name"]: h for h in traj["holes"]}
    assert traj["skipped"] == [] and by_name["TR-2"]["stations"][-1] == [62.0, *T2[2].tolist()]
    card = pclient.get(f"{API}/drillholes/sets/{set_id}/holes/{by_name['TR-2']['id']}").json()
    assert card["type"]["key"] == "trench" and card["trajectory"]["length"] == 62.0 and card["surveys"] == 3
    r = pclient.post(f"{API}/audit/sets/{set_id}/run")
    assert wait_job(pclient, r.json()["source"], r.json()["id"], timeout=120)["status"] == "done"
    found = pclient.get(f"{API}/audit/sets/{set_id}/findings").json()["findings"]
    assert {f["code"] for f in found} == {"COLLAR_EMPTY_STATUS"}, found  # no «излом», no empty readings


def test_wizard_endpoints(pclient, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """`POST /import/trenches/inspect` — the preview of the wizard (nothing is written); `POST /import/trenches`
    checks the target and starts the job. Errors are {code, message, hint} in Russian."""
    url = f"{API}/import/trenches"

    def error_of(r: Any, status: int, code: str) -> dict[str, Any]:
        body = r.json()
        assert r.status_code == status and body["code"] == code, r.text
        assert body["message"] and body["hint"]
        return body

    lines = {"path": str(strings)}
    assert pclient.post(f"{url}/inspect", json={"lines": lines}).status_code == 409  # no project is open
    pclient.post(f"{API}/projects", json={"name": "Канавы"})

    out = pclient.post(f"{url}/inspect", json={"lines": {**lines, "reverse": ["TR-2"]}, "limit": 2}).json()
    assert out["format"] == "micromine_str" and out["trenches"] == 3 and out["vertices"] == 9
    assert out["fields"] == ["JOIN"] and [layer["name"] for layer in out["layers"]] == ["TR-1", "TR-2", "TR-3"]
    assert out["length"] == pytest.approx(float(sum(chainage(t)[-1] for t in (T1, T2, T3))), abs=1e-9)
    x, y, z = T2[2].tolist()  # reversed: TR-2 starts at its last vertex; `limit` rows of the preview
    assert out["preview"] == [
        {"name": "TR-1", "vertices": 4, "length": pytest.approx(float(chainage(T1)[-1]), abs=1e-9),
         "x": T1[0, 0], "y": T1[0, 1], "z": T1[0, 2]},
        {"name": "TR-2", "vertices": 3, "length": 62.0, "x": x, "y": y, "z": z},
    ]  # fmt: skip
    one = pclient.post(f"{url}/inspect", json={"lines": {**lines, "layers": ["TR-3"], "reverse": True}}).json()
    assert one["trenches"] == 1 and one["preview"][0]["x"] == T3[1, 0]
    # JOIN is empty in this file: no trench gets a name — the wizard shows why, it is not an error
    by_join = pclient.post(f"{url}/inspect", json={"lines": {**lines, "name_from": "field", "name_field": "join"}})
    assert by_join.status_code == 200 and by_join.json()["trenches"] == 0 and by_join.json()["skipped"] == 3
    assert "пустым полем" in by_join.json()["warnings"][0], by_join.text

    err = error_of(
        pclient.post(f"{url}/inspect", json={"lines": {**lines, "name_from": "field", "name_field": "NOPE"}}),
        422, "no_name_field",
    )  # fmt: skip
    assert "JOIN" in err["hint"]
    error_of(pclient.post(f"{url}/inspect", json={"lines": {**lines, "reverse": ["TR-9"]}}), 422, "unknown_trench")
    error_of(pclient.post(f"{url}/inspect", json={"lines": {"path": str(tmp_path / "нет.str")}}), 404, "file_not_found")
    with dialect.read_tx(projects_engine(pclient)) as conn:  # the preview wrote nothing
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0

    samples = _samples(tmp_path, ["TR-2;0;20;0,5;S1", "TR-2;20;62;1,2;S2", "TR-1;0;10;2;S3", "TR-3;0;50;0,2;S4"])
    error_of(pclient.post(url, json={"lines": lines, "set_name": "  "}), 422, "name_empty")
    error_of(pclient.post(url, json={"lines": lines, "set_id": 999}), 404, "set_not_found")
    unmapped = {"path": samples["path"]}
    error_of(pclient.post(url, json={"lines": lines, "set_name": "К", "samples": unmapped}), 422, "mapping_missing")
    gone = {**samples, "path": str(tmp_path / "нет.csv")}
    error_of(pclient.post(url, json={"lines": lines, "set_name": "К", "samples": gone}), 404, "file_not_found")

    r = pclient.post(url, json={"lines": {**lines, "reverse": ["TR-2"]}, "set_name": " Канавы  2026 ",
                                "samples": samples})  # fmt: skip
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"] == "Импорт канав в «Канавы 2026»", job
    assert job["result"]["trenches"] == 3 and job["result"]["samples"] == 4 and job["result"]["new_set"]
    sets = pclient.get(f"{API}/drillholes/sets").json()["sets"]
    assert [(s["name"], s["holes"]) for s in sets] == [("Канавы 2026", 3)]
    set_id = sets[0]["id"]
    traj = pclient.get(f"{API}/drillholes/sets/{set_id}/trajectories").json()
    by_name = {h["name"]: h for h in traj["holes"]}
    assert by_name["TR-2"]["stations"][0] == [0.0, *T2[2].tolist()]  # reversed, to the last bit

    # the name is taken; the set has a table of samples already: replacing it has to be confirmed
    error_of(pclient.post(url, json={"lines": lines, "set_name": "канавы 2026"}), 409, "name_taken")
    again = {"lines": lines, "set_id": set_id, "samples": samples}
    error_of(pclient.post(url, json=again), 409, "assay_exists")
    r = pclient.post(url, json={**again, "samples": {**samples, "replace": True}})
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"] == "Импорт канав" and not job["result"]["new_set"], job
    assert pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["holes"] == 3


def projects_engine(client: Any) -> sa.Engine:
    from bf_core.projects.service import projects_of

    return projects_of(client.core).require_current().engine  # type: ignore[no-any-return]
