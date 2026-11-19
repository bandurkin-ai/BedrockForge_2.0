"""The two tables of a Datamine wireframe (`*tr.dm` — triangles, `*pt.dm` — points): which file goes with which,
and what to say about a table that is not what was asked for (ADR 0013 §2 «пара — по имени или из той же папки»).
Used by `formats/datamine_objects.py`; preliminary as that reader is (no samples).

    find_pair(path)       the other table of the pair: by the name (`oretr.dm` ↔ `orept.dm`, any case, `ore_tr`
                          and `ore-tr` as well), else the only table of that kind in the folder; several of them —
                          `pair_ambiguous`; None — the folder has none, or `path` is not a table of a wireframe
    resolve_pair(…)       (triangles, points) with their headers for a path that is either of them
    wrong_kind(…)         the FormatError of a table that is not a wireframe (not strings)

Error codes: pair_missing, pair_ambiguous, pair_mismatch, not_wireframe, not_strings, unsupported (a block model).
"""

from __future__ import annotations

import re
from pathlib import Path

from bf_core.formats.datamine import BLOCK_MODEL, POINTS, STRINGS, TRIANGLES, DmHeader, read_header
from bf_core.formats.errors import FormatError

MAX_FOLDER = 5_000  # entries of a folder looked through for the other table of a pair
MAX_HEADERS = 200  # … and tables whose headers are read
PAIR_NAME = re.compile(r"^(.*?)(tr|pt)$", re.IGNORECASE | re.DOTALL)  # (the common name, which table)
HINT_PAIR = "Каркас Datamine — это два файла: треугольники (…tr.dm) и точки (…pt.dm). Выберите оба."


def kind_of(path: Path, encoding: str | None) -> str | None:
    try:
        return read_header(path, encoding).kind
    except (FormatError, OSError):
        return None


def twin_name(name: str) -> str | None:
    """`oretr.dm` → `orept.dm` and back, in the case of the name given; None — the name does not end so."""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    found = PAIR_NAME.match(stem)
    if not found:
        return None
    end = found.group(2)
    other = "pt" if end.lower() == "tr" else "tr"
    other = other.upper() if end.isupper() else other.capitalize() if end[0].isupper() else other
    return f"{found.group(1)}{other}{dot}{suffix}"


def pair_of(path: Path, kind: str, encoding: str | None) -> tuple[Path | None, bool]:
    """(the other table of the wireframe, found by the name?)."""
    want = {TRIANGLES: POINTS, POINTS: TRIANGLES}.get(kind)
    if want is None:
        return None, False
    try:
        folder = sorted(p for _, p in zip(range(MAX_FOLDER), path.parent.iterdir(), strict=False))
    except OSError:
        return None, False
    others = [p for p in folder if p.name != path.name and p.is_file()]
    twin = twin_name(path.name)
    if twin is not None:
        for p in others:
            if p.name.casefold() == twin.casefold() and kind_of(p, encoding) == want:
                return p, True
    tables = [p for p in others if p.suffix.lower() == path.suffix.lower()][:MAX_HEADERS]
    found = [p for p in tables if kind_of(p, encoding) == want]
    if len(found) > 1:
        what = "таблиц точек" if want == POINTS else "таблиц треугольников"
        names = ", ".join(f"«{p.name}»" for p in found[:5]) + (" и другие" if len(found) > 5 else "")
        raise FormatError(
            "pair_ambiguous",
            f"В папке файла «{path.name}» несколько {what} Datamine: {names}. Какая из них относится к каркасу, "
            "по именам не определить.",
            HINT_PAIR + (f" По имени ожидается «{twin}»." if twin else ""),
        )
    return (found[0], False) if found else (None, False)


def find_pair(path: str | Path, encoding: str | None = None) -> Path | None:
    """The other table of the wireframe that the table `path` belongs to (see the module docstring); None — it is
    not a table of a wireframe or the folder has no table for it."""
    path = Path(path)
    kind = kind_of(path, encoding)
    return pair_of(path, kind, encoding)[0] if kind else None


