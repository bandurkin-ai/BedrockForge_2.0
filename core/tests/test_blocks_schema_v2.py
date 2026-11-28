"""Meta v2 of the block model (подшаг 17.1; ADR 0023 §2–3, decisions 2–6; docs/progress/e17-api.md §2–4): a v1
meta reads as v2 without losses, a turn about X or Y and a division of sub-blocks that is not a power of two up to
128 are refused with 422 {code, message, hint, param} in Russian, the grid maps local ↔ project axes, the rows go by
(P, sz, sy, sx). The fixtures `tests/data/blocks/{grids.json, contract_answers.json}` are what the code answers
(the answers of the routes of Э11 — through the real routes); regenerate:
`BF_REGEN_BLOCKS_CONTRACT=1 uv run pytest -q tests/test_blocks_schema_v2.py`."""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore

API = "/api/v1"
DATA = Path(__file__).parent / "data" / "blocks"
REGEN = os.environ.get("BF_REGEN_BLOCKS_CONTRACT") == "1"

V1_REGULAR: dict[str, Any] = {
    "v": 1,
    "grid": {"corner": [1000.5, 2000.25, -300.0], "counts": [30, 20, 20], "size": [5.0, 5.0, 2.5], "order": "F"},
    "cells": 10_000,
    "fields": [
        {"name": "Au", "kind": "number", "unit": "g/t",
         "stats": {"count": 9000, "missing": 1000, "min": 0.01, "max": 12.5, "mean": 1.25, "std": 0.75}},
        {"name": "Порода", "kind": "category", "unit": None, "labels": ["руда", "порода", "дайка"],
         "stats": {"count": 7500, "missing": 2500, "counts": [2500, 2500, 2500]}},
    ],
    "source": {"format": "omf", "file": "model.omf", "element": "Блоки"},
    "warnings": ["Значения на узлах не перенесены."],
}  # fmt: skip
V1_TENSORS: dict[str, Any] = {
    "v": 1,
    "grid": {"corner": [0, 0, 0], "counts": [3, 2, 1], "tensors": {"x": [1, 2, 4], "y": [5, 5], "z": [10]},
             "order": "F"},
    "cells": 0, "fields": [], "source": None, "warnings": [],
}  # fmt: skip
SUB_GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [30, 20, 20], "size": [5.0, 5.0, 2.5], "rotation": 30,
            "div": [8, 8, 4]}  # fmt: skip


def body(exc: ApiError) -> dict[str, Any]:
    """The answer of a refusal as the API gives it (`api_error_handler`)."""
    return {"status": exc.status, **exc.extra, "code": exc.code, "message": exc.message, "hint": exc.hint}


def refused(fn: Any, *args: Any) -> dict[str, Any]:
    with pytest.raises(ApiError) as e:
        fn(*args)
    out = body(e.value)
    assert out["message"] and out["hint"] and not out["message"].isascii()  # Russian text with a hint
    return out


# --- v1 → v2 ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("v1", [V1_REGULAR, V1_TENSORS], ids=["regular", "tensors"])
def test_v1_meta_reads_as_v2_without_losses(v1: dict[str, Any]) -> None:
    before = copy.deepcopy(v1)
    v2 = bmeta.upgrade(v1)
    assert v1 == before  # the stored meta is not touched
    assert v2["v"] == 2 and bmeta.upgrade(v2) == v2 and json.loads(json.dumps(v2)) == v2
    g1, g2 = v1["grid"], v2["grid"]
    assert g2["corner"] == g1["corner"] and g2["counts"] == g1["counts"] and g2["order"] == "F"
    assert g2.get("size") == g1.get("size") and g2.get("tensors") == g1.get("tensors")
    assert g2["rotation"] == 0.0 and g2["div"] == [1, 1, 1]
    assert v2["rows"] == v2["parents"] == v1["cells"] and v2["sub"] is False and v2["full"] is False
    for f1, f2 in zip(v1["fields"], v2["fields"], strict=True):
        assert {k: f2[k] for k in f1} == f1  # name, kind, unit, labels, stats — as they were
        assert f2["dtype"] == ("cat" if f1["kind"] == "category" else "f8")
        assert f2["file"] == "cells" and f2["role"] is None and f2["source"] is None and f2["description"] is None
    src = v1["source"]
    assert v2["source"] == (None if src is None else {"op": "import", **src})
    assert v2["warnings"] == v1["warnings"]
    assert (v2["checks"], v2["groups"], v2["inputs"], v2["algo"]) == (None, None, None, None)
    # the grid of v1 and of v2 is the same box
    assert bs.GridV2.of(g1).extent() == bs.extent(g1)
    assert bmeta.files_of(v1) == {"cells": [f["name"] for f in v1["fields"]]}


