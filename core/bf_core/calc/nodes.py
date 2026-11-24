"""The expression tree of the calculator (ТЗ 8.7.4) and its text form.

Every node is an immutable dataclass; `pos` (offset of the node's first character in the source text) is not part
of equality, so «text → tree → text → tree» compares equal. Evaluation (17.6) walks the tree with `children()`,
`walk()` and `kind`; it never needs the source text.

    Num(value)  Str(value)  Field(name)
    Neg(operand)  Not(operand)
    BinOp(op, left, right)       op: + - * / ^
    Compare(op, left, right)     op: = <> < <= > >=   (a chain `a < b < c` is a syntax error)
    BoolOp(op, operands)         op: AND OR, n-ary: `a AND (b AND c)` is flattened at once
    In(operand, values)          values: strings and numbers
    Call(name, args)             name: upper case, one of FUNCTIONS; the arity is checked by the parser

Depth: a leaf (Num, Str, Field) is 0, any other node is 1 + the deepest child; the limit is MAX_DEPTH.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

MAX_LENGTH = 4000
MAX_DEPTH = 64

#: name → (fewest arguments, most arguments or None for any number)
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "ABS": (1, 1),
    "SQRT": (1, 1),
    "LN": (1, 1),
    "LOG10": (1, 1),
    "EXP": (1, 1),
    "MIN": (2, None),
    "MAX": (2, None),
    "ROUND": (2, 2),
    "FLOOR": (1, 1),
    "CEIL": (1, 1),
    "CLIP": (3, 3),
    "IF": (3, 3),
    "ISNULL": (1, 1),
    "COALESCE": (2, None),
}
KEYWORDS = frozenset({"AND", "OR", "NOT", "IN"})
ARITH_OPS = ("+", "-", "*", "/", "^")
COMPARE_OPS = ("=", "<>", "<", "<=", ">", ">=")

# Precedence, low → high; a child with a lower precedence than its place needs parentheses.
_OR, _AND, _NOT, _CMP, _ADD, _MUL, _UNARY, _POW, _ATOM = range(1, 10)
_BINARY_PREC = {"+": (_ADD, _ADD, _MUL), "-": (_ADD, _ADD, _MUL), "*": (_MUL, _MUL, _UNARY),
                "/": (_MUL, _MUL, _UNARY), "^": (_POW, _ATOM, _UNARY)}  # op → (own, left needs, right needs)


def ascii_upper(word: str) -> str:
    """Keywords and function names are matched in ASCII case only (`ın` is not IN)."""
    return word.upper() if word.isascii() else word


class Expr:
    """Base of the nodes; the subclasses are dataclasses with `pos` as their last field."""

    __slots__ = ()
    kind: ClassVar[str]
    pos: int

    def children(self) -> tuple[Expr, ...]:
        return ()

    def walk(self) -> Iterator[Expr]:
        """All nodes, parents before children, left to right; iterative (no recursion limit)."""
        stack: list[Expr] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children()))

    def depth(self) -> int:
        """Leaf 0, otherwise 1 + the deepest child; at most MAX_DEPTH in a parsed tree."""
        nodes = list(self.walk())
        depths: dict[int, int] = {}
        for node in reversed(nodes):
            kids = node.children()
            depths[id(node)] = 1 + max(depths[id(k)] for k in kids) if kids else 0
        return depths[id(self)]

    def fields(self) -> tuple[str, ...]:
        """Distinct field names in order of first use (names compare ignoring case, the first spelling is kept)."""
        seen: dict[str, str] = {}
        for node in self.walk():
            if isinstance(node, Field):
                seen.setdefault(node.name.casefold(), node.name)
        return tuple(seen.values())

    def functions(self) -> tuple[str, ...]:
        """Distinct function names used, sorted."""
        return tuple(sorted({n.name for n in self.walk() if isinstance(n, Call)}))

    def to_text(self) -> str:
        """The canonical text: upper-case words, single spaces, the fewest parentheses; parses back to an equal tree."""
        return _emit(self, 0)

    def __str__(self) -> str:
        return self.to_text()


@dataclass(frozen=True, slots=True)
class Num(Expr):
    value: float
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "num"


@dataclass(frozen=True, slots=True)
class Str(Expr):
    value: str
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "str"


@dataclass(frozen=True, slots=True)
class Field(Expr):
    name: str
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "field"


@dataclass(frozen=True, slots=True)
class Neg(Expr):
    operand: Expr
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "neg"

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)


@dataclass(frozen=True, slots=True)
class Not(Expr):
    operand: Expr
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "not"

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)


@dataclass(frozen=True, slots=True)
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "binop"

    def children(self) -> tuple[Expr, ...]:
        return (self.left, self.right)


@dataclass(frozen=True, slots=True)
class Compare(Expr):
    op: str
    left: Expr
    right: Expr
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "compare"

    def children(self) -> tuple[Expr, ...]:
        return (self.left, self.right)


@dataclass(frozen=True, slots=True)
class BoolOp(Expr):
    op: str
    operands: tuple[Expr, ...]
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "boolop"

    def children(self) -> tuple[Expr, ...]:
        return self.operands

    @staticmethod
    def of(op: str, operands: list[Expr] | tuple[Expr, ...], pos: int = 0) -> BoolOp:
        """AND / OR of two or more operands; a nested BoolOp with the same op is merged (both are associative)."""
        if op not in ("AND", "OR") or len(operands) < 2:
            raise ValueError("BoolOp: AND или OR и не меньше двух операндов.")
        flat: list[Expr] = []
        for item in operands:
            if isinstance(item, BoolOp) and item.op == op:
                flat.extend(item.operands)
            else:
                flat.append(item)
        return BoolOp(op, tuple(flat), pos)


@dataclass(frozen=True, slots=True)
class In(Expr):
    operand: Expr
    values: tuple[str | float, ...]
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "in"

    def children(self) -> tuple[Expr, ...]:
        return (self.operand,)


@dataclass(frozen=True, slots=True)
class Call(Expr):
    name: str
    args: tuple[Expr, ...]
    pos: int = field(default=0, compare=False, repr=False)
    kind: ClassVar[str] = "call"

    def children(self) -> tuple[Expr, ...]:
        return self.args


# ---------------------------------------------------------------------------------------------- text form


def format_number(value: float) -> str:
    """A number as the parser reads it back exactly (shortest round-trip form)."""
    if not math.isfinite(value):
        raise ValueError("Число в выражении должно быть конечным.")
    if value == int(value) and abs(value) < 1e15:
        return str(int(abs(value)))
    return repr(abs(value))


def format_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_plain_name(name: str) -> bool:
    if not name or not (name[0].isalpha() or name[0] == "_"):
        return False
    if not all(c.isalpha() or c == "_" or c.isdecimal() for c in name):
        return False
    word = ascii_upper(name)
    return word not in KEYWORDS and word not in FUNCTIONS


def format_field(name: str) -> str:
    if _is_plain_name(name):
        return name
    if "]" in name or not name.strip():
        raise ValueError("Имя поля нельзя записать в выражении: нет «]» и пустых имён.")
    return f"[{name}]"


def _render(node: Expr) -> tuple[str, int]:
    """The text of a node and the precedence of its outermost operator."""
    if isinstance(node, Num):
        if node.value < 0 or (node.value == 0 and math.copysign(1.0, node.value) < 0):
            return "-" + format_number(node.value), _UNARY
        return format_number(node.value), _ATOM
    if isinstance(node, Str):
        return format_string(node.value), _ATOM
    if isinstance(node, Field):
        return format_field(node.name), _ATOM
    if isinstance(node, Neg):
        return "-" + _emit(node.operand, _UNARY), _UNARY
    if isinstance(node, Not):
        return "NOT " + _emit(node.operand, _NOT), _NOT
    if isinstance(node, BinOp):
        own, left_need, right_need = _BINARY_PREC[node.op]
        return f"{_emit(node.left, left_need)} {node.op} {_emit(node.right, right_need)}", own
    if isinstance(node, Compare):
        return f"{_emit(node.left, _ADD)} {node.op} {_emit(node.right, _ADD)}", _CMP
    if isinstance(node, BoolOp):
        own = _OR if node.op == "OR" else _AND
        return f" {node.op} ".join(_emit(o, own + 1) for o in node.operands), own
    if isinstance(node, In):
        values = ", ".join(format_string(v) if isinstance(v, str) else _signed(v) for v in node.values)
        return f"{_emit(node.operand, _ADD)} IN ({values})", _CMP
    if isinstance(node, Call):
        return f"{node.name}({', '.join(_emit(a, 0) for a in node.args)})", _ATOM
    raise TypeError(f"Неизвестный узел: {type(node).__name__}")


def _signed(value: float) -> str:
    text = format_number(value)
    return "-" + text if value < 0 or (value == 0 and math.copysign(1.0, value) < 0) else text


def _emit(node: Expr, need: int) -> str:
    text, prec = _render(node)
    return f"({text})" if prec < need else text
