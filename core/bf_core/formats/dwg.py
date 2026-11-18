"""AutoCAD DWG through ODA File Converter (ТЗ 7.35 Д-13, Д-17; ADR 0009 — вариант А; ADR 0013 §2).

BF2 does not read or write DWG itself. The user installs ODA File Converter (free, Open Design Alliance; it is not
distributed with BF2 and is not linked to it), and this module runs it as a separate program:
  * `read_dwg(path, progress, converter=…)` → `DxfRead` (`format = "dwg"`): the drawing becomes a temporary DXF
    R2018 that the DXF reader reads (`dxf_lines.read_dxf` with its options: whatever it gives for a DXF, lines
    and wireframes, it gives for a DWG; `source["format"]` of the wireframes is "dwg"). `dxf_of(path)` is the
    same temporary DXF for any other reader. The project keeps the DWG itself as the source;
  * `dwg_output(path)` gives the path of a DXF to write; when the block ends, that DXF becomes the DWG `path`
    (R2013 unless `version` says otherwise). `write_dwg(path, layers)` does so with the DXF writer of lines,
    `dxf_to_dwg(dxf, dwg)` with a DXF that already exists; a writer of wireframes takes `dwg_output` itself;
  * `find_converter(configured)` → the program to run, or None: the path of «Настройка» (the program or its
    folder), else the variable `BF_ODA_CONVERTER`, else `ODAFileConverter` in a folder of PATH (a folder PATH
    names in full: never the current one), else on Windows the newest
    `…\\Program Files\\ODA\\ODAFileConverter*\\ODAFileConverter.exe`. On other systems there is no known place.
    On Windows a program is an `.exe` only: a `.cmd`, a `.bat`, a shortcut are never run — the reader and the
    writer answer `bad_path` when the setting or the variable names such a file (`program_at`, `strict`).
    Without a converter every function raises `oda_not_found` with the text of Д-13.

Around the converter (ADR 0013, «Риски»):
  * it works in a temporary folder of its own, `bf-oda-…`: `in/drawing.dwg` is a copy of the drawing under an
    ASCII name, the result is made in `out/` next to it. The converter is given the two folders and a file mask,
    so it sees this file only and the name of the drawing never gets into its command line. The folder is made
    in the temporary folder of the user, and on Windows that path may itself hold letters beyond ASCII (a user
    named in Cyrillic): then its short 8.3 path is taken, and where the volume keeps no short names (or they
    are Cyrillic too) — `%ProgramData%\\BedrockForge\\tmp`. Only when neither can be had does the command line
    carry such letters;
  * the converter is started here, not by ezdxf (which waits for it without a limit, holds its output in pipes
    nobody reads meanwhile and makes one more folder in the temporary one): `subprocess.run` without a window,
    the output read to the end and decoded whatever its bytes are, a run longer than `TIMEOUT_S` (and
    `TIMEOUT_S_PER_MB` for a megabyte of the file) ended — `oda_timeout`. Of ezdxf only the names of the
    versions are used; its settings are not touched, so nothing is shared between threads;
  * the converter may end well and make nothing: the result is checked (it exists, is not empty, begins as a
    DXF or a DWG does);
  * the converter reports no progress and is not stopped by the user: `progress` is called before and after
    it («ждём ODA File Converter»). A job is cancelled by ending its worker (`CANCEL_GRACE_S`); folders such
    workers leave are removed by the next call when they are a day old;
  * a DWG is written next to its place as `.part` and renamed: a failed conversion leaves the old file.
Error codes: oda_not_found, oda_failed, oda_timeout, bad_path, not_dwg, empty_file, unsupported (the version
asked for) and those of the DXF reader and writer.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

if TYPE_CHECKING:
    from bf_core.formats.dxf_layers import DxfRead
    from bf_core.formats.dxf_write import DxfWritten
    from bf_core.formats.lineset import LineSet

PROGRAM = "ODAFileConverter"
ENV = "BF_ODA_CONVERTER"
READ_VERSION = "R2018"  # of the temporary DXF: the newest, nothing of the drawing is lost
WRITE_VERSION = "R2013"  # of a DWG written, unless the user chooses another (ADR 0013 §2)
VERSIONS = ("R2000", "R2004", "R2007", "R2010", "R2013", "R2018")  # to choose from when a DWG is written
PREFIX = "bf-oda-"
STALE_S = 24 * 3600.0
_PROGRAM_FILES = ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
_DWG = re.compile(rb"AC\d{4}|AC\d\.\d\d")  # the version a DWG begins with: AC1032 … AC1009, AC2.10, AC1.50
_INSTALL = "Не найден ODA File Converter: установите его (бесплатно, Open Design Alliance) и укажите путь в «Настройке»"
HINT_READ = f"{_INSTALL} или сохраните чертёж в DXF."
HINT_WRITE = f"{_INSTALL} или экспортируйте в DXF."
_HINT_FAILED_READ = (
    "Проверьте, что чертёж открывается в AutoCAD и что установлена свежая версия ODA File Converter "
    "(путь к ней — в «Настройке»), или сохраните чертёж в DXF."
)
_HINT_FAILED_WRITE = "Проверьте путь к ODA File Converter в «Настройке» или экспортируйте в DXF."
_LOCK = threading.Lock()  # the settings of ezdxf are one for the process


# ---------------------------------------------------------------- the converter


def _program(path: Path) -> Path | None:
    """`path` when it is a program, the converter inside it when it is the folder of one."""
    try:
        if path.is_file():
            return path
        for name in (PROGRAM + ".exe", PROGRAM):
            if (path / name).is_file():
                return path / name
    except OSError:
        pass
    return None


def installed(roots: Iterable[str | Path]) -> Path | None:
    """The newest converter installed under the «Program Files» folders `roots`:
    `ODA\\ODAFileConverter <version>\\ODAFileConverter.exe`; versions are compared as numbers (26.10 > 9.1)."""
    found: list[tuple[tuple[int, ...], str, Path]] = []
    for root in roots:
        try:
            folders = list((Path(root) / "ODA").glob(PROGRAM + "*"))
        except OSError:
            continue
        for folder in folders:
            exe = folder / (PROGRAM + ".exe")
            if exe.is_file():
                found.append((tuple(int(x) for x in re.findall(r"\d+", folder.name)), folder.name, exe))
    return max(found)[2] if found else None


def find_converter(configured: str | os.PathLike[str] | None = None) -> Path | None:
    """The ODA File Converter to run, or None when there is none (see the module docstring). A configured path
    that is no longer there (the converter was updated: its folder carries the version) does not stop the search."""
    for given in (configured, os.environ.get(ENV)):
        text = str(given).strip().strip('"') if given else ""
        if text:
            found = _program(Path(text).expanduser())
            if found is not None:
                return found
    on_path = shutil.which(PROGRAM)
    if on_path:
        return Path(on_path)
    if sys.platform == "win32":
        return installed(dict.fromkeys(v for k in _PROGRAM_FILES if (v := os.environ.get(k))))
    return None


def _require(converter: str | os.PathLike[str] | None, writing: bool) -> Path:
    exe = find_converter(converter)
    if exe is None:
        if writing:
            raise FormatError("oda_not_found", "Для записи DWG нужна программа ODA File Converter.", HINT_WRITE)
        raise FormatError("oda_not_found", "Для чтения DWG нужна программа ODA File Converter.", HINT_READ)
    return exe


def dwg_version(path: str | Path) -> str:
    """The version a DWG file begins with («AC1032» — AutoCAD 2018); a file that is not a DWG is an error."""
    with Path(path).open("rb") as f:
        head = f.read(6)
    if not head:
        raise FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    if not _DWG.fullmatch(head):
        raise FormatError(
            "not_dwg",
            "Файл не похож на чертёж DWG: в его начале нет версии AutoCAD.",
            "Проверьте расширение файла; чертёж можно сохранить в AutoCAD как DXF и загрузить его.",
        )
    return head.decode("ascii")


def _failed(writing: bool, what: str, detail: str = "") -> FormatError:
    tail = f" Сведения программы: {detail}" if detail else ""
    return FormatError("oda_failed", f"{what}.{tail}", _HINT_FAILED_WRITE if writing else _HINT_FAILED_READ)


def _sweep(base: Path) -> None:
    """Folders of workers that were ended while the converter ran (a cancelled job), a day old or more."""
    limit = time.time() - STALE_S
    try:
        folders = list(base.glob(PREFIX + "*"))
    except OSError:
        return
    for folder in folders:
        try:
            if folder.is_dir() and folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


@contextmanager
def _workspace() -> Iterator[Path]:
    """A temporary folder with `in/` and `out/`, removed at the end."""
    base = Path(tempfile.gettempdir())
    _sweep(base)
    folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))
    try:
        (folder / "in").mkdir()
        (folder / "out").mkdir()
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _odafc() -> Any:
    """ezdxf's wrapper of the converter (the module is not typed)."""
    from ezdxf.addons import odafc

    return odafc


