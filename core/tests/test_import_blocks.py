"""Import of a block file — Micromine DAT, CSV, CSV Micromine (ТЗ 7.31, 8.7.2; ADR 0023 decisions 5–7; Д-13): the roles
and the grid in the inspection, the codes of «пусто», **Т8.7-4** (a turned model: the centres of the file come back
to 1e−9 м), the refusals (blocks off the grid, a wrong convention), duplicates and overlaps, text fields, the portions
and the cancel, and the owner's model («Для BF»: 169 070 blocks)."""

from __future__ import annotations

import math
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.blocks import store as bm
from bf_core.blocks.schema import GridV2
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats import blocks_text as bt
from bf_core.formats.errors import FormatError
from bf_core.formats.micromine import write_dat
from bf_core.formats.table import Column
from bf_core.importing import object_blockfile as obf
from bf_core.jobs.context import JobCancelled
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_infer import DIV, ORIGIN, PARENT, expected_rows, octree_cells, project_of

REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "Для BF.zip"
ROT = 30.0


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    folder = create_folder(tmp_path / "projects", "Блоки", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    yield SimpleNamespace(folder=folder, engine=engine, store=BlobStore(folder), tmp=tmp_path)
    engine.dispose()


def run(p: Any, path: Path, blocks: dict[str, Any] | None = None, *, tick: Any = None, name: str = "Модель",
        **kw: Any) -> obf.Imported:  # fmt: skip
    opts = obf.BlockOptions.of(blocks, **kw)
    return obf.import_model(
        obf.open_source(path, opts), opts, engine=p.engine, store=p.store, workdir=p.tmp / "work", name=name,
        label=path.name, tick=tick or (lambda f, m: None),
    )  # fmt: skip


def load(p: Any, res: obf.Imported) -> dict[str, Any]:
    """The stored model as arrays: p, s, w (None without sub-blocks), the fields, the meta."""
    import pyarrow.parquet as pq

    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, res.model_id)
        table = pq.read_table(p.store.path_of(bm.cells_sha(conn, res.model_id)))
        idx, values = bm.read_cells(conn, p.store, res.model_id)
    sub = info["sub"]
    s = np.column_stack([table.column(c).to_numpy() for c in ("sx", "sy", "sz")]) if sub else None
    w = np.column_stack([table.column(c).to_numpy() for c in ("wx", "wy", "wz")]) if sub else None
    return {"p": idx, "s": s, "w": w, "values": values, "info": info, "grid": GridV2.of(info["grid"])}


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), equal_nan=True))


# --- the synthetic models ----------------------------------------------------------------------------------------


def regular(counts=(6, 5, 4), size=(10.0, 20.0, 5.0), corner=(1000.5, 2000.25, -50.0), skip=7):
    """A regular model: the centres, the sizes and two fields of the occupied cells (every `skip`-th cell is empty)."""
    cells = [(i, j, k) for k in range(counts[2]) for j in range(counts[1]) for i in range(counts[0])
             if (i + 2 * j + 3 * k) % skip != 0]  # fmt: skip
    ijk = np.array(cells, dtype=np.float64)
    centre = np.column_stack([corner[a] + (ijk[:, a] + 0.5) * size[a] for a in range(3)])
    au = np.round(0.1 + ijk[:, 0] + 0.5 * ijk[:, 1] + 0.01 * ijk[:, 2], 3)
    au[3], au[11] = -99.0, -99.0  # the code of an empty value
    rock = np.array(["oxide", "sulfide", "waste"], dtype=object)[(ijk[:, 0].astype(int) + ijk[:, 2].astype(int)) % 3]
    p = np.array([i + counts[0] * (j + counts[1] * k) for i, j, k in cells], dtype=np.uint64)
    return SimpleNamespace(cells=cells, centre=centre, size=np.broadcast_to(size, centre.shape).copy(), au=au,
                           rock=rock, p=p, counts=counts, corner=corner, sz=size)  # fmt: skip


def text(m: Any, *, names=("EAST", "NORTH", "RL", "_EAST", "_NORTH", "_RL", "AU", "ROCK"), sep=";", decimal=",",
         sized=True, extra: list[str] | None = None, types: bool = False) -> str:  # fmt: skip
    """The model as CSV text: a number as «12,5» or «12.5»; with `types` — the lines of a CSV Micromine."""
    def num(v: float) -> str:
        return repr(float(v)).replace(".", decimal)

    cols = names if sized else (names[:3] + names[6:])
    head = [sep.join(cols)]
    if types:
        kinds = ["DOUBLE"] * (len(cols) - 1) + ["CHARACTER"]
        head.append(sep.join(kinds))
        cx = [v for a in range(3) for v in (m.corner[a] + m.sz[a] / 2, m.sz[a])]  # x0, dx, y0, dy, z0, dz
        head.append(sep.join([*(num(v) for v in cx), "CENTRE", *[""] * (len(cols) - 7)]))
    lines = head
    for r in range(len(m.p)):
        row = [num(v) for v in m.centre[r]] + ([num(v) for v in m.size[r]] if sized else [])
        lines.append(sep.join([*row, num(m.au[r]), str(m.rock[r])]))
    return "\n".join(lines) + "\n"