def test_public_meta_keeps_the_names_of_e11() -> None:
    brief = bmeta.public_meta(V1_REGULAR, brief=True)
    full = bmeta.public_meta(V1_REGULAR)
    assert brief["cells"] == full["cells"] == full["rows"] == 10_000 and brief["meta_v"] == 1
    assert brief["grid"]["variable"] is False and brief["grid"]["levels"] == [0, 0, 0]
    assert brief["fields"][1] == {"name": "Порода", "kind": "category", "dtype": "cat", "unit": None, "role": None}
    assert "checks" not in brief and full["checks"] is None
    assert full["fields"][1]["labels"] == ["руда", "порода", "дайка"]
    tens = bmeta.public_meta(V1_TENSORS, brief=True)
    assert tens["grid"]["variable"] is True and "tensors" not in tens["grid"]


def sub_meta() -> dict[str, Any]:
    """A meta v2 as 17.3 writes it: turned 30°, sub-blocks 8 × 8 × 4, an imported field and a file of `fill`."""
    return {
        "v": 2, "grid": SUB_GRID, "rows": 5, "parents": 2, "sub": True, "full": False,
        "fields": [
            {"name": "AU", "kind": "number", "dtype": "f8", "unit": "г/т", "description": "Золото", "role": "grade",
             "stats": {"count": 4, "missing": 1, "min": 0.5, "max": 3.0, "mean": 1.5, "std": 1.0}, "file": "cells",
             "source": {"op": "import", "params": {}, "inputs": [], "algo": None, "versions": {}, "at": None}},
            {"name": "CODE", "kind": "category", "dtype": "cat", "unit": None, "description": None, "role": "code",
             "labels": ["Руда", "вне"], "stats": {"count": 5, "missing": 0, "counts": [3, 2]}, "file": "fld:0a1b2c3d",
             "source": {"op": "fill", "params": {"mode": "both", "m": 4}, "inputs": ["ab" * 32], "algo": "fill-1",
                        "versions": {"numpy": "2.x"}, "at": "2026-11-24T00:00:00+00:00"}},
        ],
        "checks": {"at": "2026-11-24T00:00:00+00:00", "duplicates": 0, "overlaps": {"n": 0, "method": "exact"},
                   "empty": [], "constant": [], "volume": 1250.0, "examples": []},
        "groups": [{"rows": 5, "k0": 0, "k1": 3}],
        "source": {"op": "fill", "params": {"prototype_id": 21}}, "warnings": [],
        "inputs": {"wireframes": [{"id": 5, "sha": "cd" * 32}]}, "algo": "fill-1",
    }  # fmt: skip


def test_meta_v2_round_trip_and_files() -> None:
    m = sub_meta()
    v2 = bmeta.upgrade(m)
    assert bmeta.upgrade(v2) == v2
    assert v2["grid"] == {**SUB_GRID, "rotation": 30.0, "order": "F"}
    assert bmeta.files_of(m) == {"cells": ["AU"], "fld:0a1b2c3d": ["CODE"]}
    assert bmeta.public_meta(m)["grid"]["levels"] == [3, 3, 2]


def bad(change: Any) -> dict[str, Any]:
    m = sub_meta()
    change(m)
    return refused(bmeta.upgrade, m)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["fields"][0].update(dtype="cat"),  # a number stored as a category
        lambda m: m["fields"][1].update(dtype="f4"),
        lambda m: m["fields"][0].update(role="code"),  # a role of the other kind
        lambda m: m["fields"][1].update(role="frac"),
        lambda m: m["fields"][0].update(file="fld:XYZ"),
        lambda m: m["fields"][0].update(name="SX"),  # a column of the sub-blocks
        lambda m: m["fields"][1].update(name="au"),  # a repeat ignoring case, across the files
        lambda m: m["fields"][1]["source"].update(inputs=["nothex"]),
        lambda m: m["fields"][1]["source"].update(op="guess"),
        lambda m: (
            m["fields"].append({"name": "F1", "kind": "number", "role": "frac"})
            or m["fields"].append({"name": "F2", "kind": "number", "role": "frac"})
        ),
        lambda m: m.update(parents=6),
        lambda m: m.update(sub=False),  # 5 rows of 2 parents without sub-blocks
        lambda m: m.update(grid={**SUB_GRID, "div": [1, 1, 1]}),
        lambda m: m.update(full=True),  # a prototype has no rows
        lambda m: m.update(groups=[{"rows": 4, "k0": 0, "k1": 1}]),
        lambda m: m.update(v=3),
    ],
)
def test_meta_v2_refuses_what_does_not_agree(change: Any) -> None:
    assert bad(change)["code"] == "block_model_invalid"


