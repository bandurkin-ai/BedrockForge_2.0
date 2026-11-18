"""/api/v1/tools/oda — «Настройка › Внешние программы» (ТЗ 7.35 Д-13; ADR 0009 «DWG — вариант А»; ADR 0013 §5):
the path of ODA File Converter the interface keeps, the search the DWG reader makes (`formats.dwg.find_converter`:
the setting → BF_ODA_CONVERTER → PATH → «Program Files\\ODA»), the answer «найден ли, путь, версия» with the
Russian text of how to install the program. The converter is never run here: files with its name stand for it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bf_core.api.app import CoreState, create_app
from bf_core.formats import dwg
from bf_core.importing import tools
from tests.conftest import TOKEN

pytestmark = pytest.mark.windows

URL = "/api/v1/tools/oda"
EXE = "ODAFileConverter.exe"


@pytest.fixture(autouse=True)
def alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No converter of the machine is found: no variable, an empty PATH, empty «Program Files»."""
    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))


@pytest.fixture()
def api(state: CoreState) -> TestClient:
    return TestClient(create_app(state), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN})


def program(folder: Path, name: str = EXE) -> Path:
    """A file that stands for the converter (it is looked for, never started)."""
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_bytes(b"MZ fake")
    exe.chmod(0o755)
    return exe


def test_nothing_installed_says_how_to_install(api: TestClient) -> None:
    r = api.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert (body["configured"], body["path"], body["version"], body["source"], body["auto"]) == (None,) * 5
    assert body["message"] == "ODA File Converter не найден."
    assert body["warning"] == ""
    # how to install: free, Open Design Alliance; and what to do without it (ADR 0009)
    for part in ("бесплатн", "Open Design Alliance", "ODAFileConverter.exe", "DXF"):
        assert part in body["hint"]
    assert body["install"] and body["install"] in body["hint"]
    assert "http" not in body["hint"] and "www." not in body["hint"]  # a text, not a link (ТЗ 17.4.1)


def test_put_program_found_with_version(api: TestClient, state: CoreState, tmp_path: Path) -> None:
    exe = program(tmp_path / "Program Files" / "ODA" / "ODAFileConverter 26.10.0")
    body = api.put(URL, json={"path": str(exe)}).json()
    assert body["found"] is True
    assert body["configured"] == str(exe)
    assert body["path"] == str(exe)
    assert body["source"] == "configured"
    assert body["version"] == "26.10.0"
    assert body["message"] == "ODA File Converter найден, версия 26.10.0."
    assert (body["warning"], body["hint"]) == ("", "")
    assert api.get(URL).json() == body  # the core remembers the path until it is restarted
    assert tools.oda_converter(state) == str(exe)  # what the import and export jobs give to `formats.dwg`


def test_put_folder_of_the_program(api: TestClient, tmp_path: Path) -> None:
    exe = program(tmp_path / "Чертежи и программы" / "ODA File Converter")  # Cyrillic, spaces, no version
    body = api.put(URL, json={"path": str(exe.parent)}).json()
    assert body["found"] is True
    assert body["configured"] == str(exe.parent)
    assert body["path"] == str(exe)
    assert body["source"] == "configured"
    assert body["version"] is None
    assert body["message"] == "ODA File Converter найден."


def test_quotes_and_spaces_of_a_pasted_path(api: TestClient, tmp_path: Path) -> None:
    exe = program(tmp_path / "ODAFileConverter 25.4.0")
    body = api.put(URL, json={"path": f'  "{exe}"  '}).json()  # «Копировать как путь» of Explorer puts quotes
    assert body["configured"] == str(exe)
    assert body["found"] is True and body["version"] == "25.4.0"


