"""Expressions over table columns: «Фильтр по выражению» and «Вычисляемые поля» (ТЗ 7.34 п. 21, 31).

    AU > 0.5 and [Литология] == "гранит"
    (TO - FROM) * AU
    isnull(CU) or not between(AU, 1; 5)

Column names are written as they are (letters, digits, `_`) or in brackets `[Имя с пробелами]`; names are
case-insensitive. Numbers take a decimal comma inside brackets-free numbers only when followed by a digit
(`12,5`); function arguments are separated by `;` (a comma is a decimal sign in Russian). Words `и`, `или`,
`не` work as `and`, `or`, `not`; `=` and `<>` as `==` and `!=`.

Only a fixed set of nodes and functions is allowed (parsed with `ast`, evaluated on numpy arrays — no `eval`):
missing values stay missing (NaN / None) through arithmetic; a comparison with a missing value is false.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from typing import Any

import numpy as np

MAX_LENGTH = 2000


class ExprError(ValueError):
    """A wrong expression; `message` is shown to the user."""


Resolve = Callable[[str], tuple[str, np.ndarray]]  # column name → (kind "number"|"text", values)

_FUNCS_NUM: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "log10": np.log10,
    "exp": np.exp,
    "round": np.round,
    "floor": np.floor,
    "ceil": np.ceil,
}
_WORDS = {"и": "and", "или": "or", "не": "not"}


def _prepare(text: str) -> tuple[str, dict[str, str]]:
    """Brackets → placeholders, Russian words and `=`/`<>` → Python, decimal commas → points, `;` → `,`."""
    if len(text) > MAX_LENGTH:
        raise ExprError(f"Выражение длиннее {MAX_LENGTH} символов.")
    names: dict[str, str] = {}
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            j = text.find(ch, i + 1)
            if j < 0:
                raise ExprError("Не закрыта кавычка.")
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if ch == "[":
            j = text.find("]", i + 1)
            if j < 0:
                raise ExprError("Не закрыта скобка «]».")
            ph = f"__c{len(names)}"
            names[ph] = text[i + 1 : j].strip()
            out.append(ph)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    src = "".join(out)
    # outside strings only: split again to keep string literals intact
    parts = re.split(r"(\"[^\"]*\"|'[^']*')", src)
    for k in range(0, len(parts), 2):
        p = parts[k]
        p = re.sub(r"(?<=\d),(?=\d)", ".", p)
        p = p.replace(";", ",").replace("<>", "!=")
        p = re.sub(r"(?<![<>!=])=(?!=)", "==", p)
        p = re.sub(r"\b(и|или|не)\b", lambda m: _WORDS[m.group(1).lower()], p, flags=re.IGNORECASE)
        parts[k] = p
    return "".join(parts), names


class _Value:
    """A column-length result: numbers (float64, NaN missing), text (object, None missing) or bool."""

    def __init__(self, kind: str, values: Any) -> None:
        self.kind, self.values = kind, values


def compile_expr(text: str, resolve: Resolve, n: int) -> _Value:
    src, names = _prepare(text.strip())
    if not src:
        raise ExprError("Пустое выражение.")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"Ошибка в выражении (позиция {exc.offset or 0}).") from exc

    def column(name: str) -> _Value:
        try:
            kind, values = resolve(names.get(name, name))
        except KeyError:
            raise ExprError(f"Нет колонки «{names.get(name, name)}».") from None
        return _Value(kind, values)

    def num(v: _Value) -> np.ndarray:
        if v.kind == "number":
            return v.values  # type: ignore[no-any-return]
        if v.kind == "bool":
            return v.values.astype(float)  # type: ignore[no-any-return]
        raise ExprError("Текст в арифметике: используйте числовые колонки.")

    def const(value: Any) -> _Value:
        if isinstance(value, bool):
            return _Value("bool", np.full(n, value))
        if isinstance(value, int | float):
            return _Value("number", np.full(n, float(value)))
        if isinstance(value, str):
            return _Value("text", np.full(n, value, dtype=object))
        raise ExprError("Недопустимое значение в выражении.")

    def boolean(v: _Value) -> np.ndarray:
        if v.kind == "bool":
            return v.values  # type: ignore[no-any-return]
        if v.kind == "number":
            return ~np.isnan(v.values) & (v.values != 0)  # type: ignore[no-any-return]
        return np.array([x not in (None, "") for x in v.values], dtype=bool)

    def compare(op: ast.cmpop, a: _Value, b: _Value) -> np.ndarray:
        if a.kind == "text" or b.kind == "text":
            if a.kind != "text" or b.kind != "text":
                ta, tb = _as_text(a), _as_text(b)
            else:
                ta, tb = a.values, b.values
            present = np.array([x is not None and y is not None for x, y in zip(ta, tb, strict=True)])
            fa = np.array([str(x).casefold() if x is not None else "" for x in ta], dtype=object)
            fb = np.array([str(x).casefold() if x is not None else "" for x in tb], dtype=object)
            if isinstance(op, ast.Eq):
                return present & (fa == fb)
            if isinstance(op, ast.NotEq):
                return present & (fa != fb)
            raise ExprError("Текст сравнивается только на «=» и «<>».")
        x, y = num(a), num(b)
        with np.errstate(invalid="ignore"):
            res = {
                ast.Eq: lambda: x == y,
                ast.NotEq: lambda: x != y,
                ast.Lt: lambda: x < y,
                ast.LtE: lambda: x <= y,
                ast.Gt: lambda: x > y,
                ast.GtE: lambda: x >= y,
            }.get(type(op))
        if res is None:
            raise ExprError("Недопустимое сравнение.")
        return res() & ~np.isnan(x) & ~np.isnan(y)  # type: ignore[no-any-return]

    def ev(node: ast.AST) -> _Value:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Name):
            if node.id.lower() in ("true", "false"):
                return const(node.id.lower() == "true")
            return column(node.id)
        if isinstance(node, ast.Constant):
            return const(node.value)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return _Value("bool", ~boolean(v))
            if isinstance(node.op, ast.USub):
                return _Value("number", -num(v))
            if isinstance(node.op, ast.UAdd):
                return _Value("number", num(v))
        if isinstance(node, ast.BoolOp):
            masks = [boolean(ev(x)) for x in node.values]
            acc = masks[0]
            for m in masks[1:]:
                acc = (acc & m) if isinstance(node.op, ast.And) else (acc | m)
            return _Value("bool", acc)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add) and (a.kind == "text" or b.kind == "text"):
                ta, tb = _as_text(a), _as_text(b)
                return _Value(
                    "text",
                    np.array(
                        [None if x is None or y is None else f"{x}{y}" for x, y in zip(ta, tb, strict=True)],
                        dtype=object,
                    ),
                )
            x, y = num(a), num(b)
            ops: dict[type, Callable[[], np.ndarray]] = {
                ast.Add: lambda: x + y,
                ast.Sub: lambda: x - y,
                ast.Mult: lambda: x * y,
                ast.Div: lambda: np.where(y == 0, np.nan, x / np.where(y == 0, 1, y)),
                ast.Pow: lambda: np.power(x, y),
            }
            f = ops.get(type(node.op))
            if f is None:
                raise ExprError("Недопустимая операция.")
            with np.errstate(all="ignore"):
                return _Value("number", f())
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            acc = np.ones(n, dtype=bool)
            for op, right_node in zip(node.ops, node.comparators, strict=True):
                right = ev(right_node)
                acc &= compare(op, left, right)
                left = right
            return _Value("bool", acc)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id.lower()
            args = [ev(a) for a in node.args]
            if name in _FUNCS_NUM and len(args) == 1:
                with np.errstate(all="ignore"):
                    return _Value("number", _FUNCS_NUM[name](num(args[0])))
            if name == "isnull" and len(args) == 1:
                return _Value("bool", ~boolean(_Value("bool", _present(args[0]))))
            if name == "notnull" and len(args) == 1:
                return _Value("bool", _present(args[0]))
            if name == "between" and len(args) == 3:
                x, lo, hi = (num(a) for a in args)
                with np.errstate(invalid="ignore"):
                    return _Value("bool", (x >= lo) & (x <= hi) & ~np.isnan(x))
            if name in ("min", "max") and len(args) >= 2:
                stack = np.vstack([num(a) for a in args])
                with np.errstate(all="ignore"):
                    return _Value("number", (np.nanmin if name == "min" else np.nanmax)(stack, axis=0))
            if name in ("upper", "lower", "trim") and len(args) == 1:
                t = _as_text(args[0])
                fns: dict[str, Callable[[str], str]] = {"upper": str.upper, "lower": str.lower, "trim": str.strip}
                fn = fns[name]
                return _Value("text", np.array([None if v is None else fn(str(v)) for v in t], dtype=object))
            if name == "contains" and len(args) == 2:
                t, s = _as_text(args[0]), _as_text(args[1])
                return _Value(
                    "bool",
                    np.array(
                        [
                            v is not None and w is not None and str(w).casefold() in str(v).casefold()
                            for v, w in zip(t, s, strict=True)
                        ],
                        dtype=bool,
                    ),
                )
            raise ExprError(f"Неизвестная функция «{node.func.id}» или неверное число аргументов.")
        raise ExprError("Недопустимая конструкция в выражении.")

    return ev(tree)


def _present(v: _Value) -> np.ndarray:
    if v.kind == "number":
        return ~np.isnan(v.values)  # type: ignore[no-any-return]
    if v.kind == "bool":
        return np.ones(len(v.values), dtype=bool)
    return np.array([x not in (None, "") for x in v.values], dtype=bool)


def _as_text(v: _Value) -> np.ndarray:
    if v.kind == "text":
        return v.values  # type: ignore[no-any-return]
    if v.kind == "number":
        from bf_core.formats.numbers import number_text

        return np.array([None if np.isnan(x) else number_text(float(x)) for x in v.values], dtype=object)
    return np.array(["да" if x else "нет" for x in v.values], dtype=object)


def evaluate_filter(text: str, resolve: Resolve, n: int) -> np.ndarray:
    v = compile_expr(text, resolve, n)
    if v.kind != "bool":
        raise ExprError("Фильтр должен быть условием: например «AU > 0,5».")
    return v.values  # type: ignore[no-any-return]


def evaluate_value(text: str, resolve: Resolve, n: int) -> tuple[str, np.ndarray]:
    """A computed field: ("number", float64) or ("text", object); conditions become 1/0."""
    v = compile_expr(text, resolve, n)
    if v.kind == "bool":
        return "number", v.values.astype(float)
    return v.kind, v.values
