"""The density of a block model: a constant, a field, a table «domain → density» or a formula (подшаг 17.26; ТЗ 8.7.5;
ADR 0023 §9, decision 13; the forms — `docs/progress/e17-api.md` §9). The kernels only: the job `blocks.density`, its
route and the «устарел» of the fields are 17.29.

    spec = parse(body)                                    # the body of `POST /blocks/{id}/density` (422 `bad_param`)
    plan = prepare(model, spec)                           # checked against the model: fields, labels, the formula
    r = restrict.prepare(model, tree, conn=conn, store=store)          # None — every block
    for piece in iter_density(model, plan, r, chunk=CHUNK, cancel=check):
        piece.chunk, piece.density, piece.source          # float64 [n] each, in the order of the rows of the model
    plan.tally                                            # rows, passed (by the restrictor), defaulted, empty

Methods (`spec.method`): `constant` — the same `value` for every block; `field` — the value of the number field
`field`; `table` — the density of the domain of the block (`table.field`: a category — by the label, a number — by the
exact value); `formula` — an expression of 8.7.4 over the fields of the model (`expr`, a number).

A block with no value of its own — a gap in the field or the formula (NaN), a domain missing or not in the table —
takes the default of the domain (`table.default` of the table, else `default`) and `DENS_SRC` = 1; its own value is
`DENS_SRC` = 0. With no default the density stays empty (NaN, never 0) and `DENS_SRC` is still 1: the block did not
get its own value (`tally.empty` counts them, `plan.warnings` says so). Outside the restrictor both fields are empty
(NaN in memory, null in the file). A density is a number above zero: a value ≤ 0 or ±inf in the field or the formula
of a block that passes the restrictor — 422 `density_invalid` (a density of 0 would give a zero tonnage silently); the
constant, the table and the default are checked at `parse`.

Units: the density is stored as the field `out` (f8, `role: density`) in `UNIT` («т/м³»); a field as the source keeps
its own unit label (the values are copied as they are, nothing is converted). The portions of any size give the same
bits: every block is computed from its own row alone (the formula by `calc`, bit for bit with numpy — 17.6).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.calc import ExprError, ExprTypeError, compile

from . import schema as bs
from .model import CHUNK, Chunk, Model

if TYPE_CHECKING:
    from .restrict import Restrictor

ALGO = "density-1"
UNIT = "т/м³"
METHODS = ("constant", "field", "table", "formula")
DEFAULT_OUT, DEFAULT_SRC = "DENS", "DENS_SRC"
MAX_TABLE = 5000
_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


def bad_param(param: str, message: str, hint: str = "Проверьте параметры плотности.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def field_missing(name: str, model: str, param: str) -> ApiError:
    return ApiError(
        422, "density_field_missing", f"В модели «{model}» нет поля «{name}», которое нужно для плотности.",
        "Выберите поле из списка полей модели.", {"field": name, "model": model, "param": param},
    )  # fmt: skip


def invalid(count: int, row: int, what: str) -> ApiError:
    return ApiError(
        422, "density_invalid",
        f"Плотность должна быть числом больше нуля: {what}, таких блоков — {count} (первый — строка {row + 1}).",
        "Исключите такие блоки ограничителем или исправьте значения; пустое значение заменяет «по умолчанию».",
        {"count": count, "row": row},
    )  # fmt: skip


# --- the body ---------------------------------------------------------------------------------------------------------


def _number(raw: Any, param: str, *, positive: bool, what: str = "Число") -> float:
    if isinstance(raw, bool):
        raise bad_param(param, f"{what}: ожидается число.")
    if isinstance(raw, str):
        if not _NUMBER.match(raw.strip()):
            raise bad_param(param, f"{what}: «{raw[:40]}» — не число.", "Запишите число, например 2,7.")
        raw = float(raw.strip().replace(",", "."))
    if not isinstance(raw, int | float) or not math.isfinite(float(raw)):
        raise bad_param(param, f"{what}: ожидается конечное число.")
    value = float(raw)
    if positive and not value > 0.0:
        raise bad_param(param, "Плотность должна быть больше нуля.", "Пустое значение задаёт «по умолчанию».")
    return value


def _name(body: Mapping[str, Any], key: str, default: str) -> str:
    raw = body.get(key)
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise bad_param(key, "Ожидается имя поля.")
    out = " ".join(raw.split())
    if not out or len(out) > bs.MAX_FIELD_NAME or out.casefold() == bs.IDX:
        raise bad_param(key, f"Имя поля — от 1 до {bs.MAX_FIELD_NAME} знаков, не «{bs.IDX}».")
    return out


@dataclass(frozen=True)
class Row:
    """A row of the table: the domain (`key`: a label or a number as given) and its density."""

    key: str | float
    density: float


@dataclass(frozen=True)
class Spec:
    method: str
    value: float | None
    field: str | None
    table_field: str | None
    rows: tuple[Row, ...]
    expr: str | None
    default: float | None
    out: str
    src_field: str
    table_default: float | None = None

    def params(self) -> dict[str, Any]:
        """What `source.params` of the field keeps for «Перестроить» (the same body again)."""
        out: dict[str, Any] = {"method": self.method, "default": self.default, "out": self.out,
                               "src_field": self.src_field}  # fmt: skip
        if self.method == "constant":
            out["value"] = self.value
        elif self.method == "field":
            out["field"] = self.field
        elif self.method == "formula":
            out["expr"] = self.expr
        else:
            out["table"] = {
                "field": self.table_field,
                "default": self.table_default,
                "values": [{"value": r.key, "density": r.density} for r in self.rows],
            }
        return out


def _table(raw: Any) -> tuple[str, tuple[Row, ...], float | None]:
    if not isinstance(raw, Mapping):
        raise bad_param("table", "Нужна таблица «домен — плотность».")
    name = raw.get("field")
    if not isinstance(name, str) or not name.strip():
        raise bad_param("table.field", "Укажите поле домена (категория или число).")
    values = raw.get("values")
    if not isinstance(values, Sequence) or isinstance(values, str) or not values:
        raise bad_param("table.values", "В таблице нет строк.", "Добавьте хотя бы одну строку «домен — плотность».")
    if len(values) > MAX_TABLE:
        raise bad_param("table.values", f"В таблице не больше {MAX_TABLE} строк.")
    rows: list[Row] = []
    seen: set[str] = set()
    for i, item in enumerate(values):
        if not isinstance(item, Mapping) or item.get("value") is None or item.get("value") == "":
            raise bad_param(f"table.values.{i}.value", "Не задан домен строки.")
        key = item["value"]
        if isinstance(key, bool) or not isinstance(key, str | int | float):
            raise bad_param(f"table.values.{i}.value", "Домен — подпись или число.")
        if isinstance(key, str):
            key = key.strip()
        mark = _key_text(key)
        if mark.casefold() in seen:
            raise bad_param(f"table.values.{i}.value", f"Домен «{mark}» повторяется.",
                            "Оставьте одну строку на домен.")  # fmt: skip
        seen.add(mark.casefold())
        rows.append(Row(key, _number(item.get("density"), f"table.values.{i}.density", positive=True)))
    default = raw.get("default")
    fallback = None if default in (None, "") else _number(default, "table.default", positive=True)
    return name.strip(), tuple(rows), fallback


def _key_text(key: str | float) -> str:
    if isinstance(key, str):
        return key
    return str(int(key)) if float(key).is_integer() and abs(key) < 1e15 else repr(float(key))


def parse(body: Mapping[str, Any]) -> Spec:
    """The body of `POST /blocks/{id}/density` (e17-api §9) checked as to its shape (the model is checked by
    `prepare`): 422 `bad_param {param}`. The version, the filter and `replace` are the job's (17.29)."""
    method = body.get("method")
    if method not in METHODS:
        raise bad_param("method", "Неизвестный способ задания плотности.",
                        "Допустимо: constant, field, table, formula.")  # fmt: skip
    default_raw = body.get("default")
    default = None if default_raw in (None, "") else _number(default_raw, "default", positive=True)
    out, src = _name(body, "out", DEFAULT_OUT), _name(body, "src_field", DEFAULT_SRC)
    if out.casefold() == src.casefold():
        raise bad_param("src_field", "Поле плотности и поле источника не могут называться одинаково.")
    value: float | None = None
    fld: str | None = None
    expr: str | None = None
    tfield: str | None = None
    rows: tuple[Row, ...] = ()
    tdef: float | None = None
    if method == "constant":
        if body.get("value") in (None, ""):
            raise bad_param("value", "Не задана плотность.", "Например, 2,7 т/м³.")
        value = _number(body["value"], "value", positive=True)
    elif method == "field":
        fld = body.get("field")
        if not isinstance(fld, str) or not fld.strip():
            raise bad_param("field", "Выберите поле плотности.")
        fld = fld.strip()
    elif method == "formula":
        expr = body.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            raise bad_param("expr", "Введите формулу.", "Например: IF(DOM = 'руда', 3.1, 2.7).")
        if len(expr) > 4000:
            raise bad_param("expr", "Формула — не больше 4000 знаков.")
    else:
        tfield, rows, tdef = _table(body.get("table"))
    return Spec(method, value, fld, tfield, rows, expr, default, out, src, tdef)


