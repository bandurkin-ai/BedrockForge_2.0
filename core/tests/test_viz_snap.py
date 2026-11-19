"""Snap targets of a drillhole set (ADR 0015 §4; ТЗ 7.22): collars bit for bit, paths and bounds of intervals by
the desurvey of the core (≤ 1e−9 m), the choice by the window of a section or the extent of the view,
`truncated`, ETag and 304; 5 000 holes × 100 intervals answered for a window within 0.3 s."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_rows, dh_tables
from bf_core.drillholes.trajectory import interval_xyz, trajectories
from bf_core.projects.service import projects_of
from bf_core.viz import binary
from tests.test_import_job import API
from tests.test_viz_holes import open_demo

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    return open_demo(pclient)


def snap(c: Any, set_id: int, **params: Any) -> tuple[Any, dict[str, Any], dict[str, np.ndarray]]:
    r = c.get(f"{API}/viz/sets/{set_id}/snap", params=params)
    assert r.status_code == 200, r.text
    head, arrays = binary.decode(r.content)
    return r, head, arrays


def test_collars_traces_and_bounds_are_the_desurvey_of_the_core(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    r, head, a = snap(c, set_id)
    assert head["kind"] == "snap" and head["holes"] == 49 and not head["truncated"]
    assert r.headers["cache-control"] == "no-cache"
    assert {k: v.dtype.str for k, v in a.items()} == {
        "collars": "<f8", "trace": "<f8", "trace_offsets": "<u4", "bounds": "<f8", "bound_hole": "<u4",
        "bound_depth": "<f8",
    }  # fmt: skip
    project = projects_of(c.core).require_current()
    collars = a["collars"].reshape(-1, 3)
    with dialect.read_tx(project.engine) as conn:
        holes = {
            r.name: (r.id, r.x, r.y, r.z)
            for r in conn.execute(sa.select(dh_holes).where(dh_holes.c.set_id == set_id, dh_holes.c.has_collar))
        }
        rows = conn.execute(
            sa.select(dh_rows.c.hole_id, dh_rows.c.from_m, dh_rows.c.to_m)
            .join(dh_tables, dh_tables.c.id == dh_rows.c.table_id)
            .where(dh_tables.c.set_id == set_id)
        ).all()
    for i, name in enumerate(head["names"]):
        if name.startswith("DH-"):  # a collar row: the trajectory starts at it bit for bit
            _id, x, y, z = holes[name]
            assert collars[i].tobytes() == np.array([x, y, z]).tobytes(), name
    # the paths start at the collars and lie on the arc of the core
    st = trajectories(project, set_id, "minimum_curvature")
    off = a["trace_offsets"].astype(np.int64)
    trace = a["trace"].reshape(-1, 3)
    assert len(off) == 50 and off[-1] == len(trace)
    dense = np.array([st.names.index(n) for n in head["names"]])
    assert np.array_equal(trace[off[:-1]], collars)
    ends = st.traj.positions(dense, st.traj.lengths()[dense])
    np.testing.assert_allclose(trace[off[1:] - 1], ends, rtol=0, atol=1e-9)
    # every «от» and «до» once per hole, at the position of the desurvey
    ids = np.array([r.hole_id for r in rows], dtype=np.int64)
    frm = np.array([np.nan if r.from_m is None else r.from_m for r in rows])
    to = np.array([np.nan if r.to_m is None else r.to_m for r in rows])
    pairs = zip(np.concatenate([ids, ids]).tolist(), np.concatenate([frm, to]).tolist(), strict=True)
    want = {(int(h), float(d) + 0.0) for h, d in pairs if d == d}
    got_ids = st.hole_ids[dense][a["bound_hole"].astype(np.int64)]
    got = {(int(h), float(d)) for h, d in zip(got_ids, a["bound_depth"], strict=True)}
    assert got == want and len(got) == len(a["bound_depth"])
    ref, _ = interval_xyz(st, got_ids, a["bound_depth"].copy(), a["bound_depth"].copy())
    assert np.abs(a["bounds"].reshape(-1, 3) - ref).max() <= 1e-9


def test_window_of_a_section_and_extent_choose_holes(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    _r, full, a = snap(c, set_id)
    collars = a["collars"].reshape(-1, 3)
    y0 = float(np.median(collars[:, 1]))
    # A section line along X (azimuth 90°) 5 m deep either way: the holes whose paths reach that slab.
    _r, head, s = snap(c, set_id, section=f"4000,{y0},90,5,5")
    assert 0 < head["holes"] < full["holes"]
    b = s["bounds"].reshape(-1, 3)
    assert len(b) and np.all(np.abs(b[:, 1] - y0) <= 5 + 1e-9)  # the normal of a line along X is −Y
    assert set(head["names"]) < set(full["names"])
    # the extent of the view in plan
    x0, x1 = float(collars[:, 0].min()) - 1, float(np.median(collars[:, 0]))
    _r, head, e = snap(c, set_id, bbox=f"{x0},0,{x1},1e7")
    assert 0 < head["holes"] < full["holes"]
    assert np.all(e["bounds"].reshape(-1, 3)[:, 0] <= x1 + 1e-9)
    r = c.get(f"{API}/viz/sets/{set_id}/snap", params={"section": "1,2,3"})
    assert r.status_code == 422 and r.json()["code"] == "bad_window" and r.json()["hint"]
    r = c.get(f"{API}/viz/sets/{set_id}/snap", params={"bbox": "5,0,1,1"})
    assert r.status_code == 422


def test_truncated_gives_collars_only_and_etag(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.viz import snap as vs

    c, set_id = demo
    r, _head, _a = snap(c, set_id)
    tag = r.headers["etag"]
    assert c.get(f"{API}/viz/sets/{set_id}/snap", headers={"If-None-Match": tag}).status_code == 304
    other = c.get(f"{API}/viz/sets/{set_id}/snap", params={"bbox": "0,0,1e7,1e7"}, headers={"If-None-Match": tag})
    assert other.status_code == 200 and other.headers["etag"] != tag  # another window — another key
    monkeypatch.setattr(vs, "MAX_HOLES", 10)
    _r, head, a = snap(c, set_id, bbox="0,0,-1e4,1e7,1e7,1e4")
    assert head["truncated"] and head["holes"] == 49 and set(a) == {"collars"}
    monkeypatch.setattr(vs, "MAX_HOLES", 5000)
    monkeypatch.setattr(vs, "MAX_POINTS", 100)
    _r, head, a = snap(c, set_id, bbox="0,0,1e7,1e7")
    assert head["truncated"] and set(a) == {"collars"}
    assert c.get(f"{API}/viz/sets/999/snap").status_code == 404


def _big_set(c: Any, holes: int = 5000, intervals: int = 100) -> int:
    """A grid of 100 × 50 inclined holes, 10 m apart, with 100 samples of 2 m each."""
    project = projects_of(c.core).require_current()
    from bf_core.drillholes.store import create_set

    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        set_id = create_set(conn, "Сетка")
        tid = conn.execute(
            sa.insert(dh_tables).values(
                set_id=set_id,
                name="Пробы",
                name_key="пробы",
                kind="assay",
                row_count=holes * intervals,
                rev=1,
                state="ready",
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        ).inserted_primary_key[0]
        cols = ("set_id", "name", "x", "y", "z", "td", "azimuth", "dip", "has_collar")
        rows = [
            (set_id, f"H{i:05d}", 500_000.0 + (i % 100) * 10.0, 6_000_000.0 + (i // 100) * 10.0, 400.0, 2.0 * intervals,
             90.0, -70.0, True)
            for i in range(holes)
        ]  # fmt: skip
        dialect.bulk_insert(conn, dh_holes, cols, rows)
        ids = list(conn.execute(sa.select(dh_holes.c.id).where(dh_holes.c.set_id == set_id)).scalars())
        dialect.bulk_insert(
            conn,
            dh_rows,
            ("table_id", "hole_id", "from_m", "to_m"),
            [(tid, h, 2.0 * k, 2.0 * k + 2.0) for h in ids for k in range(intervals)],
        )
    return set_id


@pytest.mark.slow
def test_5000_holes_by_100_intervals_answer_a_window_within_budget(pclient) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Замер привязки"})
    set_id = _big_set(pclient)
    t0 = time.perf_counter()
    _r, head, _a = snap(pclient, set_id, section="499990,6000245,90,10,10")  # cold: trajectories, the snapshot
    cold = time.perf_counter() - t0
    best = []
    for k in range(3):
        t0 = time.perf_counter()
        _r, head, a = snap(pclient, set_id, section=f"499990,{6000105 + 40 * k},90,10,10")
        best.append(time.perf_counter() - t0)
        assert not head["truncated"] and head["holes"] >= 100 and len(a["bound_depth"]) > 0
    print(f"snap 5000×100, window of {head['holes']} holes: {min(best) * 1000:.0f} ms (cold {cold * 1000:.0f} ms)")
    assert min(best) <= 0.3