def write_dat_model(path: Path, m: Any) -> None:
    cols = [Column(n, "number", m.centre[:, a].copy()) for a, n in enumerate(("EAST", "NORTH", "RL"))]
    cols += [Column(n, "number", m.size[:, a].copy()) for a, n in enumerate(("_EAST", "_NORTH", "_RL"))]
    import pyarrow as pa

    cols += [Column("AU", "number", m.au.copy()), Column("ROCK", "text", pa.chunked_array([pa.array(list(m.rock))]))]
    write_dat(path, cols)


def check_regular(p: Any, res: obf.Imported, m: Any, au: np.ndarray | None = None) -> dict[str, Any]:
    got = load(p, res)
    g = got["grid"]
    assert g.counts[0] >= m.counts[0] and g.size == tuple(m.sz) and not g.sub.sub and g.rotation == 0.0
    assert res.rows == len(m.p) == res.parents
    c = geo.centres(g, got["p"], None, None)
    order = np.argsort(np.lexsort((m.centre[:, 0], m.centre[:, 1], m.centre[:, 2])))  # by (z, y, x) = by P
    assert np.abs(c - m.centre[np.lexsort((m.centre[:, 0], m.centre[:, 1], m.centre[:, 2]))]).max() <= 1e-9
    assert order.size == len(m.p)
    want = (m.au if au is None else au)[np.lexsort((m.centre[:, 0], m.centre[:, 1], m.centre[:, 2]))]
    assert same(got["values"]["AU"], want)
    return got


# --- DAT, CSV, CSV Micromine: the same model -----------------------------------------------------------------------


