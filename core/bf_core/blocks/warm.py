"""Warm-up of the kernels of the block models (ADR 0023; the step `blocks` of `ops/warm.py`).

Each job module of the stage adds its `warm()` to `WARMS` when it lands (the calculator now; fill, regularization…)."""

from __future__ import annotations

import time
from collections.abc import Callable


def _calc() -> dict[str, float]:
    from bf_core.blocks.calc_job import warm

    return warm()


#: name → the warm-up of a module (a function without arguments; the seconds it took, by name)
WARMS: dict[str, Callable[[], dict[str, float]]] = {"calc": _calc}


def warm() -> dict[str, float]:
    """Compiles the kernels of the block models one by one; a module that fails does not stop the others."""
    took: dict[str, float] = {}
    for name, fn in WARMS.items():
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles the kernel itself then
            continue
        took[name] = time.perf_counter() - t0
    return took
