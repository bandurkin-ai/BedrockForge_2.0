"""«Сверка данных и модели» (подшаг 19.19; ТЗ 7.35 Д-33; ADR 0026 решение 28; e19-api.md §8.3): the intervals of a
lithology table against the bodies of an implicit model — the code of the model of an interval is `mode_by_length`
over the stretches of the bodies (priority — the order of the groups, «Прочее» last, a gap — «Вне модели»), the matrix
«код данных × код модели» by length and by count, the list of the mismatches.

**ТД33-1** exact horizontal contacts: ≥ 99 % of the length agrees, the mismatches lie within 0.5·h of the contacts;
**ТД33-2** an interval of 10 m recoded on purpose is the first of the list and +10 m off the diagonal of the matrix
(1e−9). Also: the sum of the matrix is the length of the intervals; the permutation of the holes and of the rows does
not change the answer; a missing code is «Нет кода», not 0 and not a mismatch; the diagonal is what agrees; a brute
force of 7 random holes; the refusals {code, message, hint}; the route; the imports."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import numpy as np
import pytest

from bf_core.drillholes import store as dh
from bf_core.model import compare_holes as ch
from tests.estutil import W, world
from tests.meshutil import cube
from tests.test_drillholes_from_model import (
    layers,
    model_with,
    new_set,
    refused_with,
    vertical,
)
from tests.test_model_store import _roles, make  # noqa: F401 - the autouse fixture: the roles of the files

TOL = 1e-9
Rows = list[tuple[str, float, float, str | None]]
G1, G2 = "Группа 1", "Группа 2"  # the codes of the groups: «окисл» and «перв» (`model_with`)


@pytest.fixture()
def w(tmp_path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def lith(w: W, set_id: int, rows: Rows, name: str = "Литология") -> int:
    """An interval table with a text field LITH: `rows` — (hole, from, to, code); None — an empty code."""
    return dh.write_table(w.project.engine, set_id, dh.TableData(
        name=name, kind="interval", holes=[r[0] for r in rows], from_m=[r[1] for r in rows],
        to_m=[r[2] for r in rows], columns=[dh.ColumnSpec("LITH", "text")], values=[[r[3] for r in rows]]),
        job_id=None, tick=lambda _p, _m: None)["table_id"]  # fmt: skip


def compare(w: W, model_id: int, set_id: int, table_id: int, **kw: Any) -> dict[str, Any]:
    body = {"set_id": set_id, "table": table_id, "field": "LITH", **kw}
    return ch.run(w.project, model_id, ch.parse(body))


def cell(out: dict[str, Any], key: str, data: str, model: str) -> float:
    return float(out[key][out["data_codes"].index(data)][out["model_codes"].index(model)])


def two_layers(w: W) -> int:
    upper, lower = layers()
    return model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {})])


def sliced(out: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(r["hole"], r["from"], r["to"], r["data_code"], r["model_code"]) for r in out["mismatches"]["rows"]]


# --- ТД33-1, ТД33-2 ----------------------------------------------------------------------------------------------


def test_td33_1_exact_contacts_agree_by_99_percent_and_the_rest_lies_at_the_contacts(w: W) -> None:
    ident = two_layers(w)  # the contacts of the model: depths 10, 60, 110 m (the collars are at z = 10)
    spots = [(25.0, 25.0), (10.0, 10.0), (40.0, 40.0), (25.0, 10.0), (10.0, 40.0)]
    holes = [vertical(f"H-{i}", x, y) for i, (x, y) in enumerate(spots)]
    set_id = new_set(w, holes)
    h = 0.5  # the step of the data
    rows: Rows = []
    for i, hole in enumerate(holes):
        contact = 60.0 if i < 3 else 60.0 + h  # two holes: the data put the contact one step below the model's
        for a in np.arange(10.0, 110.0, h):
            rows.append((hole.name, float(a), float(a + h), "окисл" if a + h <= contact else "перв"))
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    assert out["total_length"] == pytest.approx(500.0, abs=TOL) and out["intervals"] == 1000
    assert out["agree_share"] >= 0.99 and out["agree_share"] == pytest.approx(1.0 - 1.0 / 500.0, abs=TOL)
    assert out["mismatches"]["total"] == 2
    for r in out["mismatches"]["rows"]:  # only the step that touches the contact of the model (within 0.5·h of it)
        assert (r["data_code"], r["model_code"]) == ("окисл", G2) and r["length"] == pytest.approx(h, abs=TOL)
        assert abs(r["from"] - 60.0) <= 0.5 * h + TOL and r["hole"] in ("H-3", "H-4")
        assert r["depth"] == pytest.approx(60.0 + 0.5 * h, abs=TOL) and r["hole_id"] > 0
    assert out["model_codes"] == [G1, G2, ch.OTHER, ch.OUTSIDE] and out["data_codes"] == ["окисл", "перв"]
    assert cell(out, "length", "окисл", G1) == pytest.approx(250.0, abs=TOL)
    assert cell(out, "length", "окисл", G2) == pytest.approx(1.0, abs=TOL)  # +1 m off the diagonal: two steps of 0.5 m
    assert cell(out, "length", "перв", G2) == pytest.approx(249.0, abs=TOL) and cell(out, "length", "перв", G1) == 0.0


def test_td33_2_a_recoded_interval_of_10_m_is_the_first_of_the_list_and_off_the_diagonal(w: W) -> None:
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0), vertical("V-2", 20.0, 20.0)])
    rows: Rows = []
    for hole in ("V-1", "V-2"):
        rows += [(hole, 10.0 + 10 * k, 20.0 + 10 * k, "окисл" if k < 5 else "перв") for k in range(10)]
    rows[2] = ("V-1", 30.0, 40.0, "перв")  # recoded on purpose: 10 m of «перв» inside the first layer
    rows[13] = ("V-2", 40.0, 50.0, "кора")  # a code no group has: 10 m too — a tie of the length: by (hole, FROM)
    rows[16] = ("V-2", 70.0, 80.0, "окисл")  # …and one more of 10 m inside the second layer
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    got = sliced(out)
    assert got == [("V-1", 30.0, 40.0, "перв", G1), ("V-2", 40.0, 50.0, "кора", G1), ("V-2", 70.0, 80.0, "окисл", G2)]
    assert out["mismatches"]["rows"][0]["length"] == pytest.approx(10.0, abs=TOL)
    assert cell(out, "length", "перв", G1) == pytest.approx(10.0, abs=TOL)  # +10 m off the diagonal
    assert cell(out, "count", "перв", G1) == 1 and cell(out, "length", "кора", G1) == pytest.approx(10.0, abs=TOL)
    assert cell(out, "length", "перв", G2) == pytest.approx(90.0, abs=TOL)  # 100 m of «перв» minus the one recoded
    assert out["agree_length"] == pytest.approx(200.0 - 30.0, abs=TOL)


def test_every_body_without_a_group_is_the_other_of_the_priority_not_only_the_first(w: W) -> None:
    """A model with a fault has a «Прочее» a wing: an interval inside the second one is not «Вне модели» (Д-33)."""
    upper, lower = layers()
    far_a = cube(2, 50.0, (200.0, 0.0, -50.0), seed=None)  # two bodies with no group, apart from the layers
    far_b = cube(2, 50.0, (400.0, 0.0, -50.0), seed=None)
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {}),
                           ("Прочее, крыло 0", None, far_a, {}), ("Прочее, крыло 1", None, far_b, {})])  # fmt: skip
    set_id = new_set(w, [vertical("V-A", 225.0, 25.0), vertical("V-B", 425.0, 25.0), vertical("V-X", 800.0, 25.0)])
    rows: Rows = [("V-A", 10.0, 20.0, "окисл"), ("V-B", 10.0, 20.0, "окисл"), ("V-X", 10.0, 20.0, "окисл")]
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    assert cell(out, "length", "окисл", ch.OTHER) == pytest.approx(20.0, abs=TOL)  # both wings: 2 × 10 m
    assert cell(out, "length", "окисл", ch.OUTSIDE) == pytest.approx(10.0, abs=TOL)  # only the hole beside them


# --- the matrix ---------------------------------------------------------------------------------------------------


def brute(segments: dict[str, list[tuple[float, float, str]]], rows: list[tuple[str, float, float, str | None]],
          ) -> dict[tuple[str | None, str], tuple[float, int]]:  # fmt: skip
    """The matrix by hand: the model code of an interval is the segment with the longest overlap (a tie — the first
    down the hole); nothing covered — «Вне модели»."""
    out: dict[tuple[str | None, str], tuple[float, int]] = {}
    for hole, a, b, code in rows:
        if hole not in segments:
            continue
        best, best_len = ch.OUTSIDE, 0.0
        for lo, hi, name in segments[hole]:
            over = min(b, hi) - max(a, lo)
            if over > best_len + 1e-12:
                best, best_len = name, over
        key = (code, best)
        s, n = out.get(key, (0.0, 0))
        out[key] = (s + (b - a), n + 1)
    return out


def random_world(w: W, seed: int) -> tuple[int, int, Rows, dict[str, Any], list[dh.CollarRow]]:
    rng = np.random.default_rng(seed)
    upper, lower = layers()
    ident = model_with(w, [("Верх", 1, upper, {}), ("Низ", 2, lower, {}),
                           ("Прочее", None, cube(2, 150.0, (0.0, 0.0, -150.0), seed=None), {})])  # fmt: skip
    names = [f"R-{i}" for i in range(7)]
    holes = [vertical(names[i], float(rng.uniform(5, 45)), float(rng.uniform(5, 45)), td=130.0) for i in range(5)]
    holes += [vertical("R-5", 500.0, 500.0, td=130.0), dh.CollarRow("R-6")]  # outside the model; no collar
    set_id = new_set(w, holes, f"Случайные {seed}")
    rows: Rows = []
    for name in names:
        cuts = np.sort(rng.uniform(0.0, 140.0, size=int(rng.integers(6, 14))))
        for a, b in zip(cuts[:-1], cuts[1:], strict=True):
            rows.append((name, float(a), float(b), str(rng.choice(["окисл", "перв", "кора", "нет"]))))
        rows.append((name, float(cuts[-1]), float(cuts[-1] + 3.0), None))
    # the path reaches the deepest interval of the table (7.11), the body «Прочее» goes on to the end of it
    end = {n: max(130.0, *(r[2] for r in rows if r[0] == n)) for n in names}
    inside = {n: [(0.0, 10.0, ch.OUTSIDE), (10.0, 60.0, G1), (60.0, 110.0, G2), (110.0, end[n], ch.OTHER)]
              for n in names[:5]}  # fmt: skip
    inside["R-5"] = [(0.0, end["R-5"], ch.OUTSIDE)]  # a hole beside the model: no body at all
    return ident, set_id, rows, inside, holes


def test_the_matrix_is_the_brute_force_one_the_sums_are_the_length_and_the_diagonal_is_what_agrees(w: W) -> None:
    ident, set_id, rows, inside, _holes = random_world(w, 5)
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    mine = [r for r in rows if r[0] != "R-6"]
    want = brute(inside, mine)
    got = {(None if i == out["no_code_row"] else d, m): (out["length"][i][j], out["count"][i][j])
           for i, d in enumerate(out["data_codes"]) for j, m in enumerate(out["model_codes"])
           if out["count"][i][j]}  # fmt: skip
    assert set(got) == set(want), (set(got) ^ set(want))
    for k, (length, n) in want.items():
        assert got[k][0] == pytest.approx(length, abs=1e-8) and got[k][1] == n, k
    # the invariant: the sum of the matrix is the length of the intervals that have a path; the counts — their number
    total = sum(r[2] - r[1] for r in mine)
    assert out["total_length"] == pytest.approx(total, abs=1e-8)
    assert np.sum(out["length"]) == pytest.approx(total, abs=1e-8)
    assert int(np.sum(out["count"])) == len(mine) == out["intervals"]
    assert out["no_trajectory"]["holes"] == 1 and out["no_trajectory"]["intervals"] == len(rows) - len(mine)
    assert any("без траектории" in line for line in out["notes"])
    # the diagonal: the cells that agree — «окисл» with «Группа 1», «перв» with «Группа 2», nothing else
    agree = {(out["data_codes"][i], out["model_codes"][j]) for i, row in enumerate(out["agree"])
             for j, v in enumerate(row) if v}  # fmt: skip
    assert agree == {("окисл", G1), ("перв", G2)} and out["model_codes"] == [G1, G2, ch.OTHER, ch.OUTSIDE]
    diag = cell(out, "length", "окисл", G1) + cell(out, "length", "перв", G2)
    assert out["agree_length"] == pytest.approx(diag, abs=1e-8)
    assert out["agree_share"] == pytest.approx(diag / out["coded_length"], abs=1e-12)
    assert out["mismatches"]["total"] == sum(
        n for (d, m), (_s, n) in want.items() if d is not None and (d, m) not in agree)  # fmt: skip
    longest = [r["length"] for r in out["mismatches"]["rows"]]
    assert longest == sorted(longest, reverse=True)  # by length, the longest first


def test_a_missing_code_is_a_row_of_its_own_not_zero_and_not_a_mismatch(w: W) -> None:
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    rows: list[tuple[str, float, float, str | None]] = [
        ("V-1", 10.0, 30.0, "окисл"), ("V-1", 30.0, 45.0, None), ("V-1", 45.0, 60.0, ""), ("V-1", 60.0, 110.0, "перв")]
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    assert out["data_codes"] == ["окисл", "перв", ch.NO_CODE] and out["no_code_row"] == 2
    assert cell(out, "length", ch.NO_CODE, G1) == pytest.approx(30.0, abs=TOL)
    assert cell(out, "count", ch.NO_CODE, G1) == 2
    assert out["no_code"] == {"count": 2, "length": pytest.approx(30.0, abs=TOL)}
    assert out["mismatches"]["total"] == 0 and out["agree_length"] == pytest.approx(70.0, abs=TOL)  # 20 + 50
    assert out["total_length"] == pytest.approx(100.0, abs=TOL) and out["coded_length"] == pytest.approx(70.0, abs=TOL)
    assert out["agree_share"] == 1.0  # the base is what has a code
    assert all(not any(row) for row in [out["agree"][2]])  # «нет кода» agrees with nothing


def test_the_order_of_the_holes_and_of_the_rows_does_not_change_the_answer(w: W) -> None:
    ident, set_id, rows, _inside, holes = random_world(w, 9)
    first = compare(w, ident, set_id, lith(w, set_id, rows), size=5000)
    rng = np.random.default_rng(1)
    again = new_set(w, holes[::-1], "Наоборот")  # the same holes at the same places, written from the last to the first
    second = compare(w, ident, again, lith(w, again, [rows[i] for i in rng.permutation(len(rows))]), size=5000)
    assert second["model_codes"] == first["model_codes"] and second["data_codes"] == first["data_codes"]
    assert np.allclose(second["length"], first["length"], rtol=0, atol=1e-9)
    assert second["count"] == first["count"] and second["agree"] == first["agree"]
    assert second["agree_length"] == pytest.approx(first["agree_length"], abs=1e-9)
    assert second["total_length"] == pytest.approx(first["total_length"], abs=1e-9)
    assert sliced(second) == sliced(first) and first["mismatches"]["total"] > 0  # the list too, in the same order


def test_the_pages_of_the_list_cut_it_in_order_and_the_size_is_checked(w: W) -> None:
    ident, set_id, rows, _inside, _holes = random_world(w, 3)
    table = lith(w, set_id, rows)
    full = compare(w, ident, set_id, table, size=5000)["mismatches"]
    total = full["total"]
    assert total > 12 and len(full["rows"]) == total
    p1 = compare(w, ident, set_id, table, page=1, size=5)["mismatches"]
    p2 = compare(w, ident, set_id, table, page=2, size=5)["mismatches"]
    assert (p1["page"], p1["size"], p1["total"]) == (1, 5, total) and p1["rows"] + p2["rows"] == full["rows"][:10]
    assert compare(w, ident, set_id, table, page=10**6, size=5)["mismatches"]["rows"] == []
    for bad in ({"size": 0}, {"size": 5001}, {"page": 0}, {"page": "1"}, {"size": True}):
        refused_with(lambda b=bad: ch.parse({"set_id": set_id, "table": table, "field": "LITH", **b}), 422,
                     "bad_param", next(iter(bad)))  # fmt: skip


def test_the_path_reaches_the_deepest_interval_and_a_hole_beside_the_model_is_outside_it(w: W) -> None:
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0, td=50.0), vertical("V-9", 400.0, 400.0)])
    rows: Rows = [("V-1", 40.0, 70.0, "окисл"), ("V-1", 80.0, 90.0, "перв"), ("V-9", 0.0, 20.0, "перв")]
    out = compare(w, ident, set_id, lith(w, set_id, rows))
    # the path of V-1 is 50 m by its depth but the table has an interval down to 90 m: the path reaches it (7.11),
    # so [40; 70] is 20 m of the layer 1 and 10 m of the layer 2 → the layer 1
    assert sliced(out) == [("V-9", 0.0, 20.0, "перв", ch.OUTSIDE)]
    assert cell(out, "length", "окисл", G1) == pytest.approx(30.0, abs=TOL)
    assert cell(out, "length", "перв", G2) == pytest.approx(10.0, abs=TOL)
    assert cell(out, "length", "перв", ch.OUTSIDE) == pytest.approx(20.0, abs=TOL)


# --- the refusals, the route, the imports -------------------------------------------------------------------------


def test_refusals_come_with_code_message_and_hint(w: W) -> None:
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    table = lith(w, set_id, [("V-1", 10.0, 20.0, "окисл")])
    ok = {"set_id": set_id, "table": table, "field": "LITH"}

    def go(model: int = ident, **kw: Any) -> dict[str, Any]:
        return ch.run(w.project, model, ch.parse({**ok, **kw}))

    refused_with(lambda: go(set_id=99999), 404, "set_not_found")
    refused_with(lambda: go(table="нет такой"), 404, "table_not_found")
    refused_with(lambda: go(model=99999), 404, "model_not_found")
    refused_with(lambda: go(field="LITH2"), 422, "bad_param", "field")
    refused_with(lambda: ch.parse({**ok, "field": " "}), 422, "bad_param", "field")
    refused_with(lambda: ch.parse({**ok, "table": None}), 422, "bad_param", "table")
    refused_with(lambda: ch.parse({"table": table, "field": "LITH"}), 404, "set_not_found")
    refused_with(lambda: go(channel=""), 422, "bad_param", "channel")
    unbuilt = make(w, name="Не построена")["id"]
    refused_with(lambda: go(model=unbuilt), 409, "not_built")
    empty = make(w, name="Пустая", groups=[])["id"]
    refused_with(lambda: go(model=empty), 409, "not_built")
    assert go(table="Литология")["intervals"] == 1  # the table by its name
    assert go(field="lith")["intervals"] == 1  # the field whatever the case


def test_a_number_field_has_no_codes(w: W) -> None:
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    table = dh.write_table(w.project.engine, set_id, dh.TableData(
        name="Числа", kind="interval", holes=["V-1"], from_m=[10.0], to_m=[20.0],
        columns=[dh.ColumnSpec("AU", "number")], values=[[1.0]]), job_id=None,
        tick=lambda _p, _m: None)["table_id"]  # fmt: skip
    err = refused_with(lambda: ch.run(w.project, ident, ch.parse({"set_id": set_id, "table": table, "field": "AU"})),
                       422, "bad_param", "field")  # fmt: skip
    assert "не текстовое" in err.message


def test_a_newer_request_of_the_channel_overtakes_the_older_one(w: W) -> None:
    from bf_core.blocks import viz_spec as sp

    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    table = lith(w, set_id, [("V-1", 10.0, 20.0, "окисл")])
    newer = sp.GATE.enter("сверка-тест", 9)  # a request with seq 9 is running: seq 5 is late
    try:
        err = refused_with(lambda: compare(w, ident, set_id, table, channel="сверка-тест", seq=5), 409, "superseded")
        assert err.hint
    finally:
        sp.GATE.leave(newer)
    assert compare(w, ident, set_id, table, channel="сверка-тест", seq=10)["intervals"] == 1


def test_the_route_answers_with_the_form_of_the_contract_and_the_errors_in_russian(w: W) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bf_core.api.errors import ApiError, api_error_handler
    from bf_core.model import routes_compare
    from bf_core.projects.service import projects

    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(routes_compare.router, prefix="/api/v1/implicit")
    app.dependency_overrides[projects] = lambda: SimpleNamespace(require_current=lambda writable=False: w.project)
    ident = two_layers(w)
    set_id = new_set(w, [vertical("V-1", 25.0, 25.0)])
    table = lith(w, set_id, [("V-1", 10.0, 30.0, "окисл"), ("V-1", 30.0, 40.0, "перв")])
    c = TestClient(app)
    r = c.post(f"/api/v1/implicit/models/{ident}/compare_holes", json={"set_id": set_id, "table": table,
                                                                       "field": "LITH", "size": 10})
    assert r.status_code == 200, r.text
    got = r.json()
    assert set(got) >= {"model_codes", "data_codes", "length", "count", "agree_length", "total_length", "agree_share",
                        "mismatches"}
    assert got["mismatches"] == {"page": 1, "size": 10, "total": 1, "rows": [{
        "hole": "V-1", "hole_id": got["mismatches"]["rows"][0]["hole_id"], "from": 30.0, "to": 40.0, "depth": 35.0,
        "data_code": "перв", "model_code": G1, "length": pytest.approx(10.0)}]}  # fmt: skip
    r = c.post(f"/api/v1/implicit/models/{ident}/compare_holes", json={"set_id": set_id, "table": table})
    assert r.status_code == 422 and r.json()["param"] == "field" and r.json()["hint"], r.text
    r = c.post("/api/v1/implicit/models/99999/compare_holes", json={"set_id": set_id, "table": table, "field": "LITH"})
    assert r.status_code == 404 and r.json()["code"] == "model_not_found" and r.json()["hint"], r.text


def test_the_modules_do_not_load_numba_with_their_import() -> None:
    code = (
        "import sys, bf_core.model.compare_holes, bf_core.model.routes_compare\n"
        "bad = [m for m in ('numba', 'manifold3d') if m in sys.modules]\n"
        "raise SystemExit(3 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
