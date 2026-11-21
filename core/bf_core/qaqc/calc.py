"""Sections of QAQC of one element (ТЗ 8.4.2; ADR 0020 §5, §10): records + settings + CRM reference + filter +
element → the answers of the tab and the tables of the report, JSON-ready. No database: the caller gives the
records (`qaqc.records`), the settings as `config.get_config` returns them and the rows of the reference.

    calc(section, recs, config, crm, element, flt=None, **params) -> dict
    context(recs, config, crm, element, flt=None, tick=None) -> Context;  run(ctx, section, **params) -> dict
        several sections of one element over one filter: the mask, the types and the kernels are computed once;
        `tick()` — the beat of a job: a long step (the bootstrap of `external`) calls it, a cancel is raised there
    SECTIONS {name: function}        `summary`, `standards`, `blanks`, `batches` here; `duplicates` and
                                     `external` register themselves from `calc_pairs` (their answers — there)
    canonical(answer) -> bytes       the canonical JSON of an answer: keys sorted, numbers by `repr`, UTF-8,
                                     without `counts.rows` — for Т8.4-8 and the hashes of a report
    columns_of(schema, element) -> [column]   what the sections of an element read (`recs.load` — one pass)
    unit_ratio(src, dst) -> (num, den) | None        ppm = г/т = g/t, ppb = мг/т = 10⁻³ ppm, % = 10⁴ ppm

Every answer: {element, unit, counts {rows (records given), filtered (after the filter), control (of them — with
a control type), unmapped (with a code the dictionary does not know), missing (control ones without a value of
the element)}, limits, notes[]} and the keys of the section (ADR 0020 §10); a number that is not there is None —
never 0 and never NaN; dates of a series — milliseconds since 1970, of a batch — ISO texts.
    standards  groups [{crm, lab, state (`ok`, `no_reference`, `unit_mismatch`), n, missing, mu, sd, unit, mean,
               std, bias, bias_class, norm, warn, fail, pair_fails, run_flags, swaps}];
               group={crm, lab} → series {row, date, seq, batch, x, z, status, rule, ma, swap} (None — no such
               group), max_points=N → thinned {shown, total}: every warning and failure and an even choice of
               the rest; samples=True → samples: the series of every group in a row with the column `group`
    blanks     groups [{lab, n, missing, dl, limit, mixed, norm, fail, not_assessed}]; group={lab} → series
               {row, date, seq, batch, x, status, prev, limit}, carryover {n, pearson}; max_points, samples —
               as above (samples=True also puts `carryover` into every group)
    batches    {total, failed, not_assessed, no_batch, offset, batches [{lab, batch, date_from, date_to, n, crm,
               blanks, crm_fail, blank_fail, assessed, status}]} — by the first date; offset, limit (≤ 5000)
    summary    {types {ORIG … UMP: records}, unmapped_codes [{code, n}], standards {…}, blanks {…}, batches {…}}
               — the totals of every section in a line
Statuses: 0 «норма», 1 «предупреждение», 2 «отказ», −1 «не оценён»; `rule` — bits 1 pair, 2 run, 4 swap.

Order (ADR 0020 §5): the types by the dictionary of codes → the mask of the filter → the rows that stay, in the
order of the file → the kernels. Groups go by names: a CRM by its name without case (the spelling of the
reference when it is there), a laboratory by its label; a record whose code is not in the dictionary is counted
and takes part in nothing. The same rows in the same order give the same bits: a calculation by a filter and one
over `recs.take(mask)` differ in `counts.rows` only (Т8.4-8). CRM and DL in other units are brought to the unit
of the element when both are known; another mismatch — `unit_mismatch` for the CRM, no DL for the laboratory,
with a note. Errors — `QaqcError`: `field_not_found` {param: element}, `bad_param`, `unknown_section`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from . import records as r
from .batches import Batches, batches
from .blanks import Blanks, blanks, carryover, effective_dl
from .filters import TYPES, UNMAPPED, Filter, map_types, name_key, parse_filter, row_mask
from .limits import FAIL, NORM, WARN, Limits, QaqcError
from .standards import NO_REFERENCE, STATES, UNIT_MISMATCH, Standards, standards

CRM, BLANK = TYPES.index("CRM"), TYPES.index("BLANK")
MAX_BATCH_PAGE = 5000
_NAMED = 8  # names listed in a note
_UNITS = {
    **dict.fromkeys(("ppm", "г/т", "g/t", "gpt", "gt", "гт"), (1, 1)),
    **dict.fromkeys(("ppb", "мг/т", "mg/t"), (1, 1000)),
    **dict.fromkeys(("%", "pct", "percent"), (10_000, 1)),
}  # the names `normalize` knows and what a reference may be typed with


def unit_ratio(src: Any, dst: Any) -> tuple[int, int] | None:
    """How a value in `src` becomes one in `dst`: × num / den. A unit that is not given is taken for the other
    one; equal names need no table; None — the units cannot be brought together."""
    a, b = (name_key(u).replace(" ", "") if u is not None and str(u).strip() else None for u in (src, dst))
    if a is None or b is None or a == b:
        return 1, 1
    if a not in _UNITS or b not in _UNITS:
        return None
    num, den = _UNITS[a][0] * _UNITS[b][1], _UNITS[a][1] * _UNITS[b][0]
    g = math.gcd(num, den)
    return num // g, den // g


def _convert(value: Any, ratio: tuple[int, int]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number * ratio[0] / ratio[1]  # whole powers of ten: one rounding at most


# --- JSON -------------------------------------------------------------------------------------------------------


def _opt(x: Any) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _floats(a: np.ndarray) -> list[float | None]:
    return [v if v == v else None for v in a.tolist()]


def _moments(ms: np.ndarray) -> list[int | None]:
    return [int(v) if v == v else None for v in ms.tolist()]


def _iso(ms: float) -> str | None:
    if ms != ms:
        return None
    at = datetime(1970, 1, 1) + timedelta(milliseconds=ms)
    return at.date().isoformat() if at.time() == datetime.min.time() else at.isoformat(timespec="seconds")


def _labels(text: r.Text, at: np.ndarray) -> list[str | None]:
    names: list[str | None] = [None, *text.labels]
    return [names[c] for c in (text.codes[at] + 1).tolist()]


def _listed(names: Sequence[str | None]) -> str:
    shown = ", ".join("«" + ("—" if n is None else n) + "»" for n in names[:_NAMED])
    return shown + (f" и ещё {len(names) - _NAMED}" if len(names) > _NAMED else "")


def canonical(answer: Mapping[str, Any]) -> bytes:
    """The canonical JSON of an answer (see the module docstring)."""
    body = dict(answer)
    if isinstance(body.get("counts"), Mapping):
        body["counts"] = {k: v for k, v in body["counts"].items() if k != "rows"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


# --- the context: types, mask, rows --------------------------------------------------------------------------------


@dataclass
class Context:
    """One element of the records under one filter: what every section starts from (see `context`)."""

    recs: r.Records
    element: r.Element
    limits: Limits
    config: Mapping[str, Any]
    crm: Sequence[Mapping[str, Any]]
    flt: Filter
    rows: np.ndarray | None  # the rows of `recs` the filter leaves (None — all), ascending
    types: np.ndarray  # their types (`filters.TYPES`, −1 — not recognized)
    values: np.ndarray  # their values of the element
    counts: dict[str, int]
    notes: list[str]
    memo: dict[str, Any] = field(default_factory=dict)
    tick: Callable[[], None] | None = None  # the beat of a job inside a long step

    def take(self, column: np.ndarray) -> np.ndarray:
        """A column of the records for the rows of the filter."""
        return column if self.rows is None else column[self.rows]

    def of_type(self, kind: int) -> tuple[np.ndarray, np.ndarray]:
        """The records of a type: their places among the rows of the filter and their rows in `recs`."""
        key = f"type:{kind}"
        if key not in self.memo:
            sub = np.flatnonzero(self.types == kind)
            self.memo[key] = (sub, sub if self.rows is None else self.rows[sub])
        return self.memo[key]  # type: ignore[no-any-return]

    def once(self, key: str, make: Callable[[Context], Any]) -> Any:
        if key not in self.memo:
            self.memo[key] = make(self)
        return self.memo[key]


def context(recs: r.Records, config: Mapping[str, Any], crm: Sequence[Mapping[str, Any]], element: Any,
            flt: Any = None, *, tick: Callable[[], None] | None = None) -> Context:  # fmt: skip
    """The types by the dictionary of codes, the mask of the filter and the rows it leaves (ADR 0020 §5)."""
    el = recs.schema.element(str(element)) if isinstance(element, str) else None
    if el is None:
        names = ", ".join(e.name for e in recs.schema.elements) or "нет"
        raise QaqcError("field_not_found", f"В данных QAQC нет элемента «{element}».", f"Элементы данных: {names}.",
                        param="element")  # fmt: skip
    parsed = parse_filter(flt)
    code = recs.text(r.TYPE_CODE)
    every = map_types(code, config)
    mask = row_mask(recs, parsed, every)
    rows = None if mask is None else np.flatnonzero(mask)
    types = every if rows is None else every[rows]
    values = recs.number(r.v(el.name))
    values = values if rows is None else values[rows]
    by_type = np.bincount(types.astype(np.int64) + 1, minlength=len(TYPES) + 1)
    counts = {
        "rows": int(recs.n),
        "filtered": int(types.shape[0]),
        "control": int(by_type[2:].sum()),
        "unmapped": int(by_type[0]),
        "missing": int(np.count_nonzero(np.isnan(values) & (types >= CRM))),
    }
    ctx = Context(recs, el, Limits.parse(config.get("limits")), config, crm, parsed, rows, types, values, counts, [],
                  tick=tick)  # fmt: skip
    ctx.memo["by_type"] = by_type
    unknown = _unmapped_codes(ctx)
    if unknown:
        ctx.notes.append(
            f"Записей с нераспознанным кодом типа: {counts['unmapped']} (коды: "
            f"{_listed([u['code'] for u in unknown])}) — в расчёт они не входят. Задайте соответствие кодов в "
            "«Настройка → Типы»."
        )
    return ctx


def columns_of(schema: r.Schema, element: str) -> list[str]:
    """The columns of the records the sections of an element read (the filter may ask for more)."""
    el = schema.element(element)
    if el is None:
        return []
    own = [r.v(el.name), r.q(el.name), r.b(el.name), r.p(el.name)] + ([r.dl(el.name)] if el.has_dl else [])
    return [r.TYPE_CODE, r.LAB, r.BATCH, r.CRM_ID, r.DATE, r.SEQ, r.PARENT_ROW, r.PARENT_LAB, *own]


def _unmapped_codes(ctx: Context) -> list[dict[str, Any]]:
    code = ctx.recs.text(r.TYPE_CODE)
    lost = ctx.take(code.codes)[ctx.types == UNMAPPED]
    n = np.bincount(lost.astype(np.int64) + 1, minlength=len(code.labels) + 1)
    names: list[str | None] = [None, *code.labels]
    return [{"code": names[i], "n": int(n[i])} for i in np.flatnonzero(n).tolist()]


# --- standards -----------------------------------------------------------------------------------------------------


@dataclass
class _StandardsData:
    kernel: Standards
    at: np.ndarray  # the rows of the CRM samples in `recs`
    names: list[str | None]  # of the CRM, by the code of the kernel
    notes: list[str]


def _crm_table(ctx: Context, label: np.ndarray) -> tuple[np.ndarray, list[str | None], np.ndarray, np.ndarray,
                                                          np.ndarray]:  # fmt: skip
    """The CRM of the samples and of the reference as one list by name: the code of every sample, the names,
    μ and σ in the unit of the element (NaN — not in the reference) and the flags of other units."""
    text = ctx.recs.text(r.CRM_ID)
    used = np.flatnonzero(np.bincount(label.astype(np.int64) + 1, minlength=len(text.labels) + 1))
    key_of = {int(i): (name_key(text.labels[i - 1]) if i else "") for i in used.tolist()}
    element = name_key(ctx.element.name)
    refs = {name_key(row.get("crm_id") or ""): row for row in ctx.crm if name_key(row.get("element") or "") == element}
    refs.pop("", None)  # a row without CRM_ID is the reference of nothing
    keys = sorted(set(key_of.values()) | set(refs))
    place = {key: i for i, key in enumerate(keys)}
    names: list[str | None] = [None] * len(keys)
    for i in reversed(used.tolist()):  # of several spellings in the data — the first by the labels
        names[place[key_of[i]]] = text.labels[i - 1] if i else None
    mu, sd = np.full(len(keys), np.nan), np.full(len(keys), np.nan)
    other = np.zeros(len(keys), dtype=np.bool_)
    for key, row in refs.items():
        names[place[key]] = str(row.get("crm_id", "")).strip() or None
        ratio = unit_ratio(row.get("unit"), ctx.element.unit)
        if ratio is None:
            other[place[key]] = True
        else:
            mu[place[key]], sd[place[key]] = _convert(row.get("mean"), ratio), _convert(row.get("sd"), ratio)
    lut = np.zeros(len(text.labels) + 1, dtype=np.int64)
    for i, key in key_of.items():
        lut[i] = place[key]
    return lut[label.astype(np.int64) + 1], names, mu, sd, other


def _standards_data(ctx: Context) -> _StandardsData:
    recs = ctx.recs
    sub, at = ctx.of_type(CRM)
    code, names, mu, sd, other = _crm_table(ctx, recs.text(r.CRM_ID).codes[at])
    lab = recs.text(r.LAB).codes[at].astype(np.int64) + 1
    kernel = standards(ctx.values[sub], code, lab, recs.date()[at], recs.number(r.SEQ)[at], mu, sd, ctx.limits,
                       mismatch=other)  # fmt: skip
    notes = []
    for state, text in ((NO_REFERENCE, "В справочнике CRM нет паспортных значений элемента {e} для: {names} — "
                         "группы не оценены. Добавьте их в «Настройка → CRM»."),
                        (UNIT_MISMATCH, "Единицы справочника CRM не приводятся к единице элемента {e} для: {names} "
                         "— группы не оценены. Исправьте единицу в «Настройка → CRM».")):  # fmt: skip
        lost = list(dict.fromkeys(names[c] for c in kernel.crm[kernel.state == state].tolist()))
        if lost:
            notes.append(text.format(e=ctx.element.name, names=_listed(lost)))
    return _StandardsData(kernel, at, names, notes)


def _lab_names(ctx: Context) -> list[str | None]:
    return [None, *ctx.recs.text(r.LAB).labels]


def _find(group: Any, keys: Sequence[tuple[str | None, ...]], fields: tuple[str, ...]) -> int | None:
    """The number of the group a request names ({crm, lab} or {lab}); None — no such group now."""
    if not isinstance(group, Mapping):
        raise QaqcError("bad_param", "Группа задаётся названиями: " + ", ".join(fields) + ".",
                        "Выберите группу в таблице.", param="group")  # fmt: skip
    exact = tuple(None if group.get(f) is None else str(group.get(f)) for f in fields)
    if exact in keys:  # the names as the table shows them; else — without case and edge spaces
        return list(keys).index(exact)
    want = tuple(None if v is None else name_key(v) for v in exact)
    for i, key in enumerate(keys):
        if tuple(None if k is None else name_key(k) for k in key) == want:
            return i
    return None


def _thin(status: np.ndarray, params: Mapping[str, Any]) -> tuple[np.ndarray | None, dict[str, int] | None]:
    """The points of a series a chart takes: all of them, or — past `max_points` — every warning and failure
    and an even choice of the rest (in the order of the series)."""
    limit = params.get("max_points")
    if limit is None:
        return None, None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise QaqcError("bad_param", "Предел числа точек графика — целое число от 1.", "Задайте, например, 100000.",
                        param="max_points")  # fmt: skip
    total = int(status.shape[0])
    if total <= limit:
        return None, {"shown": total, "total": total}
    marked = status > NORM
    rest = np.flatnonzero(~marked)
    room = max(limit - (total - int(rest.shape[0])), 0)
    even = rest[(np.arange(room) * (rest.shape[0] / room)).astype(np.int64)] if room else rest[:0]
    keep = np.sort(np.concatenate((np.flatnonzero(marked), even)))
    return keep, {"shown": int(keep.shape[0]), "total": total}


def _standard_series(ctx: Context, data: _StandardsData, pick: np.ndarray) -> dict[str, Any]:
    recs, k = ctx.recs, data.kernel
    at = data.at[pick]
    swap: list[list[str | None] | None] = [None] * int(pick.shape[0])
    codes = k.swap[pick]
    for i in np.flatnonzero(codes[:, 0] >= 0).tolist():
        swap[i] = [data.names[c] for c in codes[i].tolist() if c >= 0]
    return {
        "row": recs.row[at].tolist(), "date": _moments(recs.date()[at]), "seq": _floats(recs.number(r.SEQ)[at]),
        "batch": _labels(recs.text(r.BATCH), at), "x": ctx.values[ctx.of_type(CRM)[0][pick]].tolist(),
        "z": _floats(k.z[pick]), "status": k.status[pick].tolist(), "rule": k.rule[pick].tolist(),
        "ma": _floats(k.ma[pick]), "swap": swap,
    }  # fmt: skip


def _standards(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    data: _StandardsData = ctx.once("standards", _standards_data)
    k, labs = data.kernel, _lab_names(ctx)
    groups = [
        {
            "crm": data.names[c], "lab": labs[lb], "state": STATES[s], "n": n, "missing": miss, "mu": _opt(mu),
            "sd": _opt(sd), "unit": ctx.element.unit, "mean": _opt(mean), "std": _opt(std), "bias": _opt(bias),
            "bias_class": None if cls < 0 else cls, "norm": norm, "warn": warn, "fail": fail, "pair_fails": pairs,
            "run_flags": runs, "swaps": swaps,
        }
        for c, lb, s, n, miss, mu, sd, mean, std, bias, cls, norm, warn, fail, pairs, runs, swaps in zip(
            k.crm.tolist(), k.lab.tolist(), k.state.tolist(), k.n.tolist(), k.missing.tolist(), k.mu.tolist(),
            k.sd.tolist(), k.mean.tolist(), k.std.tolist(), k.bias.tolist(), k.bias_class.tolist(), k.norm.tolist(),
            k.warn.tolist(), k.fail.tolist(), k.pair_fails.tolist(), k.run_flags.tolist(), k.swaps.tolist(),
            strict=True,
        )
    ]  # fmt: skip
    out: dict[str, Any] = {"groups": groups}
    if params.get("group") is not None:
        g = _find(params["group"], [(x["crm"], x["lab"]) for x in groups], ("crm", "lab"))
        out["series"] = None
        if g is not None:
            pick = k.rows(g)
            keep, thinned = _thin(k.status[pick], params)
            out["series"] = _standard_series(ctx, data, pick if keep is None else pick[keep])
            if thinned is not None:
                out["thinned"] = thinned
    if params.get("samples"):
        out["samples"] = {"group": np.repeat(np.arange(k.n.shape[0]), k.n).tolist(),
                          **_standard_series(ctx, data, k.order)}  # fmt: skip
    return out, data.notes


def _standards_line(ctx: Context) -> dict[str, Any]:
    k = ctx.once("standards", _standards_data).kernel
    n = int(k.n.sum())
    judged = int(k.norm.sum() + k.warn.sum() + k.fail.sum())
    return {
        "groups": int(k.n.shape[0]), "n": n, "missing": int(k.missing.sum()), "norm": int(k.norm.sum()),
        "warn": int(k.warn.sum()), "fail": int(k.fail.sum()), "not_assessed": n - judged,
        "pair_fails": int(k.pair_fails.sum()), "run_flags": int(k.run_flags.sum()), "swaps": int(k.swaps.sum()),
        "bias_attention": int(np.count_nonzero(k.bias_class == WARN)),
        "bias_bad": int(np.count_nonzero(k.bias_class == FAIL)),
    }  # fmt: skip


# --- blanks --------------------------------------------------------------------------------------------------------


@dataclass
class _BlanksData:
    kernel: Blanks
    at: np.ndarray  # the rows of the blanks in `recs`
    prev: np.ndarray  # the value of the previous sample of the batch
    notes: list[str]


def _dl_table(ctx: Context) -> tuple[np.ndarray, list[str | None]]:
    """The DL of the settings by the code of a laboratory (0 — the empty one) in the unit of the element: the
    row (element, laboratory), else (element, any); NaN — none. And the laboratories whose DL is in units that
    cannot be brought to those of the element."""
    labs = _lab_names(ctx)
    element = name_key(ctx.element.name)
    rows = [row for row in ctx.config.get("dl") or () if name_key(row.get("element") or "") == element]
    by_lab = {name_key(row["lab"]): row for row in rows if row.get("lab") is not None and str(row["lab"]).strip()}
    anywhere = next((row for row in rows if row.get("lab") is None or not str(row["lab"]).strip()), None)
    table = np.full(len(labs), np.nan)
    other: list[str | None] = []
    for i, lab in enumerate(labs):
        row = by_lab.get(name_key(lab), anywhere) if lab is not None else anywhere
        if row is None:
            continue
        ratio = unit_ratio(row.get("unit"), ctx.element.unit)
        if ratio is None:
            other.append(lab)
        else:
            table[i] = _convert(row.get("value"), ratio)
    return table, other


def _blanks_data(ctx: Context) -> _BlanksData:
    recs, name = ctx.recs, ctx.element.name
    sub, at = ctx.of_type(BLANK)
    lab = recs.text(r.LAB).codes[at].astype(np.int64) + 1
    table, other = _dl_table(ctx)
    own = recs.number(r.dl(name))[at] if ctx.element.has_dl else None
    kernel = blanks(ctx.values[sub], lab, recs.date()[at], recs.number(r.SEQ)[at], effective_dl(lab, table, own),
                    ctx.limits, below=recs.flags(r.q(name))[at] == r.Q_BELOW)  # fmt: skip
    labs = _lab_names(ctx)
    notes = []
    lost = [labs[lb] for lb in kernel.lab[kernel.not_assessed > 0].tolist()]
    if lost:
        notes.append(f"Нет DL элемента {name} для лабораторий: {_listed(lost)} — бланки без DL не оценены "
                     f"({int(kernel.not_assessed.sum())}). Задайте DL в «Настройка → DL».")  # fmt: skip
    here = set(kernel.lab.tolist())
    alien = [lab_name for lab_name in other if labs.index(lab_name) in here]
    if alien:
        notes.append(f"Единица DL не приводится к единице элемента {name} для лабораторий: {_listed(alien)} — "
                     "этот DL не применён. Исправьте единицу в «Настройка → DL».")  # fmt: skip
    return _BlanksData(kernel, at, recs.number(r.b(name))[at], notes)


def _blank_series(ctx: Context, data: _BlanksData, pick: np.ndarray) -> dict[str, Any]:
    recs, k = ctx.recs, data.kernel
    at = data.at[pick]
    return {
        "row": recs.row[at].tolist(), "date": _moments(recs.date()[at]), "seq": _floats(recs.number(r.SEQ)[at]),
        "batch": _labels(recs.text(r.BATCH), at), "x": ctx.values[ctx.of_type(BLANK)[0][pick]].tolist(),
        "status": k.status[pick].tolist(), "prev": _floats(data.prev[pick]), "limit": _floats(k.limit[pick]),
    }  # fmt: skip


def _carryover(ctx: Context, data: _BlanksData, pick: np.ndarray) -> dict[str, Any]:
    n, pearson = carryover(ctx.values[ctx.of_type(BLANK)[0][pick]], data.prev[pick])
    return {"n": n, "pearson": _opt(pearson)}


def _blanks(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    data: _BlanksData = ctx.once("blanks", _blanks_data)
    k, labs = data.kernel, _lab_names(ctx)
    groups: list[dict[str, Any]] = [
        {"lab": labs[lb], "n": n, "missing": miss, "dl": _opt(dl), "limit": _opt(limit), "mixed": mixed,
         "norm": norm, "fail": fail, "not_assessed": lost}
        for lb, n, miss, dl, limit, mixed, norm, fail, lost in zip(
            k.lab.tolist(), k.n.tolist(), k.missing.tolist(), k.dl.tolist(), k.group_limit.tolist(), k.mixed.tolist(),
            k.norm.tolist(), k.fail.tolist(), k.not_assessed.tolist(), strict=True,
        )
    ]  # fmt: skip
    out: dict[str, Any] = {"groups": groups}
    if params.get("group") is not None:
        g = _find(params["group"], [(x["lab"],) for x in groups], ("lab",))
        out["series"], out["carryover"] = None, None
        if g is not None:
            pick = k.rows(g)
            keep, thinned = _thin(k.status[pick], params)
            out["series"] = _blank_series(ctx, data, pick if keep is None else pick[keep])
            out["carryover"] = _carryover(ctx, data, pick)
            if thinned is not None:
                out["thinned"] = thinned
    if params.get("samples"):
        for g, row in enumerate(groups):
            row["carryover"] = _carryover(ctx, data, k.rows(g))
        out["samples"] = {"group": np.repeat(np.arange(k.n.shape[0]), k.n).tolist(),
                          **_blank_series(ctx, data, k.order)}  # fmt: skip
    return out, data.notes


def _blanks_line(ctx: Context) -> dict[str, Any]:
    k = ctx.once("blanks", _blanks_data).kernel
    return {
        "groups": int(k.n.shape[0]), "n": int(k.n.sum()), "missing": int(k.missing.sum()),
        "norm": int(k.norm.sum()), "fail": int(k.fail.sum()), "not_assessed": int(k.not_assessed.sum()),
    }  # fmt: skip


# --- batches -------------------------------------------------------------------------------------------------------


def _batches_data(ctx: Context) -> Batches:
    recs = ctx.recs
    st, bl = ctx.once("standards", _standards_data).kernel, ctx.once("blanks", _blanks_data).kernel
    lab = ctx.take(recs.text(r.LAB).codes).astype(np.int64) + 1
    return batches(lab, ctx.take(recs.text(r.BATCH).codes), ctx.take(recs.date()), ctx.of_type(CRM)[0], st.status,
                   ctx.of_type(BLANK)[0], bl.status)  # fmt: skip


def _page(params: Mapping[str, Any], total: int) -> tuple[int, int]:
    offset, limit = params.get("offset") or 0, params.get("limit")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise QaqcError("bad_param", "Начало страницы — целое число от 0.", "Задайте offset ≥ 0.", param="offset")
    if limit is None:
        return offset, total
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH_PAGE:
        raise QaqcError("bad_param", f"Размер страницы — целое число от 1 до {MAX_BATCH_PAGE}.",
                        "Запросите партии страницами.", param="limit")  # fmt: skip
    return offset, min(offset + limit, total)


def _batches(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    k: Batches = ctx.once("batches", _batches_data)
    a, b = _page(params, k.total)
    labs, names = _lab_names(ctx), ctx.recs.text(r.BATCH).labels
    page = [
        {"lab": labs[lb], "batch": names[bt], "date_from": _iso(first), "date_to": _iso(last), "n": n, "crm": crm,
         "blanks": blank, "crm_fail": crm_fail, "blank_fail": blank_fail, "assessed": judged, "status": status}
        for lb, bt, first, last, n, crm, blank, crm_fail, blank_fail, judged, status in zip(
            k.lab[a:b].tolist(), k.batch[a:b].tolist(), k.date_from[a:b].tolist(), k.date_to[a:b].tolist(),
            k.n[a:b].tolist(), k.crm[a:b].tolist(), k.blanks[a:b].tolist(), k.crm_fail[a:b].tolist(),
            k.blank_fail[a:b].tolist(), k.assessed[a:b].tolist(), k.status[a:b].tolist(), strict=True,
        )
    ]  # fmt: skip
    return {**_batches_line(ctx), "offset": a, "batches": page}, []


def _batches_line(ctx: Context) -> dict[str, Any]:
    k: Batches = ctx.once("batches", _batches_data)
    return {"total": k.total, "failed": k.failed, "not_assessed": k.not_assessed, "no_batch": k.no_batch}


# --- the registry --------------------------------------------------------------------------------------------------

Section = Callable[[Context, Mapping[str, Any]], tuple[dict[str, Any], list[str]]]
#: the lines of the summary, a section each, and where the notes of their data are kept (`Context.memo`)
LINES: dict[str, Callable[[Context], dict[str, Any]]] = {
    "standards": _standards_line,
    "blanks": _blanks_line,
    "batches": _batches_line,
}
NOTE_KEYS = ["standards", "blanks"]


def _summary(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    by_type = ctx.memo["by_type"]
    out: dict[str, Any] = {
        "types": {name: int(by_type[i + 1]) for i, name in enumerate(TYPES)},
        "unmapped_codes": _unmapped_codes(ctx),
    }
    for name, line in LINES.items():
        out[name] = line(ctx)
    notes: list[str] = []
    for key in NOTE_KEYS:
        notes += ctx.memo[key].notes
    return out, notes


SECTIONS: dict[str, Section] = {
    "summary": _summary,
    "standards": _standards,
    "blanks": _blanks,
    "batches": _batches,
}


def run(ctx: Context, section: str, **params: Any) -> dict[str, Any]:
    """A section of the element of a context (see the module docstring)."""
    make = SECTIONS.get(section)
    if make is None:
        raise QaqcError("unknown_section", f"Нет раздела QAQC «{section}».", f"Разделы: {', '.join(SECTIONS)}.",
                        param="section")  # fmt: skip
    body, notes = make(ctx, params)
    head = {"element": ctx.element.name, "unit": ctx.element.unit, "counts": dict(ctx.counts),
            "limits": ctx.limits.as_dict(), "notes": [*ctx.notes, *notes]}  # fmt: skip
    return {**head, **body}


def calc(section: str, recs: r.Records, config: Mapping[str, Any], crm: Sequence[Mapping[str, Any]], element: Any,
         flt: Any = None, **params: Any) -> dict[str, Any]:  # fmt: skip
    """A section of QAQC of one element of the records under a filter (see the module docstring)."""
    return run(context(recs, config, crm, element, flt), section, **params)


from . import calc_pairs  # noqa: E402, F401  the sections of the pairs register themselves; they need the names above
