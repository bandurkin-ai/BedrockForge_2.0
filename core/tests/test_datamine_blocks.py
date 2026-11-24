"""Datamine block models `.dm`, `.fbm` (ТЗ 7.31, 7.35 Д-13, 8.7.2; ADR 0023 decisions 5–6; question № 64).

References and tolerances:
  * «Для BF» (`tests/data/real/Для BF.zip`: the same model of 169 070 blocks as `.dm`, `.fbm` and Micromine `.DAT`) —
    the three files give the same blocks: centres within 1e−6 м (they are equal to the bit), values exactly; the
    `IJK` of the `.dm` (Z the fastest, then Y, then X, from 0) comes back from the cells the importer stored;
  * synthetic files — built byte by byte by `tests/formats/dm_builder.py` (not by the reader): sub-blocks and a turn
    (ANGLEn about ROTAXISn, «предварительно») come back as the centres they were made from within 1e−9 м (Т8.7-4);
  * the empty value: −1e30 of Datamine is NaN in the file itself, −99 is NaN when the user names it, 0 stays 0.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.db import dialect
from bf_core.formats import blocks_text as bt
from bf_core.formats import datamine as dm
from bf_core.formats import datamine_blocks as dmb
from bf_core.formats.blocks_text import BlockChunk
from bf_core.formats.errors import FormatError
from bf_core.importing import object_blockfile as obf
from bf_core.storage.blobs import BlobStore
from tests.formats.dm_builder import ABSENT, TRACE, F, build, build_numbers
from tests.test_blocks_infer import DIV, ORIGIN, PARENT, expected_rows, octree_cells, project_of
from tests.test_import_blocks import load, run, same

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "Для BF.zip"
ROT = 30.0
COUNTS = (4, 3, 2)  # the parents of `octree_cells`


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Блоки", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine, store=BlobStore(folder), tmp=tmp_path)
    engine.dispose()


def ijk_of(counts: tuple[int, int, int], i: Any, j: Any, k: Any) -> np.ndarray:
    """The IJK of Datamine: the cell (i, j, k) from 0, Z the fastest, then Y, then X; the first cell is 0."""
    return (np.asarray(i) * counts[1] + np.asarray(j)) * counts[2] + np.asarray(k) + 0.0


def read_all(src: bt.BlockSource, want: list[str] | None = None, rows: int = 50_000) -> dict[str, np.ndarray]:
    parts = list(src.chunks(want, rows))
    names = want or src.names
    return {n: np.concatenate([c.numbers[n] for c in parts]) for n in names if parts and n in parts[0].numbers}


# --- the owner's model: three files, one set of blocks -----------------------------------------------------------


@pytest.fixture(scope="module")
def real(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not REAL.is_file():
        pytest.skip("tests/data/real/Для BF.zip is not here")
    folder = tmp_path_factory.mktemp("datamine_blocks")
    out: dict[str, Path] = {}
    with zipfile.ZipFile(REAL) as archive:
        for info in archive.infolist():
            suffix = Path(info.filename).suffix.lower()
            out[suffix] = folder / f"model{suffix}"
            with archive.open(info) as src, out[suffix].open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return out


def test_real_the_source_of_a_dm_and_an_fbm(real) -> None:  # type: ignore[no-untyped-def]
    fields = ["Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"]
    for suffix, counts in ((".dm", (443, 558, 102)), (".fbm", (445, 560, 104))):
        src = dmb.open_blocks(real[suffix])
        assert src.format == suffix[1:] and src.rows == 169_070 and src.encoding == "cp1251"
        assert src.names == ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", *fields]  # no IJK, no constants of the grid
        assert set(src.kinds.values()) == {"number"}
        h = src.hint
        assert h is not None and h.corner == (23436265.92, 5988122.53, -157.14) and h.counts == counts
        assert h.size is None and h.rotation == 0.0 and not h.notes and not src.notes
        assert dmb.is_block_model(real[suffix])


def test_real_the_three_files_are_the_same_blocks_to_the_bit(real) -> None:  # type: ignore[no-untyped-def]
    dat = bt.open_block_file(real[".dat"])
    d = read_all(dat, ["EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", "Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"])
    assert len(d["EAST"]) == 169_070
    for suffix in (".dm", ".fbm"):
        got = read_all(dmb.open_blocks(real[suffix]))
        assert len(got["XC"]) == 169_070
        pairs = (("XC", "EAST"), ("YC", "NORTH"), ("ZC", "RL"), ("XINC", "_EAST"), ("YINC", "_NORTH"), ("ZINC", "_RL"),
                 ("Fe_m_pc", "Fe_m_pc"), ("Fe_o_pc", "Fe_o_pc"), ("1 - Insi", "1 - Insi"), ("1 Filter", "1 Filter"))
        for a, b in pairs:
            assert same(got[a], d[b]), a  # the values are exact (−99 is still −99: nothing is emptied here)
        assert np.abs(got["XC"] - d["EAST"]).max() == 0.0  # «1e−6 м, точно»: the centres are equal to the bit


def test_real_the_dm_ijk_is_the_cell_the_importer_stored(real, project) -> None:  # type: ignore[no-untyped-def]
    ijk = {s: np.concatenate([c.columns["IJK"] for c in dm.iter_chunks(real[s], fields=["IJK"])])
           for s in (".dm", ".fbm")}  # fmt: skip
    assert ijk[".dm"][0] == 1081.0 and ijk[".fbm"][0] == 1101.0
    cells: dict[str, tuple[np.ndarray, ...]] = {}
    for suffix, counts in ((".dm", (443, 558, 102)), (".fbm", (445, 560, 104))):
        res = run(project, real[suffix], {"empty": [-99]}, name=f"m{suffix}")
        got = load(project, res)
        assert tuple(got["grid"].counts) == counts and res.rows == res.parents == 169_070
        i, j, k = geo.row_ijk(got["grid"], got["p"])
        cells[suffix] = (i, j, k)
        # the cells are stored by P, the IJK of the file is in the order of the file: compare as sets
        assert np.array_equal(np.sort(ijk_of(counts, i, j, k)), np.sort(ijk[suffix]))
    assert all(np.array_equal(a, b) for a, b in zip(cells[".dm"], cells[".fbm"], strict=True))  # the same cells
    # the `.fbm` numbered with the grid of the `.dm` is the IJK of the `.dm`
    assert np.array_equal(np.sort(ijk_of((443, 558, 102), *cells[".fbm"])), np.sort(ijk[".dm"]))


def test_real_the_import_of_dm_fbm_and_dat_gives_the_same_model(real, project) -> None:  # type: ignore[no-untyped-def]
    models = {}
    for suffix in (".dm", ".fbm", ".dat"):
        res = run(project, real[suffix], {"empty": [-99]}, name=f"model{suffix}")
        models[suffix] = load(project, res)
        assert res.rows == res.parents == 169_070 and not models[suffix]["info"]["sub"]
        assert models[suffix]["grid"].size == (10.0, 10.0, 10.0) and models[suffix]["grid"].rotation == 0.0
        assert np.isnan(models[suffix]["values"]["Fe_m_pc"]).sum() == 90  # the 90 blocks of −99
        assert np.isnan(models[suffix]["values"]["Fe_o_pc"]).sum() == 90
        assert models[suffix]["info"]["checks"]["constant"] == ["1 Filter"]
    ref = models[".dat"]
    c_ref = geo.centres(ref["grid"], ref["p"], None, None)
    for suffix in (".dm", ".fbm"):
        m = models[suffix]
        assert tuple(m["grid"].corner) == (23436265.92, 5988122.53, -157.14)
        c = geo.centres(m["grid"], m["p"], None, None)
        assert len(c) == len(c_ref) and np.abs(c - c_ref).max() <= 1e-6
        for name in ("Fe_m_pc", "Fe_o_pc", "1 - Insi"):
            # the fields come back in the order of the cells: the cells are the same, so the arrays are too
            assert same(m["values"][name], ref["values"][name])


def test_real_the_inspection_of_a_dm_tells_the_grid_and_the_empty_code(real) -> None:  # type: ignore[no-untyped-def]
    info = obf.inspect_blockfile(real[".dm"])
    assert info["format"] == "dm" and info["rows"] == 169_070 and info["rows_exact"] and info["error"] is None
    assert info["grid"]["counts"] == [443, 558, 102] and info["grid"]["corner"] == [23436265.92, 5988122.53, -157.14]
    assert info["check3"]["ok"] and info["empty_candidates"] == [-99.0]
    assert [c["name"] for c in info["columns"]][-4:] == ["Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"]


# --- synthetic: the model of the dm_builder -------------------------------------------------------------------------


def grid_fields(corner=ORIGIN, counts=COUNTS, *, angle: float | None = None, axis: float = 3.0) -> list[F]:
    out = [F(n, default=v, implicit=True) for n, v in zip(("XMORIG", "YMORIG", "ZMORIG"), corner, strict=True)]
    out += [F(n, default=float(v), implicit=True) for n, v in zip(("NX", "NY", "NZ"), counts, strict=True)]
    if angle is not None:
        out += [F("ANGLE1", default=angle, implicit=True), F("ROTAXIS1", default=axis, implicit=True)]
    return out


def octree_dm(
    path: Path, phi: float = 0.0, *, angle: float | None = None, axis: float = 3.0, layout: str = "dm",
    stored_grid: bool = False, empty: dict[int, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, list]:
    """The sub-block model of `test_blocks_infer` as a `.dm`: IJK, XC, YC, ZC, XINC, YINC, ZINC, AU (+ the constants
    of the grid, implicit or stored in every record); the centres are those of a model turned by `phi`."""
    cells = octree_cells()
    c, d = project_of(cells, ORIGIN, phi)
    au = np.arange(len(cells), dtype=np.float64) * 0.25
    for row, value in (empty or {}).items():
        au[row] = value
    ijk = ijk_of(COUNTS, [x[0] for x in cells], [x[1] for x in cells], [x[2] for x in cells])
    fields = [F("IJK"), F("XC"), F("YC"), F("ZC"), F("XINC"), F("YINC"), F("ZINC"), F("AU")]
    records = [[ijk[r], *c[r], *d[r], au[r]] for r in range(len(cells))]
    consts = grid_fields(angle=angle, axis=axis)
    if stored_grid:
        fields += [F(f.name) for f in consts]
        for r in records:
            r += [float(f.default) for f in consts]
        consts = []
    path.write_bytes(build([*fields, *consts], records, layout=layout))
    return c, au, cells


def check_octree(project, path: Path, c: np.ndarray, au: np.ndarray, cells: list, blocks: dict | None = None,
                 phi: float = 0.0) -> Any:  # fmt: skip
    res = run(project, path, blocks)
    got = load(project, res)
    p, s, w = expected_rows(cells)
    order = np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))
    assert np.array_equal(got["p"], p[order]) and np.array_equal(got["s"], s[order])
    assert np.array_equal(got["w"], w[order])
    back = geo.centres(got["grid"], got["p"], got["s"], got["w"])
    assert np.abs(back - c[order]).max() <= 1e-9  # Т8.7-4
    assert got["grid"].rotation == phi and tuple(got["grid"].counts) == COUNTS
    assert tuple(got["grid"].size) == PARENT and got["grid"].sub.div == DIV
    assert same(got["values"]["AU"], au[order]) and got["info"]["checks"]["overlaps"]["n"] == 0
    return got


@pytest.mark.parametrize("layout", ["dm", "fbm"])
def test_subblocks_come_back_as_the_cells_they_were_made_from(project, layout) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / f"sub.{layout}"
    c, au, cells = octree_dm(path, layout=layout)
    src = dmb.open_blocks(path)
    assert src.format == layout and src.rows == len(cells) and "IJK" not in src.names and "NX" not in src.names
    assert src.names == ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU"]
    assert src.hint is not None and src.hint.corner == ORIGIN and src.hint.counts == COUNTS and src.hint.size is None
    got = check_octree(project, path, c, au, cells)
    assert got["info"]["sub"] and got["info"]["parents"] == len({(i, j, k) for i, j, k, *_ in cells})
    info = obf.inspect_blockfile(path)
    assert info["error"] is None and info["sub"] and info["check3"]["ok"]


@pytest.mark.parametrize("angle", [30.0, -30.0, 90.0, 270.0])
def test_a_turn_about_z_gives_the_centres_of_the_file(project, angle) -> None:  # type: ignore[no-untyped-def]
    """ANGLE1 about ROTAXIS1 = 3, as the help of Studio says: p = O + R_z(φ)·q."""
    path = project.tmp / "turned.dm"
    phi = (angle + 180.0) % 360.0 - 180.0 if angle != 270.0 else -90.0  # the angle of the grid is in (−180; 180]
    c, au, cells = octree_dm(path, phi=angle, angle=angle)
    src = dmb.open_blocks(path)
    assert src.hint is not None and src.hint.rotation == phi and "предварительный" in src.hint.notes[0]
    assert any("предварительный" in n for n in src.notes)
    got = check_octree(project, path, c, au, cells, phi=phi)
    assert np.abs(np.array(got["grid"].corner) - ORIGIN).max() <= 1e-9


def test_the_turn_stored_in_every_record_is_the_implicit_one(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "stored.dm"
    c, au, cells = octree_dm(path, phi=ROT, angle=ROT, stored_grid=True)
    src = dmb.open_blocks(path)
    assert src.names == ["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU"]  # XMORIG … ROTAXIS1 are the grid, not fields
    assert src.hint is not None and src.hint.corner == ORIGIN and src.hint.rotation == ROT
    check_octree(project, path, c, au, cells, phi=ROT)
    list(src.chunks(rows=4))
    assert all("меняется" not in n for n in src.notes)


def test_a_grid_constant_that_changes_is_said_and_the_first_record_wins(project) -> None:  # type: ignore[no-untyped-def]
    fields = [F("XC"), F("YC"), F("ZC"), F("XINC"), F("YINC"), F("ZINC"), F("XMORIG"), F("YMORIG"), F("ZMORIG")]
    rows = [[5.0 + 10 * r, 5.0, 5.0, 10.0, 10.0, 10.0, 0.0 if r < 3 else 1.0, 0.0, 0.0] for r in range(5)]
    path = project.tmp / "moved.dm"
    path.write_bytes(build(fields, rows))
    src = dmb.open_blocks(path)
    assert src.hint is not None and src.hint.corner == (0.0, 0.0, 0.0)  # the first record
    list(src.chunks(rows=2))
    assert any("«XMORIG»" in n and "2 записях" in n for n in src.notes)


def test_a_wrong_sign_of_the_turn_is_caught_by_the_check_of_three(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "sign.dm"
    octree_dm(path, phi=-ROT, angle=ROT)  # the centres are made with the opposite sign: the convention is wrong
    info = obf.inspect_blockfile(path)
    assert info["error"] is not None and info["error"]["code"] in ("rotation_mismatch", "blocks_unaligned")
    with pytest.raises(ApiError) as exc:
        run(project, path)
    assert exc.value.status == 422 and exc.value.message and exc.value.hint


def test_a_turn_about_x_or_y_is_refused(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "x.dm"
    octree_dm(path, angle=15.0, axis=1.0)
    with pytest.raises(ApiError) as exc:
        dmb.open_blocks(path)
    assert exc.value.code == "rotation_unsupported" and exc.value.status == 422


def test_a_regular_model_with_the_size_as_a_constant_of_the_file(project) -> None:  # type: ignore[no-untyped-def]
    counts, size, corner = (5, 4, 3), (10.0, 20.0, 5.0), (1000.5, 2000.25, -50.0)
    cells = [(i, j, k) for k in range(counts[2]) for j in range(counts[1]) for i in range(counts[0]) if (i + j + k) % 4]
    ijk = np.array(cells, dtype=np.float64)
    centre = np.column_stack([corner[a] + (ijk[:, a] + 0.5) * size[a] for a in range(3)])
    fields = [F("IJK"), F("XC"), F("YC"), F("ZC"), F("AU"), F("DENSITY", default=2.6, implicit=True),
              F("ROCK", "A", 8, "ore", implicit=True), F("CODE", "A", 4),
              *(F(n, default=v, implicit=True) for n, v in zip(("XINC", "YINC", "ZINC"), size, strict=True)),
              *grid_fields(corner, counts)]  # fmt: skip
    au = np.arange(len(cells)) * 0.5
    au[4] = -99.0
    au[7] = ABSENT
    rows = [[ijk_of(counts, *cells[r]), *centre[r], au[r], "AB" if r % 2 else ""] for r in range(len(cells))]
    path = project.tmp / "regular.dm"
    path.write_bytes(build(fields, rows))
    src = dmb.open_blocks(path)
    assert src.names == ["XC", "YC", "ZC", "AU", "DENSITY", "ROCK", "CODE"]
    assert src.kinds == {"XC": "number", "YC": "number", "ZC": "number", "AU": "number", "DENSITY": "number",
                         "ROCK": "text", "CODE": "text"}  # fmt: skip
    assert src.hint is not None and src.hint.size == size and src.hint.counts == counts and src.hint.corner == corner
    part = next(src.chunks(None, 1000))
    assert part.rows == len(cells) and np.all(part.numbers["DENSITY"] == 2.6)
    assert part.texts["ROCK"].labels == ["ore"] and set(part.texts["ROCK"].codes) == {0}
    code = part.texts["CODE"]
    assert code.labels == ["AB"] and code.codes[0] == -1 and code.codes[1] == 0
    assert np.isnan(part.numbers["AU"][7]) and part.numbers["AU"][4] == -99.0 and part.numbers["AU"][0] == 0.0
    res = run(project, path, {"empty": [-99]})
    got = load(project, res)
    assert tuple(got["grid"].size) == size and res.rows == len(cells) and not got["info"]["sub"]
    order = np.lexsort((centre[:, 0], centre[:, 1], centre[:, 2]))
    want = au[order].copy()
    want[(want == -99.0) | (want == ABSENT)] = np.nan
    assert same(got["values"]["AU"], want)
    assert np.abs(geo.centres(got["grid"], got["p"], None, None) - centre[order]).max() <= 1e-9
    assert np.isnan(got["values"]["AU"]).sum() == 2


def test_empty_values_are_nan_never_zero(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "empty.dm"
    c, au, cells = octree_dm(path, empty={2: -99.0, 5: ABSENT, 9: 0.0, 11: TRACE})
    src = dmb.open_blocks(path)
    values = read_all(src, ["AU"])["AU"]
    assert np.isnan(values[5]) and values[2] == -99.0 and values[9] == 0.0 and values[11] == TRACE  # −99 is data here
    assert any("«TR»" in n and "«AU»" in n for n in src.notes)
    assert bt.empty_to_nan(values, [-99.0]) == 1 and np.isnan(values[2]) and values[9] == 0.0
    got = load(project, run(project, path, {"empty": [-99]}))
    p, s, _ = expected_rows(cells)
    ordered = np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))
    column = got["values"]["AU"]
    assert np.isnan(column[np.flatnonzero(ordered == 2)[0]]) and np.isnan(column[np.flatnonzero(ordered == 5)[0]])
    assert column[np.flatnonzero(ordered == 9)[0]] == 0.0 and np.isnan(column).sum() == 2
    info = obf.inspect_blockfile(path)
    assert info["empty_candidates"] == [-99.0]


def test_portions_the_limit_and_the_wanted_columns(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "portions.dm"
    octree_dm(path)
    src = dmb.open_blocks(path)
    whole = read_all(src, rows=10_000)
    ticks: list[float] = []
    for rows in (1, 7, 64):
        parts = list(src.chunks(["XC", "AU"], rows, lambda f, m: ticks.append(f)))
        assert all(isinstance(p, BlockChunk) and set(p.numbers) == {"XC", "AU"} for p in parts)
        # a portion is whole pages of the file (about `rows` records, never less than a page): they follow each other
        assert parts[0].first == 0 and all(a.first + a.rows == b.first for a, b in zip(parts, parts[1:], strict=False))
        assert sum(p.rows for p in parts) == src.rows
        for name in ("XC", "AU"):
            assert same(np.concatenate([p.numbers[name] for p in parts]), whole[name])
    assert ticks and ticks[-1] == 1.0 and all(0.0 <= t <= 1.0 for t in ticks)
    assert sum(p.rows for p in src.chunks(["XC"], 10, limit=25)) == 25 and not list(src.chunks(["XC"], 10, limit=0))


def test_windows_of_the_map_give_the_same_rows(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The file is mapped a window at a time (the memory of a 20 million model): the windows join without a seam."""
    path = project.tmp / "windows.dm"
    octree_dm(path)
    src = dmb.open_blocks(path)
    whole = read_all(src, rows=10_000)
    monkeypatch.setattr(dmb, "WINDOW_BYTES", src.header.page)  # a page: a few windows in this small table
    parts = list(src.chunks(None, 5))
    assert len(parts) >= 3 and parts[0].first == 0
    assert all(a.first + a.rows == b.first for a, b in zip(parts, parts[1:], strict=False))
    for name in ("XC", "ZINC", "AU"):
        assert same(np.concatenate([p.numbers[name] for p in parts]), whole[name])
    assert sum(p.rows for p in src.chunks(["XC"], 5, limit=40)) == 40


