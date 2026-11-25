"""The field calculator (ТЗ 8.7.4, ADR 0023 §7): own safe parser of expressions → tree → evaluation over columns.

Parser: `parse(text) -> Expr`; `Expr.fields()`, `Expr.functions()`, `Expr.to_text()`, `Expr.walk()`, `Expr.depth()`;
`ExprError` (`to_body()` → {code, message, hint, pos, line, column, char, reason}), `ExprLimitError`.
Evaluation (17.6): `compile(text, schema) -> Program` (`fields_used`, `out_kind`, `labels`, `eval`, `eval_chunk`,
`iter_eval`), `FieldInfo`, `ExprFieldError`, `ExprTypeError`, `ExprDataError`. The parser needs no numpy; the
evaluation names are loaded on first use, so `import bf_core.calc` stays cheap.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from .evaluate import CHUNK, Program, compile
    from .runtime import ExprDataError, ExprFieldError, ExprTypeError, FieldInfo

_LAZY = {
    "CHUNK": "evaluate",
    "Program": "evaluate",
    "compile": "evaluate",
    "ExprDataError": "runtime",
    "ExprFieldError": "runtime",
    "ExprTypeError": "runtime",
    "FieldInfo": "runtime",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __name__), name)


__all__ = [
    "CHUNK",
    "FUNCTIONS",
    "MAX_DEPTH",
    "MAX_LENGTH",
    "BinOp",
    "BoolOp",
    "Call",
    "Compare",
    "Expr",
    "ExprDataError",
    "ExprError",
    "ExprFieldError",
    "ExprLimitError",
    "ExprTypeError",
    "Field",
    "FieldInfo",
    "In",
    "Neg",
    "Not",
    "Num",
    "Program",
    "Str",
    "compile",
    "parse",
]
