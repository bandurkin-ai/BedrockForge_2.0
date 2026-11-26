"""The warm-up of the numba kernels of the resource report (Э20, 20.11; the step `resources` of `ops/warm.py`; ТЗ 8.10:
compilation ≤ 30 s; ADR 0027 decision 28, the lesson 17.39б).

`warm()` calls the kernels of `kernels.py` with the types the pass of `compute.run` gives them: ONE specialization
each — `compute._w` makes the arrays of a portion writable, C-contiguous and f8 (the cut-off, the density and the
share f; the cell number P is u64, the key columns i32), whatever the model holds (read-only columns of a row group or
joined ones, f4 or f8 fields). The scalars are Python floats and ints, the cell count of a layer `nxy` is `np.uint64`.
A specialization missing from the cache is a compilation of seconds in the first report of the user; the test
`test_resources_warm.py` runs reports on models of several layouts after `warm()` and counts the signatures.

The step also imports what a report needs and the process of the API does not (`compute`, `control`, `result`,
`store_data` with pyarrow, `view`, `forms`), so the first request does not pay for the imports. A part that fails does
not stop the others (the job compiles what is missing itself). numba is not imported here: every import is inside a
function, so a module that imports `warm` does not pay for it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

PARTS = ("imports", "prepare", "sums", "bench")


def _arrays() -> dict[str, Any]:
    import numpy as np

    one = np.zeros(1)
    z = np.zeros((1, 5))
    n = np.zeros((1, 2), dtype=np.int64)
    i32 = np.zeros(1, np.int32)
    return {
        "one": one, "i32": i32, "kc": (i32, i32, i32), "kmul": np.zeros(3, np.int64), "radix": np.ones(3, np.int64),
        "acc": (z.copy(), z.copy(), n.copy(), z.copy(), z.copy(), n.copy(), 1), "g": np.zeros((1, 1)),
    }  # fmt: skip


def _imports() -> None:
    from . import compute, control, forms, result, store_data, view  # noqa: F401


def _prepare() -> None:
    """`prepare`: the density, the cut-off and the volume as arrays or as empty ones (a number, no cut-off, one volume)
    are the same type — a writable f8 vector."""
    import numpy as np

    from . import kernels as rk

    a = _arrays()
    one = a["one"]
    rk.prepare(one.copy(), one.copy(), 1.0, one.copy(), 2.5, one.copy(), one.copy(), one.copy(), a["i32"].copy(),
               a["i32"].copy(), one.copy(), one.copy(), np.zeros(3, np.int64), np.zeros(2))  # fmt: skip


def _sums() -> None:
    from . import kernels as rk

    a = _arrays()
    rk.accumulate(a["i32"], a["i32"], a["kc"], a["kmul"], a["radix"], True, a["one"], a["one"], a["g"],
                  *a["acc"])  # type: ignore[misc]  # fmt: skip


def _bench() -> None:
    """`accumulate_bench`: the regular layers (`zlo` empty) and the extent of each row (arrays) are one type."""
    import numpy as np

    from . import kernels as rk

    a = _arrays()
    rk.accumulate_bench(a["i32"], a["i32"], a["kc"], a["kmul"], a["radix"], 1, 1, 0.0, 1.0, np.empty(0), np.empty(0),
                        np.zeros(1, np.uint64), np.uint64(1), 0.0, 1.0, a["one"], a["one"], a["g"],
                        *a["acc"])  # type: ignore[misc]  # fmt: skip


WARMS: dict[str, Callable[[], None]] = {"imports": _imports, "prepare": _prepare, "sums": _sums, "bench": _bench}


def warm() -> dict[str, float]:
    """Compiles the kernels of the report (or loads them from NUMBA_CACHE_DIR): seconds of each part."""
    took: dict[str, float] = {}
    for name, fn in WARMS.items():
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles the kernel itself then
            continue
        took[name] = time.perf_counter() - t0
    return took
