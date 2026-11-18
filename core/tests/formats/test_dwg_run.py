"""How ODA File Converter is run (`formats.dwg`; substep 8.28 of Э8, the findings of the review): the command line
and the folders it is given, a converter that does not start, hangs or prints what is no text, the temporary
folder of a user named in Cyrillic, what is a program on Windows and where PATH is searched, the screen the
converter gets on Linux. The fake converter and its drawings are those of `test_dwg`.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from ezdxf.addons import odafc

from bf_core.formats import dwg
from bf_core.formats.dwg import dxf_of, find_converter, read_dwg, write_dwg
from bf_core.formats.errors import FormatError
from tests.formats import test_dwg as base
from tests.formats.test_dwg import Oda, drawing, error, layers, leftovers, same

alone, oda = base.alone, base.oda  # the fixtures of that module: no converter of the machine; a fake one


@pytest.mark.windows
def test_the_converter_is_started_as_ezdxf_starts_it(tmp_path: Path, oda: Oda, alone: Path) -> None:
    """The command line is the one of `ezdxf.addons.odafc` (1.4.5), from which only the version names are taken
    now: its settings stay as they were and it makes no folder of its own."""
    import ezdxf

    assert ezdxf.__version__ == "1.4.5"
    source, target = tmp_path / "w" / "in" / "drawing.dwg", tmp_path / "w" / "out" / "drawing.dxf"
    for audit, version in ((True, "ACAD2018"), (False, "ACAD2000")):
        want = odafc._odafc_arguments(source.name, str(source.parent), str(target.parent), "DXF", version, audit)
        assert dwg._arguments(source, target, version, audit) == want
    back = dwg._arguments(target.with_suffix(".dxf"), source.with_suffix(".dwg"), "ACAD2013", False)
    assert back[2:] == ["ACAD2013", "DWG", "0", "0", "drawing.dxf"]
    keys = ("win_exec_path", "unix_exec_path")
    saved = [ezdxf.options.get("odafc-addon", key) for key in keys]
    search = getattr(odafc, "_get_odafc_path", None)
    path, _dxf = drawing(tmp_path / "Проект")
    with dxf_of(path, converter=oda.exe):
        assert [p.name[:7] for p in alone.iterdir()] == ["bf-oda-"]  # one folder while the DXF is in use
    assert [ezdxf.options.get("odafc-addon", key) for key in keys] == saved
    assert getattr(odafc, "_get_odafc_path", None) is search and not hasattr(dwg, "_LOCK")


@pytest.mark.windows
def test_a_program_that_does_not_start(tmp_path: Path, alone: Path) -> None:
    broken = tmp_path / "ODA" / "ODAFileConverter.exe"
    broken.parent.mkdir()
    broken.write_bytes(b"MZ this is not a program")  # no execute bit on Linux, not an executable on Windows
    path, _dxf = drawing(tmp_path / "Проект")
    err = error(read_dwg, path, converter=broken)
    assert err.code == "oda_failed" and "не запустился" in err.message and "DXF" in err.hint
    target = tmp_path / "Проект" / "экспорт.dwg"
    err = error(write_dwg, target, layers(), converter=broken)
    assert err.code == "oda_failed" and "не запустился" in err.message
    assert not target.exists() and leftovers(alone) == []


@pytest.mark.skipif(os.name == "nt", reason="the fake is a .cmd there: ending it does not end the script it runs")
def test_a_converter_that_hangs_is_ended(
    tmp_path: Path, oda: Oda, alone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    target = tmp_path / "Чертёж.dwg"
    target.write_bytes(b"the drawing that was here")
    oda.mode("hang")
    monkeypatch.setattr(dwg, "TIMEOUT_S", 1.5)
    monkeypatch.setattr(dwg, "TIMEOUT_S_PER_MB", 0.0)
    started = time.monotonic()
    err = error(read_dwg, path, converter=oda.exe)
    assert err.code == "oda_timeout" and "ODA File Converter" in err.message and "DXF" in err.hint
    err = error(write_dwg, target, layers(), converter=oda.exe)
    assert err.code == "oda_timeout" and "DXF" in err.hint
    assert time.monotonic() - started < 20.0  # not the two minutes the converter would sleep
    assert target.read_bytes() == b"the drawing that was here" and leftovers(alone) == []
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [target.name]
    for call in oda.calls:  # both runs were ended, not left behind
        with pytest.raises(ProcessLookupError):
            os.kill(call["pid"], 0)
    assert len(oda.calls) == 2


def test_the_time_given_grows_with_the_file(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    given: list[float] = []
    run = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        given.append(kwargs["timeout"])
        return run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    path, _dxf = drawing(tmp_path / "Проект")
    size = path.stat().st_size
    read_dwg(path, converter=oda.exe)
    assert given == [pytest.approx(dwg.TIMEOUT_S + dwg.TIMEOUT_S_PER_MB * size / 2**20, rel=1e-12)]
    assert dwg.TIMEOUT_S >= 300.0 and given[0] > dwg.TIMEOUT_S


@pytest.mark.windows
def test_output_of_the_converter_in_any_bytes_and_any_amount(tmp_path: Path, oda: Oda, alone: Path) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    oda.mode("noise")  # not UTF-8: the error is told all the same, with what can be read of it
    err = error(read_dwg, path, converter=oda.exe)
    assert err.code == "oda_failed" and "cannot load" in err.message and "\ufffd" in err.message
    assert "код завершения 3" in err.message
    oda.mode("chatty")  # 1,4 МБ of output: read while the converter runs, the drawing is converted
    same(read_dwg(path, converter=oda.exe).layers, layers())
    oda.mode("upper")  # `drawing.DXF`: the result is found whatever the case of its extension
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert len(oda.calls) == 3 and leftovers(alone) == []


# ---------------------------------------------------------------- the temporary folder


@pytest.mark.windows
def test_cyrillic_temporary_folder(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    """The temporary folder of a user named in Cyrillic: the drawing is read; on Windows the command line of the
    converter is ASCII all the same (the short path of the folder or «ProgramData»)."""
    temp = tmp_path / "Пользователь Иванов" / "Temp"
    temp.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    target = tmp_path / "Проект" / "экспорт.dwg"
    write_dwg(target, layers(), converter=oda.exe)
    same(read_dwg(target, converter=oda.exe).layers, layers())
    assert len(oda.calls) == 3 and leftovers(temp) == []
    shared = tmp_path / "ProgramData" / "BedrockForge" / "tmp"
    assert not shared.exists() or leftovers(shared) == []
    for call in oda.calls:
        folder = Path(call["argv"][0]).parent
        assert folder.name.startswith("bf-oda-") and call["input"] in (["drawing.dwg"], ["drawing.dxf"])
        if os.name == "nt":
            assert all(a.isascii() for a in call["argv"]), call["argv"]
        else:
            assert folder.parent == temp  # other systems take such a path as it is


@pytest.mark.windows
def test_where_the_temporary_folder_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The choice, as it is made on Windows: the temporary folder when its path is ASCII, else its short path,
    else «ProgramData», else the folder as it is."""
    plain, cyr = tmp_path / "temp", tmp_path / "Иванов" / "Temp"
    shared = tmp_path / "ProgramData" / "BedrockForge" / "tmp"
    short: list[Path | None] = [tmp_path / "IVANOV~1" / "Temp"]
    monkeypatch.setattr(dwg, "_short_path", lambda folder: short[0])
    monkeypatch.setattr(tempfile, "tempdir", str(cyr))
    monkeypatch.setattr(dwg, "_WINDOWS", False)
    assert dwg._bases() == [cyr]  # not Windows: as it is
    monkeypatch.setattr(dwg, "_WINDOWS", True)
    assert dwg._bases() == [short[0], shared, cyr]
    short[0] = tmp_path / "ИВАНОВ" / "Temp"  # a short name in the code page of the system: Cyrillic again
    assert dwg._bases() == [shared, cyr]
    short[0] = None  # the volume keeps no short names
    assert dwg._bases() == [shared, cyr]
    monkeypatch.setenv("ProgramData", str(tmp_path / "Данные программ"))
    assert dwg._bases() == [cyr]
    monkeypatch.delenv("ProgramData")
    assert dwg._bases() == [cyr]
    monkeypatch.setattr(tempfile, "tempdir", str(plain))
    short[0] = tmp_path / "never asked"
    assert dwg._bases() == [plain]


