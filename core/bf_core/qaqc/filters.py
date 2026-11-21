"""The filter of the QAQC records (ТЗ 8.4.2; ADR 0020 §5): the model `v: 1` → a mask of the rows, and the types
of the records by the dictionary of codes of the settings.

    parse_filter(raw) -> Filter            the filter of a request, a command or a report, checked (None, {} — no
                                           filter); `QaqcError` `bad_param` {param: "filter.…"}
    Filter.empty, Filter.to_json()         nothing set; the stored form (`v: 1`, every key)
    map_types(type_code, config) -> int8[n]
        the type of every record: the place of its type in `TYPES` (ORIG 0, CRM 1, BLANK 2, DUP_F 3, DUP_C 4,
        DUP_P 5, UMP 6) by `config["types"]` {code: type}; an empty code — ORIG when `empty_is_orig` (the
        default); `UNMAPPED` (−1) — the code is not in the dictionary: the record takes part in nothing
    row_mask(recs, flt, types) -> bool[n] | None        the rows the filter leaves; None — all of them

The model (every key is optional; the conditions are joined by «И»):
    labs[], batches[]          names of laboratories, batches — a record stays when its value is among them
    crm[]                      names of CRM: of the records of the type CRM those stay whose CRM is among them;
                               the records of the other types (blanks, duplicates, ORIG) are not cut by it
    types[]                    types after the mapping (`TYPES`)
    date_from, date_to         «ГГГГ-ММ-ДД» or «ГГГГ-ММ-ДД чч:мм[:сс]»; both borders belong to the range, a
                               date without the time of day as `date_to` takes the whole day
    hole_prefix                the beginning of the name of the hole
    where[]                    up to 20 conditions «любое поле» {field, op, value}: a text field — eq, ne, in,
                               prefix, contains, empty; a number and a date — eq, ge, le, between ([from, to]),
                               empty
A field of `where` is a column of the records (`sample_id`, `hole`, `lab`, `seq`, `date`, `v:AU`, `x:Zone` …),
its name without case; the bare name of another field («Zone») or of an element («AU» — its value) is taken
too. Texts are compared without case and edge spaces; a record without a value satisfies only `empty` and `ne`
(of a text). A record without a date is outside any range of dates. Not a row filter: the element (it picks
the columns).

The mask looks only at the fields of the record itself (ADR 0020 §5; its type is one of them — the code of the
file by the dictionary of the settings): a calculation by the filter and one over `recs.take(mask)` get the
same rows in the same order. Text conditions are decided on the labels of a
dictionary column and spread by the codes; identifiers (`sample_id`, `parent_id`) go through pyarrow. Three
conditions over 1 million records — about 0.01 s.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from . import records as r
from .limits import QaqcError

VERSION = 1
MAX_WHERE = 20
MAX_VALUES = 5000  # values of one list (laboratories, batches, CRM, `in`)
TYPES = ("ORIG", "CRM", "BLANK", "DUP_F", "DUP_C", "DUP_P", "UMP")
UNMAPPED = -1
TEXT_OPS = ("eq", "ne", "in", "prefix", "contains", "empty")
RANGE_OPS = ("eq", "ge", "le", "between", "empty")
_KEYS = ("v", "labs", "batches", "date_from", "date_to", "crm", "types", "hole_prefix", "where")
_EPOCH = datetime(1970, 1, 1)
_DAY = 86_400_000.0


def bad(param: str, message: str, hint: str) -> QaqcError:
    return QaqcError("bad_param", message, hint, param=f"filter.{param}" if param else "filter")


def name_key(text: Any) -> str:
    """A name as it is compared: without edge spaces and case."""
    return str(text).strip().casefold()


@dataclass(frozen=True)
class Moment:
    """A border of dates: milliseconds since 1970 and whether the text had no time of day (the whole day)."""

    text: str
    ms: float
    whole_day: bool

    @property
    def end(self) -> float:
        """The first moment after the border when it is an upper one of a whole day."""
        return self.ms + _DAY if self.whole_day else self.ms


@dataclass(frozen=True)
class Where:
    field: str
    op: str
    value: Any = None  # text ops: str or tuple[str, ...]; range ops: float, Moment or a pair of them


@dataclass(frozen=True)
class Filter:
    """The filter of the records, checked (see the module docstring)."""

    labs: tuple[str, ...] = ()
    batches: tuple[str, ...] = ()
    date_from: Moment | None = None
    date_to: Moment | None = None
    crm: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    hole_prefix: str | None = None
    where: tuple[Where, ...] = ()

    @property
    def empty(self) -> bool:
        return self == _NOTHING

    def to_json(self) -> dict[str, Any]:
        def plain(v: Any) -> Any:
            if isinstance(v, Moment):
                return v.text
            return [plain(x) for x in v] if isinstance(v, tuple) else v

        where = [{"field": w.field, "op": w.op} | ({} if w.op == "empty" else {"value": plain(w.value)})
                 for w in self.where]  # fmt: skip
        return {
            "v": VERSION, "labs": list(self.labs), "batches": list(self.batches),
            "date_from": self.date_from.text if self.date_from else None,
            "date_to": self.date_to.text if self.date_to else None,
            "crm": list(self.crm), "types": list(self.types), "hole_prefix": self.hole_prefix, "where": where,
        }  # fmt: skip


_NOTHING = Filter()


# --- parsing --------------------------------------------------------------------------------------------------


def _names(raw: Any, param: str, title: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple) or len(raw) > MAX_VALUES:
        raise bad(param, f"{title} — список названий (не больше {MAX_VALUES}).", "Передайте список строк.")
    out: dict[str, None] = {}
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, str | int | float) or not str(v).strip():
            raise bad(param, f"{title}: «{v}» — не название.", "Передайте список непустых строк.")
        out[str(v).strip()] = None
    return tuple(out)


def _moment(raw: Any, param: str) -> Moment | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    hint = "Формат даты — ГГГГ-ММ-ДД или ГГГГ-ММ-ДД чч:мм."
    if not isinstance(raw, str):
        raise bad(param, "Дата фильтра должна быть текстом.", hint)
    text = raw.strip()
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        raise bad(param, f"«{text}» — не дата.", hint) from None
    if at.tzinfo is not None:
        raise bad(param, f"«{text}»: дата с часовым поясом.", "Даты записей — без пояса; уберите его.")
    return Moment(text, float((at - _EPOCH) // timedelta(milliseconds=1)), len(text) <= 10)


def _number(raw: Any, param: str) -> float:
    value = raw
    if isinstance(raw, str):
        try:
            value = float(raw.strip().replace(",", "."))
        except ValueError:
            value = None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise bad(param, f"«{raw}» — не число.", "Для числового поля задайте число.")
    return float(value)


def _where(raw: Any, i: int) -> Where:
    param = f"where[{i}]"
    if not isinstance(raw, Mapping):
        raise bad(param, "Условие фильтра — это {field, op, value}.", "Задайте поле, операцию и значение.")
    field, op = raw.get("field"), raw.get("op")
    if not isinstance(field, str) or not field.strip():
        raise bad(f"{param}.field", "В условии фильтра не задано поле.", "Выберите поле из списка.")
    if op not in TEXT_OPS and op not in RANGE_OPS:
        ops = ", ".join(dict.fromkeys(TEXT_OPS + RANGE_OPS))
        raise bad(f"{param}.op", f"Условие «{field}»: неизвестная операция «{op}».", f"Операции: {ops}.")
    value = raw.get("value")
    if op == "empty":
        return Where(field.strip(), op)
    if op in ("in", "between"):
        most, least = (2, 2) if op == "between" else (MAX_VALUES, 1)
        if not isinstance(value, list | tuple) or not least <= len(value) <= most:
            what = "два значения [от, до]" if op == "between" else f"список значений (не больше {MAX_VALUES})"
            raise bad(f"{param}.value", f"Условие «{field}»: нужно {what}.", "Задайте значения условия.")
        items = tuple(value)
    else:
        items = (value,)
    for v in items:
        if isinstance(v, bool) or not isinstance(v, str | int | float) or not str(v).strip():
            raise bad(f"{param}.value", f"Условие «{field}»: не задано значение.", "Задайте значение условия.")
    return Where(field.strip(), str(op), items if op in ("in", "between") else items[0])


def parse_filter(raw: Any) -> Filter:
    """The filter of a request checked: `bad_param` for what is not the model `v: 1`."""
    if raw is None or isinstance(raw, Filter):
        return raw or _NOTHING
    if not isinstance(raw, Mapping):
        raise bad("", "Фильтр — это набор условий {labs, batches, …}.", "Сбросьте фильтр и задайте его заново.")
    for key in raw:
        if key not in _KEYS:
            raise bad(str(key), f"Неизвестное условие фильтра «{key}».", f"Условия: {', '.join(_KEYS[1:])}.")
    if raw.get("v", VERSION) != VERSION:
        raise bad("v", "Фильтр записан другой версией программы.", "Сбросьте фильтр и задайте его заново.")
    types = tuple(t.upper() for t in _names(raw.get("types"), "types", "Типы"))
    for t in types:
        if t not in TYPES:
            raise bad("types", f"Неизвестный тип «{t}».", f"Типы: {', '.join(TYPES)}.")
    where = raw.get("where") or ()
    if not isinstance(where, list | tuple):
        raise bad("where", "Условия «любое поле» — это список.", "Задайте условия заново.")
    if len(where) > MAX_WHERE:
        raise bad("where", f"Условий «любое поле» — {len(where)}, больше {MAX_WHERE}.", "Уберите лишние условия.")
    prefix = raw.get("hole_prefix")
    if prefix is not None and not isinstance(prefix, str):
        raise bad("hole_prefix", "Префикс скважины должен быть текстом.", "Задайте начало названия скважины.")
    start, end = _moment(raw.get("date_from"), "date_from"), _moment(raw.get("date_to"), "date_to")
    if start is not None and end is not None and start.ms > end.end:
        raise bad("date_from", "Начало диапазона дат позже конца.", "Поменяйте даты местами.")
    return Filter(
        labs=_names(raw.get("labs"), "labs", "Лаборатории"),
        batches=_names(raw.get("batches"), "batches", "Партии"),
        date_from=start,
        date_to=end,
        crm=_names(raw.get("crm"), "crm", "CRM"),
        types=tuple(dict.fromkeys(types)),
        hole_prefix=prefix.strip() or None if prefix else None,
        where=tuple(_where(w, i) for i, w in enumerate(where)),
    )


# --- types ----------------------------------------------------------------------------------------------------


def map_types(type_code: r.Text, config: Mapping[str, Any]) -> np.ndarray:
    """The type of every record by the dictionary of codes (see the module docstring); the same rule as
    `config.type_of`: a code is compared trimmed and in upper case."""
    table = {str(code).strip().upper(): kind for code, kind in dict(config.get("types") or {}).items()}
    empty = TYPES.index("ORIG") if config.get("empty_is_orig", True) else UNMAPPED
    by_label = np.full(len(type_code.labels) + 1, UNMAPPED, dtype=np.int8)
    by_label[0] = empty
    for i, label in enumerate(type_code.labels):
        key = label.strip().upper()
        kind = table.get(key)
        by_label[i + 1] = (TYPES.index(kind) if kind in TYPES else UNMAPPED) if key else empty
    return np.asarray(by_label[type_code.codes + 1])


# --- the mask -------------------------------------------------------------------------------------------------


def _by_labels(text: r.Text, test: Any, empty: bool = False) -> np.ndarray:
    """The rows whose label passes `test(key)`; the rows without a value — `empty`."""
    ok = np.zeros(len(text.labels) + 1, dtype=np.bool_)
    ok[0] = empty
    for i, label in enumerate(text.labels):
        ok[i + 1] = bool(test(name_key(label)))
    return np.asarray(ok[text.codes + 1])


def _among(text: r.Text, names: tuple[str, ...]) -> np.ndarray:
    wanted = {name_key(v) for v in names}
    return _by_labels(text, wanted.__contains__)


def _text_rows(text: r.Text, w: Where) -> np.ndarray:
    if w.op == "empty":
        return _by_labels(text, lambda key: not key, empty=True)
    if w.op in ("eq", "ne", "in"):
        wanted = {name_key(v) for v in (w.value if w.op == "in" else (w.value,))}
        return _by_labels(text, (lambda key: key not in wanted) if w.op == "ne" else wanted.__contains__, w.op == "ne")
    part = name_key(w.value)
    return _by_labels(text, (lambda key: key.startswith(part)) if w.op == "prefix" else (lambda key: part in key))


def _id_rows(ids: Any, w: Where) -> np.ndarray:
    """A text condition over identifiers (a pyarrow string array, not a dictionary)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    trimmed = pc.utf8_trim_whitespace(ids)
    blank = pc.fill_null(pc.equal(pc.utf8_length(trimmed), 0), True)
    if w.op == "empty":
        out = blank
    elif w.op in ("eq", "ne", "in"):
        wanted = pa.array([str(v).strip().lower() for v in (w.value if w.op == "in" else (w.value,))], type=ids.type)
        out = pc.fill_null(pc.is_in(pc.utf8_lower(trimmed), value_set=wanted), False)
        out = pc.invert(out) if w.op == "ne" else out
    elif w.op == "prefix":
        out = pc.fill_null(pc.starts_with(trimmed, pattern=str(w.value).strip(), ignore_case=True), False)
    else:
        out = pc.fill_null(pc.match_substring(trimmed, pattern=str(w.value).strip(), ignore_case=True), False)
    return np.asarray(out.to_numpy(zero_copy_only=False), dtype=np.bool_)


