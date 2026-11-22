"""The exchange of variogram models as text: JSON and the parameters of GSLIB (ТЗ 8.6.4; ADR 0022 §12; Т8.6-8),
and the form of a model in the API (`docs/progress/e16-api.md`: `{type, c0, common_axes, structures}`). Plain
Python, no numba; files are read and written by the caller (`store`, the routes) — here is only the text.

Numbers are written by `repr` — the shortest text that reads back to the same float64: export → import keeps
every parameter bit for bit, and a second export gives the same text byte for byte.

GSLIB — the variogram block of a parameter file (kt3d, sgsim …), lines end with «\\n», ASCII:

    nst c0                         -nst, nugget effect
    it cc ang1 ang2 ang3           -it, cc, ang1, ang2, ang3        for every structure:
    a1 a2 a3                       -a_hmax, a_hmin, a_vert          it: 1 spherical, 2 exponential, 3 gaussian

Reading is that of a list-directed Fortran read: the first numbers of a line are taken, the rest of the line is
a comment; numbers are separated by spaces or tabs, the exponent may be D; empty lines and lines starting with
«#» or «!» are skipped; lines after the block are not looked at. A comma is refused, not guessed (in a Russian
file it is rather a decimal comma than a separator). The angles are normalised (`rotation.normalize_angles`);
the axes are common when the angles of all structures are equal. Types 4 (power) and 5 (hole effect) and more
than 3 structures are refused. GSLIB does not say what the model is of: its `type` is the default.

JSON — the fields of `meta` of a stored model without `inputs` (ADR 0022 §12), first the mark
`"kind": "variogram_model"`; `c0`, `structures`, `common_axes` are written canonical (`model.parse_model`),
`type` is checked. A missing number is `null` (never NaN, never a zero). Reading gives the same fields back,
without the mark; a file with only `c0` and `structures` is a model too.

    to_gslib(model) -> str                 from_gslib(text | bytes) -> Model
    to_json(meta) -> str                   from_json(text | bytes) -> dict (the fields of `meta`)
    detect_format(text | bytes) -> "json" | "gslib"
    read_model(text | bytes, format=None) -> dict       write_model(meta, format) -> str
    file_name(name, format) -> str         the name of an exported file, safe on Windows
    value_type(value) -> str               the quantity of a model: one of `VALUE_TYPES`
    api_model(obj, param="model") -> (type, Model)      model_form(type, model) -> dict    the form of the API

Bytes are decoded as UTF-8 (with or without a BOM), UTF-16 with a BOM, else cp1251.

Errors — `StatsError {code, message, hint}` (API — 422): `bad_file` — the file cannot be read as a model, with
the attribute `line` (the line at fault, from 1, or None; the API adds it to the body: `bad_file {line?}`);
`bad_param` — `format`, a `meta` that holds no valid model, a model of a request (`param` — its path, e.g.
`model.structures.1.a2`).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from bf_core.geostat.rotation import bad_param, geostat_error

from .model import GSLIB_TYPES, MAX_STRUCTURES, Model, Structure, parse_model

FORMATS = ("json", "gslib")
EXTENSIONS = {"json": ".json", "gslib": ".par"}
KIND = "variogram_model"
VALUE_TYPES = ("semivariogram", "correlogram", "pairwise", "nscore")
_CODES = {name: code for code, name in GSLIB_TYPES.items()}
_COLUMN = 40  # the comment of a GSLIB line starts here when the numbers fit
_UNSUPPORTED = {4: "степенная модель", 5: "эффект дыры"}
GSLIB_HINT = (
    "Формат GSLIB: первая строка — «nst c0»; для каждой структуры — строка «it cc ang1 ang2 ang3» и строка"
    " «a1 a2 a3». Числа разделяются пробелами, десятичный разделитель — точка."
)
JSON_HINT = "Выберите файл модели вариограммы, сохранённый из BedrockForge (.json); параметры GSLIB — формат GSLIB."
TEXT_HINT = "Выберите текстовый файл: JSON или параметры GSLIB."
TYPE_HINT = (
    "Величина модели: semivariogram (полувариограмма), correlogram (коррелограмма), pairwise (парная"
    " относительная) или nscore (нормальные оценки)."
)
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_NOT_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def bad_file(message: str, hint: str, line: int | None = None) -> ValueError:
    """The refusal of a file: `bad_file` with the line at fault (`e.line`)."""
    e = geostat_error("bad_file", message, hint)
    setattr(e, "line", line)  # noqa: B010 — `StatsError` is imported lazily (numba), not subclassed
    return e


def _text(data: Any) -> str:
    """The text of a file: a string as it is, bytes decoded."""
    if isinstance(data, str):
        text = data
    elif isinstance(data, bytes | bytearray | memoryview):
        raw = bytes(data)
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16", errors="replace")
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1251", errors="replace")
    else:
        raise bad_file("Файл модели не прочитан: ожидается текст.", TEXT_HINT)
    if "\x00" in text:
        raise bad_file("Файл не текстовый: в нём двоичные данные.", TEXT_HINT)
    return text.lstrip("﻿")


def _hint(e: Exception, own: str) -> str:
    """The hint of a refused value, then how the file is written."""
    hint = str(getattr(e, "hint", "") or "")
    return f"{hint} {own}" if hint else own


# --- GSLIB --------------------------------------------------------------------------------------------------------


def _line(values: str, comment: str) -> str:
    return f"{values.ljust(_COLUMN - 1)} -{comment}" if len(values) < _COLUMN - 1 else f"{values}  -{comment}"


def to_gslib(model: Model) -> str:
    """The variogram block of a GSLIB parameter file."""
    lines = [_line(f"{len(model.structures)} {model.c0!r}", "nst, nugget effect")]
    for s in model.structures:
        lines.append(_line(f"{_CODES[s.type]} {s.c!r} {s.ang1!r} {s.ang2!r} {s.ang3!r}", "it, cc, ang1, ang2, ang3"))
        lines.append(_line(f"{s.a1!r} {s.a2!r} {s.a3!r}", "a_hmax, a_hmin, a_vert"))
    return "\n".join(lines) + "\n"


def _numbers(row: tuple[int, str], count: int, what: str) -> list[float]:
    """The first `count` numbers of a line (a list-directed read); fewer — `bad_file` with the line."""
    no, line = row
    out: list[float] = []
    for token in line.split():
        try:
            x = math.nan if "_" in token else float(token.replace("d", "e").replace("D", "e"))
        except ValueError:
            break
        if not math.isfinite(x):
            break
        out.append(x)
        if len(out) == count:
            return out
    form = "числа" if count < 5 else "чисел"
    raise bad_file(f"Строка {no}: ожидается «{what}» — {count} {form}, прочитано {len(out)}.", GSLIB_HINT, no)


def _whole(x: float) -> int | None:
    return int(x) if x == math.floor(x) and abs(x) < 1e9 else None


def _structure(head: tuple[int, str], tail: tuple[int, str]) -> Structure:
    it, cc, ang1, ang2, ang3 = _numbers(head, 5, "it cc ang1 ang2 ang3")
    a1, a2, a3 = _numbers(tail, 3, "a1 a2 a3")
    code = _whole(it)
    if code in _UNSUPPORTED:
        raise bad_file(
            f"Строка {head[0]}: тип структуры it = {code} ({_UNSUPPORTED[code]}) не поддерживается.",
            "Поддерживаются типы 1 (сферическая), 2 (экспоненциальная) и 3 (гауссова).",
            head[0],
        )
    if code not in GSLIB_TYPES:
        raise bad_file(f"Строка {head[0]}: тип структуры it должен быть 1, 2 или 3.", GSLIB_HINT, head[0])
    try:
        return Structure(GSLIB_TYPES[code], cc, a1, a2, a3, ang1, ang2, ang3)
    except ValueError as e:
        no = tail[0] if getattr(e, "param", None) in ("a1", "a2", "a3") else head[0]
        raise bad_file(f"Строка {no}: {getattr(e, 'message', e)}", _hint(e, GSLIB_HINT), no) from e


def from_gslib(data: Any) -> Model:
    """The model of a GSLIB variogram block (text or the bytes of a file)."""
    rows = [(no, line.strip()) for no, line in enumerate(_text(data).splitlines(), 1)]
    rows = [(no, line) for no, line in rows if line and line[0] not in "#!"]
    if not rows:
        raise bad_file("Файл параметров GSLIB пуст.", GSLIB_HINT)
    first = rows[0][0]
    if rows[0][1][0] in "{[":
        raise bad_file(
            f"Строка {first}: файл похож на JSON, а не на параметры GSLIB.",
            "Импортируйте его как JSON или выберите файл параметров GSLIB.",
            first,
        )
    nst_value, c0 = _numbers(rows[0], 2, "nst c0")
    nst = _whole(nst_value)
    if nst is None or nst < 0:
        raise bad_file(
            f"Строка {first}: число структур nst должно быть целым, от 0 до {MAX_STRUCTURES}.", GSLIB_HINT, first
        )
    if nst > MAX_STRUCTURES:
        raise bad_file(
            f"Строка {first}: в файле структур: {nst}; в модели BedrockForge их не больше {MAX_STRUCTURES}.",
            "Объедините структуры или уберите лишние в исходной программе.",
            first,
        )
    need = 1 + 2 * nst
    if len(rows) < need:
        raise bad_file(
            f"В файле не хватает строк: для nst = {nst} нужно строк с числами: {need}, найдено {len(rows)}.",
            GSLIB_HINT,
            rows[-1][0] + 1,
        )
    structures = tuple(_structure(rows[1 + 2 * i], rows[2 + 2 * i]) for i in range(nst))
    try:
        return Model(c0, structures, all(s.angles == structures[0].angles for s in structures))
    except ValueError as e:
        raise bad_file(f"Строка {first}: {getattr(e, 'message', e)}", _hint(e, GSLIB_HINT), first) from e


# --- the form of the API ------------------------------------------------------------------------------------------


def value_type(value: Any, param: str = "type") -> str:
    """The quantity a model is of (`semivariogram` by default): one of `VALUE_TYPES`."""
    if value is None or value == "":
        return VALUE_TYPES[0]
    name = value.strip().lower() if isinstance(value, str) else value
    if name not in VALUE_TYPES:
        raise bad_param(param, f"Неизвестная величина модели «{value}».", TYPE_HINT)
    return str(name)


def api_model(obj: Any, param: str = "model") -> tuple[str, Model]:
    """(type, Model) of a model of a request — `{type?, c0, structures, common_axes?}`; a refusal names the path
    under `param` (`model.structures.1.a2`)."""
    if not isinstance(obj, Mapping):
        raise bad_param(param, "Модель вариограммы задана неверно.", "Ожидается объект с полями c0 и structures.")
    kind = value_type(obj.get("type"), f"{param}.type")
    try:
        return kind, parse_model(obj)
    except ValueError as e:
        inner = getattr(e, "param", None)
        raise bad_param(
            f"{param}.{inner}" if inner and inner != "model" else param,
            str(getattr(e, "message", e)),
            str(getattr(e, "hint", "")),
        ) from e


def model_form(kind: str, model: Model) -> dict[str, Any]:
    """The model of the API: `{type, c0, common_axes, structures}`."""
    d = model.as_dict()
    return {"type": kind, "c0": d["c0"], "common_axes": d["common_axes"], "structures": d["structures"]}


# --- JSON ---------------------------------------------------------------------------------------------------------


def _plain(x: Any) -> Any:
    """Values JSON can hold: a non-finite number is null, numpy values are numbers and lists."""
    if x is None or isinstance(x, bool | str | int):
        return x
    if isinstance(x, float):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, Mapping):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_plain(v) for v in x]
    if hasattr(x, "tolist"):  # numpy scalars and arrays
        return _plain(x.tolist())
    raise TypeError(f"a value of {type(x).__name__} cannot be written to JSON")


def _canonical(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a document with the model checked and canonical; no mark, no `inputs`."""
    if "structures" not in doc:
        raise bad_param("structures", "В описании нет модели вариограммы: нет поля structures.", JSON_HINT)
    model = parse_model(doc).as_dict()
    if doc.get("type") is not None:
        model = {**model, "type": value_type(doc["type"])}
    out = {k: (model[k] if k in model else v) for k, v in doc.items() if k not in ("kind", "inputs")}
    return {**out, **{k: v for k, v in model.items() if k not in out}}


