"""The bodies of the fill and coding requests, checked (подшаг 17.20; ТЗ 8.7.3; docs/progress/e17-api.md §7).

    spec = parse_fill(body)                  # POST /blocks/fill      → FillSpec
    spec = parse_code(body)                  # POST /blocks/{id}/code → FillSpec (split, expected_version)
    params = spec.to_params()                # the canonical form: `source.params` of the model, the job's input
    spec = FillSpec.of_params(params)        # and back (the job and «Перестроить» read it)

Numbers come as a number or a text («4», «4,0»); whole ones — 4 or 4.0. No key / null / "" — the default. Every
refusal is 422 `bad_param {param}` in Russian with a hint. `code` of a wireframe — the label of the category CODE
(the name of the wireframe by default); `priority` — whole, larger is the main (None — 0, equal ones — the earlier
in the list). The fields: `code_field` (CODE by default), `frac_field` (FRAC; for coding without splitting — none
unless given: a field FRAC changes the volume of the rows). No numpy here: the module loads with the core.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bf_core.api.errors import ApiError

MODES = ("inside", "outside", "both")
MAX_WIRES = 100  # bodies of one fill: a code is int16, the rays of all of them are one index
MAX_M = 16
DEFAULT_M = 4
DEFAULT_OUTSIDE = "вне"
DEFAULT_CODE_FIELD = "CODE"
DEFAULT_FRAC_FIELD = "FRAC"
MAX_NAME = 100  # a field name (`schema.MAX_FIELD_NAME`)
MAX_LABEL = 200
MAX_PRIORITY = 1_000_000
SUB_COLUMNS = ("sx", "sy", "sz", "wx", "wy", "wz")


def bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param})


def whole(value: Any, param: str, label: str, low: int, high: int, hint: str) -> int:
    """An integer in [low; high] from a number or a text; 422 `bad_param {param}` otherwise."""
    raw = value
    if isinstance(value, str):
        try:
            raw = float(value.strip().replace(",", "."))
        except ValueError:
            raw = None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw or raw in (float("inf"), float("-inf")):
        raise bad(param, f"{label}: нужно целое число.", hint)
    if float(raw) != int(raw) or not low <= int(raw) <= high:
        raise bad(param, f"{label} = {value!r}: нужно целое число от {low} до {high}.", hint)
    return int(raw)


def _text(value: Any, param: str, label: str, limit: int, default: str | None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str):
        raise bad(param, f"{label}: нужен текст.", "Введите название.")
    clean = " ".join(value.split())
    if len(clean) > limit:
        raise bad(param, f"{label} длиннее {limit} символов.", "Сократите название.")
    return clean


def _id(value: Any, param: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**12:
        raise bad(param, f"{label}: номер объекта задан неверно.", "Выберите объект из списка.")
    return int(value)


def check_field_name(name: str, param: str, label: str) -> str:
    key = name.casefold()
    if key == "idx" or key in SUB_COLUMNS:
        raise bad(param, f"{label} «{name}» совпадает со служебным столбцом.", "Задайте другое имя поля.")
    return name


@dataclass(frozen=True)
class Wire:
    id: int
    code: str | None = None
    priority: int | None = None

    def to_params(self) -> dict[str, Any]:
        return {"id": self.id, "code": self.code, "priority": self.priority}


@dataclass(frozen=True)
class FillSpec:
    """The checked request. `source_id` — the prototype (fill) or the model (coding)."""

    source_id: int
    wires: tuple[Wire, ...]
    mode: str = "inside"
    m: int = DEFAULT_M
    levels: tuple[int, int, int] | None = None
    outside_code: str = DEFAULT_OUTSIDE
    code_field: str = DEFAULT_CODE_FIELD
    frac_field: str | None = DEFAULT_FRAC_FIELD
    name: str | None = None
    folder: str | None = None
    split: bool = True  # coding: new model with splitting (True) or a field of the same model (False)
    expected_version: int | None = None  # coding

    def to_params(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "wireframes": [w.to_params() for w in self.wires], "mode": self.mode,
                "m": self.m, "levels": None if self.levels is None else list(self.levels),
                "outside_code": self.outside_code, "code_field": self.code_field, "frac_field": self.frac_field,
                "name": self.name, "folder": self.folder, "split": self.split,
                "expected_version": self.expected_version}  # fmt: skip

    @classmethod
    def of_params(cls, raw: Mapping[str, Any]) -> FillSpec:
        lv = raw.get("levels")
        return cls(int(raw["source_id"]), tuple(Wire(int(w["id"]), w.get("code"), w.get("priority"))
                                                for w in raw["wireframes"]),
                   str(raw["mode"]), int(raw["m"]), None if lv is None else (int(lv[0]), int(lv[1]), int(lv[2])),
                   str(raw["outside_code"]), str(raw["code_field"]), raw.get("frac_field"), raw.get("name"),
                   raw.get("folder"), bool(raw.get("split", True)), raw.get("expected_version"))  # fmt: skip

    def codes(self, names: Sequence[str]) -> list[str]:
        """The label of every wireframe, in the order of the request: the given `code`, else its name."""
        return [w.code or n for w, n in zip(self.wires, names, strict=True)]


def _wires(raw: Any) -> tuple[Wire, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise bad("wireframes", "Не выбрано ни одного каркаса.", "Выберите хотя бы один замкнутый каркас.")
    if len(raw) > MAX_WIRES:
        raise bad("wireframes", f"Каркасов больше {MAX_WIRES}.", f"Заполняйте не больше {MAX_WIRES} каркасов за раз.")
    out: list[Wire] = []
    seen: set[int] = set()
    for n, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise bad(f"wireframes.{n}", "Каркас задаётся номером, кодом и приоритетом.", "Выберите каркас.")
        wid = _id(item.get("id"), f"wireframes.{n}.id", "Каркас")
        if wid in seen:
            raise bad(f"wireframes.{n}.id", "Каркас указан дважды.", "Уберите повтор.")
        seen.add(wid)
        code = _text(item.get("code"), f"wireframes.{n}.code", "Код каркаса", MAX_LABEL, None)
        pr = item.get("priority")
        prio = (
            None
            if pr is None or pr == ""
            else whole(
                pr,
                f"wireframes.{n}.priority",
                "Приоритет",
                -MAX_PRIORITY,
                MAX_PRIORITY,
                "Приоритет — целое число; больше — главнее.",
            )
        )
        out.append(Wire(wid, code, prio))
    return tuple(out)


def _common(raw: Mapping[str, Any]) -> dict[str, Any]:
    mode = raw.get("mode")
    mode = "inside" if mode in (None, "") else mode
    if mode not in MODES:
        raise bad("mode", f"Режим «{mode}» неизвестен.", "Выберите «внутри», «снаружи» или «оба».", "fill_mode")
    m = raw.get("m")
    m = (
        DEFAULT_M
        if m in (None, "")
        else whole(
            m, "m", "Точек подсетки m", 1, MAX_M, "Доля объёма считается по m × m × m точкам блока; по умолчанию 4."
        )
    )
    outside = _text(raw.get("outside_code"), "outside_code", "Код «снаружи»", MAX_LABEL, DEFAULT_OUTSIDE)
    code_field = _text(raw.get("code_field"), "code_field", "Поле кода", MAX_NAME, DEFAULT_CODE_FIELD)
    assert outside is not None and code_field is not None
    check_field_name(code_field, "code_field", "Поле кода")
    name = _text(raw.get("name"), "name", "Название модели", 200, None)
    folder = _text(raw.get("folder"), "folder", "Папка", 400, None)
    return {"mode": mode, "m": m, "outside_code": outside, "code_field": code_field, "name": name, "folder": folder}


def _frac_field(raw: Mapping[str, Any], code_field: str, default: str | None) -> str | None:
    if "frac_field" not in raw:
        got: str | None = default
    else:
        got = _text(raw["frac_field"], "frac_field", "Поле доли", MAX_NAME, None)
    if got is not None:
        check_field_name(got, "frac_field", "Поле доли")
        if got.casefold() == code_field.casefold():
            raise bad("frac_field", "Поле доли и поле кода называются одинаково.", "Задайте разные имена полей.")
    return got


def _check_outside(mode: str, outside: str, wires: tuple[Wire, ...]) -> None:
    if mode == "inside":
        return
    names = {w.code.casefold() for w in wires if w.code}
    if outside.casefold() in names:
        raise bad("outside_code", f"Код «{outside}» уже задан каркасу.", "Задайте другой код для блоков снаружи.")


def parse_fill(raw: Any) -> FillSpec:
    """`POST /blocks/fill`: {prototype_id, wireframes, mode, levels, m, outside_code, code_field, frac_field, name,
    folder}."""
    if not isinstance(raw, Mapping):
        raise bad("body", "Параметры заполнения заданы неверно.", "Проверьте запрос.")
    pid = _id(raw.get("prototype_id"), "prototype_id", "Прототип")
    wires = _wires(raw.get("wireframes"))
    c = _common(raw)
    levels = raw.get("levels")
    lv: tuple[int, int, int] | None = None
    if levels is not None:
        if not isinstance(levels, Sequence) or isinstance(levels, str) or len(levels) != 3:
            raise bad("levels", "Уровни субблоков: нужны три числа (X, Y, Z).",
                      "Задайте от 0 до 7 уровней по каждой оси.")  # fmt: skip
        hint = "Каждый уровень делит блок пополам: от 0 до 7."
        lv = (whole(levels[0], "levels.0", "Уровней по X", 0, 7, hint),
              whole(levels[1], "levels.1", "Уровней по Y", 0, 7, hint),
              whole(levels[2], "levels.2", "Уровней по Z", 0, 7, hint))  # fmt: skip
    frac = _frac_field(raw, c["code_field"], DEFAULT_FRAC_FIELD)
    if frac is None:
        raise bad("frac_field", "Заполнению нужно поле доли объёма.", "Задайте имя поля, по умолчанию FRAC.")
    _check_outside(c["mode"], c["outside_code"], wires)
    return FillSpec(pid, wires, c["mode"], c["m"], lv, c["outside_code"], c["code_field"], frac, c["name"],
                    c["folder"], True, None)  # fmt: skip


def parse_code(model_id: int, raw: Any) -> FillSpec:
    """`POST /blocks/{id}/code`: {expected_version, wireframes, mode, split, m, outside_code, code_field,
    frac_field, name}. `frac_field` of coding without splitting — none unless given."""
    if not isinstance(raw, Mapping):
        raise bad("body", "Параметры кодирования заданы неверно.", "Проверьте запрос.")
    version = raw.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ApiError(409, "version_conflict", "Не указана версия модели.", "Перечитайте модель и повторите.",
                       extra={"version": None})  # fmt: skip
    split = raw.get("split", False)
    if not isinstance(split, bool):
        raise bad("split", "Признак «с дроблением» — да или нет.", "Включите или выключите флажок.")
    wires = _wires(raw.get("wireframes"))
    c = _common(raw)
    frac = _frac_field(raw, c["code_field"], DEFAULT_FRAC_FIELD if split else None)
    _check_outside(c["mode"], c["outside_code"], wires)
    return FillSpec(model_id, wires, c["mode"], c["m"], None, c["outside_code"], c["code_field"], frac, c["name"],
                    c["folder"], split, version)  # fmt: skip
