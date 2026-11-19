"""Checks of the parameters of an operation (ТЗ 7.23): numbers, vectors, choices, flags and references to
objects — every refusal is a 422 {code, message, hint} in Russian that names the field as the window shows it.
Numbers may come as text with a comma («0,5»). No numpy here: the endpoint checks before a job is queued."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from bf_core.api.errors import ApiError

MAX_REFS = 2000  # layers or wireframes named by one operation
MAX_LINES = 200_000  # lines picked in one layer


def bad(code: str, message: str, hint: str = "Исправьте значение и повторите.", **extra: Any) -> ApiError:
    return ApiError(422, code, message, hint, extra or None)


def number(
    params: dict[str, Any],
    key: str,
    label: str,
    default: float | None = None,
    *,
    positive: bool = False,
    nonnegative: bool = False,
    nonzero: bool = False,
    required: bool = False,
) -> float | None:
    """A finite number (None — the default); `required` refuses an empty one."""
    value = params.get(key, default)
    if isinstance(value, str):
        text = value.strip().replace("−", "-").replace(",", ".").replace(" ", "")
        value = None if not text else text
    if value is None:
        if required:
            raise bad("param_missing", f"Не задано: «{label}».", f"Введите значение поля «{label}».", field=key)
        return None
    if isinstance(value, bool):
        raise bad("not_a_number", f"«{label}» — не число.", "Введите число.", field=key)
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise bad("not_a_number", f"«{label}» — не число.", "Введите число, например 0,5.", field=key) from None
    if not math.isfinite(x):
        raise bad("not_a_number", f"«{label}» — не число.", "Введите конечное число.", field=key)
    if positive and x <= 0:
        raise bad("bad_param", f"«{label}» должно быть больше нуля.", "Введите положительное число.", field=key)
    if nonnegative and x < 0:
        raise bad("bad_param", f"«{label}» не может быть отрицательным.", "Введите ноль или больше.", field=key)
    if nonzero and x == 0:
        raise bad("bad_param", f"«{label}» не может быть нулём.", "Введите число, отличное от нуля.", field=key)
    return x + 0.0


def integer(
    params: dict[str, Any], key: str, label: str, default: int | None = None, *, lo: int | None = None,
    hi: int | None = None,
) -> int | None:  # fmt: skip
    x = number(params, key, label, None if default is None else float(default))
    if x is None:
        return None
    if x != int(x):
        raise bad("bad_param", f"«{label}» должно быть целым числом.", "Введите целое число.", field=key)
    n = int(x)
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        if lo is not None and hi is not None:
            span = f"от {lo} до {hi}"
        else:
            span = f"не меньше {lo}" if lo is not None else f"не больше {hi}"
        raise bad("bad_param", f"«{label}» должно быть {span}.", f"Введите число {span}.", field=key)
    return n


def vector(params: dict[str, Any], key: str, label: str, *, nonzero: bool = False) -> list[float] | None:
    """Three numbers X, Y, Z (a list, or text «1; 0; 0»); None — not given."""
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        value = [v for v in value.replace(";", " ").split() if v]
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 3:
        hint = "Введите три числа через точку с запятой."
        raise bad("bad_vector", f"«{label}» — три числа X, Y, Z.", hint, field=key)
    out = [number({"v": v}, "v", label, required=True) for v in value]
    if nonzero and not any(out):
        hint = "Задайте направление: хотя бы одно число не ноль."
        raise bad("bad_vector", f"«{label}» не может быть нулевым.", hint, field=key)
    return [float(v) for v in out]  # type: ignore[arg-type]


def flag(params: dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    return default if value is None else bool(value)


def choice(params: dict[str, Any], key: str, label: str, allowed: Sequence[str], default: str) -> str:
    value = params.get(key, default)
    value = default if value is None else str(value)
    if value not in allowed:
        message = f"«{label}»: неизвестное значение «{value[:40]}»."
        raise bad("bad_choice", message, "Выберите значение из списка.", field=key)
    return value


def object_id(value: Any, label: str) -> int | None:
    """An object id; None stays None (a source deleted before a `.bfggis` export has none)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise bad("bad_ref", f"«{label}»: неверный номер объекта.", "Выберите объект заново.")
    try:
        return int(value)
    except ValueError:
        raise bad("bad_ref", f"«{label}»: неверный номер объекта.", "Выберите объект заново.") from None


def id_list(value: Any, label: str, *, least: int = 1) -> list[int | None]:
    """Distinct ids, at least `least` of them."""
    if value is None:
        value = []
    if not isinstance(value, list | tuple):
        value = [value]
    if len(value) > MAX_REFS:
        raise bad("too_many_sources", f"«{label}»: больше {MAX_REFS} объектов.", "Выберите меньше объектов.")
    ids = [object_id(v, label) for v in value]
    if len([i for i in ids if i is not None]) != len({i for i in ids if i is not None}):
        raise bad("same_source", f"«{label}»: один объект выбран дважды.", "Выберите разные объекты.")
    if len(ids) < least:
        raise bad("too_few_sources", f"«{label}»: выберите не меньше {least}.", "Добавьте объекты к операции.")
    return ids


def lid_list(value: Any, label: str) -> list[int] | None:
    """Stable numbers of lines (`lid`); None — every line of the layer."""
    if value is None:
        return None
    if not isinstance(value, list | tuple) or len(value) > MAX_LINES:
        raise bad("bad_lines", f"«{label}»: неверный список линий.", "Выберите линии заново.")
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise bad("bad_lines", f"«{label}»: неверный номер линии.", "Выберите линии заново.")
        out.append(int(v))
    if not out:
        raise bad("no_lines", f"«{label}»: не выбрано ни одной линии.", "Выберите линии или весь слой.")
    return list(dict.fromkeys(out))
