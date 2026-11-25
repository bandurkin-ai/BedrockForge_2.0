"""The memory of the caches of the estimation (18.27б): the compiled rows of the inspector, of the preview and the
decoded columns of the inspector's row groups share ONE budget in bytes, and a project that is closed (or another one
that is opened) empties them all.

    CACHES_MB                       the budget of all the caches together (samples, tree, blocks, columns)
    row_bytes(*objs)                what a compiled row and its blocks hold in memory (arrays, trees)
    lru = ByteLru("name", keep=4)   an LRU by count and by bytes; `get`, `put(key, value, nbytes)`, `clear`
    forget_all()                    empties every cache of the estimation that is loaded (the hook of a closed project)
    install(svc)                    once per project service: `on_close` → `forget_all`

The budget is shared: after a `put` the oldest entry of the biggest cache goes while the sum is above `CACHES_MB`; the
entry just put stays (an estimate bigger than the budget is still kept, alone — a miss is a minute of work)."""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from typing import Any

import numpy as np

CACHES_MB = 512  # all the caches of the estimation together (compiled rows, blocks, decoded columns)
CACHES_BYTES = CACHES_MB << 20
_MAX_DEPTH = 4
_TREE_NODE_BYTES = 40  # a rough cost of one sample in the nodes of a cKDTree besides `data` and `indices`
_MODULES = ("inspect", "inspect_spot", "preview")  # the modules of this package that keep a cache (`forget()`)

_registry: list[ByteLru] = []
_registry_lock = threading.Lock()
_watched: list[Any] = []


def row_bytes(*objs: Any) -> int:
    """The bytes of the arrays and the trees `objs` hold (dataclasses and the objects of `bf_core` are walked a few
    levels deep; a block model is not counted: it is shared with the cache of the models)."""
    seen: set[int] = set()
    return sum(_walk(o, 0, seen) for o in objs)


def _walk(x: Any, depth: int, seen: set[int]) -> int:
    if id(x) in seen or depth > _MAX_DEPTH or x is None or isinstance(x, str | bytes | int | float | bool):
        return 0
    seen.add(id(x))
    if isinstance(x, np.ndarray):
        return int(x.nbytes) if x.base is None else 0  # a view is its owner's bytes
    tree = _tree_bytes(x)
    if tree is not None:
        return tree
    if isinstance(x, dict):
        return sum(_walk(v, depth + 1, seen) for v in x.values())
    if isinstance(x, list | tuple):
        return sum(_walk(v, depth + 1, seen) for v in x)
    cls = type(x)
    if cls.__module__.endswith(".blocks.model"):
        return 0
    if is_dataclass(x) and not isinstance(x, type):
        return sum(_walk(getattr(x, f.name, None), depth + 1, seen) for f in fields(x))
    if cls.__module__.startswith("bf_core") and hasattr(x, "__dict__"):
        return sum(_walk(v, depth + 1, seen) for v in vars(x).values())
    return 0


def _tree_bytes(x: Any) -> int | None:
    if type(x).__name__ != "cKDTree":
        return None
    n = int(getattr(x, "n", 0))
    return int(x.data.nbytes + x.indices.nbytes + n * _TREE_NODE_BYTES)


class ByteLru:
    """An LRU of values with a size in bytes: at most `keep` entries, and the sum of all the registered caches within
    `CACHES_BYTES`. Thread-safe; the build of a value is the caller's business (outside the lock)."""

    def __init__(self, name: str, keep: int) -> None:
        self.name = name
        self.keep = keep
        self._items: OrderedDict[Any, tuple[Any, int]] = OrderedDict()
        self._lock = threading.Lock()
        with _registry_lock:
            _registry.append(self)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def bytes(self) -> int:
        with self._lock:
            return sum(n for _, n in self._items.values())

    def get(self, key: Any) -> Any | None:
        with self._lock:
            got = self._items.get(key)
            if got is None:
                return None
            self._items.move_to_end(key)
            return got[0]

    def put(self, key: Any, value: Any, nbytes: int) -> None:
        with self._lock:
            self._items[key] = (value, int(nbytes))
            self._items.move_to_end(key)
            while len(self._items) > self.keep:
                self._items.popitem(last=False)
        enforce(keep_last=self)

    def evict_oldest(self) -> bool:
        """The oldest entry goes unless it is the only one; False — nothing to evict."""
        with self._lock:
            if len(self._items) <= 1:
                return False
            self._items.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class _Counted:
    """A cache that keeps its own bytes (the decoded columns): joins the budget through two functions."""

    def __init__(self, name: str, used: Callable[[], int], evict: Callable[[], bool]) -> None:
        self.name, self._used, self.evict_oldest = name, used, evict

    @property
    def bytes(self) -> int:
        return self._used()


_counted: list[_Counted] = []


def register_counted(name: str, used: Callable[[], int], evict: Callable[[], bool]) -> None:
    """A cache with its own LRU (the columns of `inspect_spot`): `used()` bytes, `evict()` drops its oldest, False if it
    could not. Registered once per name."""
    with _registry_lock:
        if all(c.name != name for c in _counted):
            _counted.append(_Counted(name, used, evict))


def _all() -> list[ByteLru | _Counted]:
    with _registry_lock:
        return [*_registry, *_counted]


def total_bytes() -> int:
    return sum(c.bytes for c in _all())


def enforce(keep_last: ByteLru | None = None) -> None:
    """Evicts the oldest entries of the biggest caches while the sum is above the budget (a cache keeps at least one
    entry; the cache that was just written to is evicted last)."""
    for _ in range(64):
        sizes = [(c.bytes, c) for c in _all()]
        if sum(n for n, _ in sizes) <= CACHES_BYTES:
            return
        order = sorted(sizes, key=lambda t: (t[1] is keep_last, -t[0]))
        if not any(c.evict_oldest() for _n, c in order):  # the first that can give up an entry does; none — stop
            return


def forget_all() -> None:
    """Empties every cache of the estimation that is loaded: a module not imported has nothing to empty (and is not
    imported by a close — numba stays out of it)."""
    pkg = __name__.rsplit(".", 1)[0]
    for name in _MODULES:
        mod = sys.modules.get(f"{pkg}.{name}")
        fn = getattr(mod, "forget", None)
        if callable(fn):
            fn()


def install(svc: Any) -> None:
    """Once per project service: a closed project empties the caches (their keys hold its uid; a project that is
    opened again would otherwise find rows of its older self, and the samples of the closed one stay in memory)."""
    if any(s is svc for s in _watched):
        return

    def closed(_project: Any) -> bool:
        forget_all()
        return False  # nothing to keep the project for

    svc.on_close.append(closed)
    _watched.append(svc)
