"""The QAQC settings of a project and the CRM reference (ADR 0020 §3): `settings(scope="qaqc")`, keys `config`, `crm`.

    get_config(conn) → {version, types: {code: type}, dl: [{element, lab | None, value, unit}], limits, empty_is_orig}
    put_config(conn, body, expected_version) → the same, version + 1; 409 `version_conflict` {current}
    get_crm(conn) → {version, rows: [{crm_id, element, mean, sd, unit, source}]};  put_crm(conn, rows, expected_version)
    read_crm_file(path, sheet?) → {rows, problems, columns} — rows of a CSV / XLSX for the form, nothing written

Nothing stored — the defaults with version 0 (the first PUT expects 0). Codes of TYPE are compared without case
and edge spaces (stored upper case, trimmed); an empty code is ORIG when `empty_is_orig`. A code not in `types`
is «не распознано» — the calculation leaves it out (14.4). Limits outside the ranges of ТЗ — 422 `bad_param
{param}`; a CRM row with μ ≤ 0 or σ ≤ 0, without CRM_ID or element, a repeated (CRM_ID, element) — 422 `bad_crm
{row}` (0-based index, the text says «строка N» from 1); > 5000 rows — 422 `too_many_rows`. Events (the router
publishes them): `qaqc.changed {what: "config" | "crm", version}`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import settings
from bf_core.qaqc.limits import Limits, QaqcError

SCOPE = "qaqc"
CONFIG, CRM = "config", "crm"
TYPES = ("ORIG", "CRM", "BLANK", "DUP_F", "DUP_C", "DUP_P", "UMP")
DEFAULT_TYPES = {**{t: t for t in TYPES}, "STD": "CRM", "STANDARD": "CRM", "BLK": "BLANK"}
MAX_CRM = 5000
MAX_DL = 2000
MAX_CODES = 500
MAX_TEXT = 100


#: name → (default, low, high, integer). th_group is odd; sigma_warn < sigma_fail (ADR 0020 §3).
def bad(message: str, hint: str, param: str, code: str = "bad_param", **extra: Any) -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param, **extra})


def default_limits() -> dict[str, Any]:
    return dict(Limits().as_dict())


def default_config() -> dict[str, Any]:
    return {"types": dict(DEFAULT_TYPES), "dl": [], "limits": default_limits(), "empty_is_orig": True}


def _number(raw: Any, param: str, title: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(float(raw)):
        raise bad(f"{title}: нужно число.", "Введите число.", param)
    return float(raw)


def check_limits(raw: Any) -> dict[str, Any]:
    """The limits filled with defaults and checked against the ranges of ТЗ (422 `bad_param {param}`).
    One source of truth — `qaqc.limits.RANGES`; the param is prefixed with `limits.` for the window."""
    try:
        return dict(Limits.parse(raw).as_dict())
    except QaqcError as e:
        raise bad(e.message, e.hint, f"limits.{e.param}" if e.param else "limits", e.code) from e


def type_key(code: Any) -> str:
    """A code of TYPE as it is compared: trimmed, upper case."""
    return str(code).strip().upper()


def check_types(raw: Any) -> dict[str, str]:
    if raw is None:
        return dict(DEFAULT_TYPES)
    if not isinstance(raw, dict) or len(raw) > MAX_CODES:
        raise bad("Неверный словарь кодов TYPE.", f"Не больше {MAX_CODES} кодов.", "types")
    out: dict[str, str] = {}
    for code, kind in raw.items():
        key = type_key(code)
        if not key or len(key) > MAX_TEXT:
            raise bad(f"Недопустимый код TYPE «{code}».", f"Код — от 1 до {MAX_TEXT} символов.", f"types.{code}")
        if kind not in TYPES:
            raise bad(f"Код «{code}»: неизвестный тип «{kind}».", "Типы: " + ", ".join(TYPES) + ".", f"types.{code}")
        if key in out and out[key] != kind:
            raise bad(f"Код «{code}» указан дважды с разными типами.", "Оставьте одно соответствие.", f"types.{code}")
        out[key] = str(kind)
    return out


def _text(raw: Any, limit: int = MAX_TEXT) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:limit] if text else None


def check_dl(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_DL:
        raise bad("Неверная таблица DL.", f"Не больше {MAX_DL} строк.", "dl")
    out, seen = [], set()
    for i, row in enumerate(raw):
        param = f"dl[{i}]"
        if not isinstance(row, dict):
            raise bad(f"DL, строка {i + 1}: неверные данные.", "Заполните строку заново.", param)
        element = _text(row.get("element"))
        if element is None:
            raise bad(f"DL, строка {i + 1}: не указан элемент.", "Укажите элемент.", f"{param}.element")
        lab = _text(row.get("lab"))
        value = _number(row.get("value"), f"{param}.value", f"DL, строка {i + 1}")
        if value <= 0:
            raise bad(f"DL, строка {i + 1}: значение должно быть больше нуля.", "Введите DL > 0.", f"{param}.value")
        key = (element.casefold(), (lab or "").casefold())
        if key in seen:
            raise bad(f"DL, строка {i + 1}: повтор элемента и лаборатории.", "Оставьте одну строку.", param)
        seen.add(key)
        out.append({"element": element, "lab": lab, "value": value, "unit": _text(row.get("unit"))})
    return out


def check_config(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise bad("Неверная настройка QAQC.", "Откройте окно «Настройка QAQC» и сохраните заново.", "config")
    empty = body.get("empty_is_orig", True)
    if not isinstance(empty, bool):
        raise bad("Неверный признак «Пустой код — ORIG».", "Отметьте или снимите флажок.", "empty_is_orig")
    return {"types": check_types(body.get("types")), "dl": check_dl(body.get("dl")),
            "limits": check_limits(body.get("limits")), "empty_is_orig": empty}  # fmt: skip


def check_crm(rows: Any) -> list[dict[str, Any]]:
    """The reference rows, checked: 422 `bad_crm {row}` or `too_many_rows`."""
    if not isinstance(rows, list):
        raise bad("Неверный справочник CRM.", "Откройте справочник и сохраните заново.", "rows", code="bad_crm",
                  row=None)  # fmt: skip
    if len(rows) > MAX_CRM:
        raise bad(f"В справочнике CRM {len(rows)} строк — больше {MAX_CRM}.", "Оставьте нужные стандарты.",
                  "rows", code="too_many_rows")  # fmt: skip
    out, seen = [], set()
    for i, row in enumerate(rows):

        def fail(text: str, hint: str, field: str, i: int = i) -> ApiError:
            return bad(f"Справочник CRM, строка {i + 1}: {text}", hint, f"rows[{i}].{field}", code="bad_crm", row=i)

        if not isinstance(row, dict):
            raise fail("неверные данные.", "Заполните строку заново.", "crm_id")
        crm_id, element = _text(row.get("crm_id")), _text(row.get("element"))
        if crm_id is None:
            raise fail("не указан CRM_ID.", "Укажите номер стандарта.", "crm_id")
        if element is None:
            raise fail("не указан элемент.", "Укажите элемент.", "element")
        values = {}
        for field, title in (("mean", "μ"), ("sd", "σ")):
            raw = row.get(field)
            if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(float(raw)):
                raise fail(f"{title} — не число.", f"Введите {title} > 0.", field)
            if float(raw) <= 0:
                raise fail(f"{title} должно быть больше нуля.", f"Введите {title} > 0.", field)
            values[field] = float(raw)
        key = (crm_id.casefold(), element.casefold())
        if key in seen:
            raise fail(f"повтор пары «{crm_id}» — «{element}».", "Оставьте одну строку на стандарт и элемент.",
                       "crm_id")  # fmt: skip
        seen.add(key)
        out.append({"crm_id": crm_id, "element": element, **values, "unit": _text(row.get("unit")),
                    "source": _text(row.get("source"), 200)})  # fmt: skip
    return out


# --- storage ---------------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection, key: str) -> tuple[Any, int] | None:
    r = conn.execute(
        sa.select(settings.c.value, settings.c.version).where(settings.c.scope == SCOPE, settings.c.key == key)
    ).first()
    return None if r is None else (r.value, int(r.version))


def _conflict(current: dict[str, Any]) -> ApiError:
    return ApiError(409, "version_conflict", "Настройку QAQC изменили, пока вы её правили.",
                    "Проверьте изменения и сохраните ещё раз.", extra={"current": current})  # fmt: skip


def _put(conn: sa.Connection, key: str, value: dict[str, Any], expected: Any, current: dict[str, Any]) -> int:
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise bad("Нет версии настройки.", "Обновите окно и повторите.", "expected_version")
    if current["version"] != expected:
        raise _conflict(current)
    stored = {"v": 1, **value}
    if expected == 0:
        conn.execute(sa.insert(settings).values(scope=SCOPE, key=key, value=stored, version=1))
        return 1
    n = conn.execute(
        sa.update(settings)
        .where(settings.c.scope == SCOPE, settings.c.key == key, settings.c.version == expected)
        .values(value=stored, version=expected + 1)
    ).rowcount
    if n != 1:
        raise _conflict(current)
    return expected + 1


def get_config(conn: sa.Connection) -> dict[str, Any]:
    found = _row(conn, CONFIG)
    if found is None:
        return {"version": 0, **default_config()}
    value, version = found
    base = default_config()
    base.update({k: value[k] for k in base if k in value})
    base["limits"] = {**default_limits(), **base["limits"]}  # a limit added later gets its default
    return {"version": version, **base}


def put_config(conn: sa.Connection, body: Any, expected_version: Any) -> dict[str, Any]:
    clean = check_config(body)
    _put(conn, CONFIG, clean, expected_version, get_config(conn))
    return get_config(conn)


def get_crm(conn: sa.Connection) -> dict[str, Any]:
    found = _row(conn, CRM)
    if found is None:
        return {"version": 0, "rows": []}
    return {"version": found[1], "rows": list(found[0].get("rows", []))}


def put_crm(conn: sa.Connection, rows: Any, expected_version: Any) -> dict[str, Any]:
    clean = check_crm(rows)
    _put(conn, CRM, {"rows": clean}, expected_version, get_crm(conn))
    return get_crm(conn)


def type_of(config: dict[str, Any], code: str | None) -> str | None:
    """The type of a code of the file by the settings; None — «не распознано»."""
    key = type_key(code) if code is not None else ""
    if not key:
        return "ORIG" if config.get("empty_is_orig", True) else None
    return config["types"].get(key)  # type: ignore[no-any-return]


# --- the reference from a file -----------------------------------------------------------------------------------

_CRM_ALIASES = {
    "crm_id": ("crmid", "crm", "standard", "standardid", "std", "stdid", "стандарт", "осо", "id"),
    "element": ("element", "elem", "analyte", "элемент", "компонент"),
    "mean": ("mean", "expected", "value", "certified", "mu", "μ", "среднее", "ожидаемое", "аттестованное"),
    "sd": ("sd", "std", "stdev", "sigma", "σ", "сигма", "ско"),
    "unit": ("unit", "units", "единица", "ед"),
    "source": ("source", "certificate", "источник", "паспорт"),
}


def read_crm_file(path: str | Path, sheet: str | None = None) -> dict[str, Any]:
    """Rows of a CSV / XLSX for the form of the reference (`POST /crm/read`): columns by names, values parsed;
    rows that `check_crm` would refuse are listed in `problems` [{row, message}] and kept in `rows`."""
    from bf_core.formats.errors import FormatError
    from bf_core.formats.inspect import inspect_table
    from bf_core.formats.numbers import parse_value
    from bf_core.formats.registry import read_table
    from bf_core.formats.table import ReadOptions

    from .normalize import _key

    try:
        guess = inspect_table(path, ReadOptions(sheet=sheet), limit=1).estimated_rows
        if guess is not None and guess > 2 * MAX_CRM:  # an estimate of a text file: refused only when far past
            raise bad(f"В файле около {guess} строк — больше {MAX_CRM}.", "Оставьте нужные стандарты.", "path",
                      code="too_many_rows")  # fmt: skip
        tab = read_table(path, ReadOptions(sheet=sheet, text_columns=()))
    except FormatError as exc:
        raise ApiError(422, exc.code, exc.message, exc.hint) from exc
    if tab.rows > MAX_CRM:
        raise bad(f"В файле {tab.rows} строк — больше {MAX_CRM}.", "Оставьте нужные стандарты.", "path",
                  code="too_many_rows")  # fmt: skip
    found: dict[str, str] = {}
    for field, aliases in _CRM_ALIASES.items():
        for name in tab.names:
            if name not in found.values() and _key(name) in {_key(a) for a in (field, *aliases)}:
                found[field] = name
                break
    if "crm_id" not in found or "mean" not in found or "sd" not in found:
        raise ApiError(422, "bad_crm_file", "В файле не найдены колонки CRM_ID, среднего и σ.",
                       "Назовите колонки CRM_ID, ELEMENT, MEAN, SD, UNIT, SOURCE и повторите.",
                       extra={"columns": tab.names})  # fmt: skip
    cols = {f: tab.column(n) for f, n in found.items()}
    texts = {f: cols[f].text() for f in ("crm_id", "element", "unit", "source") if f in cols}
    nums: dict[str, list[Any]] = {}
    for f in ("mean", "sd"):
        col = cols[f]
        nums[f] = col.to_numpy().tolist() if col.kind == "number" else [parse_value(t)[0] for t in col.text()]
    rows: list[dict[str, Any]] = []
    for i in range(tab.rows):
        row: dict[str, Any] = {f: texts[f][i] if f in texts else None for f in ("crm_id", "element", "unit", "source")}
        for f in ("mean", "sd"):
            value = nums[f][i]
            row[f] = None if value is None or math.isnan(value) else float(value)
        rows.append(row)
    problems = []
    for i, row in enumerate(rows):
        try:
            check_crm([row])
        except ApiError as exc:
            problems.append({"row": i, "message": exc.message.replace("строка 1", f"строка {i + 1}")})
    return {"rows": rows, "problems": problems, "columns": found}
