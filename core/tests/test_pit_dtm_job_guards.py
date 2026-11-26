"""The guards of the jobs `pit.surfaces` and `pit.solid` (подшаг 21.21б-2; docs/progress/e21-api.md §9, §10): a relief
edited after the run is 409 `pit_stale`, the version the route saw is checked (409 `version_conflict`), the size is
checked before a z-grid is read (422 `pit_too_large`), only the columns of the shells wanted are read, one text for
`out_of_memory`, a DTM whose version moved is replaced (never 404). The world and the helpers are those of
`test_pit_dtm_job.py`."""

# ruff: noqa: F811 - the fixtures `w` and `pit_op` are imported, and the tests take them by their names
from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.mine import dtm_job as dj
from bf_core.mine import solid as so
from bf_core.mine import store as st
from bf_core.wireframes import store as ws
from tests.estutil import W
from tests.pitutil import counts, refused
from tests.test_pit_dtm_job import (  # noqa: F401 - the fixtures
    all_wireframes,
    bowl,
    full_model,
    pit_op,
    rd,
    row_of,
    run_solid,
    run_surfaces,
    scenario,
    terrain,
    w,
)


def edit_relief(w: W, rel: int, dz: float) -> None:
    """The geometry of the relief replaced in place (as `dtm_write` does): its fingerprint in the graph changes."""
    from bf_core.mine import dtm_write as dw

    xyz, tri = rd(w, ws.read_wireframe, w.store, rel)
    staged = ws.stage_wireframe(w.store, xyz + np.array([0.0, 0.0, dz]), tri)
    try:
        with dialect.write_tx(w.project.engine) as conn:
            row = conn.execute(sa.select(objects.c.version, objects.c.name, objects.c.meta, objects.c.folder)
                               .where(objects.c.id == rel)).one()  # fmt: skip
            dw._replace(conn, w.store, staged, rel, int(row.version), name=row.name, meta=dict(row.meta or {}),
                        folder=row.folder)  # fmt: skip
    finally:
        staged.discard()


def test_a_relief_edited_after_the_run_is_pit_stale_for_both_jobs(w: W) -> None:
    """The z-grids of the shells and the top of the solid must be of one relief: the one of the run is gone → 409."""
    mid = full_model(w)
    rel = terrain(w)
    mined = bowl({1: 1, 2: 2, 3: 3})
    sid = scenario(w, mid, mined, terrain_id=rel)
    assert run_solid(w, sid, 2)["closed"] is True  # the relief as it was at the run: fine
    edit_relief(w, rel, 7.5)
    before = (counts(w), len(all_wireframes(w)))
    for call in (lambda: run_surfaces(w, sid), lambda: run_solid(w, sid, 2)):
        e = refused(call, 409, "pit_stale")
        assert "рельеф" in e.message and "Перезапустите расчёт" in e.hint
    assert (counts(w), len(all_wireframes(w))) == before  # nothing was written by a refusal
    scenario(w, mid, mined, terrain_id=rel, sid=sid, pit=False)  # the scenario is run again over the new relief
    assert run_solid(w, sid, 2)["replaced"] is True and run_surfaces(w, sid, [1])["wireframes"][0]["shell"] == 1


def test_an_edit_of_the_attributes_of_the_relief_is_not_a_stale_relief(w: W) -> None:
    mid = full_model(w)
    rel = terrain(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=rel)
    with dialect.write_tx(w.project.engine) as conn:  # the name and the version, not the geometry
        conn.execute(sa.update(objects).where(objects.c.id == rel).values(
            name="Другое имя", version=objects.c.version + 1))  # fmt: skip
    assert run_solid(w, sid, 2)["closed"] is True and run_surfaces(w, sid, [3])["wireframes"]


def test_a_relief_changed_between_the_read_and_the_write_is_stale_too(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    mid = full_model(w)
    rel = terrain(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=rel)
    real = so.build

    def edited(*a: Any, **kw: Any) -> Any:
        edit_relief(w, rel, 1.0)  # while the solid is being built
        return real(*a, **kw)

    monkeypatch.setattr(so, "build", edited)
    refused(lambda: run_solid(w, sid, 2), 409, "pit_stale")
    assert all_wireframes(w) == [rel]  # the relief only: the solid was not written


def test_the_version_the_route_saw_is_checked_by_both_jobs(w: W) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}))
    version = rd(w, st.info, sid)["version"]
    for call in (lambda: run_surfaces(w, sid, expected_version=version + 1),
                 lambda: run_solid(w, sid, 2, expected_version=version + 1)):  # fmt: skip
        assert refused(call, 409, "version_conflict").extra == {"version": version}
    assert all_wireframes(w) == []
    assert run_surfaces(w, sid, [1], expected_version=version)["wireframes"]
    assert run_solid(w, sid, 2, expected_version=version)["closed"] is True


