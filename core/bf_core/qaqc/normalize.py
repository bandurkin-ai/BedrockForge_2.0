"""A table of a file (or of a drillhole set) → QAQC records (ADR 0020 §2, ТЗ 8.4.1).

    mapping = auto_mapping([(name, kind), …])            # the form of «Импорт данных QAQC», then edited by hand
    made = normalize(table, mapping, tick)                # table: {column name: formats.table.Column}
    made.records, made.counts, made.notes

Mapping (JSON `v: 1`): `sample_id`, `type` (required), `hole`, `parent_id`, `crm_id`, `lab`, `batch`, `date`, `seq`
— the column of the table or null; `elements` — [{column, name, unit, dl_column?}] (≥ 1, ≤ 60); `extra` —
[column] (≤ 40), the fields of «любое поле» of the filter. The columns to read as text are `text_columns(mapping)`
(identifiers, codes, dates, values: «<0,01» keeps its flag only when read as text).

Numbers — `formats/numbers` («<0,01» → 0,005 with `q = 1`, «>x» → x with `q = 2`; a missing value is NaN, never
0). Dates: `ГГГГ-ММ-ДД[ чч:мм[:сс]]` (also `T`), `ДД.ММ.ГГГГ[ чч:мм[:сс]]`, `ДД/ММ/ГГГГ[ чч:мм[:сс]]`, a date
of Excel (a number of days; 1900 system); what is not parsed is empty and counted (`bad_dates`). The parent
sample: PARENT_ID → the first row with that SAMPLE_ID (trimmed, case-insensitive); its values go to `p:E`, its
laboratory to `parent_lab`; not found (or the row itself) — `parent_row = −1`, `no_parent`; repeated SAMPLE_ID —
`dup_ids`. The previous sample of a batch: within (LAB, BATCH) by (SEQ, row of the file) — the previous record of
any type (`prev_row`, `b:E`); SEQ missing goes after the numbers; no BATCH — no previous sample.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.formats import numbers as nm
from bf_core.formats.table import Column

from . import records as r

ROLES = ("sample_id", "hole", "type", "parent_id", "crm_id", "lab", "batch", "date", "seq")
ROLE_COLUMNS = {
    "sample_id": r.SAMPLE_ID, "hole": r.HOLE, "type": r.TYPE_CODE, "parent_id": r.PARENT_ID, "crm_id": r.CRM_ID,
    "lab": r.LAB, "batch": r.BATCH,
}  # fmt: skip
ROLE_TITLES = {
    "sample_id": "SAMPLE_ID", "hole": "HOLE", "type": "TYPE", "parent_id": "PARENT_ID", "crm_id": "CRM_ID",
    "lab": "LAB", "batch": "BATCH", "date": "DATE", "seq": "SEQ",
}  # fmt: skip
_ALIASES = {
    "sample_id": ("sampleid", "sample", "sampleno", "samplenumber", "sampnum", "id", "проба", "номерпробы",
                  "idпробы", "шифрпробы"),
    "hole": ("hole", "holeid", "bhid", "dhid", "скважина", "скв"),
    "type": ("type", "qctype", "sampletype", "qc", "тип", "типпробы"),
    "parent_id": ("parentid", "parent", "originalid", "origid", "original", "исходная", "исходнаяпроба"),
    "crm_id": ("crmid", "crm", "standard", "standardid", "stdid", "std", "стандарт", "осо"),
    "lab": ("lab", "laboratory", "labcode", "лаборатория", "лаб"),
    "batch": ("batch", "batchid", "batchno", "job", "jobno", "партия", "номерпартии"),
    "date": ("date", "assaydate", "dateanalysis", "дата", "датаанализа"),
    "seq": ("seq", "sequence", "order", "position", "pos", "порядок", "номервпартии"),
}  # fmt: skip
_UNITS = {
    "ppm": "ppm", "ppb": "ppb", "%": "%", "pct": "%", "percent": "%", "г/т": "г/т", "g/t": "г/т", "gpt": "г/т",
    "gt": "г/т", "гт": "г/т", "ppt": "ppt",
}  # fmt: skip
_UNIT_SPLIT = re.compile(r"^\s*(.+?)\s*(?:[,_(\[]|\s)\s*([^,_()\[\]\s]+)\s*[)\]]?\s*$")
_DL_NAME = re.compile(r"^(?:dl[_\s-]*(.+)|(.+?)[_\s-]*dl)$", re.IGNORECASE)
MAX_NAME = 64


def _key(text: str) -> str:
    return re.sub(r"[\s_\-.№#]+", "", text.casefold())


def bad_param(message: str, hint: str, param: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, extra={"param": param})


# --- the mapping ------------------------------------------------------------------------------------------------


def split_unit(column: str) -> tuple[str, str | None]:
    """«AU_ppm» → (AU, ppm), «Cu, %» → (Cu, %), «Au (г/т)» → (Au, г/т), «AG» → (AG, None)."""
    m = _UNIT_SPLIT.match(column)
    if m is not None:
        unit = _UNITS.get(m.group(2).casefold())
        if unit is not None:
            return m.group(1).strip(), unit
    if column.endswith("%") and len(column) > 1:
        return column[:-1].strip(" _,"), "%"
    return column.strip(), None


def auto_mapping(columns: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """The mapping proposed for columns [(name, kind 'number' | 'text')]: roles by names, the other number columns —
    elements (unit from the name), «AU_DL» / «DL_AU» — DL of AU by field."""
    out: dict[str, Any] = {"v": 1, **dict.fromkeys(ROLES)}
    taken: set[str] = set()
    for role in ROLES:
        for name, _kind in columns:
            if name not in taken and _key(name) in {_key(a) for a in (*_ALIASES[role], ROLE_TITLES[role])}:
                out[role] = name
                taken.add(name)
                break
    dl_of: dict[str, str] = {}
    for name, kind in columns:
        m = _DL_NAME.match(name) if kind == "number" and name not in taken else None
        if m is not None:
            dl_of[_key(split_unit(m.group(1) or m.group(2))[0])] = name
    elements = []
    for name, kind in columns:
        if kind != "number" or name in taken or name in dl_of.values():
            continue
        element, unit = split_unit(name)
        elements.append({"column": name, "name": element[:MAX_NAME], "unit": unit,
                         "dl_column": dl_of.get(_key(element))})  # fmt: skip
    out["elements"] = elements[: r.MAX_ELEMENTS]
    out["extra"] = []
    return out


@dataclass(frozen=True)
class ElementMap:
    column: str
    name: str
    unit: str | None
    dl_column: str | None


@dataclass(frozen=True)
class Mapping_:
    roles: dict[str, str | None]
    elements: tuple[ElementMap, ...]
    extra: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "v": 1, **self.roles,
            "elements": [{"column": e.column, "name": e.name, "unit": e.unit, "dl_column": e.dl_column}
                         for e in self.elements],
            "extra": list(self.extra),
        }  # fmt: skip


def parse_mapping(raw: Any, columns: Iterable[str]) -> Mapping_:
    """The mapping checked against the columns of the table: 422 `no_sample_id`, `no_type`, `no_elements`,
    `field_not_found {param}`, `bad_param {param}`, `too_many_elements`, `too_many_fields`."""
    have = list(columns)
    names = set(have)
    if not isinstance(raw, dict):
        raise bad_param("Нет сопоставления колонок.", "Откройте форму импорта и сопоставьте колонки.", "mapping")

    def column(value: Any, param: str) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or value not in names:
            raise bad_param(f"Колонки «{value}» нет в таблице.", "Выберите колонку из списка таблицы.", param,
                            code="field_not_found")  # fmt: skip
        return value

    roles = {role: column(raw.get(role), role) for role in ROLES}
    if roles["sample_id"] is None:
        raise bad_param("Не выбрана колонка SAMPLE_ID.", "Укажите колонку с номером пробы.", "sample_id",
                        code="no_sample_id")  # fmt: skip
    if roles["type"] is None:
        raise bad_param("Не выбрана колонка TYPE.", "Укажите колонку с типом пробы (ORIG, CRM, BLANK, …).", "type",
                        code="no_type")  # fmt: skip
    items = raw.get("elements") or []
    if not isinstance(items, list) or not items:
        raise bad_param("Не выбраны элементы.", "Отметьте хотя бы одну колонку содержаний как элемент.", "elements",
                        code="no_elements")  # fmt: skip
    extra_raw = raw.get("extra") or []
    if not isinstance(extra_raw, list):
        raise bad_param("Неверный список прочих полей.", "Выберите прочие поля заново.", "extra")
    r.check_size(0, len(items), len(extra_raw))
    elements: list[ElementMap] = []
    seen: set[str] = set()
    used = {c for c in roles.values() if c is not None}
    for i, item in enumerate(items):
        param = f"elements[{i}]"
        if not isinstance(item, dict):
            raise bad_param("Неверное описание элемента.", "Сопоставьте элементы заново.", param)
        col = column(item.get("column"), f"{param}.column")
        if col is None:
            raise bad_param("У элемента не выбрана колонка.", "Выберите колонку содержаний.", f"{param}.column")
        name = str(item.get("name") or split_unit(col)[0]).strip()
        if not name or len(name) > MAX_NAME or ":" in name:
            raise bad_param(f"Недопустимое имя элемента «{name}».",
                            f"Имя — от 1 до {MAX_NAME} символов, без двоеточия.", f"{param}.name")  # fmt: skip
        if name.casefold() in seen:
            raise bad_param(f"Элемент «{name}» указан дважды.", "Дайте элементам разные имена.", f"{param}.name")
        seen.add(name.casefold())
        unit = item.get("unit")
        unit = _UNITS.get(str(unit).strip().casefold(), str(unit).strip()) if unit not in (None, "") else None
        dl_col = column(item.get("dl_column"), f"{param}.dl_column")
        elements.append(ElementMap(col, name, unit, dl_col))
        used.add(col)
    extra: list[str] = []
    for i, value in enumerate(extra_raw):
        col = column(value, f"extra[{i}]")
        if col is not None and col not in extra and col not in used:
            extra.append(col)
    return Mapping_(roles, tuple(elements), tuple(extra))


def text_columns(mapping: Mapping_) -> tuple[str, ...]:
    """The columns a reader keeps as text: identifiers and codes as written, dates, «<0,01» with its flag."""
    out = [c for role, c in mapping.roles.items() if c is not None and role != "seq"]
    for e in mapping.elements:
        out += [e.column] + ([e.dl_column] if e.dl_column else [])
    return tuple(dict.fromkeys(out))


# --- cells -----------------------------------------------------------------------------------------------------


def _strings(col: Column) -> Any:
    """A column as a pyarrow string array: trimmed, empty → null (numbers as identifiers: 12.0 → «12»)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    if col.kind == "text":
        arr = col.values.combine_chunks() if isinstance(col.values, pa.ChunkedArray) else col.values
        arr = arr.cast(pa.string())
    else:
        arr = pa.array(col.text(), type=pa.string())
    arr = pc.utf8_trim_whitespace(arr)
    return pc.if_else(pc.equal(pc.utf8_length(arr), 0), pa.scalar(None, pa.string()), arr)


