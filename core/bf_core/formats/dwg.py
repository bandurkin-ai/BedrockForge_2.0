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
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress
from bf_core.paths import APP_DIR_NAME

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
TIMEOUT_S = 300.0  # of one run of the converter: one that takes longer hangs (a window that waits for an answer) …
TIMEOUT_S_PER_MB = 1.0  # … and this for every megabyte of the file it is given
XVFB_WAIT_S = 10.0  # for the screen of the converter on Linux to come up
_WINDOWS = sys.platform == "win32"
_SUFFIXES: tuple[str, ...] = (".exe",) if _WINDOWS else ()  # of a file that is a program; () — any file may be
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
_HINT_TIMEOUT = (
    "Возможно, программа ждёт ответа в своём окне или файл слишком велик. Запустите ODA File Converter вручную "
    "и проверьте, что он работает; путь к программе — в «Настройке». "
)
_SETTING = "Путь к ODA File Converter в «Настройке»"
_VARIABLE = f"Переменная окружения {ENV}"


# ---------------------------------------------------------------- the converter


def _program(path: Path, named_by: str | None = None) -> Path | None:
    """`path` when it is a program, the converter inside it when it is the folder of one. On Windows a program
    is an `.exe` (`_SUFFIXES`): any other file is passed by, or — when `named_by` tells who names it — is the
    error `bad_path`."""
    try:
        if path.is_file():
            if not _SUFFIXES or path.suffix.lower() in _SUFFIXES:
                return path
            if named_by is None:
                return None
            raise FormatError(
                "bad_path",
                f"{named_by} указывает на файл «{path.name}», а это не программа.",
                f"Укажите файл {PROGRAM}.exe или папку, в которую установлен ODA File Converter.",
            )
        for name in [PROGRAM + suffix for suffix in _SUFFIXES] or [PROGRAM + ".exe", PROGRAM]:
            if (path / name).is_file():
                return path / name
    except OSError:
        pass
    return None


def program_at(path: str | os.PathLike[str]) -> Path | None:
    """The converter a path of «Настройка» names: the program itself or the one in its folder; None when
    nothing is there. A file that is not a program (on Windows — not an `.exe`) is the error `bad_path`."""
    return _program(Path(path), _SETTING)


def _on_path() -> Path | None:
    """`ODAFileConverter` in a folder of PATH. Only the folders PATH names in full are looked into: never the
    current one (an empty or a relative entry; `shutil.which` looks there first on Windows, whatever `path` it
    is given), and on Windows the program is an `.exe`, not whatever PATHEXT allows (`.cmd`, `.bat`)."""
    names = [PROGRAM + suffix for suffix in _SUFFIXES] or [PROGRAM]
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        folder = Path(entry.strip().strip('"'))
        if not folder.is_absolute():
            continue
        for name in names:
            found = folder / name
            try:
                if found.is_file() and (bool(_SUFFIXES) or os.access(found, os.X_OK)):
                    return found
            except OSError:
                continue
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


def find_converter(configured: str | os.PathLike[str] | None = None, *, strict: bool = False) -> Path | None:
    """The ODA File Converter to run, or None when there is none (see the module docstring). A configured path
    that is no longer there (the converter was updated: its folder carries the version) does not stop the search.
    Nor does a file that is not a program, unless `strict`: then it is the error `bad_path` (the reader and the
    writer ask so: they do not run another program in place of the one the user named)."""
    for given, named_by in ((configured, _SETTING), (os.environ.get(ENV), _VARIABLE)):
        text = str(given).strip().strip('"') if given else ""
        if text:
            found = _program(Path(text).expanduser(), named_by if strict else None)
            if found is not None:
                return found
    on_path = _on_path()
    if on_path is not None:
        return on_path
    if _WINDOWS:
        return installed(dict.fromkeys(v for k in _PROGRAM_FILES if (v := os.environ.get(k))))
    return None


def _require(converter: str | os.PathLike[str] | None, writing: bool) -> Path:
    exe = find_converter(converter, strict=True)
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


