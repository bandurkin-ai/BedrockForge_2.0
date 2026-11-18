"""DWG through ODA File Converter, with a fake converter in its place (ТЗ 7.35 Д-13; ADR 0009 «Проверка»;
ADR 0013 8.9: «pytest с фальшивым конвертером: `oda_not_found`, нет результата, кириллица»).

The fake is a script with the command line of the real program (`in out version type recurse audit mask`). Its
"DWG" is a DWG signature followed by the DXF it was made from, so a drawing goes both ways through `ezdxf`'s
`odafc.convert` and the coordinates must come back bit for bit. The real converter is checked by hand on Windows
(CHECKLIST of the milestone).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
import json, os, sys
from pathlib import Path

here = Path(__file__).resolve().parent
mode = (here / "mode.txt").read_text(encoding="utf-8").strip()
in_dir, out_dir, version, kind, recurse, audit, mask = sys.argv[1:8]
with (here / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "input": sorted(os.listdir(in_dir))}) + "\n")
if mode == "fail":
    sys.stderr.write("ODA: unable to load the drawing\n")
    sys.exit(3)
if mode == "nothing":
    sys.exit(0)
source = Path(in_dir) / mask
target = Path(out_dir) / (source.stem + "." + kind.lower())
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
        else:
            self.exe = folder / "ODAFileConverter"
            self.exe.write_bytes(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n'.encode())
            self.exe.chmod(0o755)
        self.mode("ok")

    def mode(self, value: str) -> None:
        (self.folder / "mode.txt").write_text(value, encoding="utf-8")

    @property
    def calls(self) -> list[dict[str, list[str]]]:
        log = self.folder / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@contextmanager
def _no_display() -> Iterator[str]:
    yield ":0"


@pytest.fixture(autouse=True)
def alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No converter of the machine is found, temporary folders go to the test's own folder (returned), and ezdxf
    does not start an X server for the fake on Linux."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(dwg.ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing here"))
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(name, str(tmp_path / "Program Files"))
    monkeypatch.setattr(odafc, "_linux_dummy_display", _no_display, raising=False)
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
    """ezdxf looks in PATH first on Windows: the converter of «Настройка» must run all the same."""
    decoy = Oda(tmp_path / "decoy")
    decoy.mode("garbage")
    monkeypatch.setenv("PATH", str(decoy.folder))
    path, _dxf = drawing(tmp_path / "Проект")
    same(read_dwg(path, converter=oda.exe).layers, layers())
    assert len(oda.calls) == 1 and decoy.calls == []
    assert error(read_dwg, path).code == "oda_failed" and len(decoy.calls) == 1  # no setting: the one of PATH


def test_ezdxf_is_the_one_the_wrapper_was_written_for() -> None:
    """`dwg._convert` replaces ezdxf's search of the program for the call: a new ezdxf must be looked at again."""
    import ezdxf

    assert ezdxf.__version__ == "1.4.5" and callable(getattr(odafc, "_get_odafc_path", None))
    before = odafc._get_odafc_path
    saved = [ezdxf.options.get("odafc-addon", key) for key in ("win_exec_path", "unix_exec_path")]
    with pytest.raises(FormatError):
        dwg._convert(Path("нет.dwg"), Path("нет.dxf"), "ACAD2018", True, Path("нет"), writing=False)
    assert odafc._get_odafc_path is before  # put back after a failure as well
    assert [ezdxf.options.get("odafc-addon", key) for key in ("win_exec_path", "unix_exec_path")] == saved
