"""Errors of file readers and writers: `{code, message, hint}` with Russian texts for the user."""

from __future__ import annotations

from typing import Any


class FormatError(Exception):
    """A file that cannot be read or written as asked.

    Codes: empty_file, not_micromine, broken, truncated, unknown_field_type, too_large, unknown_encoding,
    unknown_delimiter, header_row, no_sheet, encrypted, unsupported, unrepresentable, blocks_unaligned.

    `extra` — more for the interface (a block file that the grid cannot hold: `count`, `rows`, `by_reason`, `diag`,
    `choices` — ADR 0030); `as_dict` gives it beside the three fields, which it never replaces."""

    def __init__(self, code: str, message: str, hint: str = "", extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint
        self.extra: dict[str, Any] = dict(extra or {})

    def as_dict(self) -> dict[str, Any]:
        return {**self.extra, "code": self.code, "message": self.message, "hint": self.hint}