# --- the plan: the spec against the model -----------------------------------------------------------------------------


@dataclass
class Tally:
    """What the portions gave so far: all rows, those that pass the restrictor, those with the default of the domain
    (`DENS_SRC` = 1), those left empty (no value and no default)."""

    rows: int = 0
    passed: int = 0
    defaulted: int = 0
    empty: int = 0


@dataclass
class Plan:
    spec: Spec
    unit: str
    fields: list[str]  # stored names the portions read
    warnings: list[str]
    fallback: float | None
    tally: Tally = field(default_factory=Tally)
    _own: Any = None  # chunk -> float64 [n] own values (NaN — none)

    def own(self, chunk: Chunk) -> np.ndarray:
        """The own density of every block of the portion; NaN — none. A value ≤ 0 or ±inf is refused — but by the
        caller, only for the blocks that pass the restrictor (`values`)."""
        out: np.ndarray = self._own(chunk)
        return out

    def values(self, chunk: Chunk, inside: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(density, source) float64 [n] of one portion; `inside` — bool [n], the blocks that pass the restrictor
        (None — all); outside both are NaN."""
        own = self.own(chunk)
        n = chunk.n
        inside = np.ones(n, dtype=bool) if inside is None else inside
        bad = inside & (np.isinf(own) | (own <= 0.0))  # NaN is false in both
        if bad.any():
            first = int(np.argmax(bad))
            what = f"в блоке {float(own[first])!r}" if np.isfinite(own[first]) else "в блоке бесконечное значение"
            raise invalid(int(bad.sum()), chunk.start + first, what)
        have = inside & ~np.isnan(own)
        dens = np.full(n, np.nan)
        dens[have] = own[have]
        src = np.full(n, np.nan)
        src[have] = 0.0
        lacking = inside & ~have
        if self.fallback is not None:
            dens[lacking] = self.fallback
        src[lacking] = 1.0
        t = self.tally
        t.rows += n
        t.passed += int(inside.sum())
        t.defaulted += int(lacking.sum()) if self.fallback is not None else 0
        t.empty += int(lacking.sum()) if self.fallback is None else 0
        return dens, src


def _lookup(model: Model, name: str, param: str) -> dict[str, Any]:
    for f in model.fields:
        if str(f["name"]).casefold() == name.casefold():
            return f
    raise field_missing(name, model.name, param)


def _table_plan(model: Model, spec: Spec, warnings: list[str]) -> tuple[list[str], Any, float | None]:
    f = _lookup(model, str(spec.table_field), "table.field")
    stored = str(f["name"])
    fallback = spec.table_default if spec.table_default is not None else spec.default
    if f["kind"] == bs.CATEGORY:
        labels = [str(x) for x in f.get("labels") or []]
        by = {s.casefold(): i for i, s in enumerate(labels)}
        lut = np.full(len(labels) + 1, np.nan)  # the last cell is for the empty (−1)
        for r in spec.rows:
            code = by.get(_key_text(r.key).casefold())
            if code is None:
                warnings.append(f"Домена «{_key_text(r.key)}» из таблицы нет среди значений поля «{stored}».")
            else:
                lut[code] = r.density

        def own(chunk: Chunk) -> np.ndarray:
            return lut[chunk.cols[stored]]  # code −1 is the last cell: NaN

        return [stored], own, fallback
    keys: list[float] = []
    for i, r in enumerate(spec.rows):
        key = r.key
        if isinstance(key, str):
            if not _NUMBER.match(key):
                raise bad_param(
                    f"table.values.{i}.value",
                    f"Поле «{stored}» числовое, а домен «{key[:40]}» — не число.",
                    "Для числового поля домен — число, например 2 или 0,5.",
                )
            key = float(key.replace(",", "."))
        keys.append(float(key))
    order = np.argsort(keys, kind="stable")
    sorted_keys = np.asarray(keys, dtype=np.float64)[order]
    dens = np.asarray([r.density for r in spec.rows], dtype=np.float64)[order]

    def own_num(chunk: Chunk) -> np.ndarray:
        x = chunk.cols[stored]
        pos = np.minimum(np.searchsorted(sorted_keys, x), len(sorted_keys) - 1)
        hit = sorted_keys[pos] == x  # NaN never equals
        return np.where(hit, dens[pos], np.nan)

    return [stored], own_num, fallback


def prepare(model: Model, spec: Spec) -> Plan:
    """The plan of `spec` on `model`: 422 `density_field_missing {field, model, param}` for a field the model lacks,
    `bad_param` for a field of the wrong kind, `expr_*` for the formula (with `param: "expr"`)."""
    warnings: list[str] = []
    fallback = spec.default
    unit = UNIT
    if spec.method == "constant":
        value = float(spec.value or 0.0)
        names: list[str] = []

        def own(chunk: Chunk) -> np.ndarray:
            return np.full(chunk.n, value)

    elif spec.method == "field":
        f = _lookup(model, str(spec.field), "field")
        if f["kind"] != bs.NUMBER:
            raise bad_param("field", f"Поле «{f['name']}» — категория, а плотность — число.",
                            "Выберите числовое поле или задайте плотность таблицей по домену.")  # fmt: skip
        stored = str(f["name"])
        names = [stored]
        unit = str(f.get("unit") or UNIT)

        def own(chunk: Chunk) -> np.ndarray:
            return chunk.cols[stored]

    elif spec.method == "formula":
        from .calc_job import expr_api_error, schema_of

        try:
            prog = compile(str(spec.expr), schema_of(model))
        except ExprError as e:
            raise expr_api_error(e, str(spec.expr)) from e
        if prog.out_kind != "num":
            hint = "Например: IF(DOM = 'руда', 3.1, 2.7)."
            err = ExprTypeError(str(spec.expr), prog.expr.pos, "Формула плотности должна давать число", hint)
            raise expr_api_error(err, str(spec.expr))
        names = list(prog.fields_used)

        def own(chunk: Chunk) -> np.ndarray:
            return prog.eval_chunk(chunk.cols, chunk.n)

    else:
        names, own, fallback = _table_plan(model, spec, warnings)
    if fallback is None:
        warnings.append("Значение по умолчанию не задано: блоки без плотности останутся пустыми.")
    return Plan(spec, unit, names, warnings, fallback, _own=own)


# --- the portions -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """A portion of the result: the rows of `chunk` (their order), the density and its source — float64 [n]."""

    chunk: Chunk
    density: np.ndarray
    source: np.ndarray


def iter_density(
    model: Model,
    plan: Plan,
    restrictor: Restrictor | None = None,
    *,
    chunk: int = CHUNK,
    cancel: Any = None,
) -> Iterator[Piece]:
    """The density of the whole model by portions of `chunk` rows (`Model.iter`); `restrictor` — what
    `restrict.prepare` made (None — every block passes). `cancel` is called between the portions."""
    names = list(dict.fromkeys([*plan.fields, *(restrictor.fields if restrictor is not None else [])]))
    for ch in model.iter(names, chunk):
        if cancel is not None:
            cancel()
        inside = None if restrictor is None else restrictor.weights(ch) > 0.0
        dens, src = plan.values(ch, inside)
        yield Piece(ch, dens, src)
