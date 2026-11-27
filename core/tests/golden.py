"""Comparison of golden answers (`tests/data/**/*.json`, the fixtures of the windows) with what the core answers now.

Why not `==`. The core is float64 and deterministic on one machine, but not bit for bit on every machine: BLAS and
LAPACK (`scipy.linalg`, `np.linalg`, `@`) pick their kernels by the processor (OpenBLAS `DYNAMIC_ARCH`: SkylakeX with
AVX-512, Haswell, Zen, Nehalem — the runners of GitHub Actions are of all of these) and sum in another order; numpy
dispatches `exp`, `log`, `sin`, `cos` and the sums by the instruction set too (AVX-512 or AVX2); libm (glibc) has
variants for FMA. The last digits (1e−14 and less, in a solved system up to 1e−10) differ, and a text that holds the
number (a CSV, a GSLIB file, a line of a journal) differs with them. The logic does not. So the numbers are compared
here with a tolerance (`REL`, `ABS`) and everything else — the keys, the order, the integers, the strings, the shape of
a text around its numbers — exactly. A tolerance is not a way to hide a change: a real change of a result is far larger
than 1e−9 of its value (the kernels are checked against their formulas by the tests of the modules).

`diff(stored, built)` is the path of the first difference (None — equal); `assert_same` raises with it.
The proof (PROGRESS.md «Допущения» «CI»): `OPENBLAS_CORETYPE=Nehalem|Haswell|Zen` and
`NPY_DISABLE_CPU_FEATURES=AVX512…` turned these tests red before, `NUMBA_CPU_NAME=generic|haswell` did not (the kernels
of numba have no `fastmath`, no `prange` in the answers: the same bytes on any processor)."""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from typing import Any

import numpy as np

#: relative and absolute tolerance of a number (a float on one side at least); integers are compared exactly
REL = 1e-9
ABS = 1e-9
#: for a file that keeps its floats rounded to 9 significant digits (`f"{x:.9g}"`): two runs may fall on the two sides
#: of a rounding, 1 in the ninth digit — up to 1e−8 of a value
ROUNDED = 2e-8
#: a number inside a text: a decimal point or comma (the texts of the windows are Russian: «35,11»), an exponent
_NUM = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][-+]?\d+)?")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def close(a: float, b: float, rel: float = REL, abs_: float = ABS) -> bool:
    """Two numbers are the same; NaN with NaN (a gap), infinities by sign."""
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def _text(a: str, b: str, path: str, rel: float, abs_: float) -> str | None:
    """Two strings: equal, or equal but for the numbers in them (compared with the tolerance)."""
    if a == b:
        return None
    na, nb = _NUM.findall(a), _NUM.findall(b)
    if len(na) == len(nb) and _NUM.sub("#", a) == _NUM.sub("#", b):
        for x, y in zip(na, nb, strict=True):
            if not close(_num(x), _num(y), rel, abs_):
                return f"{path}: {x} != {y} in {a[:80]!r}"
        return None
    return f"{path}: {a[:100]!r} != {b[:100]!r}"


def diff(
    a: Any,
    b: Any,
    path: str = "",
    *,
    rel: float = REL,
    abs_: float = ABS,
    forms: Collection[str] = (),
) -> str | None:
    """The path of the first difference of two answers read from JSON (`a` — stored, `b` — built).

    Numbers: integers exactly, floats (on either side) to `rel` / `abs_`. Strings: exactly, or with their numbers to
    the tolerance (see the module). `forms` — the keys whose value is a SHA-256 of float data (the fingerprint of a
    model of the fixture, `fp`): a hash cannot be close, so only its form (64 hex digits on both sides) is compared —
    the numbers the hash stands for are compared where they are shown."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}/{k}: no such key on one side"
            d = diff(a[k], b[k], f"{path}/{k}", rel=rel, abs_=abs_, forms=forms)
            if d:
                return d
        return None
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = diff(x, y, f"{path}/{i}", rel=rel, abs_=abs_, forms=forms)
            if d:
                return d
        return None
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        same = a is b or (type(a) is type(b) and a == b)
        return None if same else f"{path}: {a!r} != {b!r}"
    if isinstance(a, int | float) and isinstance(b, int | float):
        if isinstance(a, int) and isinstance(b, int):
            return None if a == b else f"{path}: {a!r} != {b!r}"
        return None if close(float(a), float(b), rel, abs_) else f"{path}: {a!r} != {b!r}"
    if isinstance(a, str) and isinstance(b, str):
        key = path.rsplit("/", 1)[-1]
        if key in forms and _HEX64.fullmatch(a) and _HEX64.fullmatch(b):
            return None
        return _text(a, b, path, rel, abs_)
    return None if a == b else f"{path}: {str(a)[:100]!r} != {str(b)[:100]!r}"


def assert_same(stored: Any, built: Any, note: str = "", **kw: Any) -> None:
    """`diff` as an assertion: the message is the path of the first difference."""
    d = diff(stored, built, **kw)
    assert d is None, f"{d} {note}".strip()


def same_arrays(a: dict[str, Any], b: dict[str, Any], note: str = "", rtol: float = 1e-6) -> None:
    """The arrays of two BFA1 / npz answers: the same names, dtypes, shapes; the integers and the booleans exactly; the
    floats to `rtol` of the largest value of the array (the float32 of the viewer come from the float64 that the
    processor rounds in its way: one float32 over to the next is 6e-8)."""
    assert a.keys() == b.keys(), f"{note} {sorted(a)} != {sorted(b)}".strip()
    for k, x in a.items():
        y = b[k]
        assert x.dtype == y.dtype, f"{note} {k}: {x.dtype} != {y.dtype}".strip()
        assert x.shape == y.shape, f"{note} {k}: {x.shape} != {y.shape}".strip()
        if x.dtype.kind == "f":
            top = float(np.nanmax(np.abs(x), initial=0.0)) if x.size else 0.0
            ok = np.allclose(x, y, rtol=rtol, atol=rtol * max(1.0, top), equal_nan=True)
        else:
            ok = bool(np.array_equal(x, y))
        assert ok, f"{note} {k}: the arrays differ".strip()
