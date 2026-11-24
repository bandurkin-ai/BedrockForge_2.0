"""Evaluation of the calculator tree over float64 columns, chunk by chunk (ТЗ 8.7.4, ADR 0023 §7).

    prog = compile("IF(AU > 1, AU * 2, 0)", {"AU": "num", "DOMAIN": ["OX", "SULF"]})
    prog.fields_used, prog.out_kind          # ("AU",), "num"      (for `POST /blocks/calc/check`)
    values = prog.eval({"AU": au})           # f8 array; chunks of 2**20 rows, memmap slices work

Values: a numeric field is float64 (any numeric dtype or a masked array is accepted, a gap is NaN); a category field is
int32 codes into `FieldInfo.labels` (−1 — empty). Results: `num` → float64, `bool` → bool, `cat` → int32 codes with
`prog.labels`. A condition used as the whole expression is a mask (a NaN mark is dropped: false is false).

Rules for gaps and non-finite numbers (the ТЗ gives the first four, the rest are decisions of 17.6):
  * arithmetic with NaN is NaN; any comparison with NaN (also `<>`) is false; an empty category is a gap too;
  * logic is two-valued (`NOT` of false is true); `IF` whose condition has a NaN operand anywhere in its subtree is NaN
    (the «NaN mark» travels up the condition: comparisons, IN, AND, OR, NOT); `ISNULL` and `COALESCE` handle gaps;
  * ±inf is never a value: an infinite input, `x / 0`, an overflow, `LN(0)`, `SQRT(-1)`, `0 ^ -1` all give NaN;
  * `MIN`, `MAX`, `CLIP` with NaN give NaN; `ROUND(x, n)` rounds half away from zero, `n` is a constant integer −15…15;
  * a category against a number compares the number written in its label (`DOMAIN = 2` for the label «2»); a label that
    is not a number, or an empty one, is a gap; a category against a text: only `=`, `<>`, `IN`; names ignore case.

numexpr is used for pure IEEE arithmetic only (+ − * /, unary −, ABS, SQRT, FLOOR, CEIL), so its result equals numpy's
bit for bit (constants go in as variables: numexpr rewrites `x / 3.0` into `x * (1/3.0)`); LN, LOG10, EXP, `^` and the
rest always run in numpy. Without numexpr everything runs in numpy with the same results.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import ExprError
from .nodes import BinOp, BoolOp, Call, Compare, Expr, Field, In, Neg, Not, Num, Str
from .parser import parse
from .runtime import (
    BINARY,
    UNARY,
    Cats,
    Cond,
    Ctx,
    ExprDataError,
    ExprFieldError,
    ExprTypeError,
    Fn,
    any_eq,
    as_info,
    bool_op,
    cluster_code,
    cluster_numpy,
    cmp_num,
    coalesce_cat,
    coalesce_num,
    merge_labels,
    negate,
    numeric_labels,
    numexpr_module,
    reduce_all,
    round_half_away,
    san,
    table_lookup,
)

CHUNK = 1 << 20
MAX_LABELS = 32_767
ROUND_DIGITS = 15


@dataclass(frozen=True)
class _T:
    kind: str  # num | bool | cat | str (a text literal)
    labels: tuple[str, ...] = ()


_NUM, _BOOL = _T("num"), _T("bool")
_RU = {"num": "число", "bool": "условие (истина или ложь)", "cat": "категория", "str": "текст"}


# ------------------------------------------------------------------------------------------------ compiler


class _Compiler:
    def __init__(self, schema: Mapping[str, Any], text: str, with_pos: bool) -> None:
        self.text, self.with_pos = text, with_pos
        self.schema = {name: as_info(spec) for name, spec in schema.items()}
        self.by_fold: dict[str, list[str]] = {}
        for name in self.schema:
            self.by_fold.setdefault(name.casefold(), []).append(name)
        self.resolved: dict[str, str] = {}  # casefold of the text name → schema name
        self.clusters = 0

    def err(self, cls: type[ExprError], e: Expr, detail: str, hint: str, reason: str) -> ExprError:
        return cls(self.text, e.pos if self.with_pos else 0, detail, hint, reason)

    def terr(self, e: Expr, detail: str, hint: str, reason: str = "type") -> ExprError:
        return self.err(ExprTypeError, e, detail, hint, reason)

    # -- fields
    def resolve(self, e: Field) -> str:
        key = e.name.casefold()
        if key in self.resolved:
            return self.resolved[key]
        names = list(self.schema)
        if e.name in self.schema:
            found = e.name
        else:
            same = self.by_fold.get(key, [])
            if len(same) > 1:
                raise self.err(ExprFieldError, e, f"Имя «{e.name}» подходит к нескольким полям: {', '.join(same)}",
                               "Напишите имя точно, как в списке полей модели.", "ambiguous_field")
            if not same:
                near = difflib.get_close_matches(e.name, names, n=1, cutoff=0.6)
                hint = f"Возможно, имелось в виду «{near[0]}». " if near else ""
                hint += ("Поля модели: " + ", ".join(names[:12]) + (" …" if len(names) > 12 else "") + "."
                         if names else "В модели нет полей.")
                raise self.err(ExprFieldError, e, f"Неизвестное поле «{e.name}»", hint, "unknown_field")
            found = same[0]
        self.resolved[key] = found
        return found

    # -- typing helpers
    def want(self, e: Expr, kinds: tuple[str, ...], what: str, marks: bool = False) -> tuple[_T, Fn]:
        t, fn = self.node(e, marks)
        if t.kind not in kinds:
            hints = {
                "bool": "Условие в число превращает IF(условие, 1, 0).",
                "cat": "Категорию сравнивайте с текстом ('A'), числом или перечнем IN ('A', 'B').",
                "str": "Текст можно сравнивать с категорией или выбирать через IF.",
                "num": "Число сравните с чем-нибудь: AU > 1.",
            }
            need = " или ".join(_RU[k] for k in kinds)
            raise self.terr(e, f"{what}: нужен {need}, а здесь {_RU[t.kind]}", hints[t.kind])
        return t, fn

    def number(self, e: Expr, what: str) -> Fn:
        return self.want(e, ("num",), what)[1]

    def cond(self, e: Expr, marks: bool, what: str) -> Fn:
        """A condition: a bool, or a number (not zero is true, NaN is false with a mark)."""
        t, fn = self.want(e, ("bool", "num"), what, marks)
        if t.kind == "bool":
            return fn

        def truthy(ctx: Ctx) -> Cond:
            x = fn(ctx)
            return Cond((x < 0) | (x > 0), np.isnan(x) if marks else None)

        return truthy

    # -- nodes
    def node(self, e: Expr, marks: bool) -> tuple[_T, Fn]:
        if isinstance(e, Num):
            c = np.float64(e.value)
            return _NUM, lambda ctx: c
        if isinstance(e, Str):
            sc = Cats(np.int32(0), (e.value,))
            return _T("str", (e.value,)), lambda ctx: sc
        if isinstance(e, Field):
            name = self.resolve(e)
            info = self.schema[name]
            if info.kind == "num":
                return _NUM, lambda ctx: ctx.num(name)
            labels = info.labels
            return _T("cat", labels), lambda ctx: Cats(ctx.cat(name, len(labels)), labels)
        if isinstance(e, (Neg, BinOp, Call)) and self.is_cluster(e):
            return _NUM, self.cluster(e)
        if isinstance(e, Neg):
            a = self.number(e.operand, "Унарный минус")
            return _NUM, lambda ctx: np.negative(a(ctx))
        if isinstance(e, Not):
            f = self.cond(e.operand, marks, "NOT")
            return _BOOL, lambda ctx: negate(f(ctx))
        if isinstance(e, BinOp):
            fa, fb = self.number(e.left, f"Операция «{e.op}»"), self.number(e.right, f"Операция «{e.op}»")
            if e.op == "^":
                return _NUM, lambda ctx: san(np.power(fa(ctx), fb(ctx)))
            op = BINARY[e.op]
            return _NUM, lambda ctx: san(op(fa(ctx), fb(ctx)))
        if isinstance(e, BoolOp):
            fs = [self.cond(o, marks, e.op) for o in e.operands]
            return _BOOL, bool_op(e.op, fs)
        if isinstance(e, Compare):
            return _BOOL, self.compare(e, marks)
        if isinstance(e, In):
            return _BOOL, self.in_list(e, marks)
        assert isinstance(e, Call)
        return self.call(e, marks)

    def is_cluster(self, e: Expr) -> bool:
        for n in e.walk():
            if isinstance(n, Field):
                if self.schema[self.resolve(n)].kind != "num":
                    return False
            elif isinstance(n, BinOp):
                if n.op == "^":
                    return False
            elif isinstance(n, Call):
                if n.name not in UNARY:
                    return False
            elif not isinstance(n, (Num, Neg)):
                return False
        return True

    def cluster(self, e: Expr) -> Fn:
        fields: list[str] = []
        consts: list[np.float64] = []

        def build(n: Expr) -> tuple:
            if isinstance(n, Field):
                name = self.resolve(n)
                if name not in fields:
                    fields.append(name)
                return ("f", fields.index(name))
            if isinstance(n, Num):
                consts.append(np.float64(n.value))
                return ("c", len(consts) - 1)
            if isinstance(n, Neg):
                return ("neg", build(n.operand))
            if isinstance(n, BinOp):
                left, right = build(n.left), build(n.right)
                if n.op == "/" and right[0] not in ("f", "c"):  # only an operation can overflow
                    consts.append(np.float64("nan"))
                    right = ("fin", right, len(consts) - 1)
                return ("bin", n.op, left, right)
            assert isinstance(n, Call)
            return ("fn", n.name, build(n.args[0]))

        tree = build(e)
        code = cluster_code(tree)
        self.clusters += 1

        def run(ctx: Ctx) -> Any:
            arrays = [ctx.num(f) for f in fields]
            ne = numexpr_module() if arrays and ctx.engine != "numpy" else None
            if ne is not None:
                local = {f"v{i}": a for i, a in enumerate(arrays)} | {f"c{i}": c for i, c in enumerate(consts)}
                try:
                    return san(ne.evaluate(code, local_dict=local, global_dict={}))
                except Exception:  # noqa: BLE001 — a limit of numexpr (too many operands): numpy gives the same numbers
                    pass
            return san(cluster_numpy(tree, arrays, consts))

        return run

    # -- conditions
    def compare(self, e: Compare, marks: bool) -> Fn:
        (ta, fa), (tb, fb) = self.want(e.left, ("num", "cat", "str"), "Сравнение"), self.want(
            e.right, ("num", "cat", "str"), "Сравнение"
        )
        op = e.op
        if ta.kind == "num" or tb.kind == "num":
            if "str" in (ta.kind, tb.kind) or ta.kind == tb.kind == "cat":
                raise self.terr(e, "Текст нельзя сравнивать с числом",
                            "Категорию сравнивайте с текстом ('A') или числом (по значению подписи).")
            fa, fb = self.as_numeric(ta, fa), self.as_numeric(tb, fb)
            return lambda ctx: cmp_num(op, fa(ctx), fb(ctx), marks)
        if op not in ("=", "<>"):
            raise self.terr(e, f"Категории и текст сравниваются только через «=» и «<>», а не «{op}»",
                           "Для порядка сравнивайте числовые значения подписей или используйте IN.")
        if ta.kind == tb.kind == "str":
            same = ta.labels[0] == tb.labels[0]
            res = np.bool_(same if op == "=" else not same)
            return lambda ctx: Cond(res, np.bool_(False) if marks else None)
        if tb.kind == "str" or ta.kind == "str":  # a category against a literal: one table lookup
            (cat_t, cat_f), lit = ((ta, fa), tb.labels[0]) if tb.kind == "str" else ((tb, fb), ta.labels[0])
            tbl = np.array([(lab == lit) == (op == "=") for lab in cat_t.labels] + [False])
            return lambda ctx: table_lookup(tbl, cat_f(ctx).codes, marks)
        union, (ma, mb) = merge_labels(ta.labels, tb.labels)

        def cat_cat(ctx: Ctx) -> Cond:
            ca, cb = fa(ctx).codes, fb(ctx).codes
            ia, ib = ma[ca], mb[cb]
            v = ((ia == ib) if op == "=" else (ia != ib)) & (ia >= 0) & (ib >= 0)
            return Cond(v, ((ia < 0) | (ib < 0)) if marks else None)

        return cat_cat

    def as_numeric(self, t: _T, fn: Fn) -> Fn:
        if t.kind == "num":
            return fn
        lv = numeric_labels(t.labels)
        return lambda ctx: lv[fn(ctx).codes]

    def in_list(self, e: In, marks: bool) -> Fn:
        t, fn = self.want(e.operand, ("num", "cat", "str"), "IN")
        strings = {v for v in e.values if isinstance(v, str)}
        numbers = [float(v) for v in e.values if not isinstance(v, str)]
        if t.kind == "num":
            if strings:
                raise self.terr(e, "Число нельзя искать среди текстовых значений IN",
                               "Для числового поля перечислите числа: IN (1, 2, 3).")
            arr = np.array(numbers)

            def in_num(ctx: Ctx) -> Cond:
                x = fn(ctx)
                v = np.isin(x, arr) if arr.size > 8 else any_eq(x, numbers)
                return Cond(v, np.isnan(x) if marks else None)

            return in_num
        lv = numeric_labels(t.labels)
        tbl = np.array([lab in strings or bool(np.isin(lv[i], numbers)) for i, lab in enumerate(t.labels)] + [False])
        return lambda ctx: table_lookup(tbl, fn(ctx).codes, marks)

    # -- functions
    def call(self, e: Call, marks: bool) -> tuple[_T, Fn]:
        name, args = e.name, e.args
        if name in UNARY or name in ("LN", "LOG10", "EXP"):
            a = self.number(args[0], name)
            if name in UNARY:
                u = UNARY[name]
                return _NUM, lambda ctx: san(u(a(ctx)))
            lf = {"LN": np.log, "LOG10": np.log10, "EXP": np.exp}[name]
            return _NUM, lambda ctx: san(lf(a(ctx)))
        if name in ("MIN", "MAX", "CLIP"):
            fs = [self.number(a, name) for a in args]
            if name == "CLIP":
                return _NUM, lambda ctx: np.minimum(np.maximum(fs[0](ctx), fs[1](ctx)), fs[2](ctx))
            red = np.minimum if name == "MIN" else np.maximum
            return _NUM, lambda ctx: reduce_all(red, [f(ctx) for f in fs])
        if name == "ROUND":
            a = self.number(args[0], "ROUND")
            n = self.round_digits(args[1])
            return _NUM, lambda ctx: round_half_away(a(ctx), n)
        if name == "ISNULL":
            t, fn = self.want(args[0], ("num", "cat", "str"), "ISNULL")
            if t.kind == "num":
                return _BOOL, lambda ctx: Cond(np.isnan(fn(ctx)))
            return _BOOL, lambda ctx: Cond(np.asarray(fn(ctx).codes) < 0)
        if name == "IF":
            return self.if_call(e, marks)
        assert name == "COALESCE"
        parts = [self.want(a, ("num", "cat", "str"), "COALESCE") for a in args]
        if all(t.kind == "num" for t, _ in parts):
            fs = [f for _, f in parts]
            return _NUM, lambda ctx: coalesce_num([f(ctx) for f in fs])
        if any(t.kind == "num" for t, _ in parts):
            raise self.terr(e, "COALESCE смешивает числа и текст", "Все аргументы — числа или все — категории и текст.")
        return self.text_result(e, parts, lambda ctx, cs: coalesce_cat(cs))

    def text_result(self, e: Expr, parts: list[tuple[_T, Fn]], make: Callable[[Ctx, list[Any]], Any]) -> tuple[_T, Fn]:
        union, tables = merge_labels(*(t.labels for t, _ in parts))
        if len(union) > MAX_LABELS:
            raise self.terr(e, f"В результате больше {MAX_LABELS} подписей", "Сократите набор значений.")
        fs = [f for _, f in parts]

        def run(ctx: Ctx) -> Cats:
            return Cats(make(ctx, [tb[f(ctx).codes] for tb, f in zip(tables, fs, strict=True)]), union)

        return _T("cat", union), run

    def if_call(self, e: Call, marks: bool) -> tuple[_T, Fn]:
        c = self.cond(e.args[0], True, "Условие IF")
        (ta, fa), (tb, fb) = (self.node(e.args[1], marks), self.node(e.args[2], marks))
        text = ("cat", "str")
        if ta.kind == tb.kind == "num":
            def if_num(ctx: Ctx) -> Any:
                cv = c(ctx)
                res = np.where(cv.v, fa(ctx), fb(ctx))
                return res if cv.m is None else np.where(cv.m, np.nan, res)
            return _NUM, if_num
        if ta.kind in text and tb.kind in text:
            def mk(ctx: Ctx, cs: list[Any], cf: Fn = c) -> Any:
                cv = cf(ctx)
                res = np.where(cv.v, cs[0], cs[1]).astype(np.int32, copy=False)
                return res if cv.m is None else np.where(cv.m, np.int32(-1), res)
            return self.text_result(e, [(ta, fa), (tb, fb)], mk)
        if ta.kind == tb.kind == "bool":
            def if_bool(ctx: Ctx) -> Cond:
                cv, a, b = c(ctx), fa(ctx), fb(ctx)
                m = cv.m if cv.m is not None else np.bool_(False)
                for br in (a.m, b.m):
                    if br is not None:
                        m = m | np.where(cv.v, a.m if br is a.m else False, b.m if br is b.m else False)
                v = np.where(cv.v, a.v, b.v)
                return Cond(v, m if marks else None)
            return _BOOL, if_bool
        raise self.terr(e, f"Ветки IF разных типов: {_RU[ta.kind]} и {_RU[tb.kind]}",
                       "Обе ветки — числа, либо обе — текст и категории.")

    def round_digits(self, e: Expr) -> int:
        hint = f"Запишите число от −{ROUND_DIGITS} до {ROUND_DIGITS}, например ROUND(AU, 2); поле здесь нельзя."
        bad = self.terr(e, "Число знаков в ROUND должно быть целой константой", hint, "round_digits")
        if e.fields():
            raise bad
        _, fn = self.want(e, ("num",), "Знаки ROUND")
        with np.errstate(all="ignore"):
            v = float(fn(Ctx({}, 1, "numpy")))
        if not math.isfinite(v) or v != int(v) or abs(v) > ROUND_DIGITS:
            raise bad
        return int(v)



# ------------------------------------------------------------------------------------------------ program


class Program:
    """A compiled expression: `fields_used`, `out_kind` (`num`, `bool` or `cat`), `labels` (for `cat`), `eval`."""

    def __init__(self, expr: Expr, text: str, fields_used: tuple[str, ...], t: _T, fn: Fn, clusters: int) -> None:
        self.expr, self.text, self.fields_used = expr, text, fields_used
        self.out_kind = "cat" if t.kind == "str" else t.kind
        self.labels = t.labels
        self.uses_numexpr = clusters > 0
        self._fn = fn
        self.dtype = {"num": np.dtype(np.float64), "bool": np.dtype(np.bool_), "cat": np.dtype(np.int32)}[self.out_kind]

    def eval_chunk(self, cols: Mapping[str, Any], n: int | None = None, engine: str = "auto") -> np.ndarray:
        """One chunk: `cols` — a column per field of `fields_used` (all of length `n`)."""
        if engine not in ("auto", "numpy", "numexpr"):
            raise ExprDataError(f"Неизвестный движок «{engine}».", "Допустимо: auto, numpy, numexpr.")
        if engine == "numexpr" and numexpr_module() is None:
            raise ExprDataError("numexpr не установлен.", "Выберите движок «auto» или «numpy».")
        n = self._length(cols, n)
        if n == 0:
            return np.empty(0, dtype=self.dtype)
        with np.errstate(all="ignore"):
            v = self._fn(Ctx(cols, n, engine))
        if isinstance(v, Cond):
            v = v.v
        elif isinstance(v, Cats):
            v = v.codes
        out = np.asarray(v, dtype=self.dtype)
        return out if out.shape == (n,) else np.array(np.broadcast_to(out, (n,)))

    def eval(
        self, cols: Mapping[str, Any], n: int | None = None, *, chunk: int = CHUNK, engine: str = "auto"
    ) -> np.ndarray:
        """The whole column; `cols` may be memmaps — they are read by slices of `chunk` rows."""
        n = self._length(cols, n)
        out = np.empty(n, dtype=self.dtype)
        for i in range(0, n, chunk):
            j = min(i + chunk, n)
            out[i:j] = self.eval_chunk({k: cols[k][i:j] for k in self.fields_used if k in cols}, j - i, engine)
        return out

    def iter_eval(self, chunks: Iterable[Mapping[str, Any]], engine: str = "auto") -> Iterator[np.ndarray]:
        """A result per incoming chunk of columns (the way `blocks.model.Model.iter` delivers them)."""
        for cols in chunks:
            yield self.eval_chunk(cols, None, engine)

    def _length(self, cols: Mapping[str, Any], n: int | None) -> int:
        lens = {len(cols[k]) for k in self.fields_used if k in cols}
        if not self.fields_used and cols:  # a constant expression takes the length of the columns given
            lens = {len(v) for v in cols.values()}
        if n is None:
            if len(lens) > 1:
                raise ExprDataError("Колонки разной длины.", "Все колонки поля одной модели имеют одну длину.")
            n = lens.pop() if lens else 1
        missing = [k for k in self.fields_used if k not in cols]
        if missing:
            raise ExprDataError(f"Нет данных поля «{missing[0]}».", "Передайте колонку для каждого поля fields_used.")
        return n


def compile(source: str | Expr, schema: Mapping[str, Any], *, text: str | None = None) -> Program:  # noqa: A001
    """Parse (if given a text), check names and types against `schema` {field: FieldInfo | 'num' | [labels]}.

    Raises ExprError (syntax, `expr_syntax`), ExprFieldError (`expr_field`), ExprTypeError (`expr_type`): all carry the
    place in the text and `to_body()`.
    """
    expr = parse(source) if isinstance(source, str) else source
    shown = source if isinstance(source, str) else (text if text is not None else expr.to_text())
    comp = _Compiler(schema, shown, with_pos=isinstance(source, str) or text is not None)
    for node in expr.walk():  # the leftmost unknown field is reported first
        if isinstance(node, Field):
            comp.resolve(node)
    t, fn = comp.node(expr, False)
    used = tuple(dict.fromkeys(comp.resolved[f.casefold()] for f in expr.fields()))
    return Program(expr, shown, used, t, fn, comp.clusters)
