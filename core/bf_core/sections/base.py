"""Constants and the error of a series of sections without numpy: the API process imports them at its start (the
heavy libraries load only after BF-READY — tests/test_process.py); `geometry` and `polyline` re-export them."""

from __future__ import annotations

TYPES = ("vertical", "plan", "inclined", "polyline")
MAX_K = 5_000  # sections in a series (ТЗ 8.1.2)
MIN_LEN, MAX_LEN = 0.01, 10_000.0  # the step and the windows, m
MIN_SPAN = 1e-6  # P2 ≠ P1, m
DEFAULT_WINDOW = 25.0
MAX_POLYLINE = 1_000  # vertices of a polyline (ADR 0016 §1)
DEFAULT_CORRIDOR = 25.0


class SeriesError(ValueError):
    """422 `bad_series`: what is wrong (Russian) and what to do; `field` — the parameter, when known."""

    def __init__(self, message: str, hint: str = "", *, code: str = "bad_series", field: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.hint, self.field = code, message, hint, field
