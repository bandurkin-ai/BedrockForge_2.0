"""Trenches next to drillholes: cases found in the Э7 review. «Заменить» of the surveys keeps the stations of
trenches (the vertices of their polylines) and refuses readings that name a trench; a drillhole whose name a
trench takes is named in the operation log and in the result of `import.trenches`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_surveys
from bf_core.drillholes import store as dh
from bf_core.formats.lineset import from_lines
from bf_core.jobs.context import JobCancelled, JobError
from tests.test_import_trenches import (  # noqa: F401 - `project` and `strings` are fixtures
    T1,
    T2,
    T3,
    Ctx,
    _hole_set,
    _ids,
    holes_of,
    project,
    run_job,
    state_of,
    stations_of,
    strings,
)

pytestmark = pytest.mark.windows

SURVEY = {"hole_id": "HOLEID", "depth": "DEPTH", "azimuth": "AZIMUTH", "dip": "DIP"}


def import_surveys(folder: Path, set_id: int, path: Path) -> Ctx:
    """`import.drillholes` with one file of surveys, as the worker and the scheduler run it."""
    from bf_core.importing.jobs import import_drillholes
    from bf_core.jobs.store import finish_cleanup

    spec = {"path": str(path), "kind": "survey", "mapping": SURVEY, "components": [], "options": {}}
    ctx = Ctx(folder, {"set_id": set_id, "files": [spec]}, next(_ids))
    try:
        ctx.result = import_drillholes(ctx)  # type: ignore[attr-defined]
    except (JobCancelled, JobError):
        finish_cleanup(ctx.engine(), False, ctx.id)
        raise
    finally:
        ctx.close()
    return ctx


def angles_of(engine: sa.Engine, hole_id: int) -> list[tuple[Any, ...]]:
    sv = dh_surveys.c
    with dialect.read_tx(engine) as conn:
        rows = conn.execute(sa.select(sv.depth, sv.azimuth, sv.dip, sv.x).where(sv.hole_id == hole_id).order_by(sv.id))
        return [tuple(r) for r in rows]


def test_replacing_surveys_keeps_the_stations_of_trenches(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.drillholes.trajectory import trajectories

    set_id = _hole_set(project)  # the drillhole H1: a collar, two readings, two samples
    run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)}})
    holes = holes_of(project.engine, set_id)
    before = {name: stations_of(project.engine, holes[name].id) for name in ("TR-1", "TR-2", "TR-3")}
    assert [len(d) for d, _xyz in before.values()] == [4, 3, 2]

    survey = tmp_path / "survey.csv"
    survey.write_text(
        "HOLEID,DEPTH,AZIMUTH,DIP\nH1,0,45,-70\nH1,30,46,-71\nH1,60,47,-72\nH2,0,0,-90\n", encoding="utf-8"
    )
    ctx = import_surveys(project.folder, set_id, survey)
    assert ctx.result["files"][0]["surveys"] == 4

    holes = holes_of(project.engine, set_id)
    assert sorted(holes) == ["H1", "H2", "TR-1", "TR-2", "TR-3"]
    # the readings of the drillholes are replaced by those of the file …
    assert angles_of(project.engine, holes["H1"].id) == [(0.0, 45.0, -70.0, None), (30.0, 46.0, -71.0, None),
                                                         (60.0, 47.0, -72.0, None)]  # fmt: skip
    assert angles_of(project.engine, holes["H2"].id) == [(0.0, 0.0, -90.0, None)]
    # … and every vertex of every trench is where it was, bit for bit
    for name, (depth, xyz) in before.items():
        now_depth, now_xyz = stations_of(project.engine, holes[name].id)
        assert np.array_equal(now_depth, depth) and np.array_equal(now_xyz, xyz), name
    assert np.array_equal(stations_of(project.engine, holes["TR-1"].id)[1], T1)
    st = trajectories(project, set_id)
    assert st.skipped == ["H2"]  # readings without a collar: no trajectory
    assert st.traj.lengths()[st.index[holes["TR-2"].id]] == 62.0 and st.traj.lengths()[st.index[holes["H1"].id]] == 80.0

    # the store itself: a file without the drillhole takes its readings away, the trenches stay
    with dialect.write_tx(project.engine) as conn:
        dh.replace_surveys_in(conn, set_id, [("H2", 0.0, 10.0, -80.0)], lambda _f, _m: None)
    assert angles_of(project.engine, holes["H1"].id) == []
    assert all(np.array_equal(stations_of(project.engine, holes[n].id)[1], xyz) for n, (_d, xyz) in before.items())


def test_surveys_that_name_a_trench_are_refused_and_change_nothing(project, strings: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    set_id = _hole_set(project)
    run_job(project.folder, {"set_id": set_id, "lines": {"path": str(strings)}})
    was = state_of(project.engine)
    survey = tmp_path / "with_trench.csv"
    survey.write_text("HOLEID,DEPTH,AZIMUTH,DIP\nH1,0,45,-70\nTR-2,0,90,0\nTR-2,20,90,0\n", encoding="utf-8")
    with pytest.raises(JobError) as err:
        import_surveys(project.folder, set_id, survey)
    e = err.value
    assert e.code == "survey_for_trench" and "«TR-2»" in e.message and "TR-1" not in e.message
    assert "Импорт канав" in e.hint and state_of(project.engine) == was
    # the store refuses on its own too (a caller that skipped the check of the job): nothing is deleted
    with pytest.raises(dh.TrenchSurveys) as refused, dialect.write_tx(project.engine) as conn:
        dh.replace_surveys_in(conn, set_id, [("H1", 0.0, 1.0, -1.0), ("TR-3", 0.0, 1.0, -1.0)], lambda _f, _m: None)
    assert refused.value.names == ["TR-3"] and state_of(project.engine) == was


def test_a_drillhole_that_becomes_a_trench_is_named(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.formats.str_micromine import write_str

    set_id = _hole_set(project)  # H1 is a drillhole with a collar, readings and samples
    with dialect.write_tx(project.engine) as conn:  # H9 came with a table only: no collar, no readings
        dh.ensure_holes(conn, set_id, ["H9"], dh.hole_ids(conn, set_id))
    path = tmp_path / "same_names.str"
    write_str(path, [from_lines("H1", [T1]), from_lines("H9", [T2]), from_lines("TR-3", [T3])])
    ctx = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(path)}})
    assert ctx.result["converted"] == 1 and ctx.result["converted_names"] == ["H1"]
    assert ctx.result["files"][0]["converted"] == 1
    warned = [line for line in ctx.lines if "«H1»" in line and "канав" in line.lower()]
    assert len(warned) == 1 and "H9" not in warned[0] and "TR-3" not in warned[0], ctx.lines
    assert any(m[0] == "log" and m[2] == "warn" and m[3] == warned[0] for m in ctx.sent)
    holes = holes_of(project.engine, set_id)
    assert holes["H1"].attrs["Тип"] == "Канава" and holes["H1"].x == T1[0, 0]  # it is a trench now
    assert np.array_equal(stations_of(project.engine, holes["H1"].id)[1], T1)

    # the same trenches again: they are trenches already, nobody is converted, nothing is warned about
    again = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(path)}})
    assert again.result["converted"] == 0 and again.result["converted_names"] == []
    assert not [line for line in again.lines if "стал" in line.lower()], again.lines
    # a retry of the first job after its commit tells the same
    retry = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(path)}}, job_id=ctx.id)
    assert retry.result == ctx.result


def test_many_converted_drillholes_are_listed_shortly(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.formats.str_micromine import write_str

    names = [f"С-{i:03d}" for i in range(130)]
    with dialect.write_tx(project.engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(n, 1.0, 2.0, 3.0, 10.0) for n in names])
    path = tmp_path / "many.str"
    write_str(path, [from_lines(n, [T3 + [float(i), 0.0, 0.0]]) for i, n in enumerate(names)])
    ctx = run_job(project.folder, {"set_id": set_id, "lines": {"path": str(path)}})
    assert ctx.result["converted"] == 130 and ctx.result["converted_names"] == names[:100]
    warned = [line for line in ctx.lines if "стали канавами" in line]
    assert len(warned) == 1 and "«С-000»" in warned[0] and "ещё 120" in warned[0] and "«С-129»" not in warned[0]