def _release(version: str) -> str:
    """The version as the converter names it; one it does not know is an error."""
    odafc = _odafc()
    release = str(odafc.map_version(str(version)))
    if release not in odafc.VALID_VERSIONS:
        raise FormatError(
            "unsupported", f"Версия DWG «{version}» не поддерживается.", f"Выберите одну из: {', '.join(VERSIONS)}."
        )
    return release


def _convert(source: Path, target: Path, version: str, audit: bool, exe: Path, writing: bool) -> None:
    """One run of the converter: `source` → `target` (its extension is the format), checked."""
    import ezdxf

    odafc = _odafc()
    names = "DXF в DWG" if writing else "DWG в DXF"
    keys = ("win_exec_path", "unix_exec_path")
    with _LOCK:
        saved = [ezdxf.options.get("odafc-addon", key) for key in keys]
        search = getattr(odafc, "_get_odafc_path", None)
        try:
            for key in keys:
                ezdxf.options.set("odafc-addon", key, str(exe))
            if search is not None:  # PATH comes first there on Windows: the program found here is the one to run
                odafc._get_odafc_path = lambda system: str(exe)
            odafc.convert(str(source), str(target), version=version, audit=audit, replace=True)
        except Exception as exc:  # it did not start or reported an error: ODAFCError, OSError and what ezdxf lets out
            detail = " ".join(str(exc).split())[:300]
            raise _failed(writing, f"ODA File Converter не преобразовал {names}", detail) from exc
        finally:
            if search is not None:
                odafc._get_odafc_path = search
            for key, value in zip(keys, saved, strict=True):
                ezdxf.options.set("odafc-addon", key, value)
    try:
        with target.open("rb") as f:
            head = f.read(4096)
    except OSError:
        raise _failed(writing, f"ODA File Converter не создал файл {'DWG' if writing else 'DXF'}") from None
    good = bool(_DWG.match(head)) if writing else head.startswith(b"AutoCAD Binary DXF") or b"SECTION" in head
    if not good:
        made = "DWG" if writing else "DXF"
        raise _failed(writing, f"ODA File Converter создал файл, который не похож на {made}")