def test_dat_regular_model_with_codes_of_empty_and_text(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "model.dat"
    write_dat_model(path, m)
    info = obf.inspect_blockfile(path)
    assert info["format"] == "dat" and info["rows"] == len(m.p) and info["error"] is None
    assert info["roles"] == {"x": "EAST", "y": "NORTH", "z": "RL", "dx": "_EAST", "dy": "_NORTH", "dz": "_RL"}
    assert info["empty_candidates"] == [-99.0] and info["check3"]["ok"] and info["sub"] is False
    assert info["grid"]["corner"] == list(m.corner) and info["grid"]["size"] == list(m.sz)
    by = {f["name"]: f for f in info["fields"]}
    assert by["ROCK"]["kind"] == "category" and by["ROCK"]["role"] == "code" and by["AU"]["role"] == "grade"
    assert by["AU"]["unit"] == "г/т" and info["units"] == {"AU": "г/т"}
    res = run(project, path, {"empty": [-99]})
    want = m.au.copy()
    want[[3, 11]] = np.nan  # −99 is «пусто», never a value or a zero
    got = check_regular(project, res, m, want)
    assert got["info"]["source"] == {"op": "import", "format": "dat", "file": "model.dat"}
    f = {x["name"]: x for x in got["info"]["fields"]}
    assert f["AU"]["unit"] == "г/т" and f["AU"]["role"] == "grade" and f["AU"]["stats"]["missing"] == 2
    assert f["ROCK"]["kind"] == "category" and sorted(f["ROCK"]["labels"]) == ["oxide", "sulfide", "waste"]
    codes = got["values"]["ROCK"]
    assert [f["ROCK"]["labels"][c] for c in codes[:5]] == [m.rock[i] for i in np.argsort(m.p, kind="stable")[:5]]
    assert any("-99" in w and "пустые, не нули" in w for w in got["info"]["warnings"])
    assert got["info"]["checks"]["duplicates"] == 0 and got["info"]["checks"]["empty"] == []


def test_without_the_empty_code_minus_99_stays_a_value(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "m.csv"
    path.write_text(text(m), encoding="utf-8")
    got = check_regular(project, run(project, path), m)  # the codes are the user's: none given — a value
    assert (got["values"]["AU"] == -99.0).sum() == 2 and not np.isnan(got["values"]["AU"]).any()


FORMS = [(";", ",", "cp1251"), (",", ".", "utf-8-sig"), ("\t", ",", "utf-8")]


@pytest.mark.parametrize("sep, decimal, encoding", FORMS)
def test_csv_delimiters_decimals_and_encodings(project, sep, decimal, encoding) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    m.rock[:] = np.where(m.rock == "oxide", "окисленная", m.rock)  # Cyrillic labels: the encoding matters
    path = project.tmp / "m.csv"
    path.write_bytes(text(m, sep=sep, decimal=decimal).encode(encoding))
    info = obf.inspect_blockfile(path)
    assert info["format"] == "csv" and info["roles"]["dz"] == "_RL" and info["error"] is None
    res = run(project, path, {"empty": [-99]})
    want = m.au.copy()
    want[[3, 11]] = np.nan
    got = check_regular(project, res, m, want)
    labels = next(x for x in got["info"]["fields"] if x["name"] == "ROCK")["labels"]
    assert "окисленная" in labels


def test_csv_micromine_three_lines_with_the_grid_and_no_sizes(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "mm.csv"
    path.write_text(text(m, sized=False, types=True), encoding="utf-8")
    src = bt.open_block_file(path)
    assert src.format == "csv_micromine" and src.types is not None and src.kinds["ROCK"] == "text"
    assert src.hint is not None and src.hint.size == m.sz and src.hint.corner == m.corner  # CENTRE → the corner
    info = obf.inspect_blockfile(path)
    assert info["format"] == "csv_micromine" and info["roles"]["dx"] is None and info["grid"]["size"] == list(m.sz)
    got = check_regular(project, run(project, path, {"empty": [-99.0]}), m, np.where(m.au == -99, np.nan, m.au))
    assert got["grid"].corner == m.corner  # the corner of the grid line, not of the first block


def test_the_dat_and_the_csv_of_one_model_are_the_same_model(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    write_dat_model(project.tmp / "a.dat", m)
    (project.tmp / "a.csv").write_text(text(m, decimal="."), encoding="utf-8")
    a = load(project, run(project, project.tmp / "a.dat", name="A"))
    b = load(project, run(project, project.tmp / "a.csv", name="B"))
    assert np.array_equal(a["p"], b["p"]) and same(a["values"]["AU"], b["values"]["AU"])
    assert a["info"]["grid"] == b["info"]["grid"]


def test_a_regular_model_without_a_grid_takes_the_step_of_the_centres(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "r.csv"
    path.write_text(text(m, sized=False), encoding="utf-8")
    res = run(project, path)
    g = load(project, res)["grid"]
    assert g.size == tuple(m.sz) and g.corner == m.corner and g.counts == tuple(m.counts)


# --- the codes of «пусто» ------------------------------------------------------------------------------------------


def test_codes_of_empty_are_the_users_and_zero_is_a_value(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    m.au[:4] = [-99.0, -999.0, 0.0, -99.0]
    m.rock[5] = "-999"  # a code written as text of a text field
    path = project.tmp / "e.csv"
    path.write_text(text(m, decimal="."), encoding="utf-8")
    info = obf.inspect_blockfile(path)
    assert info["empty_candidates"] == [-99.0]  # offered: −99; the others the user types
    got = load(project, run(project, path, {"empty": ["-99", "-999,0"]}))
    au = got["values"]["AU"]
    assert np.isnan(au[[0, 1, 3]]).all() and au[2] == 0.0 and np.isnan(au).sum() == 4  # + the −99 of row 11
    labels = next(x for x in got["info"]["fields"] if x["name"] == "ROCK")["labels"]
    assert "-999" not in labels and (got["values"]["ROCK"] == -1).sum() == 1
    with pytest.raises(ApiError) as exc:
        obf.BlockOptions.of({"empty": ["abc"]})
    assert exc.value.code == "blocks_options"


# --- Т8.7-4: a turned model ---------------------------------------------------------------------------------------


def turned_files(p: Any, phi: float = ROT, centres: str = "project") -> tuple[Path, Path, np.ndarray, np.ndarray, list]:
    cells = octree_cells()
    c, d = project_of(cells, ORIGIN, phi)
    if centres == "model":  # the centres in the unturned frame of the model: q + O
        c, _ = project_of(cells, ORIGIN, 0.0)
    au = np.arange(len(cells), dtype=np.float64) * 0.25
    lines = ["EAST,NORTH,RL,_EAST,_NORTH,_RL,AU", "DOUBLE,DOUBLE,DOUBLE,DOUBLE,DOUBLE,DOUBLE,DOUBLE"]
    rows = [",".join(repr(float(v)) for v in (*c[r], *d[r], au[r])) for r in range(len(cells))]
    csv = p.tmp / f"turned-{phi}-{centres}.csv"
    csv.write_text("\n".join([*lines, *rows]) + "\n", encoding="utf-8")
    dat = p.tmp / f"turned-{phi}-{centres}.dat"
    cols = [Column(n, "number", np.ascontiguousarray(c[:, a])) for a, n in enumerate(("EAST", "NORTH", "RL"))]
    cols += [Column(n, "number", np.ascontiguousarray(d[:, a])) for a, n in enumerate(("_EAST", "_NORTH", "_RL"))]
    write_dat(dat, [*cols, Column("AU", "number", au)])
    return csv, dat, c, au, cells


@pytest.mark.parametrize("kind", ["csv", "dat"])
def test_t874_a_turned_micromine_model_gives_the_centres_of_the_file(project, kind) -> None:  # type: ignore[no-untyped-def]
    csv, dat, c, au, cells = turned_files(project)
    path = csv if kind == "csv" else dat
    grid = {"corner": list(ORIGIN), "rotation": ROT, "size": list(PARENT)}
    info = obf.inspect_blockfile(path, {"blocks": {"grid": grid}})
    assert info["error"] is None and info["sub"] and info["check3"]["ok"] and info["check3"]["dist"] <= 1e-6
    assert info["grid"]["rotation"] == ROT and info["grid"]["div"] == list(DIV)
    res = run(project, path, {"grid": grid})
    got = load(project, res)
    p, s, w = expected_rows(cells)
    order = np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))
    assert np.array_equal(got["p"], p[order]) and np.array_equal(got["s"], s[order])
    assert np.array_equal(got["w"], w[order])
    back = geo.centres(got["grid"], got["p"], got["s"], got["w"])
    assert np.abs(back - c[order]).max() <= 1e-9  # Т8.7-4: 1e-9 м
    assert got["grid"].rotation == ROT and np.abs(np.array(got["grid"].corner) - ORIGIN).max() <= 1e-9
    assert same(got["values"]["AU"], au[order]) and got["info"]["checks"]["overlaps"]["n"] == 0


def test_t874_without_the_origin_the_first_centre_is_the_pivot_and_the_centres_still_agree(project) -> None:  # type: ignore[no-untyped-def]
    csv, _, c, _, cells = turned_files(project)
    res = run(project, csv, {"grid": {"rotation": ROT, "size": list(PARENT)}})
    got = load(project, res)
    p, s, w = expected_rows(cells)
    back = geo.centres(got["grid"], got["p"], got["s"], got["w"])
    assert np.abs(back - c[np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))]).max() <= 1e-6
    assert np.abs(np.array(got["grid"].corner) - ORIGIN).max() <= 1e-6


def test_t874_the_centres_written_in_the_axes_of_the_model(project) -> None:  # type: ignore[no-untyped-def]
    csv, _, _, _, cells = turned_files(project, centres="model")
    grid = {"corner": list(ORIGIN), "rotation": ROT, "size": list(PARENT)}
    res = run(project, csv, {"grid": grid, "centres": "model"})
    got = load(project, res)
    p, s, w = expected_rows(cells)
    q = geo.centres(got["grid"], got["p"], got["s"], got["w"])  # the project axes: O + R·q
    c_proj, _ = project_of(cells, ORIGIN, ROT)
    assert np.abs(q - c_proj[np.lexsort((s[:, 0], s[:, 1], s[:, 2], p))]).max() <= 1e-9


def test_a_wrong_angle_is_refused_by_the_check_of_three_centres(project) -> None:  # type: ignore[no-untyped-def]
    csv, _, _, _, _ = turned_files(project)
    wrong = {"corner": list(ORIGIN), "rotation": 35.0, "size": list(PARENT)}
    info = obf.inspect_blockfile(csv, {"blocks": {"grid": wrong}})
    assert info["error"] is not None and info["error"]["code"] in ("rotation_mismatch", "blocks_unaligned")
    with pytest.raises(ApiError) as exc:
        run(project, csv, {"grid": wrong})
    assert exc.value.code in ("rotation_mismatch", "blocks_unaligned") and exc.value.status == 422
    assert exc.value.message and exc.value.hint
    with dialect.read_tx(project.engine) as conn:  # nothing is left
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0
    assert not list(project.store.tmp_dir.glob("*")) and not (project.tmp / "work").exists()


def test_the_answers_of_the_wizard_are_checked(project) -> None:  # type: ignore[no-untyped-def]
    for blocks in ({"roles": {"q": "A"}}, {"centres": "world"}, {"grid": {"corner": [1, 2]}},
                   {"grid": {"counts": ["a", 2, 3]}}):  # fmt: skip
        with pytest.raises(ApiError) as exc:
            obf.BlockOptions.of(blocks).hint(None)
        assert exc.value.status == 422 and exc.value.code == "blocks_options" and exc.value.hint
    opts = obf.BlockOptions.of({"grid": {"corner": [1, 2, 3], "rotation": "30", "div": [2, 2, 1]}, "empty": ["-99,5"]})
    hint = opts.hint(None)
    assert hint.corner == (1.0, 2.0, 3.0) and hint.rotation == 30.0 and hint.div == (2, 2, 1) and opts.empty == [-99.5]


# --- refusals, the repeated rows, the invalid ones --------------------------------------------------------------------


def test_blocks_the_grid_cannot_hold_are_refused_with_their_rows(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "off.csv"
    path.write_text(text(m, decimal="."), encoding="utf-8")
    grid = {"corner": [m.corner[0] + 25.0, m.corner[1], m.corner[2]], "size": list(m.sz)}  # inside the model
    with pytest.raises(ApiError) as exc:
        run(project, path, {"grid": grid})
    assert exc.value.code == "blocks_unaligned" and exc.value.extra["count"] > 0 and exc.value.extra["rows"]
    assert "Строки данных (с 1)" in exc.value.hint
    info = obf.inspect_blockfile(path, {"blocks": {"grid": grid}})
    assert info["error"]["code"] == "blocks_unaligned" and info["grid"] is None
    # a sub-block across the border of its parent: the size does not divide the parent
    cells = octree_cells()
    c, d = project_of(cells)
    d[5, 0] *= 1.5
    bad = project.tmp / "cross.csv"
    rows = [",".join(repr(float(v)) for v in (*c[r], *d[r])) for r in range(len(c))]
    bad.write_text("\n".join(["X,Y,Z,DX,DY,DZ", *rows]), encoding="utf-8")
    with pytest.raises(ApiError) as exc:
        run(project, bad, {"grid": {"corner": list(ORIGIN), "size": list(PARENT)}})
    assert exc.value.code == "blocks_unaligned"


def test_wrong_columns_are_422_with_a_hint(project) -> None:  # type: ignore[no-untyped-def]
    path = project.tmp / "c.csv"
    path.write_text("A,B,C,D\n1,2,3,4\n2,3,4,5\n", encoding="utf-8")
    for blocks, code in (({}, "blocks_columns"), ({"roles": {"x": "Q", "y": "B", "z": "C"}}, "blocks_columns"),
                         ({"roles": {"x": "A", "y": "B", "z": "C", "dx": "D"}}, "blocks_columns")):  # fmt: skip
        with pytest.raises(ApiError) as exc:
            run(project, path, blocks)
        assert exc.value.code == code and exc.value.hint
    info = obf.inspect_blockfile(path)
    assert info["error"]["code"] == "blocks_columns" and [c["name"] for c in info["columns"]] == ["A", "B", "C", "D"]
    ok = run(project, path, {"roles": {"x": "A", "y": "B", "z": "C"}})  # the user names the centres: one field, D
    assert ok.fields == ["D"] and ok.rows == 2


def test_repeated_rows_keep_the_first_and_count_the_rest(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    lines = text(m, decimal=".").splitlines()
    first = lines[3]
    again = first.replace(first.split(";")[6], "999.0")  # the same block, another AU: the later row goes
    path = project.tmp / "dup.csv"
    path.write_text("\n".join([*lines, again, first]) + "\n", encoding="utf-8")
    res = run(project, path)
    got = load(project, res)
    assert res.checks["duplicates"] == 2 and res.rows == len(m.p) and (got["values"]["AU"] != 999.0).all()
    ex = [e for e in res.checks["examples"] if e["check"] == "duplicate"]
    assert {e["row"] for e in ex} == {len(lines) - 1, len(lines)} and {e["other_row"] for e in ex} == {2}
    assert any("Повторяющиеся блоки" in w for w in res.warnings)


def sub_csv(path: Path, extra: list[tuple]) -> Path:
    """The octree model of `test_blocks_infer` with more cells (i, j, k, s, w) at the end."""
    cells = [*octree_cells(), *extra]
    c, d = project_of(cells)
    rows = [",".join(repr(float(v)) for v in (*c[r], *d[r], r)) for r in range(len(c))]
    path.write_text("\n".join(["X,Y,Z,DX,DY,DZ,AU", *rows]) + "\n", encoding="utf-8")
    return path


HINT = {"grid": {"corner": list(ORIGIN), "size": list(PARENT)}}


def test_overlapping_subblocks_are_a_finding_and_their_rows_stay(project) -> None:  # type: ignore[no-untyped-def]
    # the parent (0, 0, 1) is two halves; a third block over the middle of both
    path = sub_csv(project.tmp / "over.csv", [(0, 0, 1, (2, 0, 0), (4, 4, 4))])
    res = run(project, path, HINT)
    assert res.rows == len(octree_cells()) + 1 and res.checks["overlaps"]["n"] >= 2
    assert any(e["check"] == "overlap" for e in res.checks["examples"]) and res.checks["overlaps"]["method"] == "exact"
    assert any("Перекрывающиеся блоки" in w for w in res.warnings)
    rows = {e["row"] for e in res.checks["examples"] if e["check"] == "overlap"}
    assert rows <= set(range(res.rows)) and len(octree_cells()) in rows  # the numbers are rows of the file


def test_two_sizes_at_one_place_keep_the_first_and_are_overlaps(project) -> None:  # type: ignore[no-untyped-def]
    # the whole parent (0, 0, 0) and a quarter of it at the same corner: one (P, s), two sizes — stored once
    path = sub_csv(project.tmp / "two.csv", [(0, 0, 0, (0, 0, 0), (4, 4, 4))])
    res = run(project, path, HINT)
    assert res.rows == len(octree_cells()) and res.checks["overlaps"]["n"] == 1 and res.checks["duplicates"] == 0
    assert any("другим размером" in w for w in res.warnings)
    (ex,) = [e for e in res.checks["examples"] if e["check"] == "overlap"]
    assert ex["row"] == len(octree_cells()) and ex["other_row"] == 0 and ex["sub"] == [0, 0, 0, 4, 4, 4]


def test_rows_without_a_centre_or_a_size_are_dropped_and_said(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    lines = text(m, decimal=".").splitlines()
    lines[2] = ";".join(["", *lines[2].split(";")[1:]])  # no X
    lines[4] = lines[4].replace(lines[4].split(";")[3], "0.0", 1)  # a size of 0
    path = project.tmp / "inv.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = run(project, path)
    assert res.rows == len(m.p) - 2 and any("без центра или с размером ≤ 0: 2" in w for w in res.warnings)
    assert obf.inspect_blockfile(path)["warnings"][-1].startswith("Строк без центра")


def test_text_fields_become_categories_and_a_number_field_can_be_a_code(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "cat.csv"
    path.write_text(text(m, decimal="."), encoding="utf-8")
    res = run(project, path, {"fields": {"AU": {"role": "code", "unit": None}, "ROCK": {"role": "class"}}})
    got = load(project, res)
    f = {x["name"]: x for x in got["info"]["fields"]}
    assert f["AU"]["kind"] == "category" and f["AU"]["role"] == "code" and f["ROCK"]["role"] == "class"
    assert len(f["AU"]["labels"]) == len({float(a) for a in m.au}) and "-99" in f["AU"]["labels"]
    with pytest.raises(ApiError) as exc:  # a role that does not fit the kind of the field: refused before the reading
        run(project, path, {"fields": {"ROCK": {"role": "grade"}}})
    assert exc.value.code == "blocks_options" and exc.value.hint
    res = run(project, path, {"fields": {"ROCK": {"skip": True}, "AU": {"role": None, "unit": "ppm"}}}, name="B")
    f = {x["name"]: x for x in load(project, res)["info"]["fields"]}
    assert list(f) == ["AU"] and f["AU"]["unit"] == "ppm" and f["AU"]["role"] is None
    many = project.tmp / "many.csv"
    lines = text(m, decimal=".").splitlines()
    many.write_text("\n".join([lines[0], *[f"{ln.rsplit(';', 1)[0]};id{r}" for r, ln in enumerate(lines[1:])]]) + "\n",
                    encoding="utf-8")  # a label to every block
    monkeypatch.setattr("bf_core.blocks.schema.MAX_LABELS_V2", 10)
    with pytest.raises(ApiError) as exc:
        run(project, many)
    assert exc.value.code == "too_many_labels" and exc.value.hint


def test_the_density_and_the_fraction_roles_are_given_once(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    head = "X;Y;Z;DX;DY;DZ;SG;DENSITY;FRAC;AU"
    rows = [f"{m.centre[r, 0]};{m.centre[r, 1]};{m.centre[r, 2]};10;20;5;{2.5 + r / 100};2.6;"
            f"{0.9 if r % 2 else 1.0};{r}"
            for r in range(len(m.p))]  # fmt: skip
    path = project.tmp / "roles.csv"
    path.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    info = obf.inspect_blockfile(path)
    roles = {f["name"]: f["role"] for f in info["fields"]}
    assert roles["SG"] == "density" and roles["FRAC"] == "frac"
    assert roles["DENSITY"] is None  # a constant «DENSITY» is no density
    got = load(project, run(project, path))
    assert got["info"]["checks"]["constant"] == ["DENSITY"] and got["info"]["checks"]["volume"] is not None


# --- portions, cancel, the job -----------------------------------------------------------------------------------


def test_portions_give_the_same_rows_whatever_their_size(project) -> None:  # type: ignore[no-untyped-def]
    m = regular(counts=(14, 12, 10))
    write_dat_model(project.tmp / "p.dat", m)
    (project.tmp / "p.csv").write_text(text(m, decimal="."), encoding="utf-8")
    for name, step in (("p.dat", 1100), ("p.csv", 97)):
        whole = next(bt.open_block_file(project.tmp / name).chunks(rows=10**6))
        parts = list(bt.open_block_file(project.tmp / name).chunks(rows=step))
        assert len(parts) > 1 and sum(c.rows for c in parts) == whole.rows == len(m.p)
        assert [c.first for c in parts] == list(np.cumsum([0, *[c.rows for c in parts[:-1]]]))
        for key in ("EAST", "AU"):
            assert same(np.concatenate([c.numbers[key] for c in parts]), whole.numbers[key])
        codes = [c.texts["ROCK"] for c in parts]
        labels = [t.labels[i] if i >= 0 else None for t in codes for i in t.codes]
        assert labels == [m.rock[i] for i in range(len(m.p))]
    head = list(bt.open_block_file(project.tmp / "p.csv").chunks(rows=50, limit=120))
    assert [c.rows for c in head] == [50, 50, 20]
    assert bt.open_block_file(project.tmp / "p.csv").rows is None
    src = bt.open_block_file(project.tmp / "p.dat")
    assert src.rows == len(m.p) and src.kinds["ROCK"] == "text" and src.encoding


def test_a_cancel_leaves_nothing(project) -> None:  # type: ignore[no-untyped-def]
    m = regular(counts=(12, 10, 8))
    path = project.tmp / "c.csv"
    path.write_text(text(m, decimal="."), encoding="utf-8")
    for stage in (0.2, 0.5, 0.56, 0.9):
        def tick(f: float, msg: str | None, stage: float = stage) -> None:
            if f >= stage:
                raise JobCancelled()

        with pytest.raises(JobCancelled):
            run(project, path, {"empty": [-99]}, tick=tick)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0
        assert not list(project.store.tmp_dir.glob("*")) and not (project.tmp / "work").exists()


def test_the_job_part_writes_a_building_model_of_the_job(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.importing.object_blocks import entry
    from bf_core.importing.object_files import Group
    from tests.test_import_objects import Ctx

    m = regular()
    path = project.tmp / "job.dat"
    write_dat_model(path, m)
    assert obf.is_blockfile(path) and obf.is_blockfile(project.tmp / "x.fbm", None)
    ctx = Ctx(project.folder, {}, 4242)
    try:
        chosen = {"blocks": {"empty": [-99]}, "name": "Железо"}
        found = obf.write_blockfile(ctx, project.engine, project.store, Group(path, ("dat",)), chosen,
                                    lambda f, msg: None, "Блоки")
    finally:
        ctx.close()
    assert found.format == "dat" and len(found.ids) == 1 and found.cells == len(m.p) and found.counts[0]["fields"] == 2
    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(sa.select(objects).where(objects.c.id == found.ids[0])).mappings().one()
    assert row["name"] == "Железо" and row["state"] == "building" and row["job_id"] == 4242 and row["folder"] == "Блоки"
    assert any("блочная модель «Железо»" in line for line in ctx.lines)
    e = entry("job.dat", "dat", found)
    assert e["kind"] == "block_models" and e["cells"] == len(m.p) and e["object_id"] == found.ids[0]
    with pytest.raises(FormatError) as exc:  # a file the grid cannot hold is a line of the journal
        obf.write_blockfile(Ctx(project.folder, {}, 4243), project.engine, project.store, Group(path, ("dat",)),
                            {"blocks": {"grid": {"corner": [m.corner[0] + 3, 0, 0], "size": list(m.sz)}}},
                            lambda f, msg: None, None)  # fmt: skip
    assert exc.value.code == "blocks_unaligned" and exc.value.hint


def test_the_entry_of_the_inspection_has_the_form_of_the_other_files(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()
    path = project.tmp / "entry.dat"
    write_dat_model(path, m)
    e = obf.inspect_entry(path)
    assert e["ok"] and e["kind"] == "block_model" and e["format"] == "dat" and e["blocks"]["rows"] == len(m.p)
    assert e["totals"]["block_models"] == 1 and e["totals"]["cells"] == len(m.p) and e["totals"]["wireframes"] == 0
    assert e["block_models"][0]["fields"] == ["AU", "ROCK"] and e["block_models"][0]["counts"] == list(m.counts)
    bad = obf.inspect_entry(path, {"blocks": {"roles": {"x": "Q"}}})  # no model: an entry, not a failure
    assert bad["ok"] and bad["totals"]["block_models"] == 0 and bad["blocks"]["error"]["code"] == "blocks_columns"
    assert bad["warnings"] and "Столбца «Q»" in bad["warnings"][0]


def test_is_blockfile_tells_blocks_from_points_and_holes(project) -> None:  # type: ignore[no-untyped-def]
    pts = project.tmp / "pts.csv"
    pts.write_text("X,Y,Z,VALUE\n1,2,3,4\n2,3,4,5\n", encoding="utf-8")
    holes = project.tmp / "holes.csv"
    holes.write_text("HOLEID,X,Y,Z,DX,DY,DZ,DEPTH\nA,1,2,3,1,1,1,5\n", encoding="utf-8")
    blocks = project.tmp / "b.csv"
    blocks.write_text("X,Y,Z,DX,DY,DZ,AU\n1,2,3,1,1,1,5\n", encoding="utf-8")
    assert not obf.is_blockfile(pts) and obf.is_blockfile(pts, {"blocks": {}}) and obf.is_blockfile(blocks)
    assert not obf.is_blockfile(project.tmp / "absent.csv") and obf.memory(blocks, 1 << 20) >= 1024


def test_the_reader_refuses_what_is_not_a_table(project) -> None:  # type: ignore[no-untyped-def]
    for name, body, code in (("empty.csv", b"", "empty_file"), ("nohead.csv", b"1,2,3\n4,5,6\n", "no_header"),
                             ("x.xyz", b"\x00\x01\x02", "unsupported")):  # fmt: skip
        path = project.tmp / name
        path.write_bytes(body)
        with pytest.raises(FormatError) as exc:
            bt.open_block_file(path)
        assert exc.value.code == code and exc.value.hint
    assert bt.peek_names(project.tmp / "nohead.csv") is not None and bt.peek_names(project.tmp / "x.xyz") is None


# --- the owner's model -------------------------------------------------------------------------------------------


def test_the_owners_model_dat_has_169070_blocks_and_90_empty(project) -> None:  # type: ignore[no-untyped-def]
    if not REAL.is_file():
        pytest.skip("tests/data/real/Для BF.zip is not here")
    path = project.tmp / "model.dat"
    with zipfile.ZipFile(REAL) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".dat"))
        path.write_bytes(archive.read(name))
    info = obf.inspect_blockfile(path)
    assert info["rows"] == 169_070 and info["grid"]["counts"] == [443, 558, 102] and info["check3"]["ok"]
    assert info["grid"]["corner"] == [23436265.92, 5988122.53, -157.14] and info["empty_candidates"] == [-99.0]
    res = run(project, path, {"empty": [-99]})
    got = load(project, res)
    assert res.rows == res.parents == 169_070 and not got["info"]["sub"] and got["grid"].size == (10.0, 10.0, 10.0)
    assert np.isnan(got["values"]["Fe_m_pc"]).sum() == 90 and np.isnan(got["values"]["Fe_o_pc"]).sum() == 90
    assert got["info"]["checks"]["constant"] == ["1 Filter"] and got["info"]["checks"]["duplicates"] == 0
    src = bt.open_block_file(path)
    keys = ("EAST", "NORTH", "RL", "Fe_m_pc")
    cols = {k: np.concatenate([c.numbers[k] for c in src.chunks(rows=50_000)]) for k in keys}
    from bf_core.blocks.grid_infer import infer

    ref = infer(np.column_stack([cols[k] for k in ("EAST", "NORTH", "RL")]), np.full((169_070, 3), 10.0))
    back = geo.centres(got["grid"], got["p"], None, None)
    order = np.argsort(ref.p, kind="stable")
    assert np.abs(back - np.column_stack([cols[k] for k in ("EAST", "NORTH", "RL")])[order]).max() <= 1e-6
    fe = cols["Fe_m_pc"][order].copy()
    fe[fe == -99.0] = np.nan
    assert same(got["values"]["Fe_m_pc"], fe) and math.isclose(got["info"]["checks"]["volume"], 169_070_000.0)
