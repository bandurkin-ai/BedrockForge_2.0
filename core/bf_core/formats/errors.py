"""Errors of file readers and writers: `{code, message, hint}` with Russian texts for the user."""

from __future__ import annotations


class FormatError(Exception):
    """A file that cannot be read or written as asked.

    Codes: empty_file, not_micromine, broken, truncated, unknown_field_type, too_large, unknown_encoding,
    unknown_delimiter, header_row, no_sheet, encrypted, unsupported, unrepresentable.
    """

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}