def _range_rows(values: np.ndarray, w: Where, param: str, dates: bool) -> np.ndarray:
    """A condition over numbers or dates (float64, NaN — no value)."""
    if w.op == "empty":
        return np.isnan(values)

    def border(raw: Any) -> tuple[float, float]:
        """The least and the first-after value a border stands for (a date without the time — its whole day)."""
        if dates:
            at = _moment(str(raw), param)
            if at is None:
                raise bad(param, "Не задана дата условия.", "Формат даты — ГГГГ-ММ-ДД.")
            return at.ms, at.end if at.whole_day else math.nextafter(at.ms, math.inf)
        number = _number(raw, param)
        return number, math.nextafter(number, math.inf)

    with np.errstate(invalid="ignore"):
        if w.op == "between":
            (low, _), (_, high) = border(w.value[0]), border(w.value[1])
            return np.asarray((values >= low) & (values < high))
        low, high = border(w.value)
        if w.op == "ge":
            return np.asarray(values >= low)
        if w.op == "le":
            return np.asarray(values < high)
        return np.asarray((values >= low) & (values < high))


def resolve_field(schema: r.Schema, field: str) -> str | None:
    """The column a field of a condition names: the column itself, its name without case, the bare name of
    another field or of an element (its value); None — no such field."""
    columns = schema.columns()
    if field in columns:
        return field
    key = field.casefold()
    for column in columns:
        if column.casefold() == key:
            return column
    for column in (c for c in columns if c.startswith(("x:", "v:"))):
        if column[2:].casefold() == key:
            return column
    return None


