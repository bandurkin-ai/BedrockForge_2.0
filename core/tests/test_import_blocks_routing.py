"""Block files in the general import of objects (подшаг 17.38; ADR 0023 decision 5; ТЗ 7.31): a block `.dat`, `.csv`,
`.dm` and `.fbm` go to the import of blocks (`inspect_files` gives `blocks`, the job gives a model), the files that
are not block models — a table of points, a `.dm` of strings or of a wireframe — go the old way; a block `.dm` is no
longer «появится в одной из следующих версий»."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.formats import registry as reg
from bf_core.formats.errors import FormatError
from bf_core.importing import object_files as of
from tests.formats.dm_builder import F, build
from tests.test_datamine_blocks import octree_dm
from tests.test_import_blocks import regular, text, write_dat_model
from tests.test_import_objects import run_job


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bf_core.projects.project import create_folder

    yield SimpleNamespace(folder=create_folder(tmp_path / "projects", "Блоки", {}))


@pytest.fixture()
def files(tmp_path: Path) -> dict[str, Path]:
    m = regular()
    names = ("model.dat", "model.csv", "sub.dm", "sub.fbm", "points.csv", "strings.dm", "tri.dm", "table.dm")
    out = {n: tmp_path / n for n in names}
    write_dat_model(out["model.dat"], m)
    out["model.csv"].write_text(text(m), encoding="utf-8")
    octree_dm(out["sub.dm"])
    octree_dm(out["sub.fbm"], layout="fbm")
    out["points.csv"].write_text("X;Y;Z;AU\n1;2;3;0,5\n4;5;6;0,7\n7;8;9;0,9\n", encoding="utf-8")
    strings = [[1, 1, 0, 0, 0], [1, 2, 1, 1, 1], [1, 3, 2, 0, 1]]
    out["strings.dm"].write_bytes(build([F("PVALUE"), F("PTN"), F("XP"), F("YP"), F("ZP")], strings))
    out["tri.dm"].write_bytes(build([F("TRIANGLE"), F("PID1"), F("PID2"), F("PID3")], [[1, 1, 2, 3]]))
    out["table.dm"].write_bytes(build([F("A"), F("B")], [[1, 2]]))
    return out


def inspect(*paths: Path) -> dict[str, Any]:
    return of.inspect_files([str(p) for p in paths])


def test_a_block_dm_is_a_block_model_of_the_registry(files) -> None:  # type: ignore[no-untyped-def]
    assert reg.detect_object(files["sub.dm"]).key == reg.DATAMINE_BLOCKS
    assert reg.detect_object(files["sub.fbm"]).key == reg.DATAMINE_BLOCKS
    assert reg.detect_object(files["strings.dm"]).key == reg.DATAMINE_STR
    assert reg.detect_object(files["tri.dm"]).key == reg.DATAMINE
    assert ".fbm" in reg.OBJECT_SUFFIXES and "блочные модели .dat / .csv / .dm / .fbm" in reg.SUPPORTED
    with pytest.raises(FormatError) as exc:  # a table of something else keeps its refusal
        reg.detect_object(files["table.dm"])
    assert "следующих версий" not in exc.value.message + exc.value.hint
    with pytest.raises(FormatError) as old:  # the reader of lines and wireframes does not take a block model
        reg.read_objects(files["sub.dm"])
    assert old.value.code == "block_model" and old.value.hint


def test_block_files_are_inspected_as_block_models(files) -> None:  # type: ignore[no-untyped-def]
    got = inspect(files["model.dat"], files["model.csv"], files["sub.dm"], files["sub.fbm"])
    assert got["totals"]["files"] == 4 and got["totals"]["failed"] == 0 and got["totals"]["block_models"] == 4
    for entry in got["files"]:
        assert entry["ok"] and entry["kind"] == "block_model" and entry["blocks"]["error"] is None
        assert entry["blocks"]["grid"] is not None and entry["blocks"]["check3"]["ok"] is True
        assert entry["block_models"][0]["cells"] == entry["blocks"]["rows"]
    by = {e["file"]: e["blocks"] for e in got["files"]}
    assert by["sub.dm"]["sub"] and by["sub.fbm"]["sub"] and not by["model.dat"]["sub"]
    assert by["model.dat"]["format"] == "dat" and by["sub.dm"]["format"] == "dm" and by["sub.fbm"]["format"] == "fbm"


def test_a_point_table_and_other_dm_tables_go_the_old_way(files) -> None:  # type: ignore[no-untyped-def]
    got = inspect(files["points.csv"], files["strings.dm"], files["tri.dm"], files["table.dm"])
    by = {e["file"]: e for e in got["files"]}
    assert by["points.csv"]["kind"] == "points" and by["strings.dm"]["format"] == "datamine_str"
    assert not any("blocks" in e for e in got["files"])
    bad = by["table.dm"]
    assert bad["ok"] is False and bad["error"]["code"] in ("not_wireframe", "not_strings")
    assert "следующих версий" not in bad["error"]["message"] + bad["error"]["hint"]


def test_the_wizard_options_reach_the_blocks_reader(files) -> None:  # type: ignore[no-untyped-def]
    path = files["model.csv"]
    chosen = {str(path): {"blocks": {"fields": {"AU": {"skip": True}}}}}
    entry = of.inspect_files([str(path)], chosen)["files"][0]
    assert entry["kind"] == "block_model" and "AU" not in [f["name"] for f in entry["blocks"]["fields"]]


def test_the_job_imports_block_files_and_reports_what_it_found(project, files) -> None:  # type: ignore[no-untyped-def]
    names = ("model.dat", "sub.dm", "sub.fbm", "points.csv")
    ctx = run_job(project.folder, {"files": [str(files[n]) for n in names]})
    result = {e["file"]: e for e in ctx.result["files"]}  # type: ignore[attr-defined]
    for name in ("model.dat", "sub.dm", "sub.fbm"):
        e = result[name]
        assert e["kind"] == "block_models" and e["block_models"] == 1
        assert e["rows"] == e["cells"] and e["parents"] > 0 and e["checks"] == {"duplicates": 0, "overlaps": 0}
    assert result["sub.dm"]["sub"] is True and result["model.dat"]["sub"] is False
    assert result["points.csv"]["kind"] != "block_models"
