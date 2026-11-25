"""The warm-up of the estimation kernels (Э18, 18.17; the step `estimate` of `ops/warm.py`; lesson 17.39б).

`warm()` compiles (or loads from NUMBA_CACHE_DIR) every kernel of the estimate — covariances, kriging, the search, the
methods, IK — as the job `estimate.run` (18.12) gives them its arrays: the columns of the samples and the centres of a
portion come from a cache of the project and are READ-ONLY (a read-only array is another signature for numba and made
the first request ×4–5 slower in 17.39б), the points of a block (`BlockCov.points`) are read-only, the arrays the
kernel makes itself are writable. So both variants of every input are run. The parts go one by one; a part that fails
does not stop the others (the job compiles what is missing itself).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np


def _frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def _cov() -> None:
    from .cov import warm_kernels

    warm_kernels()


def _krige() -> None:
    from .krige import warm_krige

    warm_krige()


def _search() -> None:
    from .search import warm_search

    warm_search()


def _methods() -> None:
    from .methods import warm_methods

    warm_methods()


def _indicator() -> None:
    from .indicator import warm_indicator

    warm_indicator()


def _kernel() -> None:
    """Every method, a point and a block, writable and read-only samples (`kernel.warm_kernel`)."""
    from .kernel_warm import warm_kernel

    warm_kernel()


#: part → its warm-up (a function without arguments); the order matters little: a kernel compiled by one part is a
#: call of the compiled code in the next
PARTS: dict[str, Callable[[], Any]] = {
    "cov": _cov,
    "krige": _krige,
    "search": _search,
    "methods": _methods,
    "indicator": _indicator,
    "kernel": _kernel,
}


def warm() -> dict[str, float]:
    """The parts one by one → the seconds of each by name; a part that fails is left out and does not stop the rest."""
    took: dict[str, float] = {}
    for name, fn in PARTS.items():
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles the kernel itself then
            continue
        took[name] = time.perf_counter() - t0
    return took