# --- rotation and sub-blocks -----------------------------------------------------------------------------


def test_turn_about_x_or_y_is_refused() -> None:
    text = "Поворот вокруг X/Y в v1 не поддерживается."  # ТЗ 8.7.1
    cases = [
        (bs.parse_rotation, {"x": 5.0, "z": 30.0}),
        (bs.parse_rotation, {"y": -0.5}),
        (bs.GridV2.of, {**SUB_GRID, "rotation": {"z": 10, "x": 90}}),
        (bs.rotation_of_angles, [(3, 30.0), (1, 15.0)]),  # Datamine ROTAXIS 1 = X
        (bs.rotation_of_angles, [("y", 2.0)]),
        (bs.rotation_of_axes, [1, 0, 0], [0, math.cos(0.1), math.sin(0.1)], [0, -math.sin(0.1), math.cos(0.1)]),
        (bs.rotation_of_axes, [1, 0, 0], [0, -1, 0], [0, 0, -1]),  # a mirror is no turn about Z
    ]
    for fn, *args in cases:
        out = refused(fn, *args)
        assert out["status"] == 422 and out["code"] == "rotation_unsupported" and out["message"] == text
        assert out["param"].startswith("grid.rotation")
    # whole turns about X / Y are no turn; Z composes
    assert bs.parse_rotation({"x": 360.0, "y": -720.0, "z": 30.0}) == 30.0
    assert bs.rotation_of_angles([(3, 200.0), (1, 360.0), (3, 20.0)]) == -140.0
    s, c = math.sin(math.radians(30.0)), math.cos(math.radians(30.0))
    assert bs.rotation_of_axes([c, s, 0], [-s, c, 0], [0, 0, 1]) == pytest.approx(30.0, abs=1e-12)
    assert bs.rotation_of_axes([0, 1, 0], [-1, 0, 0], [0, 0, 1]) == 90.0


@pytest.mark.parametrize("value", [0, -2, 256, 129, 2.5, True, "4", None, float("nan")])
def test_div_not_a_whole_from_1_to_128_is_refused(value: Any) -> None:
    out = refused(bs.GridV2.of, {**SUB_GRID, "div": [8, value, 4]})
    assert out["status"] == 422 and out["code"] == "subblock_div" and out["param"] == "grid.div.1"
    assert "целое от 1 до 128" in out["message"] and "128" in out["hint"]


@pytest.mark.parametrize("value", [3, 5, 6, 10, 12, 100, 127])
def test_div_any_whole_up_to_128_is_taken_and_has_no_levels(value: int) -> None:
    g = bs.GridV2.of({**SUB_GRID, "div": [8, value, 4]})
    assert g.sub.div == (8, value, 4) and g.sub.levels is None and g.sub.sub
    pub = bmeta.public_meta({**sub_meta(), "grid": g.to_meta()})
    assert pub["grid"]["levels"] is None and pub["grid"]["div"] == [8, value, 4]


def test_div_powers_of_two_and_levels() -> None:
    for d in (1, 2, 4, 8, 16, 32, 64, 128):
        spec = bs.SubblockSpec.of([d, d, 4.0])
        assert spec.div == (d, d, 4) and spec.levels == (d.bit_length() - 1, d.bit_length() - 1, 2)
    assert bs.SubblockSpec.of_levels([3, 0, 7]).div == (8, 1, 128) and not bs.SubblockSpec().sub
    assert refused(bs.SubblockSpec.of_levels, [8, 0, 0])["code"] == "subblock_div"
    assert refused(bs.SubblockSpec.of, [2, 2])["code"] == "block_model_invalid"  # not three values
    bad_rows = bs.SubblockSpec((8, 8, 4)).bad_rows([[0, 0, 0], [4, 4, 2], [7, 0, 0], [0, 0, 0]],
                                                     [[8, 8, 4], [4, 4, 2], [2, 1, 1], [1, 0, 1]])  # fmt: skip
    assert bad_rows.tolist() == [False, False, True, True]


