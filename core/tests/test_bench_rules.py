"""ТЗ 7.33 acceptance: a 30 % slowdown of >= 0.25 s fails the comparison; noise does not."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.run import compare  # noqa: E402


def rep(seconds: float) -> dict:  # type: ignore[type-arg]
    return {"scenarios": {"s": {"seconds": seconds}}}


def test_slowdown_30_percent_fails() -> None:
    assert compare(rep(1.0), rep(1.3))


def test_small_absolute_slowdown_passes() -> None:
    assert not compare(rep(0.1), rep(0.2))  # +100 % but only 0.1 s


def test_small_relative_slowdown_passes() -> None:
    assert not compare(rep(10.0), rep(11.5))  # +1.5 s but only 15 %