# ---------------------------------------------------------------- reading


@contextmanager
def dxf_of(
    path: str | Path,
    progress: Progress | None = None,
    *,
    converter: str | os.PathLike[str] | None = None,
    audit: bool = True,
) -> Iterator[Path]:
    """The DWG `path` as a temporary DXF (R2018) for the length of the block. `audit` — the converter mends
    what it can in a damaged drawing."""
    path = Path(path)
    tick = progress or no_progress
    dwg_version(path)
    exe = _require(converter, writing=False)
    release = _release(READ_VERSION)
    with _workspace() as folder:
        source, target = folder / "in" / "drawing.dwg", folder / "out" / "drawing.dxf"
        tick(0.0, "Копирование чертежа DWG")
        shutil.copyfile(path, source)
        tick(0.1, "Преобразование DWG в DXF: ждём ODA File Converter")
        _convert(source, target, release, audit, exe, writing=False)
        tick(1.0, "Чертёж преобразован в DXF")
        yield target


def read_dwg(
    path: str | Path,
    progress: Progress | None = None,
    *,
    converter: str | os.PathLike[str] | None = None,
    **options: Any,
) -> DxfRead:
    """Read a DWG drawing as the DXF reader reads the same drawing saved as DXF; `options` are those of
    `dxf_lines.read_dxf` (recover, tolerance, limits). `converter` — the path of «Настройка»."""
    from bf_core.formats.dxf_lines import read_dxf
    from bf_core.formats.str_layers import scaled

    tick = progress or no_progress
    with dxf_of(path, scaled(tick, 0.0, 0.3), converter=converter) as dxf:
        out = read_dxf(dxf, scaled(tick, 0.3, 1.0), **options)
    out.format = "dwg"
    return out


