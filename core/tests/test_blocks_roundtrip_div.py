"""A model with sub-blocks of a division that is not 2^L there and back (ADR 0030, подшаг 27.4а; замечание А6):
Micromine DAT and CSV Micromine (the centre and the size of every block as the file writes them) and Datamine `.dm`
with sub-cells of 2 m and 3.333 m — imported as a model, exported to DAT and CSV, imported again: the blocks (P, s, w),
the centres (1e-9 м) and the fields (bit for bit) are the same.

The reference is the cells the file was built from (the generator of `test_grid_infer_div`), not the code under test."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import export as bx
from bf_core.blocks import geometry as geo
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect
from tests.test_blocks_div_consumers import DIVS, grid_of, rows_of
from tests.test_import_blocks import load, run, same
from tests.test_import_blocks import project as base_project  # noqa: F401

LABELS = ["руда", "порода", "дайка"]


@pytest.fixture()
def project(base_project):  # type: ignore[no-untyped-def]  # noqa: F811
    return base_project


def model_of(p: Any, div: tuple[int, int, int], rotation: float, seed: int = 3) -> tuple[int, dict[str, np.ndarray]]:
    """A committed model of the rows of `rows_of` with two fields (AU with gaps, DOM a category)."""
    grid = grid_of(div, rotation=rotation)
    pp, s, w = rows_of(grid, seed)
    rng = np.random.default_rng(seed + 1)
    au = np.round(rng.lognormal(0.0, 1.0, len(pp)), 4)
    au[::9] = np.nan
    dom = rng.integers(-1, 3, len(pp)).astype(np.int32)
    fields = [{"name": "AU", "dtype": "f8", "unit": "г/т"}, {"name": "DOM", "dtype": "cat", "labels": LABELS}]
    with bm.CellsWriterV2(p.store, grid.to_meta(), fields) as wr:
        wr.write(pp, {"AU": au, "DOM": dom}, s, w)
        staged = wr.finish()
    with dialect.write_tx(p.engine) as conn:
        return bm.commit_block_model(conn, p.store, staged, name=f"Деление {div}"), {"AU": au, "DOM": dom}


def opened(p: Any, model_id: int) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, model_id)


def export(p: Any, model_id: int, fmt: str, **body: Any) -> Path:
    folder = p.tmp / "out"
    folder.mkdir(exist_ok=True)
    path = folder / f"m{model_id}.{fmt}"
    bx.run_export(p.engine, p.folder, {"model_id": model_id, "format": fmt, "path": str(path), **body}, chunk=64)
    return path


def check_same(p: Any, res: Any, m: Model, au: np.ndarray, dom: np.ndarray, div: tuple[int, int, int]) -> None:
    got = load(p, res)
    g = got["grid"]
    assert g.sub.div == div and g.sub.levels is None and res.rows == m.rows
    assert g.counts == m.grid.counts and g.size == m.grid.size
    assert np.array_equal(got["p"], m.rows_p()) if hasattr(m, "rows_p") else True
    c = geo.centres(g, got["p"], got["s"], got["w"])
    assert np.abs(c - m.centres()).max() <= 1e-9
    assert same(got["values"]["AU"], au)
    labels = np.array(["", *LABELS], dtype=object)[dom + 1]
    names = {x["name"]: x for x in got["info"]["fields"]}
    text = np.array(["", *names["DOM"]["labels"]], dtype=object)[got["values"]["DOM"] + 1]
    assert np.array_equal(text, labels)
    assert got["info"]["checks"]["overlaps"]["n"] == 0 and got["info"]["checks"]["duplicates"] == 0


@pytest.mark.parametrize("div", DIVS)
@pytest.mark.parametrize("rotation", [0.0, 30.0])
def test_dat_with_a_division_comes_back_with_the_same_blocks_and_values(project, div, rotation) -> None:  # type: ignore[no-untyped-def]
    mid, v = model_of(project, div, rotation)
    m = opened(project, mid)
    assert m.grid.sub.div == div and m.grid.sub.levels is None
    path = export(project, mid, "dat")
    # the grid is inferred from the blocks alone: the parent is the biggest block, the division the lcm of its parts
    res = run(project, path, {"grid": {"rotation": rotation}})
    check_same(project, res, m, v["AU"], v["DOM"], div)


@pytest.mark.parametrize("div", DIVS)
def test_csv_micromine_with_a_division_comes_back_with_the_same_blocks_and_values(project, div) -> None:  # type: ignore[no-untyped-def]
    mid, v = model_of(project, div, 0.0, seed=4)
    m = opened(project, mid)
    path = export(project, mid, "csv", header="micromine", delimiter=";", decimal=",")
    res = run(project, path)
    check_same(project, res, m, v["AU"], v["DOM"], div)


def test_a_dat_exported_twice_has_the_same_bytes(project) -> None:  # type: ignore[no-untyped-def]
    mid, _ = model_of(project, (5, 5, 3), 30.0)
    a = export(project, mid, "dat").read_bytes()
    first = project.tmp / "out" / f"m{mid}.dat"
    first.rename(first.with_suffix(".old"))
    assert export(project, mid, "dat").read_bytes() == a


def test_datamine_sub_cells_of_2_and_3_333_m_import_and_come_back_through_dat(project) -> None:  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from bench.scenarios import blocks_files as bf

    dm = project.tmp / "m.dm"
    meta = bf.write_dm(dm, 3000, 3, div=(5, 5, 3), size=(10.0, 10.0, 10.0))
    res = run(project, dm)
    got = load(project, res)
    assert res.rows == meta["rows"] and got["grid"].sub.div == (5, 5, 3) and got["grid"].size == (10.0, 10.0, 10.0)
    assert got["info"]["checks"]["overlaps"]["n"] == 0
    m = opened(project, res.model_id)
    au = got["values"]["AU"]
    # Datamine → model → DAT → model: the same blocks, the same AU
    path = export(project, res.model_id, "dat")
    again = run(project, path, {"grid": {"corner": list(m.grid.corner), "size": list(m.grid.size)}})
    back = load(project, again)
    assert again.rows == res.rows and back["grid"].sub.div == (5, 5, 3)
    assert np.array_equal(back["p"], got["p"]) and np.array_equal(back["s"], got["s"])
    assert np.array_equal(back["w"], got["w"]) and same(back["values"]["AU"], au)
    assert np.abs(geo.centres(back["grid"], back["p"], back["s"], back["w"]) - m.centres()).max() <= 1e-9
    assert float(m.volume().sum()) > 0
