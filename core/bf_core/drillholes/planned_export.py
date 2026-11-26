"""The files of a planned hole (ТЗ 7.35 Д-34 ч. 2; e19-api.md §10): `collar.csv` (the collar) and `survey.csv` (the
survey stations) — a set that imports them has the same path — or the DXF line of the path every 1 m
(`formats.dxf_write`, as the lines of `lines/export`).

`export(name, p, format, path, …)`:
  * `csv` — `path` is an existing folder; the files `collar.csv` (`HOLEID, EAST, NORTH, RL, DEPTH, AZIMUTH, DIP`)
    and `survey.csv` (`HOLEID, DEPTH, AZIMUTH, DIP`); the numbers as the shortest text that gives the same float64,
    the delimiter «;» and the decimal «,» as in the export of tables (`delimiter`, `decimal`, `encoding` change them);
  * `dxf` — `path` is the file (`.dxf` is added), one polyline in the layer named after the hole.
A file is written aside and renamed when it is whole; one that is there already — 409 `files_exist` {files} unless
`overwrite`. Result `{format, files: [path], points, size}`.

Refusals: 404 `folder_not_found`; 422 `bad_format`, `path_not_absolute`, `bad_param` {param}.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import planned as pl

FORMATS = ("csv", "dxf")
COLLAR = "collar.csv"
SURVEY = "survey.csv"
COLLAR_HEAD = ("HOLEID", "EAST", "NORTH", "RL", "DEPTH", "AZIMUTH", "DIP")
SURVEY_HEAD = ("HOLEID", "DEPTH", "AZIMUTH", "DIP")


def _text(value: float, decimal: str) -> str:
    return repr(float(value)).replace(".", decimal)


FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _safe(name: str) -> str:
    """A name that starts like a formula (= + - @, tab, CR; one from a foreign project too) gets «'»: the spreadsheet
    shows it, never runs it (`model/check.csv_bytes`). Only the name: the numbers stay numbers (−1,5)."""
    return "'" + name if name[:1] in FORMULA else name


def collar_rows(name: str, p: pl.Planned, decimal: str = ",") -> list[list[str]]:
    x, y, z = p.collar
    return [[_safe(name), *(_text(v, decimal) for v in (x, y, z, p.length, p.azimuth, p.dip))]]


def survey_rows(name: str, p: pl.Planned, decimal: str = ",") -> list[list[str]]:
    depth, azimuth, dip = pl.stations(p)
    rows = zip(depth.tolist(), azimuth.tolist(), dip.tolist(), strict=True)
    return [[_safe(name), *(_text(v, decimal) for v in row)] for row in rows]


def _check(path: str, fmt: str) -> Path:
    if fmt not in FORMATS:
        raise ApiError(422, "bad_format", "Неизвестный формат выгрузки.", "Выберите CSV или DXF.")
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        raise ApiError(422, "path_not_absolute", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    target = Path(path)
    folder = target if fmt == "csv" else target.parent
    if not folder.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите папку заново.")
    return target


def _refuse_existing(files: list[Path], overwrite: bool) -> None:
    clash = [f for f in files if f.exists() or f.is_symlink()]
    if clash and not overwrite:
        names = ", ".join(f"«{f.name}»" for f in clash)
        raise ApiError(409, "files_exist", f"В папке уже есть файлы, которые выгрузка заменит: {names}.",
                       "Подтвердите замену или выберите другую папку.", {"files": [f.name for f in clash]})  # fmt: skip


def _write_csv(file: Path, head: tuple[str, ...], rows: list[list[str]], delimiter: str, encoding: str) -> None:
    part = file.with_name(file.name + ".part")
    try:
        with part.open("w", encoding=encoding, newline="") as fh:
            w = csv.writer(fh, delimiter=delimiter)
            w.writerow(head)
            w.writerows(rows)
        os.replace(part, file)
    finally:
        part.unlink(missing_ok=True)


def export(name: str, p: pl.Planned, fmt: str, path: str, *, overwrite: bool = False,
           delimiter: str | None = None, decimal: str | None = None, encoding: str | None = None,
           traj: Any = None) -> dict[str, Any]:  # fmt: skip
    """The files of the hole `name` (module docstring)."""
    target = _check(path, fmt)
    if fmt == "csv":
        sep, dec, enc = delimiter or ";", decimal or ",", encoding or "utf-8-sig"
        if sep not in (";", ",", "\t", "|") or dec not in (",", ".") or sep == dec:
            raise pl.bad("delimiter", "Разделитель и десятичный знак заданы неверно.",
                         "Разделитель: «;», «,», табуляция или «|»; десятичный знак: «,» или «.».")  # fmt: skip
        files = [target / COLLAR, target / SURVEY]
        _refuse_existing(files, overwrite)
        survey = survey_rows(name, p, dec)
        try:
            _write_csv(files[0], COLLAR_HEAD, collar_rows(name, p, dec), sep, enc)
            _write_csv(files[1], SURVEY_HEAD, survey, sep, enc)
        except (LookupError, UnicodeEncodeError):
            raise pl.bad("encoding", f"Кодировка «{enc}» не подходит.", "Выберите utf-8-sig или cp1251.") from None
        return {"format": fmt, "files": [str(f) for f in files], "points": len(survey),
                "size": sum(f.stat().st_size for f in files)}  # fmt: skip
    from bf_core.formats.dxf_write import write_dxf
    from bf_core.formats.lineset import LineSet

    final = target if target.suffix.lower() == ".dxf" else target.with_name(target.name + ".dxf")
    _refuse_existing([final], overwrite)
    if traj is None:
        traj = pl.trajectory(p)
    depth = pl.depth_grid(p.length)
    xyz = pl.positions(traj, depth)
    layer = LineSet(name, xyz, offsets=np.array([0, xyz.shape[0]]), closed=np.array([0]))
    part = final.with_name(final.name + ".part")
    try:
        write_dxf(part, [layer])
        os.replace(part, final)
    finally:
        part.unlink(missing_ok=True)
    return {"format": fmt, "files": [str(final)], "points": int(xyz.shape[0]), "size": final.stat().st_size}