def wrong_kind(path: Path, header: DmHeader, want: str) -> FormatError:
    """The table is not what was asked for: `want` — "wireframe" or "strings"."""
    code = "not_wireframe" if want == "wireframe" else "not_strings"
    what = "каркас" if want == "wireframe" else "стринги"
    if header.kind == BLOCK_MODEL:
        return FormatError(
            "unsupported",
            f"Файл «{path.name}» — блочная модель Datamine, а не {what}.",
            "Загрузка блочных моделей Datamine появится в одной из следующих версий программы.",
        )
    if header.kind == STRINGS:
        return FormatError(code, f"Файл «{path.name}» — стринги Datamine, а не каркас.", "Загрузите его как линии.")
    if header.kind in (TRIANGLES, POINTS):
        return FormatError(
            code, f"Файл «{path.name}» — таблица каркаса Datamine, а не стринги.", "Загрузите его как каркас."
        )
    if want == "wireframe":
        message = (
            f"В таблице Datamine «{path.name}» нет полей каркаса: PID1, PID2, PID3 (треугольники) или "
            "PID, XP, YP, ZP (точки)."
        )
        return FormatError(code, message, HINT_PAIR)
    return FormatError(
        code,
        f"В таблице Datamine «{path.name}» нет полей стрингов: PTN, PVALUE, XP, YP, ZP.",
        "Стринги Datamine — таблица с номером стринга PVALUE, номером точки PTN и координатами XP, YP, ZP.",
    )


def resolve_pair(
    path: Path, points: Path | None, encoding: str | None
) -> tuple[Path, DmHeader, Path, DmHeader, list[str], bool]:
    """(triangles, their header, points, their header, warnings, the pair was found by the name)."""
    header = read_header(path, encoding)
    warnings: list[str] = []
    by_name = False
    if header.kind == POINTS and points is not None:  # given the other way round
        if kind_of(points, encoding) != TRIANGLES:
            raise FormatError(
                "pair_mismatch", f"Файл «{points.name}» — не таблица треугольников каркаса Datamine.", HINT_PAIR
            )
        path, points, header = points, path, read_header(points, encoding)
    elif header.kind == POINTS:  # the table of points was given: its triangles are the file
        other, by_name = pair_of(path, POINTS, encoding)
        if other is None:
            twin = twin_name(path.name)
            raise FormatError(
                "pair_missing",
                f"Для таблицы точек «{path.name}» не найдена таблица треугольников каркаса.",
                HINT_PAIR + (f" По имени ожидается «{twin}»." if twin else ""),
            )
        path, points, header = other, path, read_header(other, encoding)
        if not by_name:
            warnings.append(f"Таблица треугольников «{path.name}» взята из папки файла: другой в ней нет")
    elif header.kind != TRIANGLES:
        raise wrong_kind(path, header, "wireframe")
    elif points is None:
        points, by_name = pair_of(path, TRIANGLES, encoding)
        if points is None:
            twin = twin_name(path.name)
            raise FormatError(
                "pair_missing",
                f"Для каркаса «{path.name}» не найдена таблица точек.",
                HINT_PAIR + (f" По имени ожидается «{twin}»." if twin else ""),
            )
        if not by_name:
            warnings.append(f"Таблица точек «{points.name}» взята из папки файла: другой в ней нет")
    try:
        points_header = read_header(points, encoding)
    except FileNotFoundError as exc:
        raise FormatError("pair_missing", f"Файл точек каркаса «{points.name}» не найден.", HINT_PAIR) from exc
    if points_header.kind != POINTS:
        raise FormatError(
            "pair_mismatch",
            f"Файл «{points.name}» — не таблица точек каркаса Datamine: в нём нет полей PID, XP, YP, ZP.",
            HINT_PAIR,
        )
    return path, header, points, points_header, warnings, by_name