def test_not_a_block_model_is_refused_with_a_hint(project) -> None:  # type: ignore[no-untyped-def]
    tri = project.tmp / "tri.dm"
    tri.write_bytes(build([F("TRIANGLE"), F("PID1"), F("PID2"), F("PID3")], [[1, 1, 2, 3]]))
    nothing = project.tmp / "table.dm"
    nothing.write_bytes(build([F("A"), F("B")], [[1, 2]]))
    empty = project.tmp / "empty.fbm"
    empty.write_bytes(build([F("XC"), F("YC"), F("ZC"), F("XINC")], [], layout="fbm"))
    for path, code in ((tri, "not_block_model"), (nothing, "not_block_model"), (empty, "empty_file")):
        with pytest.raises(FormatError) as exc:
            dmb.open_blocks(path)
        assert exc.value.code == code and exc.value.message and exc.value.hint
        assert not dmb.is_block_model(path) or code == "empty_file"
    junk = project.tmp / "junk.dm"
    junk.write_bytes(b"not a table" * 1000)
    with pytest.raises(FormatError):
        dmb.open_blocks(junk)
    assert not dmb.is_block_model(junk) and not dmb.is_block_model(project.tmp / "absent.dm")
    assert not dmb.is_block_model(project.tmp / "x.csv")


