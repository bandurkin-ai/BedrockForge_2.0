"""Warm-up of the kernels of the block models (ADR 0023; the step `blocks` of `ops/warm.py`).

Each job module of the stage adds its `warm()` to `WARMS` when it lands (the calculator now; fill, regularization…)."""

from __future__ import annotations

import time
from collections.abc import Callable


def _calc() -> dict[str, float]:
    from bf_core.blocks.calc_job import warm

    return warm()


def _report() -> dict[str, float]:
    from bf_core.blocks.report import warm

    return warm()


def _slice() -> dict[str, float]:
    from bf_core.blocks.slice import warm

    warm()  # returns nothing: the time of the step is counted by `warm()` below
    return {}


def _fill() -> dict[str, float]:
    from bf_core.blocks.fill.warm import warm

    return warm()


def _kernels() -> dict[str, float]:
    from bf_core.blocks.warm_kernels import warm

    return warm()


def _view() -> dict[str, float]:
    from bf_core.blocks.viz_data import warm

    warm()  # the shell, the merge and the LOD on 8 blocks
    return {}


def _regularize() -> dict[str, float]:
    from bf_core.blocks.jobs import warm

    return warm()


def _list() -> dict[str, float]:
    from bf_core.blocks.list_layout import warm

    return warm()


#: name → the warm-up of a module (a function without arguments; the seconds it took, by name)
WARMS: dict[str, Callable[[], dict[str, float]]] = {
    "calc": _calc,
    "report": _report,
    "slice": _slice,
    "fill": _fill,  # the order matters: slice and report go first, fill and kernels bring pandas and pyarrow
    "kernels": _kernels,
    "view": _view,
    "regularize": _regularize,
    "list": _list,  # a list of blocks (ADR 0030): the order, the overlaps, the faces
}


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