def test_only_the_columns_of_the_shells_wanted_are_read(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}))
    real = st.read_zgrid
    asked: list[Any] = []

    def spy(conn: Any, store: Any, ident: int, shells: Any = None) -> Any:
        table = real(conn, store, ident, shells)
        asked.extend([shells, table.column_names])
        return table

    monkeypatch.setattr(st, "read_zgrid", spy)
    run_surfaces(w, sid, [3, 1])
    run_solid(w, sid, 2)
    assert asked == [[1, 3], ["i", "j", "z1", "z3"], [2], ["i", "j", "z2"]]  # the other z stay on the disk
    with dialect.read_tx(w.project.engine) as conn:  # a file that has not the shell asked for is broken
        refused(lambda: real(conn, w.store, sid, [4]), 409, "pit_scenario_broken")
        assert real(conn, w.store, sid).column_names == ["i", "j", "z1", "z2", "z3"]  # no `shells`: all, as before


def test_the_size_is_checked_before_a_z_grid_is_read(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.mine import limits

    sid = scenario(w, full_model(w), bowl({1: 1, 2: 2, 3: 3}))

    def never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the z-grids are read before the size is checked")

    monkeypatch.setattr(st, "read_zgrid", never)
    monkeypatch.setattr(limits, "MAX_ZGRID", 80 * 2)  # 80 columns: two shells fit, three do not
    e = refused(lambda: run_surfaces(w, sid), 422, "pit_too_large")
    assert "3 оболочек" in e.message and "RF" in e.hint
    monkeypatch.setattr(limits, "MAX_COLUMNS", 79)
    refused(lambda: run_surfaces(w, sid, [1]), 422, "pit_too_large")
    refused(lambda: run_solid(w, sid, 1), 422, "pit_too_large")
    assert all_wireframes(w) == []


def test_a_job_that_ran_out_of_memory_says_so_in_one_way() -> None:
    from bf_core.mine import limits

    e = dj._failure(MemoryError())
    assert (e.code, e.message, e.hint) == limits.OUT_OF_MEMORY and "регуляризацией" in e.hint
    assert dj._memory_mb({}) == 1024 and dj._memory_mb({"size_columns": 1 << 20, "size_shells": 100}) > 1024 + 800
    assert limits.view_memory_mb({"size_columns": 1 << 26, "size_shells": 1}) < 20_000  # the plan of the limit: < 20 GB


def test_a_dtm_changed_while_the_job_works_is_replaced_not_404(w: W) -> None:
    """The version of the DTM moved after `found` was made (a colour, an attribute): the row is read again with its
    version, the update does not miss it, the edit stays; a version that moves under the update is a 409."""
    from bf_core.mine import dtm_write as dw

    mid = full_model(w)
    sid = scenario(w, mid, bowl({1: 1, 2: 2, 3: 3}), terrain_id=terrain(w))
    first = run_surfaces(w, sid, [2])["wireframes"][0]["wireframe_id"]
    with dialect.read_tx(w.project.engine) as conn:
        assert dw.outputs_of(conn, sid, dw.OP_SURFACE) == {2: (first, 1)}
    with dialect.write_tx(w.project.engine) as conn:  # the colour edited in another window: version + 1
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == first)).scalar() or {})
        conn.execute(sa.update(objects).where(objects.c.id == first).values(
            meta={**meta, "color": "#336699"}, version=objects.c.version + 1))  # fmt: skip
    again = run_surfaces(w, sid, [2])["wireframes"][0]
    assert again["wireframe_id"] == first and again["replaced"] is True
    row = row_of(w, first)
    assert row.version == 3 and row.meta["color"] == "#336699" and row.meta["source"]["op"] == dw.OP_SURFACE
    staged = ws.stage_wireframe(w.store, *rd(w, ws.read_wireframe, w.store, first))
    try:
        with dialect.write_tx(w.project.engine) as conn:
            e = refused(lambda: dw._replace(conn, w.store, staged, first, 1, name="Х", meta={}, folder="Карьер"),
                        409, "version_conflict")  # fmt: skip
        assert "Повторите" in e.hint
    finally:
        staged.discard()