@dataclass
class Parsed:
    values: np.ndarray
    flags: np.ndarray
    numbers: int = 0
    missing: int = 0
    below: int = 0
    above: int = 0
    bad: int = 0


def parse_values(col: Column) -> Parsed:
    """float64 values and `q` flags of a column of contents (a number column has no «<» flags left: 0)."""
    if col.kind == "number":
        values = np.ascontiguousarray(col.values, dtype=np.float64)
        miss = int(np.isnan(values).sum())
        return Parsed(values, np.zeros(len(values), np.uint8), len(values) - miss, miss,
                      bad=len(col.bad_cells or ()))  # fmt: skip
    cells = col.values.to_pylist()
    values, kinds = nm.classify(cells)
    flags = np.zeros(len(values), np.uint8)
    flags[kinds == nm.BELOW] = r.Q_BELOW
    flags[kinds == nm.ABOVE] = r.Q_ABOVE
    c = np.bincount(kinds, minlength=8)
    return Parsed(
        values, flags, numbers=int(c[nm.NUMBER] + c[nm.IDLIKE] + c[nm.BELOW] + c[nm.ABOVE]),
        missing=int(c[nm.EMPTY] + c[nm.MARKER]), below=int(c[nm.BELOW]), above=int(c[nm.ABOVE]), bad=int(c[nm.BAD]),
    )  # fmt: skip


