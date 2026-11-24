"""Runtime of the calculator evaluation (ТЗ 8.7.4, ADR 0023 §7): errors, field types, column loading, the numeric
kernels. The compiler and the `Program` are in `evaluate.py`; the rules are described there.

Values flowing through a compiled tree: a number — float64 array or np.float64; a condition — `Cond(v, m)` (`v` truth,
`m` the «NaN mark»: rows where an operand was a gap, None when nobody needs it); a category or a text literal —
`Cats(codes, labels)` (int32, −1 — empty).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import ExprError

Fn = Callable[["Ctx"], Any]


class ExprFieldError(ExprError):
    """A field of the text is not in the model (or its name is ambiguous): 422 `expr_field`."""

    code = "expr_field"


class ExprTypeError(ExprError):
    """A wrong use of types (text in arithmetic, a condition as a number, ROUND digits…): 422 `expr_type`."""

    code = "expr_type"


class ExprDataError(ValueError):
    """The columns given for evaluation do not fit the program: 422 `expr_data`."""

    code = "expr_data"

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message, self.hint = message, hint

    def to_body(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


@dataclass(frozen=True)
class FieldInfo:
    """The type of a model field: `num` or `cat` (int32 codes into `labels`)."""

    kind: str = "num"
    labels: tuple[str, ...] = ()

    @staticmethod
    def num() -> FieldInfo:
        return FieldInfo("num")

    @staticmethod
    def cat(labels: Iterable[str]) -> FieldInfo:
        return FieldInfo("cat", tuple(labels))


def as_info(spec: Any) -> FieldInfo:
    if isinstance(spec, FieldInfo):
        return spec
    if spec is None or spec == "num":
        return FieldInfo("num")
    if isinstance(spec, (list, tuple)):
        return FieldInfo("cat", tuple(str(s) for s in spec))
    raise TypeError("Тип поля: FieldInfo, 'num' или список подписей категории.")


class Cond:
    """A condition: `v` — truth, `m` — rows where an operand was a gap (None when nobody needs the mark)."""

    __slots__ = ("m", "v")

    def __init__(self, v: Any, m: Any = None) -> None:
        self.v, self.m = v, m


class Cats:
    """Category values: int32 codes (scalar for a text literal) and the dictionary they point into."""

    __slots__ = ("codes", "labels")

    def __init__(self, codes: Any, labels: tuple[str, ...]) -> None:
        self.codes, self.labels = codes, labels


# ------------------------------------------------------------------------------------------------ runtime


def san(x: Any) -> Any:
    """±inf → NaN, in place for a fresh array."""
    if isinstance(x, np.ndarray) and x.ndim > 0:
        bad = np.isinf(x)
        if bad.any():
            x[bad] = np.nan
        return x
    v = np.float64(x)
    return np.float64("nan") if np.isinf(v) else v


class Ctx:
    """One chunk: its columns (converted once) and its length."""

    __slots__ = ("cache", "cols", "engine", "n")

    def __init__(self, cols: Mapping[str, Any], n: int, engine: str) -> None:
        self.cols, self.n, self.engine = cols, n, engine
        self.cache: dict[str, Any] = {}

    def _col(self, name: str) -> Any:
        try:
            col = self.cols[name]
        except KeyError:
            raise ExprDataError(f"Нет данных поля «{name}».", "Передайте колонку каждого поля fields_used.") from None
        if len(col) != self.n:
            raise ExprDataError(
                f"Колонка «{name}» длиной {len(col)}, а порция — {self.n} строк.", "Все колонки порции — одной длины."
            )
        return col

    def num(self, name: str) -> np.ndarray:
        a = self.cache.get(name)
        if a is None:
            col = self._col(name)
            if isinstance(col, np.ma.MaskedArray):
                col = np.ma.filled(col.astype(np.float64), np.nan)
            a = np.asarray(col)
            if a.dtype != np.float64:
                if a.dtype.kind not in "fiub":
                    raise ExprDataError(f"Поле «{name}» не числовое ({a.dtype}).", "Числовое поле — float или целое.")
                a = a.astype(np.float64)
            bad = np.isinf(a)
            if bad.any():  # ±inf is not a value of a field
                a = np.where(bad, np.nan, a)
            self.cache[name] = a
        return a

    def cat(self, name: str, n_labels: int) -> np.ndarray:
        a = self.cache.get(name)
        if a is None:
            col = self._col(name)
            if isinstance(col, np.ma.MaskedArray):
                col = np.ma.filled(col.astype(np.int64), -1)
            a = np.asarray(col)
            if a.dtype.kind not in "iu":
                raise ExprDataError(f"Поле «{name}» не категориальное ({a.dtype}).", "Нужны целые коды, −1 пусто.")
            if a.size and (int(a.min()) < -1 or int(a.max()) >= n_labels):
                raise ExprDataError(f"Код категории поля «{name}» вне словаря (0…{n_labels - 1}, −1 — пусто).")
            a = a.astype(np.int32, copy=False)
            self.cache[name] = a
        return a


UNARY: dict[str, Callable[[Any], Any]] = {"ABS": np.abs, "SQRT": np.sqrt, "FLOOR": np.floor, "CEIL": np.ceil}
BINARY: dict[str, Callable[[Any, Any], Any]] = {
    "+": np.add, "-": np.subtract, "*": np.multiply, "/": np.true_divide,
}  # fmt: skip


def numexpr_module() -> Any:
    try:
        import numexpr
    except ImportError:  # optional: numpy gives the same numbers
        return None
    return numexpr


def cluster_code(t: tuple) -> str:
    """The numexpr text of a cluster tree: ("f", i) field, ("c", i) constant, ("neg", t), ("bin", op, l, r),
    ("fn", NAME, t), ("fin", t, i) — `t` with ±inf → the NaN constant `i` (a divisor that may have overflowed:
    IEEE would turn `1 / inf` into 0, but an overflow is a gap)."""
    kind = t[0]
    if kind in ("f", "c"):
        return f"{'v' if kind == 'f' else 'c'}{t[1]}"
    if kind == "neg":
        return f"(-{cluster_code(t[1])})"
    if kind == "bin":
        return f"({cluster_code(t[2])} {t[1]} {cluster_code(t[3])})"
    if kind == "fin":
        inner = cluster_code(t[1])
        return f"where(isfinite({inner}), {inner}, c{t[2]})"
    return f"{t[1].lower()}({cluster_code(t[2])})"


def cluster_numpy(t: tuple, arrays: list[Any], consts: list[Any]) -> Any:
    kind = t[0]
    if kind == "f":
        return arrays[t[1]]
    if kind == "c":
        return consts[t[1]]
    if kind == "fin":
        x = cluster_numpy(t[1], arrays, consts)
        return np.where(np.isfinite(x), x, consts[t[2]])
    if kind == "neg":
        return np.negative(cluster_numpy(t[1], arrays, consts))
    if kind == "bin":
        return BINARY[t[1]](cluster_numpy(t[2], arrays, consts), cluster_numpy(t[3], arrays, consts))
    return UNARY[t[1]](cluster_numpy(t[2], arrays, consts))


def numeric_labels(labels: tuple[str, ...]) -> np.ndarray:
    """The numeric value of each label (NaN for a non-number) plus a last NaN for the empty code −1."""
    out = np.full(len(labels) + 1, np.nan)
    for i, lab in enumerate(labels):
        try:
            v = float(lab.strip().replace(",", "."))
        except ValueError:
            continue
        if math.isfinite(v):
            out[i] = v
    return out


def merge_labels(*parts: tuple[str, ...]) -> tuple[tuple[str, ...], list[np.ndarray]]:
    """A common dictionary and, for each part, the table old code → new code (last cell: empty −1 → −1)."""
    union: dict[str, int] = {}
    for labels in parts:
        for lab in labels:
            union.setdefault(lab, len(union))
    tables = [np.array([union[lab] for lab in labels] + [-1], dtype=np.int32) for labels in parts]
    return tuple(union), tables


def round_half_away(x: Any, n: int) -> Any:
    """Half away from zero; `n` digits (negative: tens, hundreds…)."""
    s = 10.0 ** abs(n)
    ax = np.abs(x)
    t = ax * s if n >= 0 else ax / s
    r = np.floor(t)
    r = r + (t - r >= 0.5)
    out = np.copysign(r / s if n >= 0 else r * s, x) + 0.0  # + 0.0: −0 → 0
    if n >= 0:
        out = np.where(ax >= 4.5e15, x, out)  # already whole numbers (and the scaling would overflow)
    return san(out)
def negate(b: Cond) -> Cond:
    return Cond(np.logical_not(b.v), b.m)


def bool_op(op: str, fs: list[Fn]) -> Fn:
    red = np.logical_and if op == "AND" else np.logical_or

    def run(ctx: Ctx) -> Cond:
        bs = [f(ctx) for f in fs]
        v, m = bs[0].v, bs[0].m
        for b in bs[1:]:
            v = red(v, b.v)
            m = b.m if m is None else (m if b.m is None else m | b.m)
        return Cond(v, m)

    return run


def cmp_num(op: str, a: Any, b: Any, marks: bool) -> Cond:
    if op == "=":
        v = a == b
    elif op == "<>":
        v = (a < b) | (a > b)  # NaN on either side: false, like every comparison
    elif op == "<":
        v = a < b
    elif op == "<=":
        v = a <= b
    elif op == ">":
        v = a > b
    else:
        v = a >= b
    return Cond(v, (np.isnan(a) | np.isnan(b)) if marks else None)


def any_eq(x: Any, values: list[float]) -> Any:
    v = np.zeros(np.shape(x), dtype=bool)
    for c in values:
        v |= x == c
    return v


def table_lookup(tbl: np.ndarray, codes: Any, marks: bool) -> Cond:
    return Cond(tbl[codes], (np.asarray(codes) < 0) if marks else None)


def reduce_all(f: Callable[[Any, Any], Any], xs: list[Any]) -> Any:
    out = xs[0]
    for x in xs[1:]:
        out = f(out, x)
    return out


def coalesce_num(xs: list[Any]) -> Any:
    out = xs[0]
    for x in xs[1:]:
        out = np.where(np.isnan(out), x, out)
    return out


def coalesce_cat(cs: list[Any]) -> Any:
    out = cs[0]
    for c in cs[1:]:
        out = np.where(np.asarray(out) < 0, c, out)
    return np.asarray(out).astype(np.int32, copy=False)