def to_json(meta: Any) -> str:
    """The text of a model: the mark, then the fields of `meta` in their order, without `inputs`."""
    if not isinstance(meta, Mapping):
        raise bad_param("model", "Модель вариограммы задана неверно.", "Ожидается объект с полями c0 и structures.")
    doc = {"kind": KIND, **_plain(_canonical(meta))}
    return json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def from_json(data: Any) -> dict[str, Any]:
    """The fields of `meta` of a model file (text or bytes): the model checked and canonical, the rest as it is."""
    text = _text(data)
    if not text.strip():
        raise bad_file("Файл модели вариограммы пуст.", JSON_HINT)
    try:
        doc = json.loads(text)
    except ValueError as e:
        if isinstance(e, json.JSONDecodeError):
            where = f"строка {e.lineno}, позиция {e.colno}"
            raise bad_file(f"Файл не читается как JSON: {where}.", JSON_HINT, e.lineno) from e
        raise bad_file("Файл не читается как JSON.", JSON_HINT) from e
    if not isinstance(doc, dict):
        raise bad_file("Файл не является моделью вариограммы: ожидается объект JSON.", JSON_HINT, 1)
    if doc.get("kind", KIND) != KIND:
        raise bad_file(f"Файл не является моделью вариограммы: это объект «{doc['kind']}».", JSON_HINT)
    if "structures" not in doc:
        raise bad_file("Файл не является моделью вариограммы: в нём нет поля structures.", JSON_HINT)
    try:
        fields = _canonical(doc)
    except ValueError as e:
        raise bad_file(f"Модель в файле задана неверно. {getattr(e, 'message', e)}", _hint(e, JSON_HINT)) from e
    return dict(_plain(fields))


