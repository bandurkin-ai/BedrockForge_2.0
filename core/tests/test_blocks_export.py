"""Export of a block model (подшаг 17.16; ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12, decision 20): **Т8.7-8** — DAT, CSV, OMF
and Parquet and back (the blocks and the values, a turned model to 1e−9 м, Т8.7-4), sub-blocks as they are, the filter
and the fields, gaps and the code of «пусто», encodings, the checks of the path (409 `files_exist`, 422 `bad_path`),
a cancel that leaves nothing, the same bytes twice, the registry of Д-17 and the journal of the job."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import export as bx
from bf_core.blocks import geometry as geo
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.blocks.schema import GridV2
from bf_core.db import dialect
from bf_core.db.schema import export_log
from bf_core.exporting import blocks_writers as bw
from bf_core.exporting import omf_job
from bf_core.exporting.registry import Env, Item, Writer
from bf_core.formats import omf_read as orr
from bf_core.formats import omf_volume as ov
from bf_core.jobs.context import JobCancelled
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_fields_store import GRID, make_model, sample
from tests.test_blocks_infer import DIV, ORIGIN, PARENT, expected_rows, octree_cells
from tests.test_filters_store import cond
from tests.test_import_blocks import load, run, same
from tests.test_import_blocks import project as base_project  # noqa: F401
from tests.test_omf_rotation import Sink

ROT = 30.0
SUB_GRID = {"corner": list(ORIGIN), "counts": [4, 3, 2], "size": list(PARENT), "rotation": ROT, "div": list(DIV)}
HINT = {"corner": GRID["corner"], "rotation": ROT, "size": GRID["size"]}
LABELS = ["руда", "порода", "дайка"]


@pytest.fixture()
def project(base_project):  # type: ignore[no-untyped-def]  # noqa: F811
    return base_project


def out_dir(p: Any) -> Path:
    d = p.tmp / "out"
    d.mkdir(exist_ok=True)
    return d


def model_a(p: Any, name: str = "Повёрнутая") -> int:
    """A turned regular model: AU (f8), CU (f4), N (i2) with gaps, DOM (category) — `test_blocks_fields_store`."""
    return make_model(p, p.store, name=name)


def ordered_rows(cells: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(P, s, w) of the cells in the order of the file: by P, then sz, sy, sx."""
    pp, s, w = expected_rows(cells)
    order = np.lexsort((s[:, 0], s[:, 1], s[:, 2], pp))
    return pp[order], s[order], w[order]


def model_sub(p: Any, name: str = "Субблоки") -> tuple[int, dict[str, np.ndarray]]:
    """A turned model with sub-blocks (the octree of 17.11) and two fields."""
    cells = octree_cells()
    pp, s, w = ordered_rows(cells)
    rng = np.random.default_rng(5)
    au = np.round(rng.lognormal(0.0, 1.0, len(pp)), 4)
    au[::7] = np.nan
    dom = rng.integers(-1, 3, len(pp)).astype(np.int32)
    fields = [{"name": "AU", "dtype": "f8", "unit": "г/т"}, {"name": "DOM", "dtype": "cat", "labels": LABELS}]
    with bm.CellsWriterV2(p.store, SUB_GRID, fields) as wr:
        wr.write(pp, {"AU": au, "DOM": dom}, s, w)
        staged = wr.finish()
    with dialect.write_tx(p.engine) as conn:
        return bm.commit_block_model(conn, p.store, staged, name=name), {"AU": au, "DOM": dom}


def opened(p: Any, model_id: int) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, model_id)


def export(p: Any, model_id: int, fmt: str, name: str | None = None, **body: Any) -> dict[str, Any]:
    path = out_dir(p) / (name or f"m{model_id}.{fmt}")
    params = {"model_id": model_id, "format": fmt, "path": str(path), **body}
    return bx.run_export(p.engine, p.folder, params, chunk=64)  # small portions: the files are written in several


def check_back(p: Any, res: Any, m: Model, au: np.ndarray, tol: float = 1e-9) -> None:
    """The imported model has the blocks of `m`: the same rows (P, s, w), the centres to `tol`, the AU bit for bit."""
    got = load(p, res)
    c = geo.centres(got["grid"], got["p"], got["s"], got["w"])
    assert res.rows == m.rows and np.abs(c - m.centres()).max() <= tol  # Т8.7-4
    assert same(got["values"]["AU"], au)