def _short_path(folder: Path) -> Path | None:
    """The short (8.3) path of a folder there is, on Windows; None when it cannot be had. A volume that keeps
    no short names gives the path as it is; a short name may be Cyrillic as well (the caller looks)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    get = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get.restype = wintypes.DWORD
    size = get(str(folder), None, 0)
    if size == 0:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    if get(str(folder), buffer, size) == 0:
        return None
    return Path(buffer.value)


def _bases() -> list[Path]:
    """Where the folder of a call may be made, the best first; the last is the temporary folder of the user as
    it is. On Windows, when its path has letters beyond ASCII, before it come its short 8.3 path and
    `%ProgramData%\\BedrockForge\\tmp`, each when it is ASCII (see the module docstring)."""
    base = Path(tempfile.gettempdir())
    out: list[Path] = []
    if _WINDOWS and not str(base).isascii():
        short = _short_path(base)
        if short is not None and str(short).isascii():
            out.append(short)
        shared = os.environ.get("ProgramData") or ""
        if shared and shared.isascii():
            out.append(Path(shared) / APP_DIR_NAME / "tmp")
    return [*out, base]


def _folder() -> Path:
    """A new folder `bf-oda-…` (of this user only) in the first of `_bases()` that lets it be made."""
    *better, base = _bases()
    for place in better:
        try:
            place.mkdir(parents=True, exist_ok=True)
            _sweep(place)
            return Path(tempfile.mkdtemp(prefix=PREFIX, dir=place))
        except OSError:
            continue
    _sweep(base)
    return Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))


@contextmanager
def _workspace() -> Iterator[Path]:
    """A temporary folder with `in/` and `out/`, removed at the end."""
    folder = _folder()
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


def _arguments(source: Path, target: Path, version: str, audit: bool) -> list[str]:
    """The command line of the converter after its name, as `ezdxf.addons.odafc` writes it: «Input Folder»
    «Output Folder» version type recurse audit filter. The type is the extension of `target`, the filter is the
    name of `source`: this file only, and it is made in the folder of `target`."""
    return [
        str(source.parent), str(target.parent), version, target.suffix[1:].upper(), "0", "1" if audit else "0",
        source.name,
    ]  # fmt: skip


@contextmanager
def _screen() -> Iterator[dict[str, str] | None]:
    """The environment the converter runs in (None — that of this process). On Linux the converter opens its
    window even when it has its arguments: an `Xvfb` of PATH, when there is one, is started as a screen nobody
    sees — on a display the server finds free and names itself once it listens (`-displayfd`), so converters
    that run at the same time have a screen each — and is ended with the converter."""
    xvfb = shutil.which("Xvfb") if sys.platform == "linux" else None
    server: subprocess.Popen[bytes] | None = None
    answer = b""
    if xvfb is not None:
        read, write = os.pipe()
        try:
            quiet = subprocess.DEVNULL
            command = [xvfb, "-displayfd", str(write), "-screen", "0", "800x600x24"]
            server = subprocess.Popen(command, stdin=quiet, stdout=quiet, stderr=quiet, pass_fds=(write,))
            os.close(write)
            write = -1
            # the number of the display and a new line, written apart: a server whose reader goes after the
            # number ends with an error. Nothing — the server has ended
            deadline = time.monotonic() + XVFB_WAIT_S
            with selectors.DefaultSelector() as waiting:
                waiting.register(read, selectors.EVENT_READ)
                while not answer.endswith(b"\n") and waiting.select(max(deadline - time.monotonic(), 0.0)):
                    part = os.read(read, 64)
                    if not part:
                        break
                    answer += part
        except (OSError, ValueError):
            pass  # no screen of its own: the converter runs with the one there is
        finally:
            os.close(read)
            if write >= 0:
                os.close(write)
    number = answer.decode("ascii", errors="replace").strip() if answer.endswith(b"\n") else ""
    try:
        yield {**os.environ, "DISPLAY": f":{number}"} if number.isdigit() else None
    finally:
        if server is not None:
            server.terminate()  # not killed at once: the server removes its socket and its lock
            try:
                server.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def _run(exe: Path, arguments: Sequence[str], timeout: float) -> tuple[int, str, str]:
    """Runs the converter to its end: (the return code, what it printed, what it complained of). No window is
    shown, nothing is asked of the user; the output is read while the converter runs (it never waits for a
    reader) and is text whatever bytes it holds. `subprocess.TimeoutExpired` — it did not end in `timeout`
    seconds and was ended; `OSError` — it did not start."""
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        hidden = subprocess.STARTUPINFO()
        hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        hidden.wShowWindow = subprocess.SW_HIDE  # the converter is a program with a window: it stays hidden
        options = {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": hidden}
    with _screen() as env:
        done = subprocess.run(
            [str(exe), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
            **options,
        )
    out, err = (bytes(raw or b"").decode("utf-8", errors="replace") for raw in (done.stdout, done.stderr))
    return done.returncode, out, err


def _made(target: Path) -> Path | None:
    """The file the converter made for `target`: of its name, in whatever case the extension is written."""
    if target.is_file():
        return target
    try:
        for found in target.parent.iterdir():
            if found.name.casefold() == target.name.casefold() and found.is_file():
                return found
    except OSError:
        pass
    return None


def _convert(source: Path, target: Path, version: str, audit: bool, exe: Path, writing: bool) -> None:
    """One run of the converter: `source` → `target` (its extension is the format, its folder is where the
    converter writes), checked."""
    names = "DXF в DWG" if writing else "DWG в DXF"
    kind = "DWG" if writing else "DXF"
    timeout = TIMEOUT_S + TIMEOUT_S_PER_MB * source.stat().st_size / 2**20
    try:
        code, out, err = _run(exe, _arguments(source, target, version, audit), timeout)
    except subprocess.TimeoutExpired:
        raise FormatError(
            "oda_timeout",
            f"ODA File Converter не преобразовал {names} за {round(timeout)} с и был остановлен.",
            _HINT_TIMEOUT + ("Или экспортируйте в DXF." if writing else "Или сохраните чертёж в DXF."),
        ) from None
    except OSError as exc:  # no such program, no right to run it, not a program at all
        raise _failed(writing, "ODA File Converter не запустился", " ".join(str(exc).split())[:300]) from exc
    complaint = err.strip()
    if sys.platform == "linux":  # there the converter ends with a crash even after a good conversion (ezdxf #707)
        failed = complaint not in ("", "Quit (core dumped)")
    else:
        failed = code != 0 or complaint != ""
    if failed:
        said = " ".join((complaint or out).split())[:300]
        detail = (f"код завершения {code}" + (f": {said}" if said else "")) if code != 0 else said
        raise _failed(writing, f"ODA File Converter не преобразовал {names}", detail)
    made = _made(target)
    if made is None:
        raise _failed(writing, f"ODA File Converter не создал файл {kind}")
    if made != target:
        os.replace(made, target)
    with target.open("rb") as f:
        head = f.read(4096)
    good = bool(_DWG.match(head)) if writing else head.startswith(b"AutoCAD Binary DXF") or b"SECTION" in head
    if not good:
        raise _failed(writing, f"ODA File Converter создал файл, который не похож на {kind}")


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
    for mesh in out.wireframes:  # `objects.meta.source` of a wireframe names the file it came from
        mesh.source["format"] = "dwg"
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
    "program_at",
    "read_dwg",
    "write_dwg",
]