def test_stale_path_falls_back_to_the_search(api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The converter was updated: the folder of the setting carries the old version (`find_converter`)."""
    new = program(tmp_path / "apps" / "ODAFileConverter 27.1.0")
    monkeypatch.setenv(dwg.ENV, str(new))
    old = tmp_path / "apps" / "ODAFileConverter 26.10.0" / EXE
    body = api.put(URL, json={"path": str(old)}).json()
    assert body["found"] is True
    assert body["configured"] == str(old)
    assert body["path"] == str(new) and body["auto"] == str(new)
    assert body["source"] == "auto"
    assert body["version"] == "27.1.0"
    assert "По указанному пути программы нет" in body["warning"]
    assert str(new) in body["warning"]


def test_stale_path_and_nothing_else(api: TestClient, state: CoreState, tmp_path: Path) -> None:
    gone = tmp_path / "ODAFileConverter 26.10.0" / EXE
    body = api.put(URL, json={"path": str(gone)}).json()
    assert body["found"] is False
    assert body["configured"] == str(gone)  # kept: the interface owns the setting
    assert body["path"] is None and body["source"] is None
    assert body["message"] == "ODA File Converter не найден: по указанному пути программы нет."
    assert "Open Design Alliance" in body["hint"]
    assert tools.oda_converter(state) == str(gone)


def test_found_without_a_setting(api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = program(tmp_path / "bin", "ODAFileConverter.exe" if os.name == "nt" else "ODAFileConverter")
    monkeypatch.setenv("PATH", str(exe.parent))
    body = api.get(URL).json()
    assert body["found"] is True
    assert body["configured"] is None
    assert Path(body["path"]) == exe and body["auto"] == body["path"]
    assert body["source"] == "auto"
    assert body["message"] == "ODA File Converter найден."
    assert body["warning"] == ""


def test_clear(api: TestClient, state: CoreState, tmp_path: Path) -> None:
    exe = program(tmp_path / "ODAFileConverter 26.10.0")
    assert api.put(URL, json={"path": str(exe)}).json()["found"] is True
    for empty in (None, "", "   ", '""'):
        api.put(URL, json={"path": str(exe)})
        body = api.put(URL, json={"path": empty}).json()
        assert (body["configured"], body["found"], body["path"]) == (None, False, None)
        assert tools.oda_converter(state) is None
    assert api.put(URL, json={}).json()["configured"] is None


def test_another_program_is_warned_about(api: TestClient, tmp_path: Path) -> None:
    other = program(tmp_path / "Autodesk", "acad.exe")
    body = api.put(URL, json={"path": str(other)}).json()
    # `formats.dwg` runs the program of the setting whatever its name: the answer says what will be run
    assert body["found"] is True and body["path"] == str(other)
    assert body["version"] is None
    assert "ODAFileConverter" in body["warning"] and "acad.exe" in body["warning"]


def test_version_from_the_file_name(api: TestClient, tmp_path: Path) -> None:
    exe = program(tmp_path / "Applications", "ODAFileConverter_QT6_lnxX64_8.3dll_26.10.AppImage")
    assert api.put(URL, json={"path": str(exe)}).json()["version"] == "26.10"


@pytest.mark.skipif(os.name == "nt", reason="the execute bit is a POSIX matter")
def test_not_executable_is_warned_about(api: TestClient, tmp_path: Path) -> None:
    exe = program(tmp_path / "ODAFileConverter 26.10.0", "ODAFileConverter")
    exe.chmod(0o644)
    body = api.put(URL, json={"path": str(exe)}).json()
    assert body["found"] is True
    assert "не помечен как исполняемый" in body["warning"]


@pytest.mark.parametrize(
    "bad",
    ["ODAFileConverter.exe", "relative/folder", "C:\x00\\ODA", "/tmp/a\nb", "x" * 2001],
    ids=["name only", "relative", "nul", "newline", "too long"],
)
def test_bad_paths_are_refused_and_nothing_changes(api: TestClient, state: CoreState, tmp_path: Path, bad: str) -> None:
    exe = program(tmp_path / "ODAFileConverter 26.10.0")
    api.put(URL, json={"path": str(exe)})
    r = api.put(URL, json={"path": bad})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] in {"bad_path", "bad_request"}
    assert body["message"] and body["hint"]
    assert tools.oda_converter(state) == str(exe)


def test_wrong_type_is_a_bad_request(api: TestClient) -> None:
    r = api.put(URL, json={"path": 5})
    assert r.status_code == 422 and r.json()["code"] == "bad_request"


def test_guarded_by_the_token(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={"path": None}).status_code == 401


def test_each_core_has_its_own_setting(tmp_path: Path) -> None:
    exe = program(tmp_path / "ODAFileConverter 26.10.0")
    one, two = CoreState(token=TOKEN), CoreState(token=TOKEN)
    tools.set_oda_converter(one, str(exe))
    assert tools.oda_converter(one) == str(exe)
    assert tools.oda_converter(two) is None
    assert tools.oda_status(None)["found"] is False