def test_short_path_or_program_data_keep_the_command_line_ascii(
    tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same on any system: a link stands for the short 8.3 path of the Cyrillic folder."""
    cyr = tmp_path / "Иванов" / "Temp"
    cyr.mkdir(parents=True)
    alias = tmp_path / "IVANOV~1"
    try:
        alias.symlink_to(tmp_path / "Иванов", target_is_directory=True)
    except OSError:
        pytest.skip("no right to make a link")
    monkeypatch.setattr(tempfile, "tempdir", str(cyr))
    monkeypatch.setattr(dwg, "_WINDOWS", True)
    monkeypatch.setattr(dwg, "_short_path", lambda folder: alias / "Temp")
    path, _dxf = drawing(tmp_path / "Проект")
    with dxf_of(path, converter=oda.exe) as made:
        assert made.parent.parent.parent == alias / "Temp" and str(made).isascii()
        assert len(leftovers(cyr)) == 1  # it is the same folder
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert all(a.isascii() for call in oda.calls for a in call["argv"]) and leftovers(cyr) == []
    # no short path that can be used (it names nothing and cannot be made): «ProgramData»
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file, not a folder")
    monkeypatch.setattr(dwg, "_short_path", lambda folder: blocker / "Temp")
    shared = tmp_path / "ProgramData" / "BedrockForge" / "tmp"
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert Path(oda.calls[-1]["argv"][0]).parent.parent == shared and leftovers(shared) == []
    assert all(a.isascii() for a in oda.calls[-1]["argv"])
    # nor «ProgramData»: the folder as it is, the drawing is read all the same
    monkeypatch.setenv("ProgramData", str(blocker / "ProgramData"))
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert Path(oda.calls[-1]["argv"][0]).parent.parent == cyr and leftovers(cyr) == []


@pytest.mark.windows
def test_short_path_of_windows(tmp_path: Path) -> None:
    folder = tmp_path / "Каталог пользователя"
    folder.mkdir()
    short = dwg._short_path(folder)
    if os.name == "nt":  # a volume may keep no short names: then there is none
        assert short is None or (short.is_dir() and os.path.samefile(short, folder))
        assert dwg._short_path(tmp_path / "нет такой папки") is None
    else:
        assert short is None


# ---------------------------------------------------------------- what is a program


@pytest.mark.windows
def test_only_an_exe_is_a_program_on_windows(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule of Windows, set here so that it is checked on any system."""
    monkeypatch.setattr(dwg, "_SUFFIXES", (".exe",))
    folder = tmp_path / "tools"
    folder.mkdir()
    scripts = [
        folder / name for name in ("ODAFileConverter.cmd", "ODAFileConverter.bat", "ODAFileConverter", "oda.lnk")
    ]
    for script in scripts:
        shutil.copyfile(oda.exe, script)
        script.chmod(0o755)
    path, _dxf = drawing(tmp_path / "Проект")
    for script in scripts:
        assert find_converter(script) is None  # passed by, as a path that names nothing
        with pytest.raises(FormatError) as info:
            find_converter(script, strict=True)
        assert info.value.code == "bad_path" and script.name in info.value.message and ".exe" in info.value.hint
        with pytest.raises(FormatError) as info:
            dwg.program_at(script)
        assert info.value.code == "bad_path"
        err = error(read_dwg, path, converter=script)  # the reader says so and runs nothing
        assert err.code == "bad_path" and "«Настройке»" in err.message
        assert error(write_dwg, tmp_path / "x.dwg", layers(), converter=script).code == "bad_path"
    assert oda.calls == [] and not (tmp_path / "x.dwg").exists()
    assert find_converter(folder) is None and dwg.program_at(folder) is None  # a folder with scripts only
    assert dwg.program_at(tmp_path / "нет") is None
    upper, exe = folder / "ODAFC.EXE", folder / "ODAFileConverter.exe"
    for program in (upper, exe):
        program.write_bytes(b"MZ")
        assert find_converter(program, strict=True) == program and dwg.program_at(program) == program
    assert find_converter(folder, strict=True) == exe and dwg.program_at(folder) == exe
    other = folder / "acad.exe"  # another name is a matter of «Настройка»: it warns
    other.write_bytes(b"MZ")
    assert find_converter(other, strict=True) == other
    # the variable: the same rule; a good setting comes first and the variable is not looked at
    monkeypatch.setenv(dwg.ENV, str(scripts[0]))
    assert find_converter() is None and find_converter(exe, strict=True) == exe
    with pytest.raises(FormatError) as info:
        find_converter(strict=True)
    assert info.value.code == "bad_path" and dwg.ENV in info.value.message
    err = error(read_dwg, path)
    assert err.code == "bad_path" and dwg.ENV in err.message


@pytest.mark.windows
def test_path_is_searched_without_the_current_folder(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    here = tmp_path / "current"
    here.mkdir()
    for name in ("ODAFileConverter", "ODAFileConverter.exe", "ODAFileConverter.cmd"):
        shutil.copy(oda.exe, here / name)  # executable, in the current folder
    monkeypatch.chdir(here)
    for value in ("", ".", os.pathsep, f".{os.pathsep}", "current", f"{os.pathsep}{tmp_path / 'nothing here'}"):
        monkeypatch.setenv("PATH", value)
        assert find_converter() is None, value
    monkeypatch.delenv("PATH")
    assert find_converter() is None
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(tmp_path / "nothing here"), f'"{oda.folder}"']))
    assert find_converter() == oda.folder / oda.exe.name
    if os.name != "nt":  # a file that is not executable is not a program of PATH
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "ODAFileConverter").write_bytes(b"#!/bin/sh\n")
        monkeypatch.setenv("PATH", str(plain))
        assert find_converter() is None
    # Windows: an `.exe` of PATH only, whatever PATHEXT allows
    monkeypatch.setattr(dwg, "_SUFFIXES", (".exe",))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("ODAFileConverter.cmd", "ODAFileConverter.bat", "ODAFileConverter.com", "ODAFileConverter"):
        shutil.copy(oda.exe, bin_ / name)
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setenv("PATHEXT", ".COM;.BAT;.CMD;.EXE")
    assert find_converter() is None
    (bin_ / "ODAFileConverter.exe").write_bytes(b"MZ")
    assert find_converter() == bin_ / "ODAFileConverter.exe"