# ---------------------------------------------------------------- writing


@contextmanager
def dwg_output(
    path: str | Path,
    progress: Progress | None = None,
    *,
    version: str = WRITE_VERSION,
    converter: str | os.PathLike[str] | None = None,
    audit: bool = False,
) -> Iterator[Path]:
    """Gives the path of a DXF to write; when the block ends without an error, the converter turns that DXF into
    the DWG `path` of the `version` (R2000 … R2018). The converter is looked for before anything is written;
    `progress` covers the conversion only."""
    path = Path(path)
    tick = progress or no_progress
    exe = _require(converter, writing=True)
    release = _release(version)
    with _workspace() as folder:
        source, target = folder / "in" / "drawing.dxf", folder / "out" / "drawing.dwg"
        yield source
        if not source.is_file():
            raise FileNotFoundError(f"no DXF was written to {source}")
        tick(0.0, "Преобразование DXF в DWG: ждём ODA File Converter")
        _convert(source, target, release, audit, exe, writing=True)
        part = path.with_name(path.name + ".part")
        try:
            shutil.move(target, part)
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)
        tick(1.0, "Файл DWG записан")


def dxf_to_dwg(
    dxf: str | Path,
    dwg: str | Path,
    progress: Progress | None = None,
    *,
    version: str = WRITE_VERSION,
    converter: str | os.PathLike[str] | None = None,
) -> Path:
    """Turn a DXF file into the DWG `dwg`; the DXF itself is not given to the converter (a copy is)."""
    with dwg_output(dwg, progress, version=version, converter=converter) as staged:
        shutil.copyfile(dxf, staged)
    return Path(dwg)


def write_dwg(
    path: str | Path,
    layers: Sequence[LineSet],
    progress: Progress | None = None,
    *,
    version: str = WRITE_VERSION,
    converter: str | os.PathLike[str] | None = None,
) -> DxfWritten:
    """Write line and point layers as a DWG: the DXF of `dxf_write.write_dxf`, then the converter."""
    from bf_core.formats.dxf_write import write_dxf
    from bf_core.formats.str_layers import scaled

    path = Path(path)
    tick = progress or no_progress
    with dwg_output(path, scaled(tick, 0.6, 1.0), version=version, converter=converter) as dxf:
        written = write_dxf(dxf, layers, scaled(tick, 0.0, 0.6))
    written.path, written.size = path, path.stat().st_size
    return written


__all__ = [
    "ENV",
    "HINT_READ",
    "HINT_WRITE",
    "READ_VERSION",
    "VERSIONS",
    "WRITE_VERSION",
    "dwg_output",
    "dwg_version",
    "dxf_of",
    "dxf_to_dwg",
    "find_converter",
    "installed",
    "read_dwg",
    "write_dwg",
]
