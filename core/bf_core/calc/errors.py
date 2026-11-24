"""Errors of the field calculator (ТЗ 8.7.4): {code, message, hint} in Russian with the place in the text.

The parser never raises anything else for a wrong text. `pos` is a 0-based offset in code points of the
Python string (the text as the client sent it); `line` and `column` are 1-based; `char` is the character at
`pos` (empty at the end of the text). The API layer turns it into 422 `expr_syntax` with `to_body()`.
"""

from __future__ import annotations

CODE = "expr_syntax"


def show_char(char: str) -> str:
    """A character for a message: whitespace and control characters are spelled out."""
    if char == "":
        return ""
    if char == "\n":
        return "\\n"
    if char == "\r":
        return "\\r"
    if char == "\t":
        return "\\t"
    if char.isspace() or not char.isprintable():
        return f"U+{ord(char):04X}"
    return char


class ExprError(ValueError):
    """A wrong expression text. Nothing of it has been executed."""

    code = CODE

    def __init__(self, text: str, pos: int, detail: str, hint: str = "", reason: str = "syntax") -> None:
        pos = max(0, min(pos, len(text)))
        before = text[:pos]
        self.pos = pos
        self.line = before.count("\n") + 1
        self.column = pos - (before.rfind("\n") + 1) + 1
        self.char = text[pos] if pos < len(text) else ""
        self.detail = detail
        self.hint = hint
        #: What kind of failure, for tests and the interface: syntax, empty, too_long, too_deep, unknown_function,
        #: arity, unclosed_paren, unclosed_string, unclosed_bracket, bad_number, bad_char, chained_compare.
        self.reason = reason
        where = (
            f"строка {self.line}, столбец {self.column}, символ «{show_char(self.char)}»"
            if self.char
            else f"строка {self.line}, столбец {self.column}, конец выражения"
        )
        self.message = f"{detail} ({where})."
        super().__init__(self.message)

    def to_body(self) -> dict[str, object]:
        """The body of the 422 answer: {code, message, hint} plus the machine place."""
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "pos": self.pos,
            "line": self.line,
            "column": self.column,
            "char": self.char,
            "reason": self.reason,
        }


class ExprLimitError(ExprError):
    """The text is longer than 4000 characters or nested deeper than 64 levels (same `code`)."""
