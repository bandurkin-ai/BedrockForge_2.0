"""File and folder names of the export for Windows (ТЗ 17.2.1, 17.2.3; ADR 0017 §5 «Имена»).

Names of objects are free text of the user: Cyrillic, spaces, `/`, a trailing dot, «CON». They become names of
files and folders on Windows by these rules (on Linux and macOS the same, so a file made anywhere opens on
Windows):

* `< > : " / \\ ? *`, the vertical bar and control characters (U+0000…U+001F, U+007F) → `_`;
* dots and spaces at the end go (Windows drops them silently, and «тело.» would be another «тело»);
* a name that is a device — CON, PRN, AUX, NUL, COM1–COM9, LPT1–LPT9 (also with the superscript digits ¹ ² ³),
  bare or with any extension, in any case — gets `_` in front: «CON.dxf» → «_CON.dxf»;
* a name is at most `MAX_NAME` characters; an empty one is «без имени»;
* the whole path is at most `MAX_PATH` characters (260 with the final NUL of the Win32 API): a longer one gets
  its stem cut and `~` + 4 hex digits of the full stem after it, so two long names stay two names;
* names of one folder differ in more than the case (17.2.1): a repeat gets « (2)», « (3)»… before the
  extension — «Тело.dxf», «тело (2).dxf».

Pure functions and `Folder` (the names taken in one folder of the plan); nothing touches the disk.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

MAX_NAME = 120
MAX_PATH = 259
EMPTY = "без имени"
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"{port}{n}" for port in ("com", "lpt") for n in [*"123456789", "¹", "²", "³"]}
)
_SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,4}$")


class ExportNameError(ValueError):
    """The path cannot be made short enough: {code, message, hint} for the job report."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def is_device(name: str) -> bool:
    """`name` is a reserved device name of Windows, bare or with an extension («con», «Aux.txt», «COM1.dxf»)."""
    return name.split(".", 1)[0].rstrip(" ").casefold() in _DEVICES


def _trim(name: str) -> str:
    return name.rstrip(". ")


def clean(name: str, *, limit: int = MAX_NAME) -> str:
    """One part of a path (a file or folder name) that Windows takes as it is: see the module docstring."""
    out = _trim(_BAD.sub("_", str(name)).strip(" "))
    if len(out) > limit:
        out = _trim(out[:limit])
    if not out:
        out = EMPTY
    if is_device(out):
        out = "_" + out
        if len(out) > limit:
            out = _trim(out[:limit])
    return out


def folder_parts(folder: str | None) -> list[str]:
    """The folder of an object (`objects.folder`: «Участок 1/Борт», with `/` or `\\`) as cleaned parts."""
    if not folder:
        return []
    return [clean(part) for part in re.split(r"[/\\]+", str(folder)) if part.strip(" .")]


def stem_of(name: str) -> str:
    """The name of an object that is the name of a file it came from («контуры.str») without its extension."""
    return _SUFFIX.sub("", name) or name


def key(name: str) -> str:
    """The key under which Windows (NTFS, case-insensitive) sees a name as the same."""
    return name.casefold()


def shorten(stem: str, room: int) -> str:
    """`stem` in at most `room` characters: cut, with `~` + 4 hex digits of the whole stem (two long names that
    start the same stay two names). Raises `ExportNameError` when there is no room even for «x~abcd»."""
    if len(stem) <= room:
        return stem
    tag = "~" + hashlib.sha1(stem.encode("utf-8")).hexdigest()[:4]
    if room < len(tag) + 1:
        raise ExportNameError(
            "path_too_long",
            "Путь к папке экспорта слишком длинный: имена файлов в нём не помещаются в 259 символов.",
            "Выберите папку ближе к корню диска, например C:\\Экспорт.",
        )
    return _trim(stem[: room - len(tag)]) + tag


@dataclass
class Folder:
    """The names taken in one folder of the plan (case-insensitive). `path_length` — the characters of the
    longest form of the folder path the files will be under (the temporary folder of the job is longer than the
    final one), without the separator after it."""

    path_length: int = 0
    taken: set[str] = field(default_factory=set)

    def claim(self, stem: str, suffixes: Sequence[str] = ("",), *, limit: int = MAX_PATH) -> str:
        """A stem free for every `stem + suffix` of `suffixes` (a file and the files written next to it, or a
        folder with suffix ""), cleaned, fitted into the path limit and taken. Raises `ExportNameError`."""
        stem = clean(stem)
        longest = max((len(s) for s in suffixes), default=0)
        room = min(MAX_NAME - longest, limit - self.path_length - 1 - longest)
        base = shorten(stem, room)
        n = 1
        while True:
            candidate = base if n == 1 else shorten(stem, room - len(f" ({n})")) + f" ({n})"
            names = [key(candidate + s) for s in suffixes]
            if not any(name in self.taken for name in names) and not is_device(candidate):
                self.taken.update(names)
                return candidate
            n += 1

    def take(self, names: Iterable[str]) -> None:
        """Names that are there already (the files of an existing folder)."""
        self.taken.update(key(name) for name in names)


def fits(path: str | PurePath, limit: int = MAX_PATH) -> bool:
    """The whole path is within the limit of Windows without long path support."""
    return len(str(path)) <= limit
