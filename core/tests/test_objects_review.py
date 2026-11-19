"""«Импорт файлов»: cases found in the Э7 review. The read options of a file (code page, the X, Y, Z columns of
a table of points) are found whatever way its path is spelled — «/» or «\\», another case on Windows."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import numpy as np
import pytest

from bf_core.importing.object_files import inspect_files, option_key, options_by_file
from tests.test_import_job import API, wait_job
from tests.test_import_objects import project, run_job  # noqa: F401 - `project` is a fixture

pytestmark = pytest.mark.windows

# Columns no reader would guess: without the options of the wizard the file does not load.
SURVEY = "A;B;H;Метка\n5912345,5;512345,25;412,5;Репер 1\n5912390,75;512371,5;415,25;Репер 2\n"
XYZ = np.array([[512_345.25, 5_912_345.5, 412.5], [512_371.5, 5_912_390.75, 415.25]])
COLUMNS = {"x": "B", "y": "A", "z": "H", "encoding": "cp1251"}


def survey_file(folder: Path) -> Path:
    path = folder / "Съёмка.csv"
    path.write_bytes(SURVEY.encode("cp1251"))
    return path


def test_option_keys_do_not_depend_on_the_spelling_of_a_path() -> None:
    win = PureWindowsPath
    plain = option_key(win(r"C:\Данные\Участок 1\Съёмка.csv"), win)  # what `plan` gives for a file on Windows
    for spelled in (
        "C:/Данные/Участок 1/Съёмка.csv",
        r"C:\Данные\Участок 1\Съёмка.csv",
        r"C:\Данные/Участок 1\\Съёмка.csv",
        "c:/данные/участок 1/съёмка.CSV",  # NTFS has no case
    ):
        assert option_key(spelled, win) == plain, spelled
    assert option_key(r"\\server\share\points.xyz", win) == option_key("//server/share/POINTS.xyz", win)
    assert option_key("C:/a/b.csv", win) != option_key("C:/a/c.csv", win)
    # where the file system has a case the names stay apart; a doubled separator is still the same file
    posix = PurePosixPath
    assert option_key("/data//pts.csv", posix) == option_key("/data/pts.csv", posix) == "/data/pts.csv"
    assert option_key("/data/Pts.csv", posix) != option_key("/data/pts.csv", posix)
    by_file = options_by_file({"C:/Данные/Съёмка.csv": COLUMNS, r"D:\x\y.xyz": {"z": ""}}, win)
    assert by_file[option_key(win(r"C:\Данные\Съёмка.csv"), win)] == COLUMNS
    assert by_file[option_key(win("d:/X/Y.XYZ"), win)] == {"z": ""}
    assert options_by_file(None) == {}


def spellings(path: Path) -> list[str]:
    """One file, spelled as a wizard may send it on this system."""
    return [str(path), f"{path.parent}//{path.name}", f"{path.parent}/./{path.name}"]


def test_options_reach_the_reader_however_the_path_is_spelled(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.db import dialect
    from bf_core.lines import store as ls_store
    from bf_core.storage.blobs import BlobStore

    path = survey_file(tmp_path)
    bare = inspect_files([str(path)])  # no options: the columns A, B, H say nothing
    assert bare["totals"]["failed"] == 1 and bare["files"][0]["ok"] is False
    for k, spelled in enumerate(spellings(path)):
        seen = inspect_files([str(path)], {spelled: COLUMNS})
        assert seen["totals"]["failed"] == 0 and seen["totals"]["points"] == 2, spelled
        ctx = run_job(project.folder, {"files": [str(path)], "options": {spelled: COLUMNS}})
        assert ctx.result["points"] == 2 and not ctx.result["failed"], spelled
        with dialect.read_tx(project.engine) as conn:
            layer_id = ls_store.layer_ids(conn, ctx.result["files"][0]["object_id"])[0]
            ls = ls_store.read_layer(conn, BlobStore(project.folder), layer_id)
        assert np.array_equal(ls.xyz, XYZ), (k, spelled)  # X and Y not swapped, the heights taken from «H»
        assert ls.attrs.column("Метка").to_pylist() == ["Репер 1", "Репер 2"]  # read as Windows-1251
    # the files of the selection may be spelled differently from the keys too
    ctx = run_job(project.folder, {"files": [spellings(path)[1]], "options": {str(path): COLUMNS}})
    assert ctx.result["points"] == 2 and not ctx.result["failed"]


def test_options_through_the_api(pclient, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pclient.post(f"{API}/projects", json={"name": "Точки"})
    path = survey_file(tmp_path)
    spelled = spellings(path)[1]
    body: dict[str, Any] = {"files": [str(path)], "options": {spelled: COLUMNS}}
    seen = pclient.post(f"{API}/import/objects/inspect", json=body)
    assert seen.status_code == 200, seen.text
    assert seen.json()["totals"]["failed"] == 0 and seen.json()["totals"]["points"] == 2
    r = pclient.post(f"{API}/import/objects", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(pclient, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["result"]["points"] == 2 and not job["result"]["failed"], job
    layer = pclient.get(f"{API}/lines/files").json()["files"][0]["layers"][0]
    page = pclient.get(f"{API}/lines/layers/{layer['id']}/attrs").json()
    assert [f["name"] for f in page["fields"]] == ["Метка"] and page["rows"] == [["Репер 1"], ["Репер 2"]]