def test_sub_blocks_need_a_regular_step() -> None:
    tens = {**V1_TENSORS["grid"], "div": [2, 1, 1]}
    out = refused(bs.GridV2.of, tens)
    assert out["code"] == "variable_grid" and out["param"] == "grid.tensors"
    g = bs.GridV2.of({**V1_TENSORS["grid"], "rotation": 45})  # a turned variable grid: show, filter, report
    assert g.tensors is not None and g.rotation == 45.0 and not g.sub.sub
    assert refused(g.regular, "Регуляризация")["message"].startswith("Регуляризация:")


def test_angles_and_exact_quarter_turns() -> None:
    assert [bs.normalize_angle(a) for a in (-180, 180, 540, -540, 390, -0.0, 179.5, -179.5)] == [
        180.0, 180.0, 180.0, 180.0, 30.0, 0.0, 179.5, -179.5]  # fmt: skip
    assert math.copysign(1.0, bs.normalize_angle(-0.0)) == 1.0
    for phi, m in [(90, [[0, -1, 0], [1, 0, 0], [0, 0, 1]]), (180, [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]),
                   (-90, [[0, 1, 0], [-1, 0, 0], [0, 0, 1]])]:  # fmt: skip
        assert bs.GridV2.of({**SUB_GRID, "rotation": phi}).matrix() == m
    assert refused(bs.parse_rotation, float("inf"))["code"] == "bad_param"
    assert refused(bs.parse_rotation, True)["code"] == "bad_param"


def test_local_and_project_axes() -> None:
    g = bs.GridV2.of(SUB_GRID)
    q = np.random.default_rng(3).uniform(0.0, 150.0, (1000, 3))
    p = g.to_project(q)
    assert np.abs(g.to_local(p) - q).max() < 1e-9
    s, c = math.sin(math.radians(30.0)), math.cos(math.radians(30.0))
    assert np.allclose(p[:, :2] - g.corner[:2], q[:, :2] @ np.array([[c, s], [-s, c]]), rtol=0, atol=1e-9)
    corners = g.to_project([[x, y, z] for z in (0, 50) for y in (0, 100) for x in (0, 150)])
    assert g.extent() == pytest.approx([*corners.min(axis=0), *corners.max(axis=0)], abs=1e-9)
    assert g.lengths() == (150.0, 100.0, 50.0) and g.subcell() == (0.625, 0.625, 0.625) and g.cells == 12_000
    flat = bs.GridV2.of(V1_REGULAR["grid"])  # φ = 0: exactly the box of v1
    assert flat.to_project([[1.0, 2.0, 3.0]]).tolist() == [[1001.5, 2002.25, -297.0]]


def test_grid_limits_of_v1_and_v2() -> None:
    big = {"corner": [0, 0, 0], "counts": [2000, 1000, 1000], "size": [1, 1, 1]}
    assert bs.GridV2.of(big).cells == bs.MAX_GRID_V2  # ТЗ 8.7.2: ≤ 2·10⁹
    assert refused(bs.GridV2.of, {**big, "counts": [2000, 1000, 1001]})["code"] == "block_model_too_large"
    assert refused(bs.check_grid, {**big, "counts": [1000, 1000, 201]})["code"] == "block_model_too_large"  # v1
    assert refused(bs.GridV2.of, {**big, "order": "C"})["code"] == "block_model_invalid"


def test_a_regular_grid_has_exact_lengths_and_a_fast_extent_without_a_running_sum() -> None:
    import time

    big = bs.GridV2.of({"corner": [0, 0, 0], "counts": [20_000_000, 10, 10], "size": [0.1, 1, 1]})
    t0 = time.perf_counter()
    ext = big.extent()
    assert time.perf_counter() - t0 < 0.1  # no list of 2e7 steps
    assert ext == [0.0, 0.0, 0.0, 20_000_000 * 0.1, 10.0, 10.0]  # size·count: 2000000.0, not a sum of 2e7 steps
    small = bs.GridV2.of({"corner": [0, 0, 0], "counts": [10, 10, 10], "size": [0.1, 1, 1]})
    assert small.lengths() == (1.0, 10.0, 10.0)
    e = small.edges(0)
    assert e.shape == (11,) and e[0] == 0.0 and e[3] == 3 * 0.1 and small.steps(0).tolist() == [0.1] * 10
    var = bs.GridV2.of({"corner": [0, 0, 0], "counts": [3, 1, 1], "tensors": {"x": [1, 2, 3], "y": [1], "z": [1]}})
    assert var.edges(0).tolist() == [0.0, 1.0, 3.0, 6.0] and var.lengths() == (6.0, 1.0, 1.0)