# --- by the format ---------------------------------------------------------------------------------------------------


def detect_format(data: Any) -> str:
    """«json» when the text starts with a brace or a bracket, else «gslib»."""
    return "json" if _text(data).lstrip()[:1] in ("{", "[") else "gslib"


def _format(fmt: Any) -> str:
    name = fmt.strip().lower() if isinstance(fmt, str) else fmt
    if name not in FORMATS:
        raise bad_param("format", f"Неизвестный формат обмена «{fmt}».", "Форматы обмена моделями: JSON и GSLIB.")
    return str(name)


def read_model(data: Any, fmt: Any = None) -> dict[str, Any]:
    """The fields of a model file; of GSLIB — only `c0`, `structures`, `common_axes`. Without `fmt` the format is
    told by the content. The model of the window: `model_form(*api_model(fields))`."""
    name = detect_format(data) if fmt is None else _format(fmt)
    return from_json(data) if name == "json" else from_gslib(data).as_dict()


def write_model(meta: Any, fmt: Any) -> str:
    """The text of a model in the format «json» or «gslib»."""
    if _format(fmt) == "json":
        return to_json(meta)
    if not isinstance(meta, Mapping):
        raise bad_param("model", "Модель вариограммы задана неверно.", "Ожидается объект с полями c0 и structures.")
    _canonical(meta)
    return to_gslib(parse_model(meta))


def file_name(name: Any, fmt: Any) -> str:
    """The name of an exported file: the name of the model without what Windows refuses (`<>:"/\\|?*`, control
    characters, the trailing dots and spaces, CON, NUL …), at most 120 characters, and the extension of the format
    (`.json`, `.par`)."""
    base = _NOT_IN_NAMES.sub("_", str(name or "")).strip().rstrip(". ")[:120].rstrip(". ") or "Модель вариограммы"
    if _RESERVED.match(base):
        base = f"_{base}"
    return base + EXTENSIONS[_format(fmt)]
