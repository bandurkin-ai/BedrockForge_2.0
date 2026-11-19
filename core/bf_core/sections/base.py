"""Constants and the error of a series of sections without numpy: the API process imports them at its start (the
heavy libraries load only after BF-READY — tests/test_process.py); `geometry` and `polyline` re-export them."""

from __future__ import annotations

import math
from typing import Any

TYPES = ("vertical", "plan", "inclined", "polyline")
MAX_K = 5_000  # sections in a series (ТЗ 8.1.2)
MIN_LEN, MAX_LEN = 0.01, 10_000.0  # the step and the windows, m
MIN_SPAN = 1e-6  # P2 ≠ P1, m
DEFAULT_WINDOW = 25.0
MAX_POLYLINE = 1_000  # vertices of a polyline (ADR 0016 §1)
DEFAULT_CORRIDOR = 25.0
MAX_ID = 10**12  # ids of layers and wireframes: 0 < id < MAX_ID (as `viewstate/keys._id`), never beyond int64 to SQL
MAX_KEY_DIGITS = 6  # the key of an own window: k from 0, ASCII digits


def as_float(v: Any) -> float | None:
    """A finite float of a JSON number, None for anything else: a bool, a string, NaN or ±inf, an integer that
    float64 cannot hold (10**400 → OverflowError in float())."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def is_id(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < MAX_ID


def section_key(key: Any) -> int | None:
    """k of a key of `overrides` («12» or 12), None — not one: «²» and «١» pass `str.isdigit`, 5 000 digits — `int`
    refuses them, so ASCII decimal digits only, at most `MAX_KEY_DIGITS`."""
    if isinstance(key, int) and not isinstance(key, bool):
        return key if 0 <= key < 10**MAX_KEY_DIGITS else None
    if isinstance(key, str) and key.isascii() and key.isdecimal() and len(key) <= MAX_KEY_DIGITS:
        return int(key)
    return None


class SeriesError(ValueError):
    """422 `bad_series`: what is wrong (Russian) and what to do; `field` — the parameter, when known."""

    def __init__(self, message: str, hint: str = "", *, code: str = "bad_series", field: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.hint, self.field = code, message, hint, field
