"""Tokens of the calculator language (ТЗ 8.7.4). No `eval`, no `ast`: a hand-written scanner.

    numbers   12  3.5  .5  1e-3        (a decimal point only: the comma separates arguments)
    strings   'руда'  'it''s'          (a quote inside is doubled)
    fields    AU  Au_ppm  [Имя с пробелами]
    operators + − - * / ^ = <> < <= > >=   and  ( ) ,
    words     AND OR NOT IN and function names — any case, ASCII only
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .errors import ExprError

NUM, STR, IDENT, FIELD, OP, LPAREN, RPAREN, COMMA, END = "num", "str", "ident", "field", "op", "(", ")", ",", "end"

_NUMBER = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_OPERATORS = ("<=", ">=", "<>", "<", ">", "=", "+", "-", "*", "/", "^")
_VALID = "Допустимы числа, поля, строки в одинарных кавычках, операторы + − * / ^ = <> < <= > >=, скобки и запятая."
_BAD_CHAR_HINTS = {
    '"': "Строки пишутся в одинарных кавычках: 'руда'.",
    ";": "Аргументы функций разделяются запятой.",
    "!": "Неравенство пишется «<>», отрицание — NOT.",
    "&": "Логическое «и» пишется AND.",
    "|": "Логическое «или» пишется OR.",
    "{": "Имя поля с пробелами пишется в квадратных скобках: [имя поля].",
    "}": "Имя поля с пробелами пишется в квадратных скобках: [имя поля].",
    ".": "Точка допустима только в числе: 0.5. Имя поля с точкой пишется в квадратных скобках.",
    "%": "Остаток от деления не поддерживается.",
}


class Token(NamedTuple):
    kind: str
    value: str | float
    pos: int
    end: int


def _ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _ident_part(ch: str) -> bool:
    return ch.isalpha() or ch == "_" or ch.isdecimal()


def tokenize(text: str) -> list[Token]:
    """The tokens of `text`, the last one is END. Raises ExprError at the first bad character."""
    out: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "0123456789" or (ch == "." and i + 1 < n and text[i + 1] in "0123456789"):
            m = _NUMBER.match(text, i)
            assert m is not None
            j = m.end()
            if j < n and (_ident_part(text[j]) or text[j] == "."):
                raise ExprError(text, i, "Некорректное число", "Число пишется с точкой: 12.5 или 1e-3. "
                                "Между числом и полем нужен оператор, например 2 * AU.", "bad_number")
            value = float(m.group())
            if value in (float("inf"), float("-inf")):
                raise ExprError(text, i, "Число слишком велико", "Допустимы значения до 1.8e308.", "bad_number")
            out.append(Token(NUM, value, i, j))
            i = j
        elif _ident_start(ch):
            j = i + 1
            while j < n and _ident_part(text[j]):
                j += 1
            out.append(Token(IDENT, text[i:j], i, j))
            i = j
        elif ch == "'":
            j, parts = i + 1, []
            while True:
                k = text.find("'", j)
                if k < 0:
                    raise ExprError(text, i, "Не закрыта строка в одинарных кавычках",
                                    "Закройте строку кавычкой «'». Кавычка внутри строки удваивается: 'it''s'.",
                                    "unclosed_string")
                parts.append(text[j:k])
                if k + 1 < n and text[k + 1] == "'":
                    parts.append("'")
                    j = k + 2
                    continue
                j = k + 1
                break
            out.append(Token(STR, "".join(parts), i, j))
            i = j
        elif ch == "[":
            k = text.find("]", i + 1)
            if k < 0:
                raise ExprError(text, i, "Не закрыта квадратная скобка в имени поля",
                                "Имя поля с пробелами пишется целиком: [Имя поля].", "unclosed_bracket")
            name = text[i + 1 : k]
            if not name.strip():
                raise ExprError(text, i, "Пустое имя поля в квадратных скобках",
                                "Впишите имя поля: [Имя поля].", "syntax")
            out.append(Token(FIELD, name, i, k + 1))
            i = k + 1
        elif ch == "(":
            out.append(Token(LPAREN, ch, i, i + 1))
            i += 1
        elif ch == ")":
            out.append(Token(RPAREN, ch, i, i + 1))
            i += 1
        elif ch == ",":
            out.append(Token(COMMA, ch, i, i + 1))
            i += 1
        elif ch == "−":  # U+2212, the minus sign of the ТЗ
            out.append(Token(OP, "-", i, i + 1))
            i += 1
        else:
            two = text[i : i + 2]
            if two == "==":
                raise ExprError(text, i, "Двойной знак «==» не поддерживается",
                                "Равенство пишется одним знаком: AU = 1.", "bad_char")
            if two == "!=":
                raise ExprError(text, i, "Знак «!=» не поддерживается",
                                "Неравенство пишется «<>»: AU <> 1.", "bad_char")
            if two == "**":
                raise ExprError(text, i, "Знак «**» не поддерживается", "Степень пишется знаком ^: AU ^ 2.", "bad_char")
            for op in _OPERATORS:
                if text.startswith(op, i):
                    out.append(Token(OP, op, i, i + len(op)))
                    i += len(op)
                    break
            else:
                raise ExprError(text, i, "Недопустимый символ", _BAD_CHAR_HINTS.get(ch, _VALID), "bad_char")
    out.append(Token(END, "", n, n))
    return out