# ---------------------------------------------------------------- a screen for the converter (Linux)

XVFB = """
import os, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
(here / "args.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
(here / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
if (here / "broken.txt").exists():
    sys.exit(1)
fd = int(sys.argv[sys.argv.index("-displayfd") + 1])
os.write(fd, b"77")  # the display the server listens on: the number, then — apart, as Xvfb does — a new line
time.sleep(0.2)
os.write(fd, b"\\n")  # a reader that went away after the number ends the real server here (a broken pipe)
os.close(fd)
(here / "ready.txt").write_text("the answer was read whole", encoding="utf-8")
time.sleep(120)
"""


def fake_xvfb(folder: Path) -> None:
    folder.mkdir()
    (folder / "fake_xvfb.py").write_text(XVFB, encoding="utf-8")
    (folder / "Xvfb").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{folder}/fake_xvfb.py" "$@"\n', encoding="utf-8")
    (folder / "Xvfb").chmod(0o755)


@pytest.mark.skipif(sys.platform != "linux", reason="the converter needs a screen of its own on Linux only")
def test_xvfb_of_path_is_the_screen_of_the_converter(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_ = tmp_path / "bin"
    fake_xvfb(bin_)
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setenv("DISPLAY", ":0")
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert oda.calls[-1]["display"] == ":77"  # the screen the server named, not the one of the user
    args = (bin_ / "args.txt").read_text(encoding="utf-8").split()
    assert args[0] == "-displayfd" and args[2:] == ["-screen", "0", "800x600x24"]
    assert (bin_ / "ready.txt").exists()  # the converter was run when the server had said all: not after the number
    with pytest.raises(ProcessLookupError):  # ended with the converter
        os.kill(int((bin_ / "pid.txt").read_text(encoding="utf-8")), 0)
    oda.mode("hang")  # … and when the converter is ended for its time
    monkeypatch.setattr(dwg, "TIMEOUT_S", 0.5)
    assert error(read_dwg, path, converter=oda.exe).code == "oda_timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int((bin_ / "pid.txt").read_text(encoding="utf-8")), 0)
    # a server that does not come up: the converter runs with the screen there is
    (bin_ / "broken.txt").write_text("the server ends at once", encoding="utf-8")
    oda.mode("ok")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert oda.calls[-1]["display"] == ":0"


@pytest.mark.skipif(sys.platform != "linux" or not Path("/usr/bin/Xvfb").exists(), reason="no Xvfb on this machine")
def test_real_xvfb_gives_a_screen(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    monkeypatch.delenv("DISPLAY", raising=False)
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    call = oda.calls[-1]
    assert call["display"][:1] == ":" and call["display"][1:].isdigit() and call["screen"] is True
    # drawings at once (other tests of the machine may run as well): each converter has a screen that is there
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        for read in pool.map(lambda _: read_dwg(path, converter=oda.exe), range(8)):
            same(read.layers, layers())
    assert len(oda.calls) == 9 and all(c["display"][1:].isdigit() and c["screen"] is True for c in oda.calls)
