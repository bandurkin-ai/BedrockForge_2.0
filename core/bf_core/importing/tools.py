"""/api/v1/tools — the programs of the user that BF2 runs («Настройка › Внешние программы»; ADR 0013 §5).

`GET /tools/oda`, `PUT /tools/oda {path}` — ODA File Converter, through which DWG is read and written (ТЗ 7.35
Д-13, Д-17; ADR 0009 «вариант А»). The path belongs to the interface (the file of user settings, like the number
of job workers): it sends the path with `PUT` when the core comes up and when the user changes it; the core keeps
it until it stops. A path that names nothing is kept too — the answer says so and the search goes on, as
`formats.dwg.find_converter` does: the setting → the variable `BF_ODA_CONVERTER` → `ODAFileConverter` of PATH →
the newest `…\\Program Files\\ODA\\ODAFileConverter*\\ODAFileConverter.exe`.

The answer of both requests:
  configured  the path of «Настройка» as the core holds it, or null;
  found       a converter there is — `path` is the program the DWG reader and writer will run, `source` says where
              it comes from: "configured" (the setting itself or the program in its folder) or "auto" (the search);
  auto        what the search finds without the setting («Найти» of the window), or null;
  version     of the program, from the name of its folder («ODAFileConverter 26.10.0») or of its file, or null:
              the converter is never started here (without arguments it opens its window);
  message, warning, hint — Russian texts for the window: the state, what is wrong with the setting, what to do;
  install     how to get the program (free, Open Design Alliance) — the same words whatever the state.

`oda_converter(state)` gives the path of the setting to the code that starts DWG jobs: they run in worker
processes, so the path goes into the parameters of a job (`converter=` of `formats.dwg`), not through a global.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from bf_core.api.app import CoreState
from bf_core.api.errors import ApiError
from bf_core.formats import dwg

router = APIRouter(prefix="/tools")

MAX_PATH_CHARS = 2000
_SERVICE = "tools.oda"
_lock = threading.Lock()
_VERSION = re.compile(r"\d+(?:\.\d+)+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

INSTALL = (
    "ODA File Converter — бесплатная программа Open Design Alliance. Скачайте её на сайте opendesign.com "
    "(раздел «Guest Files», «ODA File Converter»), установите и нажмите «Найти» — или укажите файл "
    "ODAFileConverter.exe кнопкой «Выбрать…»."
)
_WITHOUT = "Без неё чертежи DWG не читаются и не записываются: сохраните чертёж в DXF."


class OdaBody(BaseModel):
    path: str | None = Field(default=None, max_length=MAX_PATH_CHARS)


def _clean(path: str | None) -> str | None:
    """The path as the user gave it without the spaces and quotes around it; an empty one is no path."""
    if path is None:
        return None
    text = path.strip().strip('"').strip()
    if not text:
        return None
    place = None if _CONTROL.search(text) else _expanded(text)
    if place is None or not place.is_absolute():
        raise ApiError(
            422,
            "bad_path",
            "Путь к ODA File Converter указан неверно.",
            "Укажите полный путь к файлу ODAFileConverter.exe или к его папке — либо оставьте поле пустым: "
            "установленная программа будет найдена сама.",
        )
    return text


def _expanded(text: str) -> Path | None:
    """The path with `~` opened as `formats.dwg` opens it; None when there is no such home folder."""
    try:
        return Path(text).expanduser()
    except RuntimeError:
        return None


def oda_converter(state: CoreState) -> str | None:
    """The path of «Настройка» (None — not set): the `converter` of `formats.dwg` for the jobs of this core."""
    with _lock:
        value = state.services.get(_SERVICE)
    return value if isinstance(value, str) else None


def set_oda_converter(state: CoreState, path: str | None) -> str | None:
    """Keeps the path of «Настройка» (checked, see `_clean`) and gives it back."""
    text = _clean(path)
    with _lock:
        state.services[_SERVICE] = text
    return text


def _version(exe: Path) -> str | None:
    """«26.10.0» of the folder `ODAFileConverter 26.10.0`, else of the file: `ODAFileConverter_…_26.10.AppImage`."""
    for name in (exe.parent.name, exe.stem):
        if name.lower().startswith(dwg.PROGRAM.lower()):
            found = _VERSION.findall(name)
            if found:
                return str(found[-1])
    return None


def _concerns(exe: Path, from_setting: bool) -> list[str]:
    """What is unusual about a program the setting names (one found by the search has the name by definition)."""
    out = []
    if from_setting and not exe.name.lower().startswith(dwg.PROGRAM.lower()):
        out.append(
            f"Имя файла «{exe.name}» не похоже на ODAFileConverter.exe — проверьте, что выбрана программа "
            "ODA File Converter, а не другая."
        )
    if os.name != "nt" and not os.access(exe, os.X_OK):
        out.append("Файл не помечен как исполняемый — программа не запустится.")
    return out


def oda_status(configured: str | None) -> dict[str, Any]:
    """The state of the converter for the path of «Настройка» (see the module docstring)."""
    found = dwg.find_converter(configured)
    auto = dwg.find_converter(None)
    given = _expanded(configured) if configured else None
    own = found is not None and given is not None and (found == given or found.parent == given)
    warnings: list[str] = []
    if found is None:
        message = "ODA File Converter не найден" + (": по указанному пути программы нет." if configured else ".")
        hint = f"{INSTALL} {_WITHOUT}"
        version = None
    else:
        version = _version(found)
        message = "ODA File Converter найден" + (f", версия {version}." if version else ".")
        hint = ""
        if configured and not own:
            warnings.append(f"По указанному пути программы нет. Будет использована найденная: {found}")
        warnings.extend(_concerns(found, own))
    return {
        "configured": configured,
        "found": found is not None,
        "path": None if found is None else str(found),
        "source": None if found is None else ("configured" if own else "auto"),
        "auto": None if auto is None else str(auto),
        "version": version,
        "message": message,
        "warning": " ".join(warnings),
        "hint": hint,
        "install": INSTALL,
    }


@router.get("/oda")
def get_oda(request: Request) -> dict[str, Any]:
    return oda_status(oda_converter(request.app.state.core))


@router.put("/oda")
def put_oda(request: Request, body: OdaBody) -> dict[str, Any]:
    return oda_status(set_oda_converter(request.app.state.core, body.path))