# --- DAT and CSV: there and back ----------------------------------------------------------------------------------


def test_t878_dat_of_a_turned_model_comes_back_with_the_same_blocks_and_values(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    out = export(project, mid, "dat")
    assert out["rows"] == out["total"] == 350 and Path(out["path"]).stat().st_size == out["bytes"]
    assert not list(out_dir(project).glob("*.part"))
    m = opened(project, mid)
    res = run(project, Path(out["path"]), {"grid": HINT})
    got = load(project, res)
    idx, values = sample()
    assert np.array_equal(got["p"], idx) and got["grid"].rotation == ROT  # the same cells of the same grid
    assert np.abs(geo.centres(got["grid"], got["p"], None, None) - m.centres()).max() <= 1e-9  # Т8.7-4
    f = got["values"]
    assert same(f["AU"], values["AU"]) and same(f["CU"], m.columns(["CU"])["CU"]) and same(f["N"], values["N"])
    labels = np.array(["", *LABELS], dtype=object)[values["DOM"] + 1]
    back = got["info"]["fields"]
    names = {x["name"]: x for x in back}
    text = np.array(["", *names["DOM"]["labels"]], dtype=object)[f["DOM"] + 1]
    assert np.array_equal(text, labels)  # a category is its label, an empty one is nothing
    assert got["info"]["checks"]["overlaps"]["n"] == 0


@pytest.mark.parametrize(
    "delimiter,decimal,encoding",
    [(";", ",", "utf-8-sig"), (",", ".", "utf-8"), ("\t", ",", "cp1251"), ("|", ".", "utf-16")],
)
def test_t878_csv_of_a_turned_model_with_every_delimiter_decimal_and_encoding(
    project, delimiter, decimal, encoding
) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    out = export(project, mid, "csv", delimiter=delimiter, decimal=decimal, encoding=encoding)
    path = Path(out["path"])
    head = path.read_bytes()[:400]
    assert (b"\xef\xbb\xbf" in head[:3]) == (encoding == "utf-8-sig")
    m = opened(project, mid)
    got = load(project, run(project, path, {"grid": HINT}))
    idx, values = sample()
    assert np.array_equal(got["p"], idx)
    assert np.abs(geo.centres(got["grid"], got["p"], None, None) - m.centres()).max() <= 1e-9
    assert same(got["values"]["AU"], values["AU"]) and same(got["values"]["N"], values["N"])


@pytest.mark.parametrize("fmt", ["dat", "csv"])
def test_t878_sub_blocks_are_written_as_they_are_and_come_back(project, fmt) -> None:  # type: ignore[no-untyped-def]
    mid, values = model_sub(project)
    out = export(project, mid, fmt)
    assert out["rows"] == out["total"]
    m = opened(project, mid)
    assert m.sub and m.rows == out["rows"]
    res = run(project, Path(out["path"]), {"grid": {"corner": list(ORIGIN), "rotation": ROT, "size": list(PARENT)}})
    got = load(project, res)
    pp, s, w = ordered_rows(octree_cells())
    assert np.array_equal(got["p"], pp) and np.array_equal(got["s"], s) and np.array_equal(got["w"], w)
    assert got["info"]["grid"]["div"] == list(DIV)
    check_back(project, res, m, values["AU"])


def test_the_columns_are_the_centre_in_the_project_axes_and_the_size_along_the_model(project) -> None:  # type: ignore[no-untyped-def]
    mid, _ = model_sub(project)
    m = opened(project, mid)
    path = Path(export(project, mid, "csv", delimiter=";", decimal=".", encoding="utf-8")["path"])
    lines = path.read_bytes().decode("utf-8").split("\r\n")
    assert lines[0].split(";")[:8] == ["X", "Y", "Z", "DX", "DY", "DZ", "AU", "DOM"] and lines[-1] == ""
    first = [float(x) for x in lines[1].split(";")[:6]]
    assert first[:3] == m.centres()[:1].ravel().tolist()  # float64, the shortest text that comes back
    assert first[3:] == geo.row_widths(m.grid, m.p()[:1], m.subs()[0][:1], m.subs()[1][:1]).ravel().tolist()
    assert len(lines) == m.rows + 2


def test_csv_micromine_has_the_types_and_the_grid_line_and_the_importer_reads_them(project) -> None:  # type: ignore[no-untyped-def]
    grid = {**GRID, "rotation": 0}
    mid = make_model(project, project.store, name="Прямая", grid=grid)
    out = export(project, mid, "csv", header="micromine", delimiter=";", decimal=",")
    lines = Path(out["path"]).read_bytes().decode("utf-8-sig").split("\r\n")
    assert lines[1].split(";")[:7] == ["DOUBLE"] * 7 and lines[1].split(";")[-1] == "CHARACTER"
    x0, dx, y0, dy, z0, dz, at = lines[2].split(";")[:7]
    assert (x0, dx, y0, dy, z0, dz, at) == ("1003", "5", "2002,75", "5", "-298,75", "2,5", "CENTRE")
    assert not out["warnings"]
    got = load(project, run(project, Path(out["path"])))  # no hint: the grid line gives the corner
    assert np.abs(np.array(got["grid"].corner) - GRID["corner"]).max() <= 1e-9 and got["grid"].size == (5.0, 5.0, 2.5)
    turned = export(project, model_a(project, "Повёрнутая 2"), "csv", header="micromine", name="t.csv")
    assert any("модель повёрнута" in w for w in turned["warnings"])
    lines = Path(turned["path"]).read_bytes().decode("utf-8-sig").split("\r\n")
    assert len(lines) == 350 + 3 and lines[2].split(";")[0].startswith("1001,41")  # the first row, no grid line
    got = load(project, run(project, Path(turned["path"]), {"grid": HINT}))
    assert np.array_equal(got["p"], sample()[0])


def test_dat_keeps_the_grid_in_the_description_and_cuts_the_names_to_micromine(project) -> None:  # type: ignore[no-untyped-def]
    fields = [{"name": "Содержание золота", "dtype": "f8"}, {"name": "x", "dtype": "f8"}]
    with bm.CellsWriterV2(project.store, GRID, fields) as w:
        w.write(
            np.array([1, 5, 9], np.uint64), {"Содержание золота": np.array([1.0, 2.0, 3.0]), "x": np.array([4.0, 5, 6])}
        )
        staged = w.finish()
    with dialect.write_tx(project.engine) as conn:
        mid = bm.commit_block_model(conn, project.store, staged, name="Имена")
    out = export(project, mid, "dat")
    raw = Path(out["path"]).read_bytes()
    assert b"BedrockForge block model" in raw[:8192] and b"rotation 30" in raw[:8192]
    assert any("записано как" in w for w in out["warnings"]) and any(
        "«x» записано как «x_2»" in w for w in out["warnings"]
    )
    from bf_core.formats.micromine_header import read_header

    assert read_header(Path(out["path"])).names == ["X", "Y", "Z", "DX", "DY", "DZ", "Содержание", "x_2"]


# --- Parquet -------------------------------------------------------------------------------------------------------


def test_t878_parquet_has_the_geometry_the_fields_and_the_grid_bit_for_bit(project) -> None:  # type: ignore[no-untyped-def]
    mid, values = model_sub(project)
    m = opened(project, mid)
    out = export(project, mid, "parquet")
    table = pq.read_table(out["path"])
    assert table.column_names == [
        "idx",
        "sx",
        "sy",
        "sz",
        "wx",
        "wy",
        "wz",
        "X",
        "Y",
        "Z",
        "DX",
        "DY",
        "DZ",
        "AU",
        "DOM",
    ]
    p, (s, w) = m.p(), m.subs()  # type: ignore[misc]
    assert np.array_equal(table.column("idx").to_numpy(), p)
    assert np.array_equal(np.column_stack([table.column(c).to_numpy() for c in ("sx", "sy", "sz")]), s)
    assert np.array_equal(np.column_stack([table.column(c).to_numpy() for c in ("wx", "wy", "wz")]), w)
    c = np.column_stack([table.column(k).to_numpy() for k in ("X", "Y", "Z")])
    assert np.array_equal(c, m.centres())  # bit for bit
    au = table.column("AU")
    assert au.null_count == int(np.isnan(values["AU"]).sum())
    assert same(au.to_numpy(zero_copy_only=False), values["AU"])
    text = table.column("DOM").to_pylist()
    want = [None if k < 0 else LABELS[k] for k in values["DOM"]]
    assert text == want
    meta = json.loads(table.schema.metadata[b"bf2.block_model"])
    assert GridV2.of(meta["grid"]) == m.grid and meta["fields"][0]["unit"] == "г/т"
    assert meta["fields"][1]["labels"] == LABELS


def test_parquet_of_a_model_without_sub_blocks_has_no_sub_columns(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    names = pq.read_table(export(project, mid, "parquet")["path"]).column_names
    assert names[:7] == ["idx", "X", "Y", "Z", "DX", "DY", "DZ"] and "sx" not in names


# --- OMF -----------------------------------------------------------------------------------------------------------


def read_omf(path: Path, tmp: Path) -> Sink:
    with orr.open_reader(path) as r:
        (vol,) = r.volumes()
        return ov.read_volume(r.file, vol, Sink, tmp_dir=tmp).staged


def test_t878_omf_of_a_turned_model_comes_back_through_the_axes(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    out = export(project, mid, "omf")
    sink = read_omf(Path(out["path"]), project.tmp)
    idx, values = sample()
    gi, got = sink.all()
    present = ~(np.isnan(values["AU"]) & np.isnan(values["CU"]) & np.isnan(values["N"]) & (values["DOM"] < 0))
    assert np.array_equal(gi, idx[present]) and sink.grid["rotation"] == pytest.approx(ROT, abs=1e-12)
    assert same(got["AU"], values["AU"][present]) and same(got["N"], values["N"][present])
    assert np.array_equal(got["DOM"], values["DOM"][present])
    m = opened(project, mid)
    c = geo.centres(GridV2.of(sink.grid), gi)
    assert np.abs(c - m.centres()[present]).max() <= 1e-9
    assert out["rows"] == 350 and not list(out_dir(project).glob("*.part"))


def test_omf_of_a_model_with_sub_blocks_is_refused_after_the_regularization_text(project) -> None:  # type: ignore[no-untyped-def]
    mid, _ = model_sub(project)
    path = out_dir(project) / "s.omf"
    with dialect.read_tx(project.engine) as conn:
        with pytest.raises(ApiError) as e:
            bx.request(conn, project.folder, mid, {"format": "omf", "path": str(path)})
    assert e.value.status == 422 and e.value.code == "omf_subblocks" and "только после регуляризации" in e.value.message
    assert e.value.hint
    with pytest.raises(ApiError) as e2:  # the job says the same when it is reached anyway
        bx.run_export(project.engine, project.folder, {"model_id": mid, "format": "omf", "path": str(path)})
    assert e2.value.code == "omf_subblocks"
    assert not list(out_dir(project).glob("*"))


def test_omf_of_a_model_over_the_limit_is_not_offered(project, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    monkeypatch.setattr(omf_job, "MAX_CELLS", 100)  # 10 × 8 × 6 = 480 cells
    with dialect.read_tx(project.engine) as conn:
        with pytest.raises(ApiError) as e:
            bx.request(conn, project.folder, mid, {"format": "omf", "path": str(out_dir(project) / "m.omf")})
    assert e.value.status == 422 and e.value.code == "omf_not_exportable" and "Разделите" in e.value.hint


# --- the filter, the fields, gaps -----------------------------------------------------------------------------------


def test_only_the_blocks_that_pass_the_filter_and_the_quick_restrictor_are_written(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    m = opened(project, mid)
    idx, values = sample()
    with dialect.write_tx(project.engine) as conn:
        from bf_core.blocks.filters import store as filters

        fid = filters.create(conn, {"name": "Руда", "rules": cond("AU", ">", 1)})
    quick = cond("DOM", "=", "руда")
    want = (values["AU"] > 1) & (values["DOM"] == 0)  # NaN > 1 is false: a gap does not pass
    for fmt in ("dat", "csv", "parquet", "omf"):
        out = export(project, mid, fmt, filter_id=fid, restrict=quick, name=f"f.{fmt}")
        assert out["rows"] == int(want.sum()) and out["total"] == 350
    table = pq.read_table(out_dir(project) / "f.parquet")
    assert np.array_equal(table.column("idx").to_numpy(), idx[want])
    assert np.array_equal(np.column_stack([table.column(k).to_numpy() for k in ("X", "Y", "Z")]), m.centres()[want])
    only = load(project, run(project, out_dir(project) / "f.dat", {"grid": HINT}))
    assert same(only["values"]["AU"], values["AU"][want]) and np.all(only["values"]["AU"] > 1)
    sink = read_omf(out_dir(project) / "f.omf", project.tmp)
    gi, got = sink.all()
    assert set(gi.tolist()) <= set(idx[want].tolist()) and np.all(got["AU"][~np.isnan(got["AU"])] > 1)
    with pytest.raises(ApiError) as e:  # a filter over a field the model has not
        export(project, mid, "dat", restrict=cond("NOPE", ">", 1), name="x.dat")
    assert e.value.status == 422 and e.value.code == "filter_field_missing"
    with pytest.raises(ApiError) as e2:  # nothing passes: nothing is written
        export(project, mid, "csv", restrict=cond("AU", ">", 1e9), name="none.csv")
    assert e2.value.code == "export_empty" and not (out_dir(project) / "none.csv").exists()
    assert not list(out_dir(project).glob("*.part")) and not list(out_dir(project).glob("none*"))


def test_the_fields_asked_for_are_written_in_that_order_without_case(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    out = export(project, mid, "csv", fields=["dom", "AU"], delimiter=";", decimal=".", encoding="utf-8")
    head = Path(out["path"]).read_bytes().decode("utf-8").split("\r\n")[0]
    assert head == "X;Y;Z;DX;DY;DZ;DOM;AU"
    none = export(project, mid, "csv", fields=[], delimiter=";", encoding="utf-8", name="g.csv")
    assert Path(none["path"]).read_bytes().decode("utf-8").split("\r\n")[0] == "X;Y;Z;DX;DY;DZ"
    with pytest.raises(ApiError) as e:
        export(project, mid, "csv", fields=["NOPE"], name="h.csv")
    assert e.value.status == 404 and e.value.code == "block_field_not_found"
    with dialect.read_tx(project.engine) as conn:
        with pytest.raises(ApiError) as e2:
            bx.request(
                conn, project.folder, mid, {"format": "omf", "path": str(out_dir(project) / "z.omf"), "fields": []}
            )
    assert e2.value.code == "bad_param" and e2.value.extra["param"] == "fields"


def test_a_gap_is_empty_never_zero_and_the_code_of_empty_is_the_users(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    idx, values = sample()
    gap = np.isnan(values["AU"])
    text = Path(export(project, mid, "csv", fields=["AU"], delimiter=";", decimal=".", encoding="utf-8")["path"])
    rows = [line.split(";") for line in text.read_bytes().decode("utf-8").split("\r\n")[1:-1]]
    au = [r[6] for r in rows]
    assert [a == "" for a in au] == gap.tolist() and "0" not in [a for a, g in zip(au, gap, strict=True) if g]
    coded = Path(
        export(
            project, mid, "csv", fields=["AU"], delimiter=";", decimal=",", encoding="utf-8", empty=-99, name="c.csv"
        )["path"]
    )
    cells = [line.split(";")[6] for line in coded.read_bytes().decode("utf-8").split("\r\n")[1:-1]]
    assert [c == "-99" for c in cells] == gap.tolist()
    back = load(project, run(project, coded, {"grid": HINT, "empty": ["-99"]}))
    assert same(back["values"]["AU"], values["AU"])  # the code comes back as a gap
    dat = export(project, mid, "dat", fields=["AU"], empty=-99, name="c.dat")
    again = load(project, run(project, Path(dat["path"]), {"grid": HINT, "empty": [-99]}))
    assert same(again["values"]["AU"], values["AU"])
    plain = load(
        project, run(project, Path(export(project, mid, "dat", fields=["AU"], name="p.dat")["path"]), {"grid": HINT})
    )
    assert same(plain["values"]["AU"], values["AU"]) and np.isnan(plain["values"]["AU"]).sum() == gap.sum()
    with pytest.raises(ApiError) as e:  # a real value equal to the code: they cannot be told apart
        export(project, mid, "dat", fields=["N"], empty=values["N"][~np.isnan(values["N"])][0], name="k.dat")
    assert e.value.status == 422 and e.value.code == "empty_code_taken" and e.value.hint
    assert not (out_dir(project) / "k.dat").exists() and not list(out_dir(project).glob("*.part"))


# --- encodings -------------------------------------------------------------------------------------------------------


def odd_model(p: Any) -> int:
    fields = [{"name": "Тип", "dtype": "cat", "labels": ["naïve", "руда; «А»", 'с "кавычкой"']}]
    with bm.CellsWriterV2(p.store, GRID, fields) as w:
        w.write(np.array([0, 1, 2], np.uint64), {"Тип": np.array([0, 1, 2], np.int32)})
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        return bm.commit_block_model(conn, p.store, staged, name="Тексты")


def test_a_character_the_code_page_has_not_is_refused_and_utf8_keeps_it(project) -> None:  # type: ignore[no-untyped-def]
    mid = odd_model(project)
    for fmt, enc in (("dat", "cp1251"), ("csv", "cp1251"), ("csv", "cp866")):
        with pytest.raises(ApiError) as e:
            export(project, mid, fmt, encoding=enc, name=f"e.{fmt}")
        assert e.value.status == 422 and e.value.code == "export_encoding" and "naïve" in e.value.message
        assert e.value.hint and not list(out_dir(project).glob("e.*"))
    for fmt, enc in (("dat", "bocu1"), ("dat", "utf-8"), ("csv", "utf-8")):
        out = export(project, mid, fmt, encoding=enc, name=f"u-{enc}.{fmt}")
        got = load(project, run(project, Path(out["path"]), {"grid": HINT}))
        labels = got["info"]["fields"][0]["labels"]
        text = np.array(labels, dtype=object)[got["values"]["Тип"]]
        assert text.tolist() == ["naïve", "руда; «А»", 'с "кавычкой"']  # the delimiter and the quotes survive


def test_a_label_wider_than_a_micromine_field_is_refused(project) -> None:  # type: ignore[no-untyped-def]
    fields = [{"name": "Т", "dtype": "cat", "labels": ["а" * 300]}]
    with bm.CellsWriterV2(project.store, GRID, fields) as w:
        w.write(np.array([0], np.uint64), {"Т": np.array([0], np.int32)})
        staged = w.finish()
    with dialect.write_tx(project.engine) as conn:
        mid = bm.commit_block_model(conn, project.store, staged, name="Длинная метка")
    with pytest.raises(ApiError) as e:
        export(project, mid, "dat")
    assert e.value.code == "export_label_too_long" and not list(out_dir(project).glob("*"))
    assert export(project, mid, "csv", name="long.csv")["rows"] == 1  # a CSV has no such limit


# --- the path, the same bytes, the cancel -----------------------------------------------------------------------------


def test_the_path_is_checked_before_the_job(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    folder = out_dir(project)

    def ask(path: str, **kw: Any) -> ApiError:
        with dialect.read_tx(project.engine) as conn, pytest.raises(ApiError) as e:
            bx.request(conn, project.folder, mid, {"format": "dat", "path": path, **kw})
        return e.value

    assert ask("m.dat").code == "bad_param" and ask("m.dat").extra == {"param": "path"}
    assert ask(str(folder / "нет" / "m.dat")).code == "bad_param"
    inside = ask(str(project.folder / "m.dat"))
    assert inside.status == 422 and inside.code == "bad_path" and inside.extra["param"] == "path"
    assert ask(str(folder / "m.dat"), format="gpx").code == "bad_param"
    with dialect.read_tx(project.engine) as conn:  # the extension is added
        assert bx.request(conn, project.folder, mid, {"format": "dat", "path": str(folder / "m")})["target"] == str(
            folder / "m.dat"
        )
    (folder / "m.dat").write_bytes(b"old")
    clash = ask(str(folder / "m.dat"))
    assert clash.status == 409 and clash.code == "files_exist" and clash.extra["files"] == ["m.dat"]
    assert (folder / "m.dat").read_bytes() == b"old"
    with pytest.raises(ApiError) as e:  # the job checks again
        bx.run_export(project.engine, project.folder, {"model_id": mid, "format": "dat", "path": str(folder / "m.dat")})
    assert e.value.code == "files_exist"
    out = bx.run_export(
        project.engine,
        project.folder,
        {"model_id": mid, "format": "dat", "path": str(folder / "m.dat"), "overwrite": True},
    )
    assert out["bytes"] > 3 and (folder / "m.dat").stat().st_size == out["bytes"]
    for bad in (
        {"delimiter": "x"},
        {"decimal": "ё"},
        {"header": "x"},
        {"encoding": "ebcdic"},
        {"empty": "abc"},
        {"fields": "AU"},
        {"filter_id": 0},
        {"overwrite": 1},
    ):
        assert ask(str(folder / "q.dat"), **bad).code == "bad_param"
    assert ask(str(folder / "q.csv"), format="csv", delimiter=",", decimal=",").extra["param"] == "decimal"
    with dialect.read_tx(project.engine) as conn, pytest.raises(ApiError) as e3:
        bx.request(conn, project.folder, 99999, {"format": "dat", "path": str(folder / "w.dat")})
    assert e3.value.status == 404


@pytest.mark.parametrize("fmt", ["dat", "csv", "parquet"])
def test_the_same_model_gives_the_same_bytes(project, fmt) -> None:  # type: ignore[no-untyped-def]
    mid, _ = model_sub(project)
    a = Path(export(project, mid, fmt, name=f"a.{fmt}")["path"])
    b = Path(export(project, mid, fmt, name=f"b.{fmt}")["path"])
    assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()


@pytest.mark.parametrize("fmt", ["dat", "csv", "parquet", "omf"])
def test_a_cancel_leaves_nothing(project, fmt) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    calls = []

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        calls.append(1)
        if len(calls) >= 5:
            raise JobCancelled()

    path = out_dir(project) / f"c.{fmt}"
    with pytest.raises(JobCancelled):
        bx.run_export(
            project.engine, project.folder, {"model_id": mid, "format": fmt, "path": str(path)}, tick=tick, chunk=40
        )
    assert len(calls) >= 5 and not list(out_dir(project).glob("*"))
    assert not list(project.store.tmp_dir.glob("*")) if project.store.tmp_dir.exists() else True


def test_progress_goes_up_in_the_order_of_the_stages(project) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    seen: list[tuple[float, str | None]] = []
    bx.run_export(
        project.engine,
        project.folder,
        {"model_id": mid, "format": "csv", "path": str(out_dir(project) / "p.csv")},
        tick=lambda f, m=None: seen.append((f, m)),
        chunk=100,
    )  # type: ignore[arg-type]
    values = [f for f, _ in seen]
    assert values == sorted(values) and values[0] == 0.0 and values[-1] == 1.0
    assert any(m and m.startswith("Запись:") and "из 350" in m for _, m in seen)


# --- the registry of Д-17 and the job --------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["dat", "csv", "parquet", "omf"])
def test_the_writers_of_the_registry_write_the_whole_model(project, fmt) -> None:  # type: ignore[no-untyped-def]
    mid = model_a(project)
    w = Writer("block_model", fmt, fmt, bw.write(fmt), bw.suffixes(fmt), memory=bw.memory(fmt))
    item = Item(mid, "block_model", "Повёрнутая", None, 1, (), {"grid_cells": 480})
    env = Env(engine=project.engine, store=project.store)
    ticks: list[float] = []
    path = out_dir(project) / ("Повёрнутая" + w.suffixes(item, env)[0])
    done = w.write(item, path, env, lambda f=0.0, m=None: ticks.append(f))  # type: ignore[misc]
    assert done.files == [path] and done.size == path.stat().st_size and ticks and w.memory(item) >= 512
    path.write_bytes(b"x")  # the window has chosen the name: a file there is replaced
    assert w.write(item, path, env, lambda f=0.0, m=None: None).size > 1  # type: ignore[misc]


def test_the_job_writes_the_journal_and_maps_the_errors(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.context import JobError

    mid = model_a(project)
    logs: list[str] = []
    params = {"model_id": mid, "format": "csv", "path": str(out_dir(project) / "j.csv"), "fields": ["AU"]}
    ctx = SimpleNamespace(
        engine=lambda: project.engine,
        project_folder=project.folder,
        params=params,
        id=7,
        tick=lambda f=None, m=None: None,
        log=lambda t, level="info": logs.append(t),
    )
    out = bx.export_job(ctx)
    assert out["rows"] == 350 and any("Записан файл «j.csv»" in t for t in logs)
    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(sa.select(export_log)).one()
    assert row.object_id == mid and row.file_name == "j.csv" and row.format == "csv" and row.counts["rows"] == 350
    assert row.settings["fields"] == ["AU"]
    again = SimpleNamespace(**{**ctx.__dict__, "params": params})
    with pytest.raises(JobError) as e:
        bx.export_job(again)
    assert e.value.code == "files_exist" and e.value.hint
    assert "blocks.export" in __import__("bf_core.jobs.registry", fromlist=["KINDS"]).KINDS


def test_a_model_of_the_project_not_in_the_folder_of_the_project_is_found(project) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ApiError) as e:
        bx.run_export(
            project.engine, project.folder, {"model_id": 4242, "format": "dat", "path": str(out_dir(project) / "n.dat")}
        )
    assert e.value.status == 404 and e.value.code == "block_model_not_found"
    assert isinstance(BlobStore(project.folder), BlobStore)
