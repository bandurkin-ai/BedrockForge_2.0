"""The fields PIT and ORE of the block model (подшаг 21.9; ТЗ 8.11.4; ADR 0028 decision 22, № 119–120): the types and
the values (i2 / i1, a gap is never 0), the sub-blocks (PIT of a parent is the same in every row, ORE by row), the
prefix, 409 `pit_fields_taken`, the re-run of the same scenario replaces its fields in place, `source {op: "pit",
scenario_id}`, the map of the shells of the parents.

`FIELD_OPS` of `blocks/schema.py` gets "pit" from the orchestrator (ADR 0028 decision 22): until the line is there the
tests add the value themselves (the fixture `pit_op`)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.mine import fields as pf
from bf_core.mine import graph
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_report import BF_FIELDS, bf_blocks, bf_model, put

FIELDS = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}]
GRID = {"corner": [0.0, 0.0, 0.0], "counts": [6, 5, 4], "size": [10.0, 10.0, 10.0]}
ROWS = 6 * 5 * 4


@pytest.fixture(autouse=True)
def pit_op(monkeypatch: pytest.MonkeyPatch) -> None:
    if "pit" not in schema.FIELD_OPS:
        ops = (*schema.FIELD_OPS, "pit")
        monkeypatch.setattr(schema, "FIELD_OPS", ops)
        monkeypatch.setattr(bmeta, "FIELD_OPS", ops)


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Карьер"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def opened(c: Any, mid: int) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, mid)


def field_of(model: Model, name: str) -> dict[str, Any]:
    return next(f for f in model.meta["fields"] if f["name"] == name)


def write(c: Any, mid: int, shells: pf.ShellMap, node: np.ndarray, ore: np.ndarray, *, prefix: str = "",
          scenario_id: int = 7, chunk: int = 1 << 20) -> int:  # fmt: skip
    """The write of the job in the test: the stage and the commit in a write transaction → the new version."""
    model = opened(c, mid)
    staged = pf.stage_fields(c.store, model, model.version, prefix, scenario_id, shells, node, ore, chunk=chunk)
    try:
        with dialect.write_tx(c.project.engine) as conn:
            return pf.commit_scenario_fields(conn, c.store, staged, scenario_id=scenario_id, params={"a": 1})
    finally:
        staged.discard()


def regular(c: Any) -> tuple[int, np.ndarray]:
    rng = np.random.default_rng(5)
    au = rng.lognormal(0.0, 0.9, ROWS)
    return put(c, GRID, FIELDS, np.arange(ROWS, dtype=np.uint64), {"AU": au}), au


def shells_of(p: np.ndarray) -> np.ndarray:
    """The shell of a parent: 1…4 from P, 0 (none) for every fifth parent."""
    s = (p % 4 + 1).astype(np.int64)
    s[p % 5 == 0] = 0
    return s


# --- the values -----------------------------------------------------------------------------------------------------


def test_types_values_and_source(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = regular(rc)
    p = np.arange(ROWS, dtype=np.uint64)
    smap = pf.ShellMap.from_parents(p, shells_of(p))
    node = np.arange(ROWS, dtype=np.int32)
    node[p % 7 == 0] = -1  # «воздух» / no tonnage: not a node
    ore = (p % 3 == 0).astype(np.uint8)
    ore[p % 7 == 0] = 1  # a row outside the graph keeps no ore even if the table says so
    v0 = opened(rc, mid).version
    assert write(rc, mid, smap, node, ore, chunk=17) == v0 + 1  # portions of 17 rows: the same as one portion
    model = opened(rc, mid)
    got = model.columns(["PIT", "ORE"])
    assert np.array_equal(got["PIT"], shells_of(p).astype(np.float64))  # 0 — outside, never empty
    want = (p % 3 == 0).astype(np.float64)
    want[p % 7 == 0] = np.nan  # empty, not 0
    assert np.array_equal(got["ORE"], want, equal_nan=True)
    pit, ore_f = field_of(model, "PIT"), field_of(model, "ORE")
    assert (pit["dtype"], ore_f["dtype"]) == ("i2", "i1")
    assert pit["stats"]["missing"] == 0 and ore_f["stats"]["missing"] == int((p % 7 == 0).sum())
    for f in (pit, ore_f):
        assert f["source"]["op"] == "pit" and f["source"]["scenario_id"] == 7 and f["source"]["algo"] == "pit-1"
        assert f["source"]["params"] == {"a": 1} and f["description"]
    assert [f["name"] for f in model.meta["fields"]][:1] == ["AU"]  # the old field is not touched


def test_prefix_names(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = regular(rc)
    p = np.arange(ROWS, dtype=np.uint64)
    smap = pf.ShellMap.from_parents(p, shells_of(p))
    node, ore = np.arange(ROWS, dtype=np.int32), np.ones(ROWS, dtype=np.uint8)
    write(rc, mid, smap, node, ore)
    write(rc, mid, smap, node, ore, prefix="A_", scenario_id=8)  # another scenario: its own names
    names = [f["name"] for f in opened(rc, mid).meta["fields"]]
    assert names == ["AU", "PIT", "ORE", "A_PIT", "A_ORE"]
    assert pf.field_names("B_") == ("B_PIT", "B_ORE") and pf.field_names() == ("PIT", "ORE")


# --- 409 pit_fields_taken ----------------------------------------------------------------------------------


def test_pit_fields_taken_by_a_foreign_field(rc) -> None:  # type: ignore[no-untyped-def]
    p = np.arange(ROWS, dtype=np.uint64)
    mid = put(rc, GRID, [*FIELDS, {"name": "pit", "dtype": "f8"}], p, {"AU": np.ones(ROWS), "pit": np.ones(ROWS)})
    smap = pf.ShellMap.from_parents(p, shells_of(p))
    node, ore = np.arange(ROWS, dtype=np.int32), np.ones(ROWS, dtype=np.uint8)
    version = opened(rc, mid).version
    with pytest.raises(ApiError) as e:  # a field of an import (any case of the name)
        write(rc, mid, smap, node, ore)
    err = e.value
    assert (err.status, err.code, err.extra) == (409, "pit_fields_taken", {"field": "pit", "param": "prefix"})
    assert "префикс" in err.hint and "pit" in err.message
    assert opened(rc, mid).version == version  # nothing written
    with pytest.raises(ApiError) as e2:
        pf.check_names(opened(rc, mid).meta, "", 7)
    assert e2.value.code == "pit_fields_taken"
    write(rc, mid, smap, node, ore, prefix="A_")  # with a prefix it is fine
    assert [f["name"] for f in opened(rc, mid).meta["fields"]] == ["AU", "pit", "A_PIT", "A_ORE"]


def test_rerun_replaces_own_fields_other_scenario_is_refused(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = regular(rc)
    p = np.arange(ROWS, dtype=np.uint64)
    node, ore = np.arange(ROWS, dtype=np.int32), np.ones(ROWS, dtype=np.uint8)
    write(rc, mid, pf.ShellMap.from_parents(p, shells_of(p)), node, ore)
    other = np.maximum(shells_of(p) - 1, 0)
    write(rc, mid, pf.ShellMap.from_parents(p, other), node, ore)  # the same scenario: replaced in place
    model = opened(rc, mid)
    assert [f["name"] for f in model.meta["fields"]] == ["AU", "PIT", "ORE"]
    assert np.array_equal(model.columns(["PIT"])["PIT"], other.astype(np.float64))
    with pytest.raises(ApiError) as e:
        write(rc, mid, pf.ShellMap.from_parents(p, shells_of(p)), node, ore, scenario_id=9)
    assert e.value.code == "pit_fields_taken"
    assert np.array_equal(opened(rc, mid).columns(["PIT"])["PIT"], other.astype(np.float64))  # still the old


# --- sub-blocks (№ 119) ------------------------------------------------------------------------------------


def test_sub_blocks_pit_by_parent_ore_by_row(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    n = m["n"]
    assert n > len(np.unique(m["p"])) and BF_FIELDS  # parents with several rows
    parent_shell = {int(q): 1 + int(q) % 3 for q in np.unique(m["p"]) if q % 4}
    smap = pf.ShellMap.from_parents(np.array(list(parent_shell)), np.array(list(parent_shell.values())))
    node = np.arange(n, dtype=np.int32)
    node[np.arange(n) % 5 == 0] = -1  # a sub-block without tonnage
    ore = (np.arange(n) % 2).astype(np.uint8)
    write(rc, mid, smap, node, ore)
    got = opened(rc, mid).columns(["PIT", "ORE"])
    pit = got["PIT"]
    for q in np.unique(m["p"]):
        rows = pit[m["p"] == q]
        assert (rows == parent_shell.get(int(q), 0)).all(), q  # the same in every row of a parent
    by_parent = [set(got["ORE"][m["p"] == q][~np.isnan(got["ORE"][m["p"] == q])]) for q in np.unique(m["p"])]
    assert any(len(v) > 1 for v in by_parent)  # ORE differs by row inside a parent
    want = (np.arange(n) % 2).astype(np.float64)
    want[np.arange(n) % 5 == 0] = np.nan
    assert np.array_equal(got["ORE"], want, equal_nan=True)
    # a row outside the graph still has its parent's PIT: «воздух» of a mined parent is in the pit, ORE is empty
    mined_rows = np.isin(m["p"], list(parent_shell))
    assert (pit[mined_rows] > 0).all() and np.isnan(got["ORE"][mined_rows & (np.arange(n) % 5 == 0)]).all()


# --- the map of the shells ---------------------------------------------------------------------------------


def test_shell_map_lookup_and_folding() -> None:
    sm = pf.ShellMap.from_parents(np.array([9, 3, 3, 5, 7], dtype=np.uint64), np.array([2, 4, 1, 0, 3]))
    assert sm.cells.tolist() == [3, 7, 9] and sm.shell.tolist() == [1, 3, 2]  # a repeat keeps the smaller; 0 is none
    assert sm.count == 3
    assert sm.lookup(np.array([0, 3, 5, 7, 9, 10], dtype=np.uint64)).tolist() == [0, 1, 0, 3, 2, 0]
    empty = pf.ShellMap.from_parents(np.zeros(0, dtype=np.uint64), np.zeros(0, dtype=np.int64))
    assert empty.lookup(np.array([1, 2], dtype=np.uint64)).tolist() == [0, 0] and empty.count == 0
    with pytest.raises(ValueError):
        pf.ShellMap.from_parents(np.array([1], dtype=np.uint64), np.array([40_000]))


def test_shell_map_from_graph() -> None:
    dims = (3, 2, 2)
    total = 12
    air = np.zeros(total, dtype=bool)
    air[[0, 7]] = True  # P 0 and 7 are «воздух»
    g = graph.build_graph(dims, np.ones(total, dtype=np.int64), air, None, [np.zeros((0, 3), dtype=np.int32)])
    shell = np.arange(1, g.n + 1) % 3  # 1, 2, 0, 1, 2, 0 …: the smallest shell of every node
    sm = pf.ShellMap.from_graph(g, shell)
    nodes_p = np.flatnonzero(~air)
    assert sm.cells.tolist() == nodes_p[shell > 0].tolist()
    assert sm.lookup(np.array([0, 7], dtype=np.uint64)).tolist() == [0, 0]  # «воздух» is in no shell
    with pytest.raises(ValueError):
        pf.ShellMap.from_graph(g, shell[:-1])


def test_ore_of_gap_is_not_zero() -> None:
    got = pf.ore_of(np.array([0, -1, 3, -1], dtype=np.int32), np.array([1, 1, 0, 0], dtype=np.uint8))
    assert got[0] == 1.0 and got[2] == 0.0 and np.isnan(got[[1, 3]]).all()