_EPOCH = datetime(1970, 1, 1)
_EXCEL = datetime(1899, 12, 30)
_DATE_FORMS = (
    re.compile(
        r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
        r"(?:[ T](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.(?P<f>\d{1,6}))?)?)?$"
    ),
    re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})(?: (?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?$"),
    re.compile(r"^(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})(?: (?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?$"),
)  # fmt: skip
_EXCEL_MAX = 2_958_466  # 9999-12-31


def excel_ms(serial: float) -> float:
    """A date of Excel (1900 system) → ms since 1970; NaN — not a date (≤ 0, 60 — 29.02.1900 of Excel, ≥ 10000)."""
    if not math.isfinite(serial) or serial <= 0 or serial >= _EXCEL_MAX or 60 <= serial < 61:
        return math.nan
    days = serial if serial >= 61 else serial + 1  # Excel counts a 29 February 1900 that never was
    ms = round(days * 86_400_000)
    return float((_EXCEL - _EPOCH) // timedelta(milliseconds=1) + ms)


def date_ms(text: str) -> float:
    """One written date → ms since 1970 (the date as written, no time zone); NaN — not parsed."""
    for form in _DATE_FORMS:
        m = form.match(text)
        if m is None:
            continue
        g = m.groupdict()
        try:
            dt = datetime(int(g["y"]), int(g["m"]), int(g["d"]), int(g.get("H") or 0), int(g.get("M") or 0),
                          int(g.get("S") or 0), int((g.get("f") or "0").ljust(6, "0")))  # fmt: skip
        except ValueError:
            return math.nan
        return float((dt - _EPOCH) // timedelta(milliseconds=1))
    value, kind = nm.parse_value(text)
    return excel_ms(value) if kind == "number" and value is not None else math.nan


def parse_dates(col: Column) -> tuple[np.ndarray, int, int]:
    """(ms float64 [n], empty, not parsed) — each distinct text is parsed once."""
    import pyarrow.compute as pc

    if col.kind == "number":
        values = np.asarray(col.values, dtype=np.float64)
        out = np.array([excel_ms(v) for v in values.tolist()], dtype=np.float64)
        empty = int(np.isnan(values).sum())
        return out, empty, int(np.isnan(out).sum()) - empty
    enc = pc.dictionary_encode(_strings(col))
    table = np.array([date_ms(s) for s in enc.dictionary.to_pylist()] + [math.nan], dtype=np.float64)
    idx = enc.indices.fill_null(len(table) - 1).to_numpy(zero_copy_only=False)
    out = table[idx]
    empty = int(enc.indices.null_count)
    return out, empty, int(np.isnan(out).sum()) - empty


def parse_seq(col: Column) -> tuple[np.ndarray, int]:
    if col.kind == "number":
        return np.ascontiguousarray(col.values, dtype=np.float64), len(col.bad_cells or ())
    parsed = nm.parse_numbers(col.values)
    return parsed.values, parsed.bad


# --- normalization -------------------------------------------------------------------------------------------------


@dataclass
class Normalized:
    records: r.Records
    counts: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def _gather(values: np.ndarray, rows: np.ndarray, missing: Any) -> np.ndarray:
    out = values[np.maximum(rows, 0)]
    out[rows < 0] = missing
    return out


def previous_in_batch(lab: np.ndarray, batch: np.ndarray, seq: np.ndarray) -> np.ndarray:
    """int32 [n]: the row before each row in its (LAB, BATCH) by (SEQ, row); −1 — first, or no BATCH."""
    n = len(batch)
    rows = np.arange(n, dtype=np.int64)
    order = np.lexsort((rows, seq, batch, lab))  # SEQ NaN sorts after the numbers
    prev = np.full(n, -1, dtype=np.int32)
    if n > 1:
        a, b_ = order[:-1], order[1:]
        same = (lab[a] == lab[b_]) & (batch[a] == batch[b_]) & (batch[b_] >= 0)
        prev[b_[same]] = a[same]
    return prev


def normalize(
    table: Mapping[str, Column], mapping: Any, tick: Callable[[float, str], None] | None = None
) -> Normalized:
    """The records of a table by its mapping (see the module docstring) with the counts of the import."""
    import pyarrow as pa
    import pyarrow.compute as pc

    def beat(f: float, text: str) -> None:
        if tick is not None:
            tick(f, text)

    m = mapping if isinstance(mapping, Mapping_) else parse_mapping(mapping, table.keys())
    n = len(next(iter(table.values()))) if table else 0
    r.check_size(n, len(m.elements), len(m.extra))
    beat(0.0, "Номера проб и коды")
    cols: dict[str, Any] = {}
    ids = _strings(table[m.roles["sample_id"]])  # type: ignore[index]
    cols[r.SAMPLE_ID] = ids
    parent = _strings(table[m.roles["parent_id"]]) if m.roles["parent_id"] else pa.nulls(n, pa.string())
    cols[r.PARENT_ID] = parent
    for role in ("hole", "type", "crm_id", "lab", "batch"):
        c = m.roles[role]
        cols[ROLE_COLUMNS[role]] = r.Text.of(_strings(table[c])) if c else r.Text(np.full(n, -1, np.int32), ())
    counts: dict[str, Any] = {"rows": n}
    notes: list[str] = []
    beat(0.15, "Даты и порядок в партии")
    if m.roles["date"]:
        cols[r.DATE], counts["empty_dates"], counts["bad_dates"] = parse_dates(table[m.roles["date"]])
    else:
        cols[r.DATE], counts["empty_dates"], counts["bad_dates"] = np.full(n, np.nan), n, 0
    if m.roles["seq"]:
        cols[r.SEQ], bad_seq = parse_seq(table[m.roles["seq"]])
    else:
        cols[r.SEQ], bad_seq = np.full(n, np.nan), 0
    counts["empty_seq"] = int(np.isnan(cols[r.SEQ]).sum()) - bad_seq
    beat(0.3, "Исходные пробы")
    keys = pc.utf8_lower(ids)
    pkeys = pc.utf8_lower(parent)
    found = pc.index_in(pkeys, value_set=keys, skip_nulls=True)
    parent_row = found.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int32)
    self_ref = parent_row == np.arange(n, dtype=np.int32)
    parent_row[self_ref] = -1
    counts["no_parent"] = int(n - parent.null_count - int((parent_row >= 0).sum()))
    counts["empty_ids"] = int(ids.null_count)
    counts["dup_ids"] = int(n - ids.null_count - len(pc.unique(keys.drop_null())))
    cols[r.PARENT_ROW] = parent_row
    lab = cols[r.LAB]
    cols[r.PARENT_LAB] = r.Text(_gather(lab.codes, parent_row, -1), lab.labels)
    prev_row = previous_in_batch(lab.codes, cols[r.BATCH].codes, cols[r.SEQ])
    cols[r.PREV_ROW] = prev_row
    elements: list[r.Element] = []
    per: dict[str, dict[str, int]] = {}
    below = above = bad = bad_seq
    for i, e in enumerate(m.elements):
        beat(0.4 + 0.5 * i / len(m.elements), f"Элемент {e.name}")
        got = parse_values(table[e.column])
        cols[r.v(e.name)] = got.values
        cols[r.q(e.name)] = got.flags
        cols[r.p(e.name)] = _gather(got.values, parent_row, np.nan)
        cols[r.b(e.name)] = _gather(got.values, prev_row, np.nan)
        stats = {"numbers": got.numbers, "missing": got.missing, "below": got.below, "above": got.above,
                 "bad": got.bad}  # fmt: skip
        if e.dl_column:
            dl_vals = parse_values(table[e.dl_column])
            cols[r.dl(e.name)] = dl_vals.values
            stats["bad_dl"] = dl_vals.bad
            bad += dl_vals.bad
        per[e.name] = stats
        below, above, bad = below + got.below, above + got.above, bad + got.bad
        elements.append(r.Element(e.name, e.unit, e.dl_column is not None))
    extra: list[r.Extra] = []
    for name in m.extra:
        col = table[name]
        if col.kind == "number":
            cols[r.x(name)] = np.ascontiguousarray(col.values, dtype=np.float64)
        else:
            cols[r.x(name)] = r.Text.of(_strings(col))
        extra.append(r.Extra(name, r.NUMBER if col.kind == "number" else r.TEXT))
    counts.update(below_dl=below, above_dl=above, bad_numbers=bad, elements=per)
    beat(0.95, "Записи")
    recs = r.Records.of(r.Schema(tuple(elements), tuple(extra)), cols)
    notes += _notes(counts)
    return Normalized(recs, counts, notes)


def _notes(c: dict[str, Any]) -> list[str]:
    out = []
    if c["empty_ids"]:
        out.append(f"Строк без SAMPLE_ID: {c['empty_ids']} — они не могут быть исходными пробами.")
    if c["dup_ids"]:
        out.append(f"Повторы SAMPLE_ID: {c['dup_ids']} — исходной считается первая строка с номером.")
    if c["no_parent"]:
        out.append(f"Исходная проба не найдена: {c['no_parent']} — такие пробы без пары.")
    if c["bad_dates"]:
        out.append(f"Неразобранных дат: {c['bad_dates']} — они пустые; порядок — по SEQ и строке файла.")
    if c["bad_numbers"]:
        out.append(f"Значений не числом: {c['bad_numbers']} — они пустые (не ноль).")
    if c["empty_seq"] and c["rows"]:
        out.append(f"Строк без SEQ: {c['empty_seq']} — порядок в партии по строке файла.")
    return out