def test_rows_go_by_p_then_sub_blocks() -> None:
    p = np.array([7, 3, 3, 3, 9], dtype=np.uint64)
    s = np.array([[0, 0, 0], [0, 4, 0], [4, 0, 0], [0, 0, 1], [0, 0, 0]], dtype=np.uint8)
    order = bs.sort_rows(p, s)
    assert order.tolist() == [2, 1, 3, 0, 4]  # P 3: (sz, sy, sx) = (0, 0, 4) < (0, 4, 0) < (1, 0, 0)
    assert bs.ascending(p[order], s[order]) and not bs.ascending(p, s)
    assert not bs.ascending(p[order][[0, 0]], s[order][[0, 0]])  # a repeat
    assert bs.sort_rows(p).tolist() == [1, 2, 3, 0, 4] and bs.ascending(np.array([1, 5], np.uint64))
    role = bs.new_field_file()
    assert bs.FLD_RE.fullmatch(role) and role != bs.new_field_file()


# --- fixtures for the lanes: grids.json and the answers of the routes -------------------------------------


GRIDS: list[tuple[str, dict[str, Any]]] = [
    ("v1_regular", V1_REGULAR["grid"]),
    ("v1_tensors", V1_TENSORS["grid"]),
    ("v2_turned_30_sub", SUB_GRID),
    ("v2_turned_90", {**SUB_GRID, "rotation": 90, "div": [1, 1, 1]}),
    ("v2_turned_minus_150", {"corner": [500000.0, 7000000.0, 100.0], "counts": [100, 100, 100],
                             "size": [10.0, 10.0, 10.0], "rotation": -150.0, "div": [2, 4, 1]}),
]  # fmt: skip
REFUSALS: list[tuple[str, dict[str, Any]]] = [
    ("rotation_x", {**SUB_GRID, "rotation": {"x": 5, "z": 30}}),
    ("div_0", {**SUB_GRID, "div": [0, 8, 4]}),
    ("div_256", {**SUB_GRID, "div": [8, 8, 256]}),
    ("div_tensors", {**V1_TENSORS["grid"], "div": [2, 1, 1]}),
    ("too_large", {"corner": [0, 0, 0], "counts": [2001, 1000, 1000], "size": [1, 1, 1]}),
]


def grids_fixture() -> dict[str, Any]:
    out: dict[str, Any] = {"grids": [], "upgrades": [], "refusals": []}
    for name, raw in GRIDS:
        g = bs.GridV2.of(raw)
        q = [[0.0, 0.0, 0.0], [g.lengths()[0], 0.0, 0.0], [g.lengths()[0] / 2, g.lengths()[1] / 2, g.lengths()[2] / 2]]
        out["grids"].append({
            "name": name, "raw": raw, "grid": g.to_meta(), "cells": g.cells, "levels": list(g.sub.levels),
            "subcell": None if g.size is None else list(g.subcell()), "lengths": list(g.lengths()),
            "matrix": g.matrix(), "extent": g.extent(), "local": q, "project": g.to_project(q).tolist(),
        })  # fmt: skip
    for name, m in [("v1_regular", V1_REGULAR), ("v1_tensors", V1_TENSORS), ("v2_sub", sub_meta())]:
        out["upgrades"].append({"name": name, "meta": m, "v2": bmeta.upgrade(m), "public": bmeta.public_meta(m),
                                "public_brief": bmeta.public_meta(m, brief=True)})  # fmt: skip
    for name, raw in REFUSALS:
        with pytest.raises(ApiError) as e:
            bs.GridV2.of(raw)
        out["refusals"].append({"name": name, "raw": raw, "answer": body(e.value)})
    return out


def masked(x: Any) -> Any:
    """The times — their type; other numbers to 1e−12 relative (the text of the JSON file)."""
    if isinstance(x, dict):
        return {k: type(v).__name__ if k in ("created_at", "updated_at") else masked(v) for k, v in x.items()}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.12g}")
    return x


