"""The registry of export writers (ТЗ 7.35 Д-17; ADR 0010 §1, ADR 0017 §5 «Реестр», decisions 5–6):
every pair «type × format» of the window is a writer or the reason why not, in Russian; the table of
`GET /export/formats`; the writers of the demo objects write files that the importers read back."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.exporting import names
from bf_core.exporting import registry as reg
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API

CYRILLIC = re.compile("[А-Яа-яЁё]")
EXPECTED = {  # ADR 0017 §5: the formats of each type, the default first (decision 6)
    "drillhole_set": ["csv", "xlsx", "dat", "source"],
    "wireframe": ["dxf", "dwg", "tridb", "obj", "stl", "dtm", "omf", "source"],
    "dtm": ["dxf", "dwg", "tridb", "obj", "stl", "dtm", "omf", "source"],
    "line_file": ["str", "dxf", "surpac", "omf", "source"],
    "cloud": ["las", "csv", "source"],
    "raster": ["source", "geotiff"],
    "block_model": ["omf", "dat", "csv", "source"],
}


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return project, reg.Env(engine=project.engine, store=BlobStore(project.folder))


def test_every_pair_is_a_writer_or_a_reason_in_russian() -> None:
    env = reg.Env()
    assert [k.key for k in reg.KINDS] == list(EXPECTED)
    for kind, fmts in EXPECTED.items():
        writers = reg.formats_of(kind)
        assert [w.format for w in writers] == fmts, kind
        for w in writers:
            assert CYRILLIC.search(w.title) or w.title.isascii(), (kind, w.format)
            why = reg.reason_of(w, env) if w.format in ("dwg", "omf") or w.write is None else None
            if w.write is None:
                assert why is not None and CYRILLIC.search(why.message) and CYRILLIC.search(why.hint), (kind, w.format)
            elif why is not None:  # DWG without ODA, OMF before its writer: a reason and a hint
                assert CYRILLIC.search(why.message) and CYRILLIC.search(why.hint), (kind, w.format)
    for fmt in ("dat", "csv"):
        why = reg.reason_of(reg.formats_of("block_model")[EXPECTED["block_model"].index(fmt)], env)
        assert why is not None and "этап Э17" in why.message
    dwg = reg.writer("wireframe", "dwg")
    assert dwg is not None
    no_oda = reg.reason_of(dwg, reg.Env(oda=str(Path("/нет/такого/ODAFileConverter.exe"))))
    if no_oda is not None:  # a converter of the machine may be found on PATH
        assert "ODA File Converter" in no_oda.message and "DXF" in no_oda.hint
    assert reg.writer("drillhole_set", "omf") is None


def test_formats_of_the_demo(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.db import dialect

    project, env = demo
    with dialect.read_tx(project.engine) as conn:
        table = reg.formats(conn, env)["types"]
    by = {t["kind"]: t for t in table}
    assert list(by) == list(EXPECTED)
    for t in table:
        assert CYRILLIC.search(t["title"]) and CYRILLIC.search(t["folder"])
        assert [f["format"] for f in t["formats"]] == EXPECTED[t["kind"]]
        for f in t["formats"]:
            assert set(f) == {"format", "title", "available", "reason", "hint"}
            assert f["available"] == (f["reason"] is None)
            if not f["available"]:
                assert CYRILLIC.search(f["reason"]), (t["kind"], f)
        assert t["default"] == next((f["format"] for f in t["formats"] if f["available"]), None)
    assert by["drillhole_set"]["count"] >= 1 and by["drillhole_set"]["default"] == "csv"
    assert by["wireframe"]["count"] >= 1 and by["wireframe"]["default"] == "dxf"
    assert by["dtm"]["count"] >= 1
    assert by["line_file"]["count"] >= 1 and by["line_file"]["default"] == "str"
    assert by["block_model"]["count"] == 0
    assert {f["format"]: f["available"] for f in by["block_model"]["formats"]}["dat"] is False
    # the demo is made in the project: no source files — «Исходный файл» is unavailable with the reason
    src = {f["format"]: f for f in by["wireframe"]["formats"]}["source"]
    assert src["available"] is False and "исходного файла нет" in src["reason"]


def _ticks() -> tuple[list[float], Any]:
    seen: list[float] = []

    def tick(f: float, _message: str | None = None) -> None:
        seen.append(f)

    return seen, tick


def test_writers_of_the_demo_write_files_that_read_back(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.db import dialect
    from bf_core.formats.registry import read_objects

    project, env = demo
    with dialect.read_tx(project.engine) as conn:
        found = reg.items(conn)
    first: dict[str, reg.Item] = {}
    for item in found:
        first.setdefault(item.kind, item)
    assert {"drillhole_set", "wireframe", "dtm", "line_file"} <= set(first)
    for kind, item in first.items():
        for w in reg.formats_of(kind):
            if reg.reason_of(w, env) is not None or w.accepts(item) is not None:
                continue
            assert w.write is not None
            folder = tmp_path / kind / w.format
            folder.mkdir(parents=True)
            suffixes = w.suffixes(item, env)
            stem = names.Folder(len(str(folder))).claim(w.stem_of(item), suffixes)
            seen, tick = _ticks()
            out = w.write(item, folder / (stem + suffixes[0]), env, tick)
            assert out.files and all(p.exists() for p in out.files), (kind, w.format)
            assert out.size > 0 and all(0.0 <= f <= 1.0 for f in seen), (kind, w.format)
            assert w.memory(item) >= 256
            on_disk = sorted(p.name for p in folder.rglob("*") if p.is_file())
            assert not any(n.endswith(".part") for n in on_disk), on_disk
            if kind in ("wireframe", "dtm") and w.format in ("dxf", "tridb", "obj"):
                meshes = list(read_objects(out.files[0]).meshes or [])
                assert len(meshes) == 1 and meshes[0].triangle_count == item.info["triangles"], (kind, w.format)
    book = next(p for p in (tmp_path / "drillhole_set" / "xlsx").iterdir())
    import openpyxl

    sheets = openpyxl.load_workbook(book, read_only=True).sheetnames
    assert sheets[:2] == ["Устья", "Инклинометрия"] and len(sheets) >= 3
    tables = sorted(p.name for p in (tmp_path / "drillhole_set" / "csv").rglob("*.csv"))
    assert "Устья.csv" in tables and "Инклинометрия.csv" in tables and len(tables) == len(sheets)


def test_sheet_names_follow_excel() -> None:
    from bf_core.drillholes.export_book import sheet_name

    taken: set[str] = set()
    assert sheet_name("Пробы", taken) == "Пробы"
    assert sheet_name("пробы", taken) == "пробы (2)"
    assert sheet_name("a[b]:c*?/\\d", taken) == "a_b__c____d"
    long = sheet_name("Очень длинное имя интервальной таблицы", taken)
    assert len(long) <= 31


def test_registry_loads_no_heavy_library() -> None:
    code = (
        "import sys; from bf_core.exporting import registry as r; r.load_writers();"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy','openpyxl') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "", f"imported with the registry: {run.stdout.strip()}"


REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"


def test_source_files_come_out_byte_for_byte(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """«Исходный файл» of objects loaded from files: the file as loaded, under its own name, once for all the
    wireframes of one TRIDB; a Micromine STR written as STR keeps the layout of its source — the same bytes."""
    from bf_core.db import dialect
    from tests.test_import_objects import run_job

    r = pclient.post(f"{API}/projects", json={"name": "Исходные файлы"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    tridb, strings = REAL / "ORE SEP 2025.tridb", REAL / "Восточный Цители-Сопели.STR"
    run_job(project.folder, {"files": [str(tridb), str(strings)]})
    env = reg.Env(engine=project.engine, store=BlobStore(project.folder))
    with dialect.read_tx(project.engine) as conn:
        found = reg.items(conn)
    wfs = [i for i in found if i.kind == "wireframe"]
    lines = [i for i in found if i.kind == "line_file"]
    assert wfs and len(lines) == 1
    w = reg.writer("wireframe", "source")
    assert w is not None and all(w.accepts(i) is None for i in wfs)
    out = tmp_path / "Каркасы"
    out.mkdir()
    folder = names.Folder(len(str(out)))
    written = []
    for item in wfs:
        suffixes = w.suffixes(item, env)
        assert w.stem_of(item) == "ORE SEP 2025" and suffixes[0] == ".tridb"
        stem = folder.claim(w.stem_of(item), suffixes)
        assert w.write is not None
        written.append(w.write(item, out / (stem + suffixes[0]), env, lambda *_a: None))
    assert [p.name for p in written[0].files] == ["ORE SEP 2025.tridb"]
    assert all(not x.files and x.notes for x in written[1:])  # one file for the wireframes of one TRIDB
    assert (out / "ORE SEP 2025.tridb").read_bytes() == tridb.read_bytes()
    for fmt in ("source", "str"):
        lw = reg.writer("line_file", fmt)
        assert lw is not None and lw.write is not None
        target = tmp_path / fmt / (names.clean(lw.stem_of(lines[0])) + lw.suffixes(lines[0], env)[0])
        target.parent.mkdir()
        lw.write(lines[0], target, env, lambda *_a: None)
        assert target.name == "Восточный Цители-Сопели" + (".STR" if fmt == "source" else ".str")
        assert target.read_bytes() == strings.read_bytes(), fmt