def _where_rows(recs: r.Records, w: Where, i: int) -> np.ndarray:
    column = resolve_field(recs.schema, w.field)
    if column is None:
        fields = [c for c in recs.schema.columns() if c.split(":")[0] in ("x", "v") or ":" not in c]
        raise bad(f"where[{i}].field", f"В данных нет поля «{w.field}».", f"Поля: {', '.join(fields[:40])}.")
    kind = recs.schema.kind(column)
    text = kind in ("text", "id")
    if w.op not in (TEXT_OPS if text else RANGE_OPS):
        ops = ", ".join(TEXT_OPS if text else RANGE_OPS)
        what = "текстового" if text else "числового" if kind != "date" else "поля дат"
        raise bad(f"where[{i}].op", f"Поле «{w.field}»: операция «{w.op}» не для {what} поля.", f"Операции: {ops}.")
    if kind == "text":
        return _text_rows(recs.text(column), w)
    if kind == "id":
        return _id_rows(recs.ids(column), w)
    values = recs.date() if kind == "date" else np.asarray(recs.column(column), dtype=np.float64)
    if kind == "index":
        values = np.where(values < 0, np.nan, values)
    return _range_rows(values, w, f"where[{i}].value", kind == "date")


def row_mask(recs: r.Records, flt: Filter | None, types: np.ndarray) -> np.ndarray | None:
    """The rows the filter leaves (see the module docstring); None — the filter is empty: all the rows."""
    if flt is None or flt.empty:
        return None
    mask = np.ones(recs.n, dtype=np.bool_)
    if flt.types:
        wanted = np.zeros(len(TYPES) + 1, dtype=np.bool_)
        wanted[[TYPES.index(t) + 1 for t in flt.types]] = True
        mask &= wanted[types + 1]
    for names, column in ((flt.labs, r.LAB), (flt.batches, r.BATCH)):
        if names:
            mask &= _among(recs.text(column), names)
    if flt.crm:  # of the CRM samples only: a blank, a duplicate, an ORIG has no CRM to be chosen by
        mask &= (types != TYPES.index("CRM")) | _among(recs.text(r.CRM_ID), flt.crm)
    if flt.hole_prefix:
        part = name_key(flt.hole_prefix)
        mask &= _by_labels(recs.text(r.HOLE), lambda key: key.startswith(part))
    if flt.date_from is not None or flt.date_to is not None:
        date = recs.date()
        with np.errstate(invalid="ignore"):
            if flt.date_from is not None:
                mask &= date >= flt.date_from.ms
            if flt.date_to is not None:
                end = flt.date_to
                mask &= (date < end.end) if end.whole_day else (date <= end.ms)
    for i, w in enumerate(flt.where):
        mask &= _where_rows(recs, w, i)
    return mask