def compare(name: str, built: dict[str, Any]) -> dict[str, Any]:
    path = DATA / name
    built = json.loads(json.dumps(built, ensure_ascii=False))
    if REGEN:
        DATA.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    return stored


def test_grids_fixture_is_what_the_schema_answers() -> None:
    stored = compare("grids.json", grids_fixture())
    assert {r["answer"]["code"] for r in stored["refusals"]} == {
        "rotation_unsupported", "subblock_div", "variable_grid", "block_model_too_large"}  # fmt: skip


@pytest.fixture()
def bc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Контракт блоков"}).status_code == 200
    return pclient


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def row_part(info: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "name", "folder", "version", "extent", "crs", "size", "created_at", "updated_at")
    return {k: info[k] for k in keys}


def test_contract_answers_are_what_the_core_answers(bc) -> None:  # type: ignore[no-untyped-def]
    p = projects_of(bc.core).require_current()
    store = BlobStore(p.folder)
    grid = {"corner": [1000.5, 2000.25, -300.0], "counts": [4, 3, 2], "size": [5.0, 5.0, 2.5]}
    fields = [{"name": "Au", "kind": "number", "unit": "г/т"},
              {"name": "Порода", "kind": "category", "labels": ["руда", "порода"]}]  # fmt: skip
    idx = np.array([0, 1, 5, 7, 12, 23], dtype=np.uint64)
    values = {"Au": np.array([0.5, np.nan, 1.25, 3.0, 0.75, 2.0]), "Порода": np.array([0, 1, -1, 0, 0, 1])}
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.put_block_model(conn, store, grid, fields, idx, values, name="Модель",
                                   source={"format": "omf", "file": "model.omf", "element": "Блоки"})  # fmt: skip
    out: dict[str, Any] = {"list": answer(bc.get(f"{API}/blocks")), "get": answer(bc.get(f"{API}/blocks/{bm_id}"))}
    with dialect.read_tx(p.engine) as conn:
        stored_meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == bm_id)).scalar_one()
    assert stored_meta["v"] == 1
    info = out["get"]["body"]
    out["get_v2"] = {**row_part(info), "stale": False, "stale_reason": None, **bmeta.public_meta(stored_meta)}
    sub = sub_meta()
    out["get_v2_sub"] = {
        "id": 1, "name": "Субблоки", "folder": None, "version": 1, "extent": bmeta.grid_of(sub).extent(), "crs": None,
        "size": None, "created_at": "2026-11-24T00:00:00+00:00", "updated_at": "2026-11-24T00:00:00+00:00",
        "stale": False, "stale_reason": None, **bmeta.public_meta(sub),
    }  # fmt: skip
    out["patch"] = answer(bc.patch(f"{API}/blocks/{bm_id}", json={"name": "Модель Э17", "expected_version": 1}))
    out["patch_conflict"] = answer(bc.patch(f"{API}/blocks/{bm_id}", json={"name": "Х", "expected_version": 1}))
    out["not_found"] = answer(bc.get(f"{API}/blocks/999"))
    out["delete"] = answer(bc.delete(f"{API}/blocks/{bm_id}"))
    stored = compare("contract_answers.json", out)
    # what the lanes rely on
    v1, v2 = stored["get"]["body"], stored["get_v2"]
    assert v2["source"] == {"op": "import", **v1["source"]} and v2["warnings"] == v1["warnings"]
    assert v2["cells"] == v1["cells"] == 6 and v2["rows"] == v2["parents"] == 6 and v2["meta_v"] == 1
    assert {k: v2["grid"][k] for k in v1["grid"]} == v1["grid"]  # v2 only adds rotation, div, levels
    assert [{k: f2[k] for k in f1} for f1, f2 in zip(v1["fields"], v2["fields"], strict=True)] == v1["fields"]
    assert stored["patch"]["body"]["version"] == 2 and stored["patch_conflict"]["status"] == 409
    assert stored["patch_conflict"]["body"]["code"] == "version_conflict"
    assert stored["not_found"]["body"]["code"] == "block_model_not_found"
    assert stored["delete"]["body"] == {"deleted": bm_id}
    s = stored["get_v2_sub"]
    assert s["grid"]["rotation"] == 30.0 and s["grid"]["div"] == [8, 8, 4] and s["fields"][1]["file"] == "fld:0a1b2c3d"
