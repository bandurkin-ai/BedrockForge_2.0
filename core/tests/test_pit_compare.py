"""Two pit scenarios against each other (подшаг 21.15; ADR 0028 решения 27, 30; docs/progress/e21-api.md §11):
A = A gives 0 in every difference; an RF only one scenario has is a gap (`only_a` / `only_b`, `diff` null), never 0;
different `cells_sha` — «сетки различаются», no blocks; the parameters that differ as a list by the path of the keys;
the blocks of the final pit only in A / only in B by the field PIT of each scenario (a field of another scenario —
a note); NaN never becomes 0; the same bytes every time; 404 / 409."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.mine import compare as cmp
from bf_core.mine import store as st
from bf_core.mine.spec_parts import Calendar
from tests.estutil import W, world
from tests.pitutil import GRID, built, new, rd, refused, shells_of
from tests.test_blocks_fields_store import add_fields, make_model

CAL = {"q_ore": 1.0e6, "q_mass": None, "rate": 0.1}
ROWS = 350  # the rows of the model of the world: 62,5 m³ each (5 · 5 · 2,5), the density 2,7


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def scen(w: W, name: str, shells: list[dict[str, Any]] | None = None, model: int | None = None, **over: Any) -> int:
    """A scenario with a result made of the tables of `pitutil` (`shells`), the calendar CAL unless it is given;
    `model` — another model than the one of the world."""
    over.setdefault("calendar", CAL)
    if model is not None:
        over["model_id"] = model
    ident = new(w, name, **over)
    b = built(w, shells=shells, **over)
    if model is not None:
        b = dataclasses.replace(b, model={"id": model, "name": "Другая", "grid": GRID})
    with dialect.write_tx(w.project.engine) as conn:
        st.write_result(conn, w.store, ident, b)
    return ident


def put_pit(w: W, ident: int, rows: range | list[int], prefix: str = "", owner: int | None = None) -> None:
    """The field `<prefix>PIT` as the run writes it: 1 in `rows`, 0 elsewhere; `owner` — the scenario in `source`."""
    name = f"{prefix}PIT"
    vals = np.zeros(ROWS, dtype=np.int16)
    vals[list(rows)] = 1
    src = {"op": "pit", "params": {}, "inputs": [], "algo": "pit-1", "scenario_id": ident if owner is None else owner}
    add_fields(w.project, w.store, w.model, [{"name": name, "dtype": "i2"}], {name: vals}, source=src)


def run(w: W, a: int, b: int) -> dict[str, Any]:
    return rd(w, cmp.compare_scenarios, w.store, a, b)  # type: ignore[no-any-return]


def loaded(w: W, ident: int) -> cmp.Scenario:
    return rd(w, cmp.load, w.store, ident)  # type: ignore[no-any-return]


def blocks_of(w: W, a: cmp.Scenario, b: cmp.Scenario) -> tuple[cmp.BlockDiff | None, list[dict[str, Any]]]:
    return rd(w, cmp.read_blocks, w.store, a, b)  # type: ignore[no-any-return]


def codes(out: dict[str, Any]) -> list[str]:
    return [x["code"] for x in out["warnings"]]


# --- A = A ------------------------------------------------------------------------------------------------------------


def test_a_against_itself_is_zero_everywhere(w: W) -> None:
    a = scen(w, "A")
    out = run(w, a, a)
    assert out["same_grid"] is True and out["params_diff"] == [] and out["a"] == out["b"]
    assert [s["status"] for s in out["shells"]] == ["both"] * 3
    for s in out["shells"]:
        assert s["a"] == s["b"] and s["k_a"] == s["k_b"]
        assert all(v == 0.0 for v in s["diff"].values()), s["diff"]  # blocks, ore_t, waste_t, cf and both NPV
    diff = out["totals"]["diff"]
    assert all(v == 0.0 for k, v in diff.items() if k != "elements" and v is not None)
    assert diff["npv_best"] == 0.0 and diff["npv_worst"] == 0.0 and diff["cf"] == 0.0
    assert all(v == 0.0 for e in diff["elements"].values() for v in e.values())
    json.dumps(out, allow_nan=False)  # no NaN anywhere


def test_two_equal_scenarios_are_zero_too(w: W) -> None:
    a, b = scen(w, "A"), scen(w, "B")
    out = run(w, a, b)
    assert out["a"]["id"] != out["b"]["id"] and out["params_diff"] == []
    assert all(v == 0.0 for s in out["shells"] for v in s["diff"].values())


def test_the_head_of_each_side(w: W) -> None:
    a = scen(w, "A")
    out = run(w, a, a)
    sc = loaded(w, a)
    assert out["a"] == {"id": a, "name": "A", "version": sc.version, "solver": "hpf", "params_hash": sc.params_hash,
                        "model": {"id": w.model, "name": "Модель", "cells_sha": sc.model["cells_sha"]}}  # fmt: skip
    assert out["a"]["model"]["cells_sha"] and out["a"]["params_hash"]


def test_the_cumulative_numbers_of_the_shells(w: W) -> None:
    a = scen(w, "A")
    out = run(w, a, a)
    for s, r in zip(out["shells"], shells_of(), strict=True):
        assert s["rf"] == r["rf"] and s["k_a"] == r["k"]
        assert s["a"]["blocks"] == r["cum"]["blocks"] and s["a"]["ore_t"] == r["cum"]["ore_t"]
        assert s["a"]["waste_t"] == r["cum"]["waste_t"] and s["a"]["cf"] == pytest.approx(r["cum"]["cf"])
    assert all(s["a"]["npv_best"] is not None for s in out["shells"])  # the NPV of both cases is shown
    assert out["totals"]["a"]["npv_best"] == out["shells"][-1]["a"]["npv_best"]
    assert out["totals"]["a"]["calendar"] == CAL and out["totals"]["a"]["blocks"] == 6000


# --- the RF that only one scenario has --------------------------------------------------------------------------------


def test_an_rf_only_one_has_is_a_gap_not_zero(w: W) -> None:
    a = scen(w, "A", shells_of(3), rf=[0.8, 1.0, 1.2])
    b = scen(w, "B", shells_of(2, scale=1.1), rf=[0.8, 1.0])
    out = run(w, a, b)
    got = {s["rf"]: s for s in out["shells"]}
    assert list(got) == [0.8, 1.0, 1.2] and [s["status"] for s in out["shells"]] == ["both", "both", "only_a"]
    lone = got[1.2]
    assert lone["b"] is None and lone["diff"] is None and lone["k_b"] is None and lone["a"]["ore_t"] is not None
    both = got[1.0]
    assert both["diff"]["ore_t"] == pytest.approx(both["a"]["ore_t"] - both["b"]["ore_t"])
    assert both["diff"]["ore_t"] != 0
    swapped = run(w, b, a)
    assert [s["status"] for s in swapped["shells"]] == ["both", "both", "only_b"]
    last = swapped["shells"][2]
    assert last["a"] is None and last["diff"] is None and last["k_a"] is None and last["b"]["blocks"] == 6000
    assert "rf.2" in [d["param"] for d in out["params_diff"]]


def test_the_rf_are_matched_by_value_not_by_number(w: W) -> None:
    a = scen(w, "A", shells_of(3), rf=[0.8, 1.0, 1.2])
    rows = shells_of(3)[1:]  # B has the shells 1.0 and 1.2 only: k = 1, 2 there
    for k, r in enumerate(rows, 1):
        r["k"] = k
    b = scen(w, "B", rows, rf=[1.0, 1.2])
    out = run(w, a, b)
    assert [(s["rf"], s["status"], s["k_a"], s["k_b"]) for s in out["shells"]] == [
        (0.8, "only_a", 1, None), (1.0, "both", 2, 1), (1.2, "both", 3, 2)]  # fmt: skip


# --- the parameters ---------------------------------------------------------------------------------------------------


def test_the_parameters_that_differ_as_a_list(w: W) -> None:
    el = [{"field": "AU", "unit": "g/t", "price": 2200.0, "sales": 5.0, "royalty": 0.03}]
    a = scen(w, "A")
    b = scen(w, "B", elements=el, mining={"c_m0": 3.0}, calendar={"q_ore": 2.0e6, "q_mass": None, "rate": 0.05})
    out = run(w, a, b)
    got = {d["param"]: (d["a"], d["b"]) for d in out["params_diff"]}
    assert got == {"elements.0.price": (2000.0, 2200.0), "mining.c_m0": (2.5, 3.0)}  # the calendar is not a parameter
    assert [d["param"] for d in out["params_diff"]] == sorted(got)
    assert "calendar_differs" in codes(out) and "calendar_differs" not in codes(run(w, a, a))


def test_params_diff_by_the_path_of_the_keys() -> None:
    pa = {"rf": [0.8, 1.0, 1.2], "mining": {"c_m0": 2.5, "z_ref": None}, "solver": "hpf", "calendar": {"rate": 0.1},
          "rocks": {"types": [{"name": "A", "rec": {"AU": 0.9}}]}, "k": None}  # fmt: skip
    pb = {"rf": [0.8, 1.0], "mining": {"c_m0": 2.5, "z_ref": 100.0, "extra": 1}, "solver": "ortools",
          "calendar": {"rate": 0.2}, "rocks": {"types": [{"name": "A", "rec": {"AU": 0.8}}, {"name": "B"}]},
          "k": None}  # fmt: skip
    assert cmp.params_diff(pa, pa) == []
    got = {d["param"]: (d["a"], d["b"]) for d in cmp.params_diff(pa, pb)}
    assert got == {"rf.2": (1.2, None), "mining.z_ref": (None, 100.0), "mining.extra": (None, 1),
                   "rocks.types.0.rec.AU": (0.9, 0.8), "rocks.types.1": (None, {"name": "B"})}  # fmt: skip
    assert cmp.params_diff({"x": 1}, {"x": 1.0}) == []  # 1 = 1.0


# --- the grids --------------------------------------------------------------------------------------------------------


def test_different_cells_sha_gives_no_blocks_and_the_flag(w: W) -> None:
    a, b = scen(w, "A", prefix="A_"), scen(w, "B", prefix="B_")
    put_pit(w, a, range(0, 200), "A_")
    put_pit(w, b, range(100, 250), "B_")
    sa_, sb_ = loaded(w, a), loaded(w, b)
    same = cmp.compare(sa_, sb_, *blocks_of(w, sa_, sb_))
    assert same["same_grid"] is True and same["blocks"] is not None and "grids_differ" not in codes(same)
    other = dataclasses.replace(sb_, model={**sb_.model, "cells_sha": "f" * 64})
    out = cmp.compare(sa_, other, blocks_of(w, sa_, sb_)[0])  # even given blocks, a different grid shows none
    assert out["same_grid"] is False and out["blocks"] is None and "grids_differ" in codes(out)
    assert [s["status"] for s in out["shells"]] == ["both"] * 3  # the shells are compared all the same
    assert out["a"]["model"]["cells_sha"] != out["b"]["model"]["cells_sha"]


def test_another_model_through_the_wrapper(w: W) -> None:
    other = make_model(w.project, w.store, n=300, name="Другая")  # other rows → another cells_sha
    a, c = scen(w, "A"), scen(w, "C", model=other)
    out = run(w, a, c)
    assert out["same_grid"] is False and out["blocks"] is None and "grids_differ" in codes(out)
    assert out["a"]["model"]["id"] == w.model and out["b"]["model"]["id"] == other
    assert out["a"]["model"]["cells_sha"] != out["b"]["model"]["cells_sha"]
    assert [s["status"] for s in out["shells"]] == ["both"] * 3 and out["params_diff"][0]["param"] == "model_id"


# --- the blocks of the final pit --------------------------------------------------------------------------------------


def test_the_blocks_only_in_a_and_only_in_b(w: W) -> None:
    a = scen(w, "A", prefix="A_")
    b = scen(w, "B", prefix="B_", density="3,0")
    put_pit(w, a, range(0, 200), "A_")
    put_pit(w, b, range(100, 250), "B_")
    out = run(w, a, b)
    assert out["same_grid"] is True and "grids_differ" not in codes(out)
    assert out["blocks"] == {"only_a": {"blocks": 100, "tonnes": 100 * 62.5 * 2.7, "no_tonnes": 0},
                             "only_b": {"blocks": 50, "tonnes": 50 * 62.5 * 3.0, "no_tonnes": 0}}  # fmt: skip
    again = run(w, b, a)  # the sides swap
    assert again["blocks"]["only_a"]["blocks"] == 50 and again["blocks"]["only_b"]["blocks"] == 100
    same = run(w, a, a)
    assert same["blocks"] == {"only_a": {"blocks": 0, "tonnes": 0.0, "no_tonnes": 0},
                              "only_b": {"blocks": 0, "tonnes": 0.0, "no_tonnes": 0}}  # fmt: skip


def test_a_field_of_another_scenario_is_a_note_not_blocks(w: W) -> None:
    a, b = scen(w, "A"), scen(w, "B")  # both write `PIT` (no prefix): the second run overwrote the field of the first
    put_pit(w, b, range(0, 200), "", owner=b)
    out = run(w, a, b)
    assert out["blocks"] is None
    foreign = [x for x in out["warnings"] if x["code"] == "pit_field_foreign"]
    assert [x["side"] for x in foreign] == ["a"] and "принадлежит другому сценарию" in foreign[0]["message"]
    assert foreign[0]["hint"]
    assert [s["status"] for s in out["shells"]] == ["both"] * 3  # the rest of the comparison stays


def test_no_field_is_a_note(w: W) -> None:
    a, b = scen(w, "A", prefix="A_"), scen(w, "B", prefix="B_")
    put_pit(w, a, range(0, 10), "A_")
    out = run(w, a, b)
    missing = [x for x in out["warnings"] if x["code"] == "pit_field_missing"]
    assert out["blocks"] is None and [x["side"] for x in missing] == ["b"] and missing[0]["hint"]


def test_a_gone_model_is_a_note(w: W) -> None:
    a = scen(w, "A")
    sc = loaded(w, a)
    ghost = dataclasses.replace(sc, model={**sc.model, "id": 9999})
    diff, notes = blocks_of(w, ghost, ghost)
    assert diff is None and [x["code"] for x in notes] == ["blocks_unavailable"] and notes[0]["hint"]


# --- gaps are not zeros -----------------------------------------------------------------------------------------------


def test_nan_is_not_turned_into_zero(w: W) -> None:
    rows = shells_of(3)
    rows[1]["cf"] = None  # a shell whose CF is not known (a gap in the file) …
    for r in rows[1:]:
        r["cum"]["cf"] = None  # … and so is every sum over it
    a = scen(w, "A", rows)
    b = scen(w, "B")
    out = run(w, a, b)
    first, second = out["shells"][0], out["shells"][1]
    assert first["diff"]["cf"] == pytest.approx(0.0)
    assert second["a"]["cf"] is None and second["b"]["cf"] is not None and second["diff"]["cf"] is None
    assert second["a"]["npv_best"] is None and second["diff"]["npv_best"] is None  # NaN CF — no NPV, not 0
    assert second["a"]["ore_t"] == second["b"]["ore_t"] and second["diff"]["ore_t"] == 0.0
    assert out["totals"]["a"]["cf"] is None and out["totals"]["diff"]["cf"] is None
    json.dumps(out, allow_nan=False)


def test_a_shell_without_ore_has_no_strip_ratio(w: W) -> None:
    a = scen(w, "A")  # the first shell has no ore: W/O is a gap (NaN), the totals have a number
    sc = loaded(w, a)
    out = run(w, a, a)
    assert np.isnan(sc.report.strip()[0]) and out["shells"][0]["a"]["ore_t"] == 0.0
    assert out["shells"][0]["diff"]["ore_t"] == 0.0 and out["totals"]["a"]["strip"] is not None


def test_a_calendar_without_q_ore_has_no_npv(w: W) -> None:
    a = scen(w, "A")
    sc = loaded(w, a)
    bare = dataclasses.replace(sc, calendar=Calendar(None, None, 0.08))
    out = cmp.compare(sc, bare)
    assert "no_calendar" in codes(out) and "calendar_differs" in codes(out)
    warn = next(x for x in out["warnings"] if x["code"] == "no_calendar")
    assert warn["side"] == "b" and warn["hint"] and "«Сценарий»" not in warn["message"]
    for s in out["shells"]:
        assert s["a"]["npv_best"] is not None and s["b"]["npv_best"] is None and s["diff"]["npv_best"] is None
        assert s["diff"]["ore_t"] == 0.0  # the rest is compared
    assert out["totals"]["b"]["npv_worst"] is None and out["totals"]["diff"]["npv_worst"] is None


def test_a_stale_scenario_is_a_warning(w: W) -> None:
    sc = loaded(w, scen(w, "A"))
    out = cmp.compare(sc, dataclasses.replace(sc, stale=True))
    assert [(x["code"], x["side"]) for x in out["warnings"] if x["code"] == "stale"] == [("stale", "b")]


# --- the same bytes ---------------------------------------------------------------------------------------------------


def test_the_same_bytes_every_time(w: W) -> None:
    a = scen(w, "A", shells_of(3), prefix="A_", rf=[0.8, 1.0, 1.2])
    b = scen(w, "B", shells_of(2, scale=1.2), prefix="B_", rf=[0.8, 1.0], density="2,9")
    put_pit(w, a, range(0, 200), "A_")
    put_pit(w, b, range(100, 250), "B_")
    first = json.dumps(run(w, a, b), allow_nan=False, sort_keys=False)
    assert json.dumps(run(w, a, b), allow_nan=False) == first
    swapped = run(w, b, a)  # A − B = −(B − A)
    out = json.loads(first)
    for s, t in zip(out["shells"][:2], swapped["shells"][:2], strict=True):
        assert all(s["diff"][k] == -t["diff"][k] for k in s["diff"])
    assert out["blocks"]["only_a"]["blocks"] == swapped["blocks"]["only_b"]["blocks"] == 100


# --- the errors -------------------------------------------------------------------------------------------------------


def test_the_errors(w: W) -> None:
    a = scen(w, "A")
    empty = new(w, "Без результата")
    err = refused(lambda: run(w, a, 9999), 404, "pit_scenario_not_found")
    assert "сценари" in err.message.lower()
    refused(lambda: run(w, 9999, a), 404, "pit_scenario_not_found")
    refused(lambda: run(w, a, empty), 409, "pit_no_result")
    refused(lambda: run(w, empty, empty), 409, "pit_no_result")
