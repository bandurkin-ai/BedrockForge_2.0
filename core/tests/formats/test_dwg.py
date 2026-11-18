"""DWG through ODA File Converter, with a fake converter in its place (ТЗ 7.35 Д-13; ADR 0009 «Проверка»;
ADR 0013 8.9: «pytest с фальшивым конвертером: `oda_not_found`, нет результата, кириллица»).

The fake is a script with the command line of the real program (`in out version type recurse audit mask`). Its
"DWG" is a DWG signature followed by the DXF it was made from, so a drawing goes both ways through the converter
and the coordinates must come back bit for bit. The real converter is checked by hand on Windows (CHECKLIST of
the milestone). On Windows the fake is a `.cmd`, which `formats.dwg` does not take for a program: `Oda` lets it
(`dwg._SUFFIXES`), and the tests of that rule set the rule themselves.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from ezdxf.addons import odafc

from bf_core.formats import dwg
from bf_core.formats.dwg import dwg_output, dxf_of, dxf_to_dwg, find_converter, read_dwg, write_dwg
from bf_core.formats.dxf_lines import read_dxf
from bf_core.formats.dxf_write import write_dxf
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, from_lines, from_points

X0, Y0 = 466570.58600000001, 4584699.697000001
MAGIC = b"AC1027".ljust(128, b"\x00")
INSTALL = "Не найден ODA File Converter: установите его (бесплатно, Open Design Alliance) и укажите путь в «Настройке»"
FAKE = r"""
import json, os, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
mode = (here / "mode.txt").read_text(encoding="utf-8").strip()
in_dir, out_dir, version, kind, recurse, audit, mask = sys.argv[1:8]
display = os.environ.get("DISPLAY") or ""
seen = {"argv": sys.argv[1:], "input": sorted(os.listdir(in_dir)), "pid": os.getpid(), "display": display,
        "screen": os.path.exists("/tmp/.X11-unix/X" + display.lstrip(":"))}
with (here / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(seen) + "\n")
if mode == "fail":
    sys.stderr.write("ODA: unable to load the drawing\n")
    sys.exit(3)
if mode == "noise":  # Windows-1251 and bytes that are no text at all
    sys.stderr.buffer.write(b"ODA: \xed\xe5 \xf7\xe8\xf2\xe0\xe5\xf2\xf1\xff \xff\xfe\x80 cannot load\n")
    sys.exit(3)
if mode == "hang":
    time.sleep(120)
if mode == "nothing":
    sys.exit(0)
if mode == "chatty":  # more than a pipe holds: a program nobody listens to would wait for ever
    sys.stdout.buffer.write(b"ODA: a line of the progress report\n" * 40000)
    sys.stdout.flush()
source = Path(in_dir) / mask
target = Path(out_dir) / (source.stem + "." + (kind if mode == "upper" else kind.lower()))
magic = b"AC1027".ljust(128, b"\x00")
if mode == "garbage":
    target.write_bytes(b"\x00\x01 this is not a drawing")
elif mode == "empty":
    target.write_bytes(b"")
else:
    data = source.read_bytes()
    target.write_bytes(data[len(magic):] if kind == "DXF" else magic + data)
