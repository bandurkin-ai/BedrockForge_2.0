"""The warm-up of the kernels of composites (ADR 0018 §5, решение 26): every kernel of composites by length and of the
summary «до/после» once on three holes, with the dtypes of the job and of the preview — compiled into
NUMBA_CACHE_DIR, or loaded from it, before the first window asks. `ops/warm.py` calls `warm()` as its step
`composites` (in the workers and in the API process); the kernels of Д-26 — `ore_set.warm`; TODO(12.4–12.6):
domains, benches, merging and coordinates add their kernels here.
"""

from __future__ import annotations


def warm() -> None:
    import numpy as np

    from . import intervals as iv
    from .length import LengthParams, composite_by_length, summary

    holes = np.array([0, 0, 0, 1, 1, 2], dtype=np.int64)
    frm = np.array([0.0, 1.0, 3.0, 0.0, 2.5, 5.0])
    to = np.array([1.0, 3.0, 4.0, 2.5, 4.0, 7.0])
    au = np.array([1.0, 2.0, np.nan, 0.5, 4.0, 3.0])
    rho = np.array([2.5, 3.0, 2.7, np.nan, 2.6, 2.8])
    codes = np.array([0, 1, -1, 0, 0, 1], dtype=np.int32)
    lines = iv.build_lines(holes, frm, to)
    values = iv.stack_fields(lines, [au, au * 2.0])
    for remainder in ("attach", "equal"):
        p = LengthParams.of({"length": 2.0, "remainder": remainder})
        c = composite_by_length(lines, values, p, density=lines.take(rho), by_density=True,
                                texts=[lines.take(codes)], ends=np.array([5.0, np.nan, 8.0]))  # fmt: skip
        summary(c.lines.to - c.lines.frm, c.values.values, ["AU", "AU2"])
    summary(lines.lengths(), values, ["AU", "AU2"])

    from .ore_set import warm as warm_ore

    warm_ore()  # Д-26 (12.10–12.14): the five modes, the events, the nested rows, the means of other fields