def test_the_wizard_can_choose_the_grid_and_the_columns_of_a_dm(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "wizard.dm"
    c, au, cells = octree_dm(path)
    opts = obf.BlockOptions.of({"grid": {"corner": list(ORIGIN), "size": list(PARENT), "counts": list(COUNTS)}})
    assert obf.open_source(path, opts).names[-1] == "AU"
    check_octree(project, path, c, au, cells, {"grid": {"size": list(PARENT)}})


def test_single_precision_tables_are_read_too(project) -> None:  # type: ignore[no-untyped-def]
    fields = [F("XC"), F("YC"), F("ZC"), F("XINC"), F("YINC"), F("ZINC"), F("AU")]
    rows = [[5.0 + 10 * i, 5.0, 5.0, 10.0, 10.0, 10.0, 1.5 * i] for i in range(6)]
    path = project.tmp / "single.dm"
    path.write_bytes(build(fields, rows, word=4))
    src = dmb.open_blocks(path)
    got = read_all(src)
    assert same(got["AU"], np.array([r[-1] for r in rows])) and src.hint is None
    assert src.rows == 6 and src.header.precision == "single"


def test_a_million_rows_stream_in_portions(tmp_path: Path) -> None:
    """A table of 1,2 million blocks (built with numpy) goes through in portions and keeps its values to the bit."""
    n = 1_200_000
    rng = np.random.default_rng(3)
    k = np.arange(n)
    sizes = [np.full(n, v) for v in (10.0, 10.0, 5.0)]
    table = np.column_stack([k + 1.0, 500.0 + k % 120 * 10, 7000.0 + k // 120 % 100 * 10, -200.0 + k // 12_000 * 5.0,
                             *sizes, rng.random(n), rng.random(n)])
    fields = [F("IJK"), F("XC"), F("YC"), F("ZC"), F("XINC"), F("YINC"), F("ZINC"), F("AU"), F("CU"),
              *grid_fields((495.0, 6995.0, -202.5), (200, 200, 200))]
    path = tmp_path / "big.dm"
    path.write_bytes(build_numbers(fields, table))
    src = dmb.open_blocks(path)
    assert src.rows == n
    seen = 0
    for chunk in src.chunks(["XC", "AU"], 1 << 18):
        assert chunk.first == seen and len(chunk.numbers["XC"]) == chunk.rows
        assert np.array_equal(chunk.numbers["AU"], table[seen : seen + chunk.rows, 7])
        seen += chunk.rows
    assert seen == n