"""


class Oda:
    """A fake converter in a folder of its own: `exe` to run, `mode` — what it does, `calls` — how it was run."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True)
        self.folder = folder
        script = folder / "fake_oda.py"
        script.write_text(FAKE, encoding="utf-8")
        if os.name == "nt":
            self.exe = folder / "ODAFileConverter.cmd"
            self.exe.write_bytes(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n'.encode())
            dwg._SUFFIXES = (".exe", ".cmd")  # a script stands for the program: so it stays for the session
        else:
            self.exe = folder / "ODAFileConverter"
            self.exe.write_bytes(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n'.encode())
            self.exe.chmod(0o755)
        self.mode("ok")

    def mode(self, value: str) -> None:
        (self.folder / "mode.txt").write_text(value, encoding="utf-8")

    @property
    def calls(self) -> list[dict[str, Any]]:
        log = self.folder / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@contextmanager
def _no_display() -> Iterator[str]:
    """What the tests of other modules put in place of ezdxf's X server (ezdxf does not start the converter now)."""
    yield ":0"


@pytest.fixture(autouse=True)
def alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No converter of the machine is found (and no Xvfb: PATH is empty), temporary folders go to the test's own
    folder (returned), «ProgramData» is a folder of the test."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    return temp


@pytest.fixture()
def oda(tmp_path: Path) -> Oda:
    return Oda(tmp_path / "oda")


def layers() -> list[LineSet]:
    rnd = np.random.default_rng(7)

    def cloud(n: int, low: float) -> np.ndarray:
        return np.column_stack(
            [X0 + rnd.uniform(-5e3, 5e3, n), Y0 + rnd.uniform(-5e3, 5e3, n), rnd.uniform(low, 900, n)]
        )

    lines = [cloud(n, -200.0) for n in (2, 3, 7, 60, 4)]
    return [
        from_lines("Контуры карьера", lines, [False, True, True, False, False], color="#ff0000"),
        from_points("Устья скважин", cloud(50, 0.0), color="#0000ff"),
    ]


def same(got: list[LineSet], want: list[LineSet]) -> None:
    assert [x.name for x in got] == [x.name for x in want]
    for a, b in zip(got, want, strict=True):
        assert a.color == b.color and a.points_only == b.points_only
        assert a.xyz.tobytes() == b.xyz.tobytes()  # float64, bit for bit
        if b.offsets is not None and b.closed is not None:
            assert a.offsets is not None and a.closed is not None
            assert a.offsets.tolist() == b.offsets.tolist() and a.closed.tolist() == b.closed.tolist()


def drawing(folder: Path, name: str = "Чертёж участка №5.dwg") -> tuple[Path, Path]:
    """A fake DWG of `layers()` under a Russian name in a folder with other files; (the DWG, its DXF)."""
    folder.mkdir(parents=True, exist_ok=True)
    dxf = write_dxf(folder / "исходник.dxf", layers()).path
    path = folder / name
    path.write_bytes(MAGIC + dxf.read_bytes())
    (folder / "Секретный отчёт.dwg").write_bytes(MAGIC + b"another drawing of the same folder")
    (folder / "заметки.txt").write_text("не для конвертера", encoding="utf-8")
    return path, dxf


def error(call: object, *args: object, **kwargs: object) -> FormatError:
    with pytest.raises(FormatError) as info:
        call(*args, **kwargs)  # type: ignore[operator]
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def leftovers(temp: Path) -> list[str]:
    return sorted(p.name for p in temp.iterdir())


# ---------------------------------------------------------------- no converter


@pytest.mark.windows
def test_without_converter_says_how_to_install(tmp_path: Path, alone: Path) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    assert find_converter() is None and find_converter(tmp_path / "нет" / "ODAFileConverter.exe") is None
    err = error(read_dwg, path)
    assert err.code == "oda_not_found" and err.hint == f"{INSTALL} или сохраните чертёж в DXF."  # the text of Д-13
    target = tmp_path / "Проект" / "экспорт.dwg"
    err = error(write_dwg, target, layers())
    assert err.code == "oda_not_found" and err.hint == f"{INSTALL} или экспортируйте в DXF."
    assert not target.exists() and leftovers(alone) == []  # nothing was written before the converter was missed


def test_find_converter(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    assert find_converter(oda.exe) == oda.exe and find_converter(f'  "{oda.exe}" ') == oda.exe
    folder = tmp_path / "ODA" / "ODAFileConverter 26.4.0"
    folder.mkdir(parents=True)
    (folder / "ODAFileConverter.exe").write_bytes(b"MZ")
    assert find_converter(folder) == folder / "ODAFileConverter.exe"  # the folder of the program will do
    assert find_converter(tmp_path / "nothing") is None
    monkeypatch.setenv(dwg.ENV, str(oda.exe))
    assert find_converter() == oda.exe and find_converter(tmp_path / "удалён" / "ODAFileConverter.exe") == oda.exe
    assert find_converter(folder) == folder / "ODAFileConverter.exe"  # the setting comes before the variable
    monkeypatch.delenv(dwg.ENV)
    monkeypatch.setenv("PATH", str(oda.folder))
    found = find_converter()
    assert found is not None and found.parent == oda.folder


def test_installed_takes_the_newest(tmp_path: Path) -> None:
    root, other = tmp_path / "Program Files", tmp_path / "Program Files (x86)"
    names = ["ODAFileConverter 9.1.0", "ODAFileConverter 26.10.0", "ODAFileConverter 26.4.0", "ODAFileConverter"]
    for base, name in [(root, n) for n in names] + [(other, "ODAFileConverter_title 21.3.0")]:
        (base / "ODA" / name).mkdir(parents=True)
        (base / "ODA" / name / "ODAFileConverter.exe").write_bytes(b"MZ")
    (root / "ODA" / "ODAFileConverter 99.0.0").mkdir()  # a folder without the program
    assert dwg.installed([root, other, tmp_path / "нет"]) == root / "ODA" / names[1] / "ODAFileConverter.exe"
    assert dwg.installed([other]) == other / "ODA" / "ODAFileConverter_title 21.3.0" / "ODAFileConverter.exe"
    assert dwg.installed([tmp_path / "нет"]) is None and dwg.installed([]) is None


# ---------------------------------------------------------------- reading


@pytest.mark.windows
def test_read_drawing_with_russian_name(tmp_path: Path, oda: Oda, alone: Path) -> None:
    path, dxf = drawing(tmp_path / "Папка проекта «Восточный»")
    before = path.read_bytes()
    seen: list[tuple[float, str | None]] = []
    read = read_dwg(path, lambda fraction, message=None: seen.append((fraction, message)), converter=oda.exe)
    want = read_dxf(dxf)
    assert read.format == "dwg" and read.records == want.records == 55 and read.warnings == want.warnings
    same(read.layers, want.layers)
    same(read.layers, layers())
    (call,) = oda.calls
    assert call["argv"][2:] == ["ACAD2018", "DXF", "0", "1", "drawing.dwg"]
    assert call["input"] == ["drawing.dwg"]  # the converter saw one file: not the folder of the drawing
    assert Path(call["argv"][0]).parent.name.startswith("bf-oda-") and Path(call["argv"][0]).parent.parent == alone
    # the result is made in the folder of the call itself: no other folder appears in the temporary one
    assert [Path(a).name for a in call["argv"][:2]] == ["in", "out"]
    assert Path(call["argv"][1]).parent == Path(call["argv"][0]).parent
    assert not any("Чертёж" in a or "Папка" in a for a in call["argv"])  # no name of the user in the command line
    assert leftovers(alone) == [] and path.read_bytes() == before  # nothing is left, the drawing is as it was
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert any(m and "ждём ODA File Converter" in m for _, m in seen)


def test_dxf_of_gives_the_temporary_dxf(tmp_path: Path, oda: Oda, alone: Path) -> None:
    path, dxf = drawing(tmp_path / "Проект")
    with dxf_of(path, converter=oda.exe) as made:
        assert made.name == "drawing.dxf" and made.read_bytes() == dxf.read_bytes()
        assert len(leftovers(alone)) == 1
    assert leftovers(alone) == []
    with pytest.raises(RuntimeError), dxf_of(path, converter=oda.exe):  # an error of the reader: the folder goes
        raise RuntimeError
    assert leftovers(alone) == []


@pytest.mark.parametrize(
    ("mode", "text"),
    [
        ("nothing", "не создал файл DXF"),
        ("empty", "не похож на DXF"),
        ("garbage", "не похож на DXF"),
        ("fail", "не преобразовал DWG в DXF"),
    ],
)
def test_converter_gives_no_result(tmp_path: Path, oda: Oda, alone: Path, mode: str, text: str) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    oda.mode(mode)
    err = error(read_dwg, path, converter=oda.exe)
    assert err.code == "oda_failed" and text in err.message and "DXF" in err.hint
    assert len(oda.calls) == 1 and leftovers(alone) == []
    if mode == "fail":
        assert "unable to load the drawing" in err.message  # what the converter said


@pytest.mark.windows
def test_not_a_drawing(tmp_path: Path, oda: Oda, alone: Path) -> None:
    empty = tmp_path / "пусто.dwg"
    empty.write_bytes(b"")
    assert error(read_dwg, empty, converter=oda.exe).code == "empty_file"
    text = tmp_path / "текст.dwg"
    text.write_bytes(b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n")
    assert error(read_dwg, text, converter=oda.exe).code == "not_dwg"
    assert error(read_dwg, text).code == "not_dwg"  # told before the converter is looked for
    assert oda.calls == [] and leftovers(alone) == []
    old = tmp_path / "старый.dwg"
    old.write_bytes(b"AC2.10" + b"\x00" * 100)
    assert dwg.dwg_version(old) == "AC2.10" and dwg.dwg_version(drawing(tmp_path / "Проект")[0]) == "AC1027"


def test_cancel_before_the_converter(tmp_path: Path, oda: Oda, alone: Path) -> None:
    path, _dxf = drawing(tmp_path / "Проект")

    class Cancelled(Exception):
        pass

    def tick(fraction: float, message: str | None = None) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        read_dwg(path, tick, converter=oda.exe)
    assert oda.calls == [] and leftovers(alone) == []


def test_old_folders_of_ended_workers_are_removed(tmp_path: Path, oda: Oda, alone: Path) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    old, fresh, other = alone / "bf-oda-old", alone / "bf-oda-fresh", alone / "bf-dxf-other"
    for folder in (old, fresh, other):
        (folder / "in").mkdir(parents=True)
        (folder / "in" / "drawing.dwg").write_bytes(b"x")
    long_ago = time.time() - 2 * 24 * 3600
    for folder in (old, other):
        os.utime(folder, (long_ago, long_ago))
    read_dwg(path, converter=oda.exe)
    assert leftovers(alone) == ["bf-dxf-other", "bf-oda-fresh"]  # only its own, only the old


# ---------------------------------------------------------------- writing


@pytest.mark.windows
def test_write_and_read_back(tmp_path: Path, oda: Oda, alone: Path) -> None:
    folder = tmp_path / "Экспорт «Восточный»"
    folder.mkdir()
    target = folder / "Контуры и устья.dwg"
    seen: list[float] = []
    written = write_dwg(target, layers(), lambda fraction, message=None: seen.append(fraction), converter=oda.exe)
    assert written.path == target and written.size == target.stat().st_size > 128
    assert (written.lines, written.points) == (5, 50) and target.read_bytes().startswith(b"AC1027")
    assert sorted(p.name for p in folder.iterdir()) == [target.name] and leftovers(alone) == []
    (call,) = oda.calls
    assert call["argv"][2:] == ["ACAD2013", "DWG", "0", "0", "drawing.dxf"] and call["input"] == ["drawing.dxf"]
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0
    same(read_dwg(target, converter=oda.exe).layers, layers())  # the cycle is bit for bit


def test_version_of_the_dwg(tmp_path: Path, oda: Oda) -> None:
    target = tmp_path / "v.dwg"
    write_dwg(target, layers(), version="R2018", converter=oda.exe)
    write_dwg(target, layers(), version="ac1015", converter=oda.exe)  # the old file is replaced
    assert [c["argv"][2] for c in oda.calls] == ["ACAD2018", "ACAD2000"]
    err = error(write_dwg, target, layers(), version="R99", converter=oda.exe)
    assert err.code == "unsupported" and "R2013" in err.hint and len(oda.calls) == 2  # the converter was not run
    assert all(v in odafc.VERSION_MAP for v in (*dwg.VERSIONS, dwg.READ_VERSION, dwg.WRITE_VERSION))


@pytest.mark.parametrize("mode", ["nothing", "garbage", "fail"])
def test_failed_writing_keeps_the_old_file(tmp_path: Path, oda: Oda, alone: Path, mode: str) -> None:
    target = tmp_path / "Чертёж.dwg"
    target.write_bytes(b"the drawing that was here")
    oda.mode(mode)
    err = error(write_dwg, target, layers(), converter=oda.exe)
    assert err.code == "oda_failed" and "DXF" in err.hint
    assert target.read_bytes() == b"the drawing that was here"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [target.name] and leftovers(alone) == []


def test_dxf_to_dwg_and_output_block(tmp_path: Path, oda: Oda, alone: Path) -> None:
    dxf = write_dxf(tmp_path / "готовый.dxf", layers()).path
    made = dxf_to_dwg(dxf, tmp_path / "из готового.dwg", converter=oda.exe)
    assert made.read_bytes() == MAGIC + dxf.read_bytes() and dxf.exists()
    target = tmp_path / "не будет.dwg"
    with pytest.raises(RuntimeError), dwg_output(target, converter=oda.exe) as staged:  # the writer of the DXF failed
        staged.write_bytes(b"half a drawing")
        raise RuntimeError
    assert not target.exists() and len(oda.calls) == 1 and leftovers(alone) == []


# ---------------------------------------------------------------- which program runs


@pytest.mark.windows
def test_the_configured_converter_runs_not_the_one_in_path(
    tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converter of «Настройка» runs, whatever PATH holds; without the setting — the one of PATH."""
    decoy = Oda(tmp_path / "decoy")
    decoy.mode("garbage")
    monkeypatch.setenv("PATH", str(decoy.folder))
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert len(oda.calls) == 1 and decoy.calls == []
    assert error(read_dwg, path).code == "oda_failed" and len(decoy.calls) == 1  # no setting: the one of PATH


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
def test_a_converter_that_hangs_is_ended(tmp_path: Path, oda: Oda, alone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _dxf = drawing(tmp_path / "Проект")
    target = tmp_path / "Чертёж.dwg"
    target.write_bytes(b"the drawing that was here")
    oda.mode("hang")
    monkeypatch.setattr(dwg, "TIMEOUT_S", 0.5)
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
    assert "код завершения 3" in err.message or sys.platform == "linux"
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


def test_where_the_temporary_folder_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The choice, as it is made on Windows: the temporary folder when its path is ASCII, else its short path,
    else «ProgramData», else the folder as it is."""
    plain, cyr = tmp_path / "temp", tmp_path / "Иванов" / "Temp"
    shared = tmp_path / "ProgramData" / "BedrockForge" / "tmp"
    short: list[Path | None] = [tmp_path / "IVANOV~1" / "Temp"]
    monkeypatch.setattr(dwg, "_short_path", lambda folder: short[0])
    monkeypatch.setattr(tempfile, "tempdir", str(cyr))
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


def test_only_an_exe_is_a_program_on_windows(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule of Windows, set here so that it is checked on any system."""
    monkeypatch.setattr(dwg, "_SUFFIXES", (".exe",))
    folder = tmp_path / "tools"
    folder.mkdir()
    scripts = [folder / name for name in ("ODAFileConverter.cmd", "ODAFileConverter.bat", "ODAFileConverter", "oda.lnk")]
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
    exe = folder / "ODAFileConverter.EXE"
    exe.write_bytes(b"MZ")
    assert find_converter(exe, strict=True) == exe and dwg.program_at(exe) == exe
    assert find_converter(folder, strict=True) in (exe, folder / "ODAFileConverter.exe")  # the name as Windows finds it
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


def test_path_is_searched_without_the_current_folder(
    tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "current"
    here.mkdir()
    shutil.copy(oda.exe, here / "ODAFileConverter")  # executable, in the current folder
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


# ---------------------------------------------------------------- wireframes of a drawing


@pytest.mark.windows
def test_wireframes_of_a_dwg_name_the_dwg_as_their_source(tmp_path: Path, oda: Oda) -> None:
    from bf_core.formats.dxf_write_mesh import write_dxf_meshes
    from bf_core.formats.meshset import MeshSet

    from ..meshutil import cube, grid

    meshes = [MeshSet("Рудное тело", *cube(2, 10.0)), MeshSet("Рельеф", *grid(4, 3, lambda x, y: 0.5 * x + y))]
    dxf = write_dxf_meshes(tmp_path / "каркасы.dxf", meshes).path
    path = tmp_path / "Каркасы участка.dwg"
    path.write_bytes(MAGIC + dxf.read_bytes())
    want = read_dxf(dxf)
    read = read_dwg(path, converter=oda.exe)
    assert read.format == "dwg" and len(read.wireframes) == len(want.wireframes) == 2
    for got, ref in zip(read.wireframes, want.wireframes, strict=True):
        assert ref.source["format"] == "dxf" and got.source == {**ref.source, "format": "dwg"}
        assert got.name == ref.name and got.xyz.tobytes() == ref.xyz.tobytes() and got.tri.tobytes() == ref.tri.tobytes()
    assert [m.name for m in read.wireframes] == ["Рудное тело", "Рельеф"]
    assert read_dwg(path, converter=oda.exe, wireframes=False).wireframes == []


# ---------------------------------------------------------------- a screen for the converter (Linux)

XVFB = """#!/bin/sh
echo "$@" > "{here}/args.txt"
echo $$ > "{here}/pid.txt"
eval "echo 77 >&$2"
exec sleep 120
"""


@pytest.mark.skipif(sys.platform != "linux", reason="the converter needs a screen of its own on Linux only")
def test_xvfb_of_path_is_the_screen_of_the_converter(tmp_path: Path, oda: Oda, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "Xvfb").write_text(XVFB.format(here=bin_), encoding="utf-8")
    (bin_ / "Xvfb").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")  # `sleep` of the fake screen
    monkeypatch.setenv("DISPLAY", ":0")
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert oda.calls[-1]["display"] == ":77"  # the screen the server named, not the one of the user
    args = (bin_ / "args.txt").read_text(encoding="utf-8").split()
    assert args[0] == "-displayfd" and args[2:] == ["-screen", "0", "800x600x24"]
    with pytest.raises(ProcessLookupError):  # ended with the converter
        os.kill(int((bin_ / "pid.txt").read_text(encoding="utf-8")), 0)
    oda.mode("hang")  # … and when the converter is ended for its time
    monkeypatch.setattr(dwg, "TIMEOUT_S", 0.5)
    assert error(read_dwg, path, converter=oda.exe).code == "oda_timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int((bin_ / "pid.txt").read_text(encoding="utf-8")), 0)
    # a server that does not come up: the converter runs with the screen there is
    (bin_ / "Xvfb").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
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
    assert call["display"].startswith(":") and call["display"][1:].isdigit() and call["screen"] is True
    time.sleep(0.2)
    assert not os.path.exists("/tmp/.X11-unix/X" + call["display"][1:])  # the server is gone with its screen
