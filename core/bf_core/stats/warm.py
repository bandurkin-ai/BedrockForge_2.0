"""The warm-up of the kernels of the charts and the statistics of Э13 (ADR 0019 «Замеры», решение 18): every
numba kernel of `stats` once on a few rows — compiled into NUMBA_CACHE_DIR, or loaded from it, before the first
window asks. `ops/warm.py` calls `warm()` as its step `stats` (ADR 0019: in the workers and in the process of
the API, where the charts are computed).

    warm() -> {module: seconds}
        `MODULES` in turn, each by its own `warm()`: the charts (track Г — `select`, `describe`, `histogram`,
        `qq`, `dendrogram` with `correlation`, `scatter`, `boxplot`, `probplot`, `domain_stats`, `ternary`,
        `contact`), swath (track С — `swath`, `swath_blocks`); then `columns()`
    columns()
        the same kernels on read-only columns, as the routes of `stats` give them: the cached columns of
        `stats.source` are read-only, and numba compiles a kernel anew for a read-only array — without this the
        first chart of a whole table would still compile for 0.2 … 0.8 s per kernel after the warm-up. Covered:
        every chart and the swath of samples. The swath of a block model and of samples «внутри блоков» —
        by `warm()` of `swath` and `swath_blocks` themselves, on read-only arrays as the routes give them (the
        columns of `stats.blocks_cache`, the middles of `stats.source`; the empty columns of a series without a
        density or a selection, the tail of the groups and `selected_idx` are read-only there too — one
        specialization a type of `idx`, as many as the writable ones before)
    frozen(a) -> a read-only copy of the array

A module that is not there (a branch without it) is skipped; one that fails is logged and the next goes on — a
warm-up never stops a worker, the chart compiles its kernel itself then. The kernels of «Обработка проб» the
charts share (`weighted`, `composites.intervals`, `declus3d`) belong to the step `composites`; here they are
loaded from the cache."""

from __future__ import annotations

import importlib
import logging
import time
from typing import Any

MODULES = (
    "select", "describe", "histogram", "qq", "dendrogram", "scatter", "boxplot", "probplot", "domain_stats",
    "ternary", "contact", "swath", "swath_blocks",
)  # fmt: skip


def frozen(a: Any) -> Any:
    """A read-only copy of the array — what a cached column of `stats.source` is."""
    import numpy as np

    out = np.array(a)
    out.setflags(write=False)
    return out


def columns() -> None:
    """The kernels on read-only columns (see the module docstring), called as the routes of `stats` call them:
    the values, the lengths, the weight field, the codes of a domain, the ids of the rows and the middles of the
    samples are read-only; what the routes make of them (the weights «длина × поле», the pairs of a scatter) is
    not."""
    import numpy as np

    from . import contact, select
    from .boxplot import box_chart
    from .correlation import METHODS, CorrSpec
    from .dendrogram import dendrogram_chart
    from .domain_stats import domain_stats
    from .scatter import scatter_chart
    from .ternary import shares

    hole = np.array([0, 0, 0, 0, 1, 1], dtype=np.int32)  # `Columns.hole_of()`
    frm = frozen([0.0, 1.0, 2.0, 3.5, 0.0, 2.0])
    to = frozen([1.0, 2.0, 3.0, 4.5, 2.0, 3.0])
    values = frozen([1.0, np.nan, 3.0, 2.0, 5.0, 0.5])
    other = frozen([2.0, 1.5, 0.5, np.nan, 4.0, 3.0])
    third = frozen([0.3, 0.2, 0.9, 0.4, 0.1, 0.6])
    field = frozen([2.5, 2.7, np.nan, 3.1, 2.6, 2.4])
    length = frozen(to - frm)
    codes, labels = frozen(np.array([0, 0, 1, 1, -1, 0], dtype=np.int32)), ["a", "b"]
    row_ids = frozen(np.arange(6, dtype=np.int64))
    sel = None
    for weight in select.WEIGHTS:
        flt = select.SampleFilter(weight=weight, weight_field="W" if "field" in weight else None)
        sel = select.select_sample(values, flt, length=length, weight_values=field)  # histogram, Q-Q, by domains
        box_chart(sel, codes, labels)
        domain_stats(sel, codes, labels, length=length, decl_weights=field)
        contact.contact_chart(hole, frm, to, codes, labels, "a", "b", values, flt, length=length, weight_values=field)
        w = select.sample_weights(weight, length, field)
        for method in METHODS:
            dendrogram_chart(["A", "B", "C"], [values, other, third], w, CorrSpec(method=method, log=False))
        x, y = np.where(np.isfinite(values), values, np.nan), np.where(np.isfinite(other), other, np.nan)
        for color in (None, field, codes):
            scatter_chart(x, y, w, color=color, row_ids=row_ids)
        scatter_chart(x, y, w, row_ids=row_ids, max_points=2)  # the density grid
    shares(values, other, third)
    _swath_columns(sel)


def _swath_columns(sel: Any) -> None:
    """A swath of the samples `sel` (six rows): their middles (`source.midpoints`) are read-only."""
    import numpy as np

    from . import swath_frame as sf
    from .swath import cuboid, sample_bounds, sample_series

    points = frozen([[0.0, 0.0, 0.0], [3.0, 4.0, 1.0], [np.nan, 0.0, 0.0], [9.0, 9.0, 2.0], [1.0, 2.0, 3.0],
                     [5.0, 5.0, 5.0]])  # fmt: skip
    f = sf.frame(30.0, -10.0, 5.0)
    sample_series(points, sel, f, cuboid(f, samples=[sample_bounds(f, points, sel)], width=2.0))


def warm() -> dict[str, float]:
    """Every module of `MODULES` by its `warm()`, then `columns()`. → seconds a module (a module that is not
    there or that failed is missing). Never raises."""
    log = logging.getLogger("bf_core.stats.warm")
    took: dict[str, float] = {}
    for name in MODULES:
        full = f"{__package__}.{name}"
        t0 = time.perf_counter()
        try:
            importlib.import_module(full).warm()
        except ModuleNotFoundError as e:
            if e.name != full:
                log.exception("warm-up of %s failed", full)
            continue
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the chart compiles its kernel then
            log.exception("warm-up of %s failed", full)
            continue
        took[name] = time.perf_counter() - t0
    t0 = time.perf_counter()
    try:
        columns()
    except Exception:  # noqa: BLE001 - as above
        log.exception("warm-up of the read-only columns failed")
    else:
        took["columns"] = time.perf_counter() - t0
    return took
