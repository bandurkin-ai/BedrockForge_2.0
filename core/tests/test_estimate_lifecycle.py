"""The life of what an estimation leaves behind (18.27б): the rows of the graph of a deleted row / field go (an id of
SQLite that is used again inherits no `pin` and no inputs), `lineage.sweep` runs when a project is opened, the caches of
the estimation are emptied when a project is closed and keep within one budget in bytes."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core import lineage
from bf_core.db import dialect
from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate import memory as mem
from bf_core.geostat.estimate import preview as pv
from bf_core.geostat.estimate import store as st
from bf_core.lineage import store as ls
from tests import estapi_util as U
from tests.estapi_util import API, GS, finish, make_row, ok
from tests.test_estimate_api import run_body

reg, ea = U.reg, U.ea  # the fixtures


def graph(c: Any, node: lineage.Node) -> tuple[list[Any], set[Any]]:
    with dialect.read_tx(c.project.engine) as conn:
        return ls.inputs_of(conn, [node]).get(node, []), ls.pins_of(conn, [node])


# --- the graph forgets what was deleted -----------------------------------------------------------------------------


def test_a_deleted_row_leaves_the_graph_and_a_reused_id_inherits_nothing(ea) -> None:  # type: ignore[no-untyped-def]
    a = make_row(ea)["id"]
    node = lineage.Node(st.LINEAGE_KIND, a)
    with dialect.write_tx(ea.project.engine) as conn:
        lineage.pin(conn, node, True)
    inputs, pins = graph(ea, node)
    assert inputs and pins == {node}
    ok(ea.delete(f"{GS}/estimations/{a}"))
    assert graph(ea, node) == ([], set())
    b = make_row(ea, name="Другая строка")["id"]
    assert b == a  # SQLite gives the freed id again: the new row must not be pinned or hold the old inputs
    with dialect.read_tx(ea.project.engine) as conn:
        state = lineage.status(conn, [node])[node]
    assert state.state != lineage.PINNED
    assert graph(ea, node)[1] == set()


def test_a_deleted_field_leaves_the_graph(ea) -> None:  # type: ignore[no-untyped-def]
    a = make_row(ea)["id"]
    job = finish(ea, ok(ea.post(f"{GS}/estimate/run", json=run_body(ea, [a]))))
    assert job["status"] == "done", job
    node = lineage.Node("bm_field", ea.w.model, "au_ok_est")
    inputs, _ = graph(ea, node)
    assert inputs  # the job recorded what the field is built from
    with dialect.write_tx(ea.project.engine) as conn:
        lineage.pin(conn, node, True)
    version = ok(ea.get(f"{API}/blocks/{ea.w.model}"))["version"]
    r = ea.delete(f"{API}/blocks/{ea.w.model}/fields/AU_OK_EST", params={"expected_version": version})
    ok(r)
    assert graph(ea, node) == ([], set())


def test_sweep_runs_when_a_project_is_opened(ea) -> None:  # type: ignore[no-untyped-def]
    ghost = lineage.Node(st.LINEAGE_KIND, 987_654)  # an object that does not exist: left by a deletion without `forget`
    with dialect.write_tx(ea.project.engine) as conn:
        ls.set_pin(conn, ghost, True)
        assert ghost in ls.all_dst(conn)
    uid = ea.project.uid
    ok(ea.post(f"{API}/projects/close"))
    ok(ea.post(f"{API}/projects/{uid}/open"))
    from bf_core.projects.service import projects_of

    project = projects_of(ea.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        assert ghost not in ls.all_dst(conn)


# --- the caches -----------------------------------------------------------------------------------------------------


def test_closing_a_project_empties_the_caches_of_the_estimation(ea) -> None:  # type: ignore[no-untyped-def]
    ins._CACHE.put(("k",), object(), 100)  # noqa: SLF001
    pv._cache.put("k", object(), 100)  # noqa: SLF001
    assert len(ins._CACHE) == 1 and len(pv._cache) == 1  # noqa: SLF001
    ok(ea.post(f"{API}/projects/close"))
    assert len(ins._CACHE) == 0 and len(pv._cache) == 0  # noqa: SLF001


def test_the_caches_share_one_budget_in_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    mem.forget_all()
    monkeypatch.setattr(mem, "CACHES_BYTES", 1000)
    a, b = mem.ByteLru("t-a", 10), mem.ByteLru("t-b", 10)
    try:
        for i in range(4):
            a.put(i, f"a{i}", 300)  # 1200 > 1000: the oldest goes
        assert len(a) == 3 and a.get(0) is None and a.get(3) == "a3"
        b.put("x", "bx", 600)  # 900 + 600: the biggest cache gives up its oldest entries, never the one just written
        assert a.bytes + b.bytes <= 1000 and b.get("x") == "bx" and len(a) <= 1
        b.put("big", "bb", 5000)  # a single entry above the budget stays alone
        assert len(b) == 1 and b.get("big") == "bb"
    finally:
        for c in (a, b):
            mem._registry.remove(c)  # noqa: SLF001


def test_row_bytes_counts_arrays_and_trees_once() -> None:
    from scipy.spatial import cKDTree

    arr = np.zeros(1000)
    tree = cKDTree(np.zeros((100, 3)))
    got = mem.row_bytes({"a": arr, "again": arr, "view": arr[:10]}, tree)
    assert 8000 + 100 * 3 * 8 <= got <= 8000 + 100 * 3 * 8 + 100 * 8 + 100 * 64


# --- the freshness of the fields of a row ---------------------------------------------------------------------------


def field_state(c: Any, name: str) -> str:
    node = lineage.Node("bm_field", c.w.model, name)
    with dialect.read_tx(c.project.engine) as conn:
        return str(lineage.status(conn, [node], store=c.w.store)[node].state)


def test_a_rename_leaves_the_fields_current_and_new_parameters_make_them_stale(ea) -> None:  # type: ignore[no-untyped-def]
    row = make_row(ea)
    a = row["id"]
    job = finish(ea, ok(ea.post(f"{GS}/estimate/run", json=run_body(ea, [a]))))
    assert job["status"] == "done", job
    assert field_state(ea, "au_ok_est") == lineage.CURRENT
    renamed = ok(ea.put(f"{GS}/estimations/{a}", json={"expected_version": row["version"], "name": "Новое имя"}))
    assert renamed["version"] == row["version"] + 1  # a PUT: the version moved, the parameters did not
    assert field_state(ea, "au_ok_est") == lineage.CURRENT
    ok(ea.put(f"{GS}/estimations/{a}", json={"expected_version": renamed["version"], "disc": [3, 3, 1]}))
    assert field_state(ea, "au_ok_est") == lineage.STALE
