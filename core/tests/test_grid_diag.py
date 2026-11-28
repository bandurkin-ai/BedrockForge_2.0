"""The diagnosis of a block file the grid cannot hold (ADR 0030 decisions 5–6, подшаг 27.4б; замечание А6): the causes
(`div_too_fine`, `off_lattice`, `two_origins`, `rounded`, `crosses_parent`, `off_grid`) with the numbers behind them,
the candidates of the grid with their shares, the choices of the wizard («Задать сетку вручную», «Не импортировать»),
the answer ≤ 16 КБ, the same bytes twice, the 422 in `inspect` and in the job (`FormatError.extra`), and the way
out: the grid of the first choice, given as the hint, holds the blocks.

The models are built by hand from the edges of the blocks (no code under test builds the reference)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks.grid_diag import CAUSES, Sample, choices_of, current_choices, diagnose
from bf_core.blocks.grid_infer import GridHint, GridScan, Unaligned, infer
from bf_core.formats.errors import FormatError
from bf_core.formats.micromine import write_dat
from bf_core.formats.micromine_header import Field
from bf_core.formats.table import Column
from bf_core.importing import object_blockfile as obf
from bf_core.importing.blockfile_parts import column_types
from bf_core.importing.object_files import _failure
from tests.test_import_blocks import project  # noqa: F401  (fixture)

O = np.array([466167.5, 4584527.5, 202.5])  # noqa: E741  (the corner of the models below)


def blocks(x: list[tuple[float, float]], y: list[tuple[float, float]] | None = None,
           z: list[tuple[float, float]] | None = None) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """Centres and sizes of blocks given by (low edge, size) per axis, taken together row by row (the lists are as
    long as the rows; an axis not given is one 10 m layer)."""
    n = len(x)
    y = y or [(0.0, 10.0)] * n
    z = z or [(0.0, 10.0)] * n
    c = np.array([[O[0] + a + s / 2, O[1] + b + t / 2, O[2] + e + u / 2]
                  for (a, s), (b, t), (e, u) in zip(x, y, z, strict=True)])
    d = np.array([[s, t, u] for (_a, s), (_b, t), (_e, u) in zip(x, y, z, strict=True)])
    return c, d


def refuse(c: np.ndarray, d: np.ndarray, hint: GridHint | None = None, quanta: Any = None,
           ) -> tuple[GridScan, Any, Unaligned, dict[str, Any]]:  # fmt: skip
    scan = GridScan(hint, quanta=quanta)
    scan.add(c, d)
    found = scan.finish()
    lost = Unaligned()
    lost.add(0, found.rows_of(c, d))
    assert lost.count > 0, "the model was expected to be refused"
    sample = Sample(len(c))
    sample.add(0, c, d)
    diag = diagnose(scan, found, *sample.arrays(), rows=len(c), columns={"x": "R 8", "dx": "N 10.2"}, lost=lost)
    return scan, found, lost, diag


def the_way_out(c: np.ndarray, d: np.ndarray, diag: dict[str, Any], quanta: Any = None) -> None:
    """The grid of the first choice, given as the hint of the wizard, holds every block."""
    ch = choices_of(diag)
    assert [x["id"] for x in ch] == ["grid", "cancel"] and ch[0]["aligned"] == 1.0
    g = ch[0]["grid"]
    hint = GridHint(corner=tuple(g["corner"]), size=tuple(g["size"]), div=tuple(g["div"]), tol=g["tol"],
                    rotation=g["rotation"])  # fmt: skip
    res = infer(c, d, hint, quanta=quanta)
    assert res.valid is None and res.grid.sub.div == tuple(g["div"])


# --- the causes ------------------------------------------------------------


def test_the_lcm_over_128_is_too_fine() -> None:
    sevenths = [(100.0 * i + 10 / 7 * j, 10 / 7) for i in range(30) for j in range(5)]
    nineteenths = [(100.0 * i + 50 + 10 / 19 * j, 10 / 19) for i in range(30) for j in range(5)]
    whole = [(10.0 * k + 3000, 10.0) for k in range(100)]
    c, d = blocks(whole + sevenths + nineteenths)
    _, _, lost, diag = refuse(c, d)
    assert "div_too_fine" in diag["causes"] and "off_lattice" not in diag["causes"]
    x = diag["axes"][0]
    assert x["axis"] == "X" and x["div"] is None and x["fit"]["off"] > 0
    assert {f["q"] for f in x["fractions"]} >= {1, 7, 19}  # the denominators found: each fine, together over 128
    assert any("общее деление родителя больше 128" in t for t in diag["text"])
    assert lost.by_reason[2] > 0  # not on the sub-cells


def test_sizes_with_no_fraction_are_sub_blocks_of_any_size() -> None:
    rng = np.random.default_rng(1)
    heights = [(0.0, float(h)) for h in rng.uniform(1.0, 10.0, 300)]
    layers = [(0.0, 10.0)] * 200
    c, d = blocks([(10.0 * (i % 40), 10.0) for i in range(500)], z=layers + heights)
    _, _, _, diag = refuse(c, d)
    assert "off_lattice" in diag["causes"]
    z = diag["axes"][2]
    assert z["axis"] == "Z" and z["distinct"] > 250 and z["div"] is None
    assert any("различных высот" in t and "общего деления родителя нет" in t for t in diag["text"])


def test_whole_parents_on_two_grids_are_two_origins() -> None:
    a = [(10.0 * k, 10.0) for k in range(60)]  # a grid from 0
    b = [(1000.0 + 3.0 + 10.0 * k, 10.0) for k in range(60)]  # another, 3 m aside
    c, d = blocks(a + b)
    _, _, _, diag = refuse(c, d)
    assert "two_origins" in diag["causes"]
    res = diag["axes"][0]["residues"]
    assert {round(r["v"], 6) for r in res} >= {0.0, 3.0} and all(r["n"] >= 60 for r in res[:2])


def test_numbers_rounded_to_two_decimals_are_a_matter_of_tolerance() -> None:
    third = [(10.0 * k + 10 / 3 * j, 10 / 3) for k in range(40) for j in range(3)]  # the file rounds them: 3.33
    whole = [(10.0 * k + 1000, 10.0) for k in range(40)]
    c, d = blocks(third + whole)
    c, d = np.round(c, 2), np.round(d, 2)
    _, _, _, diag = refuse(c, d)  # the columns are exact: the numbers are refused
    assert "rounded" in diag["causes"]
    x = diag["axes"][0]
    assert x["suggest_tol"] in (0.005, 0.01) and any("с допуском" in t for t in diag["text"])
    best = diag["candidates"][0]
    assert best["aligned"] == 1.0 and max(best["tol"]) >= 0.005
    the_way_out(c, d, diag)


def test_a_block_over_the_border_of_the_given_parent_and_the_parent_that_holds_it() -> None:
    whole = [(10.0 * k, 10.0) for k in range(50)]
    over = [(10.0 * (k * 2) + 7.0, 7.0) for k in range(20)]  # 7…14 crosses the border at 10
    c, d = blocks(whole + over)
    hint = GridHint(size=(10.0, 10.0, 10.0), corner=tuple(O))
    _, _, lost, diag = refuse(c, d, hint)
    assert "crosses_parent" in diag["causes"] and lost.by_reason[3] > 0
    assert diag["axes"][0]["fit"]["crosses"] == 20 and any("через границу родителя 10 м" in t for t in diag["text"])
    sizes = [cand["size"][0] for cand in diag["candidates"]]
    assert 20.0 in sizes and diag["candidates"][0]["aligned"] == 1.0  # the parent of 20 m holds them
    assert choices_of(diag)[0]["grid"]["size"][0] == 20.0


def test_blocks_before_the_corner_of_the_wizard_are_off_grid() -> None:
    c, d = blocks([(10.0 * k, 10.0) for k in range(40)])
    hint = GridHint(size=(10.0, 10.0, 10.0), corner=(O[0] + 100.0, O[1], O[2]))
    _, _, lost, diag = refuse(c, d, hint)
    assert "off_grid" in diag["causes"] and lost.by_reason[1] == 10
    assert any("за началом сетки" in t for t in diag["text"])


# --- the answer ------------------------------------------------------------


def test_a_wrong_division_of_the_wizard_has_no_cause_but_the_grid_that_holds() -> None:
    whole = [(10.0 * k, 10.0) for k in range(40)]
    pieces = [(10.0 * (k * 3) + 2.0 * j, 2.0) for k in range(10) for j in range(5)]  # 2 m pieces: 5 in a parent
    c, d = blocks(whole + pieces)
    hint = GridHint(size=(10.0, 10.0, 10.0), corner=tuple(O), div=(4, 1, 1))  # 2.5 m sub-cells
    _, _, _, diag = refuse(c, d, hint)
    assert diag["causes"] == [] and diag["text"][0].startswith("Заданная сетка не подходит блокам")
    best = diag["candidates"][0]
    assert best["aligned"] == 1.0 and best["div"][0] == 5 and best["current"] is False
    the_way_out(c, d, diag)


def hard_model() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    x = [(10.0 * (i % 30), float(rng.choice([10.0, 5.0, 10 / 3]))) for i in range(20_000)]
    z = [(0.0, float(h)) for h in rng.uniform(1.0, 10.0, 20_000)]
    return blocks(x, z=z)


def test_the_answer_is_small_deterministic_and_whole_json() -> None:
    c, d = hard_model()
    scan, found, lost, diag = refuse(c, d)
    err = lost.error(diag, choices_of(diag))
    assert isinstance(err, ApiError) and err.code == "blocks_unaligned"
    body = {"code": err.code, "message": err.message, "hint": err.hint, **err.extra}
    text = json.dumps(body, ensure_ascii=False)
    assert len(text.encode("utf-8")) <= 16 * 1024
    assert set(err.extra) >= {"count", "rows", "by_reason", "diag", "choices"} and len(err.extra["rows"]) <= 20
    assert set(diag["causes"]) <= set(CAUSES) and diag["rows"] == len(c) and diag["sample"] == len(c)
    again = refuse(c, d)[3]
    assert json.dumps(again, ensure_ascii=False, sort_keys=True) == json.dumps(diag, ensure_ascii=False, sort_keys=True)
    assert "Блоков, которые нельзя уложить в сетку модели" in err.message and diag["text"][0] in err.message
    assert "вручную" in err.hint and "Строки данных (с 1)" in err.hint
    assert len(diag["candidates"]) <= 5 and len(diag["text"]) <= 12
    for ax in diag["axes"]:
        assert len(ax["sizes"]) <= 12 and len(ax["fractions"]) <= 12 and len(ax["residues"]) <= 5


def test_the_sample_is_the_same_rows_in_any_portions() -> None:
    n = 1000
    c = np.arange(n * 3, dtype=float).reshape(n, 3)
    whole = Sample(n, limit=100)
    whole.add(0, c, None)
    for step in (1, 7, 333):
        parts = Sample(n, limit=100)
        for a in range(0, n, step):
            parts.add(a, c[a : a + step], None)
        assert np.array_equal(parts.arrays()[0], whole.arrays()[0])
    assert whole.step == 10 and len(whole.arrays()[0]) == 100 and whole.arrays()[1] is None


def test_the_choices_are_the_grid_by_hand_and_to_leave_the_file_out() -> None:
    c, d = hard_model()
    _, found, _, diag = refuse(c, d)
    ch = choices_of(diag, rotation=30.0)
    assert [x["id"] for x in ch] == ["grid", "cancel"]
    assert ch[0]["label"] == "Задать сетку вручную" and ch[1]["label"] == "Не импортировать этот файл"
    assert set(ch[0]["grid"]) == {"corner", "size", "div", "tol", "rotation"} and ch[0]["grid"]["rotation"] == 30.0
    cur = current_choices(found)
    assert cur[0]["grid"]["size"] == pytest.approx(list(found.grid.regular()), abs=1e-6) and cur[1]["id"] == "cancel"
    nothing = choices_of({"candidates": []})
    assert nothing[0]["grid"] is None and nothing[1]["id"] == "cancel"


# --- the file: inspect, the job ------------------------------------------------------------


def write_hard_dat(path: Path) -> None:
    rng = np.random.default_rng(4)
    n = 3000
    x = [(10.0 * (i % 30), 10.0) for i in range(n)]
    z = [(0.0, float(h)) for h in rng.uniform(1.0, 10.0, n)]
    c, d = blocks(x, y=[(10.0 * (i // 30 % 10), 10.0) for i in range(n)], z=z)
    cols = [Column(name, "number", c[:, a].copy()) for a, name in enumerate(("EAST", "NORTH", "RL"))]
    cols += [Column(name, "number", d[:, a].copy()) for a, name in enumerate(("_EAST", "_NORTH", "_RL"))]
    write_dat(path, cols)


def test_inspect_gives_the_reason_and_the_choices(project) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    path = project.tmp / "hard.dat"
    write_hard_dat(path)
    info = obf.inspect_blockfile(path)
    err = info["error"]
    assert err["code"] == "blocks_unaligned" and info["grid"] is None
    assert err["diag"]["causes"] == ["off_lattice"] and err["diag"]["columns"]["x"] == "R 8"
    assert [c["id"] for c in err["choices"]] == ["grid", "cancel"] and err["count"] > 0
    entry = obf.inspect_entry(path)
    assert entry["blocks"]["error"]["diag"]["rows"] == info["rows"] and entry["totals"]["block_models"] == 0


def test_the_job_refusal_carries_diag_and_choices_into_the_failure(
    project,  # noqa: F811
) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "hard.dat"
    write_hard_dat(path)
    opts = obf.BlockOptions.of(None)
    with pytest.raises(ApiError) as e:
        obf.import_model(obf.open_source(path, opts), opts, engine=project.engine, store=project.store,
                         workdir=project.tmp / "work", name="М", label=path.name, tick=lambda f, m: None)  # fmt: skip
    assert e.value.code == "blocks_unaligned" and e.value.extra["diag"]["causes"] == ["off_lattice"]
    assert e.value.extra["diag"]["rows"] == 3000 and [c["id"] for c in e.value.extra["choices"]] == ["grid", "cancel"]
    failure = _failure(path, FormatError(e.value.code, e.value.message, e.value.hint, e.value.extra))
    assert failure["code"] == "blocks_unaligned" and failure["file"] == "hard.dat" and "diag" in failure
    assert FormatError("broken", "m").extra == {} and FormatError("broken", "m").as_dict()["code"] == "broken"
    assert _failure(path, FormatError("broken", "m", "h")) == {"path": str(path), "file": "hard.dat", "code": "broken",
                                                               "message": "m", "hint": "h"}  # fmt: skip


def test_the_job_writes_a_failure_with_the_diagnosis(project) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """`write_blockfile` (the body of the job for a block file) turns the refusal into a `FormatError` that keeps
    `extra`; the failure of the journal and of `failed[]` carries it."""
    path = project.tmp / "hard.dat"
    write_hard_dat(path)
    log: list[tuple[str, str]] = []
    ctx = SimpleNamespace(project_folder=project.folder, id=7, log=lambda text, level: log.append((text, level)))
    group = SimpleNamespace(main=path)
    with pytest.raises(FormatError) as e:
        obf.write_blockfile(ctx, project.engine, project.store, group, None, lambda f, m: None, None)
    assert e.value.code == "blocks_unaligned" and e.value.extra["diag"]["causes"] == ["off_lattice"]
    assert [c["id"] for c in e.value.extra["choices"]] == ["grid", "cancel"] and e.value.as_dict()["count"] > 0
    failure = _failure(path, e.value)
    assert failure["file"] == "hard.dat" and failure["diag"]["rows"] == 3000 and failure["choices"][1]["id"] == "cancel"
    assert json.dumps(failure, ensure_ascii=False)  # whole JSON: it goes into the result of the job


def test_a_wrong_angle_offers_the_grid_by_hand_too() -> None:
    from tests.test_blocks_infer import octree_cells, project_of

    c, d = project_of(octree_cells(), phi=30.0)
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(rotation=20.0))
    assert e.value.code == "rotation_mismatch" and "diag" not in e.value.extra
    ch = e.value.extra["choices"]
    assert [x["id"] for x in ch] == ["grid", "cancel"] and ch[0]["grid"]["rotation"] == 20.0
    assert ch[0]["grid"]["size"] == [8.0, 8.0, 4.0] and ch[0]["grid"]["div"] == [8, 4, 4]


def test_the_type_of_the_columns_for_the_diagnosis() -> None:
    header = SimpleNamespace(fields=[Field("X", "R", 8, 2), Field("DX", "N", 10, 2), Field("Y", "F", 4, 0)])
    got = column_types(SimpleNamespace(header=header), {"x": "X", "dx": "DX", "y": "Y", "z": None, "dy": ""})
    assert got == {"x": "R 8", "dx": "N 10.2", "y": "F 4"}
    assert column_types(SimpleNamespace(), {"x": "A"}) == {"x": "число"}
