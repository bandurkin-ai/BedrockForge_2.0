"""The field calculator (ТЗ 8.7.4, ADR 0023 §7): own safe parser of expressions → tree → (17.6) evaluation.

Public: `parse(text) -> Expr`; `Expr.fields()`, `Expr.functions()`, `Expr.to_text()`, `Expr.walk()`, `Expr.depth()`;
`ExprError` (`to_body()` → {code, message, hint, pos, line, column, char, reason}), `ExprLimitError`.
No numpy here: the package is cheap to import.
"""

from __future__ import annotations

from .errors import ExprError, ExprLimitError
from .nodes import (
    FUNCTIONS,
    MAX_DEPTH,
    MAX_LENGTH,
    BinOp,
    BoolOp,
    Call,
    Compare,
    Expr,
    Field,
    In,
    Neg,
    Not,
    Num,
    Str,
)
from .parser import parse

__all__ = [
    "FUNCTIONS",
    "MAX_DEPTH",
    "MAX_LENGTH",
    "BinOp",
    "BoolOp",
    "Call",
    "Compare",
    "Expr",
    "ExprError",
    "ExprLimitError",
    "Field",
    "In",
    "Neg",
    "Not",
    "Num",
    "Str",
    "parse",
]
