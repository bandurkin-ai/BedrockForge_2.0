"""Strips of holes on Python threads for the interval kernels (ADR 0018 §5, ADR 0014).

A kernel that takes `h0, h1` handles the holes h0 ≤ h < h1 only and writes only their part of the outputs; it is
compiled with `nogil=True`, so strips run on Python threads. The strips are fixed — `STRIP_HOLES` holes each,
whatever the number of threads — and nothing is summed across strips inside a thread: the result is the same
bit for bit with 1, 2, 4 and 8 threads (ТЗ 8.0.2). Totals over the whole set are taken afterwards in one thread
(`total`: exact, order-free `math.fsum`).

Variable-size outputs go in two passes: «счёт → смещения → заполнение». The count pass writes the number of
outputs of every hole, `offsets` turns the counts into starts, the fill pass writes each hole from its start.

    run(fn, n, threads=1, tick=None, size=STRIP_HOLES, span=(0.0, 1.0)) -> list   results in strip order
    offsets(counts) -> int64[n + 1]
    default_threads(heavy_workers=1) -> int      min(8, cores // workers of the heavy queue), as `ground.classify`
    total(parts) -> float                        sum of per-strip parts, independent of their order

Cancellation. `tick(fraction)` is called in the calling thread after every strip (a strip of 4096 holes takes
milliseconds); what it raises (`JobCancelled`) stops the run: strips not yet started are dropped, running ones
finish (they only write to arrays the caller is about to discard).
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

STRIP_HOLES = 4096  # holes in a strip; fixed, so the result does not depend on the number of threads
MAX_THREADS = 8

Tick = Callable[[float], None]


def default_threads(heavy_workers: int = 1) -> int:
    """Threads of a job: min(8, cores // workers of the heavy queue), at least 1."""
    return max(1, min(MAX_THREADS, (os.cpu_count() or 1) // max(1, int(heavy_workers))))


def ranges(n: int, size: int = STRIP_HOLES) -> list[tuple[int, int]]:
    """The fixed strips [lo, hi) of n items."""
    return [(lo, min(lo + size, n)) for lo in range(0, int(n), size)]


def run[T](
    fn: Callable[[int, int], T],
    n: int,
    *,
    threads: int = 1,
    tick: Tick | None = None,
    size: int = STRIP_HOLES,
    span: tuple[float, float] = (0.0, 1.0),
) -> list[T]:
    """fn(lo, hi) over the fixed strips of n items; the results come in strip order. `tick` gets the fraction
    done, mapped into `span`, after every strip — in the calling thread, so it may raise to cancel."""
    todo = ranges(n, size)
    a, b = span
    out: list[T] = []

    def done(k: int) -> None:
        if tick is not None:
            tick(a + (b - a) * (k + 1) / len(todo))

    if threads <= 1 or len(todo) < 2:
        for k, (lo, hi) in enumerate(todo):
            out.append(fn(lo, hi))
            done(k)
        return out
    pool = ThreadPoolExecutor(min(int(threads), len(todo)), thread_name_prefix="bf-strips")
    try:
        for k, res in enumerate(pool.map(lambda r: fn(r[0], r[1]), todo)):
            out.append(res)
            done(k)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return out


def offsets(counts: np.ndarray) -> np.ndarray:
    """Starts of the outputs of every item from their counts: int64[n + 1], the last one is the total."""
    starts = np.zeros(len(counts) + 1, dtype=np.int64)
    np.cumsum(counts, out=starts[1:])
    return starts


def total(parts: Iterable[float]) -> float:
    """The sum of per-strip parts: exactly rounded, so it does not depend on how the strips were scheduled."""
    return math.fsum(parts)
