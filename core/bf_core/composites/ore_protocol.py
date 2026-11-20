"""Protocol of оконтуривание (ТЗ 7.35 Д-26 «Результат» (4); ADR 0018 §9, решения 10, 19): the decisions of the
kernel as the form И1, сл. 17 of one hole and as the long form of a set of holes.

    hole_protocol(lines, params, hole=0, *, unit="", edits=None) -> HoleProtocol   the form and the long form
    long_form(lines, params, *, holes=None, edits=None, tick=None) -> Iterator[LongBlock]   in blocks of holes

The protocol is not stored (решение 10): it is the kernel run again with the parameters of the result
(`ore_intervals(…, events=True)`), so it is that of the stored result while the samples and `ALGO` are the
same — the caller answers 409 `stale` otherwise (решение 19). A hole does not depend on the other holes: its
protocol is taken from its own samples, and `HoleProtocol.intervals` are its rows of the table bit for bit.
A result with manual edits keeps them in `report.edits`; given here as `edits`, they are applied to the hole
again (`ore_edit`): «Итог» and `intervals` are the rows after them (`manual`), the intervals they made are
the records of step 6, what they break is in `violations`.

The form (И1, сл. 17). `rows` — the lines of the form from the top of the hole down: the samples of the hole
(also those outside the sampling lines, with the reason in `note`) and the stretches without samples counted
as waste («пустая порода до L_н»; `sample` is None). `columns` — in this order:

    n, in0          N — the number of the elementary interval in its sampling line; IN₀ — «M/C/MC»
    c1, c2, in1 …   stage s of the прирезка: C₂ₛ₋₁ — C(Р_в ∪ Б), висячий бок; C₂ₛ — C(Б ∪ Р_н), лежачий бок; a
                    waste unit longer than Мпп — in C₂ₛ₋₁; IN_s — the units after the stage (when it changed
                    them). «Упрощённый»: C_s — the average of the interval grown by its s-th pair, IN_s
    layer           step 3: the fragments «порода … порода» — excluded, torn, passed over, not checked
    near            step 4: the chains of close intervals
    final           the rows of the table with NESTED = 0: the class, «M/C/MC»; LABEL for `ore`

`cells` — a cell is a span of lines of the form in a lane of its column (spans of one column that overlap go
to different lanes): `col`, `lane`, `row0`, `row1` (lines of the form, 0-based, inclusive), `text`, `tone`
(a class of `CLASSES`, "pass", "reject" or "none"), `from`, `to`, `m`, `c`, `mc`, `ore`, `decision`, `event`
(the index of its record in `events`, None — none). `HoleProtocol.grid()` lays them out as a sheet.

The long form — a record per decision (ADR 0018 §9), by hole, line and step; in `HoleProtocol.events` — dicts
(`OreEvents.records` with `row0`, `row1` counted from the first sample of the hole, 0-based; `n` — the number
of the record in the hole; `mark` — обозначение; `text` — the record as a line of Russian text); for a set —
`LongBlock` (columns) and `LongBlock.rows(names)` (tuples under `LONG_HEAD`, for XLSX and CSV). Numbers that
do not apply are None, never zero.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import ore_units as ou
from .ore_intervals import ALGO, ore_intervals
from .ore_set import _ru, label
from .ore_types import IV_HOLE, OreEvents, OreLines, OreParams, pieces
from .strips import Tick

__all__ = [
    "CLASS_TITLES", "COMPARED_TITLES", "DECISION_TITLES", "LONG_HEAD", "SIDE_TITLES", "STEP_TITLES",
    "HoleProtocol", "LongBlock", "event_text", "hole_protocol", "long_form", "mark",
]  # fmt: skip

LONG_CHUNK = 100_000  # samples in a block of the long form
LONG_HEAD = (
    "Скважина", "Линия", "№", "Шаг", "Этап", "Обозначение", "Проба с", "Проба по", "От, м", "До, м",
    "M", "C", "MC", "Сторона", "Сравнение", "Решение",
)  # fmt: skip
STEP_TITLES = (
    "разрыв линии", "элементарные интервалы", "прирезка", "пустой прослой", "сближенные интервалы",
    "класс интервала", "ручная правка",
)  # fmt: skip
DECISION_TITLES = {
    "": "",
    "pass": "C ≥ Сб",
    "joined": "присоединён",
    "rejected_c": "отклонён — C < Сб",
    "rejected_m": "отклонён — M > Мпп",
    "demoted": "некондиционный → безрудный",
    "interlayer": "прослой исключён",
    "torn": "разорван",
    "near_joined": "сближенные объединены",
    "not_checked": "не проверяется в этом режиме",
    "break": "линия опробования прервана",
    "missing_waste": "пропуск — пустая порода (флаг)",
    "ore": "кондиционный",
    "subgrade": "некондиционный",
    "not_selected": "не выбран",
}
COMPARED_TITLES = {
    "": "",
    "cutoff": "Сб",
    "m_waste_max": "Мпп",
    "m_min": "Мрт",
    "mc_min": "МС",
    "c_min": "Смин",
    "k": "условие К",
    "layer": "прослой",
    "no_value": "нет значения",
    "gap": "промежуток без проб",
    "domain": "граница домена",
    "missing_max": "наибольший пропуск",
}
SIDE_TITLES = {"": "", "upper": "висячий бок", "lower": "лежачий бок"}
CLASS_TITLES = {
    "ore": "кондиционный рудный",
    "subgrade": "некондиционный рудный",
    "waste_in": "включённый прослой",
    "interlayer": "исключённый прослой",
    "waste": "безрудный",
    "frame": "обрамление",
}
STATE_NOTES = {
    "piece": "",
    "bad": "неверный интервал (TO ≤ FROM) — не участвует",
    "overlap": "перекрытие с предыдущей пробой — не участвует",
    "missing": "нет значения — вне линий опробования",
    "no_domain": "вне доменов",
    "bridged": "нет значения — пустая порода (флаг)",
    "gap": "нет проб — пустая порода (флаг)",
}
_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _sub(n: int) -> str:
    return str(n).translate(_SUB)


def _num(x: float | None, digits: int) -> str:
    """A number of the long form: at most `digits` decimals, half up, a comma; «—» — no value."""
    if x is None or not math.isfinite(x):
        return "—"
    text = _ru(x, digits)
    return text.rstrip("0").rstrip(",") if "," in text else text


def _mcm(m: float | None, c: float | None, mc: float | None) -> str:
    """«3,0/0,57/1,7» — M, C and MC of a unit as the form И1, сл. 17 writes them."""
    if m is None or c is None or mc is None:
        return "—"
    return f"{_ru(m, 1)}/{_ru(c, 2)}/{_ru(mc, 1)}"


def mark(step: int, stage: int, kind: str, side: str, seq: int) -> str:
    """Обозначение of a record (ADR 0018 §9): N₃ — an elementary unit, IN₁ — a unit of stage 1, C₁ / C₂ — the
    прирезки of stage 1 from the висячий and the лежачий бок (C₃ / C₄ — of stage 2; «упрощённый» — C_s)."""
    if kind == "unit":
        return f"N{_sub(seq)}" if step == ou.STEP_UNIT else f"IN{_sub(stage)}"
    if kind == "trim":
        return f"C{_sub(stage if side == '' else 2 * stage - (side == 'upper'))}"
    if step == ou.STEP_EDIT:
        return "правка"
    return {"gap": "Б", "layer": "прослой", "chain": "цепочка", "interval": "итог"}.get(kind, "")


def event_text(e: dict[str, Any]) -> str:
    """A record as a line of the long form: «Этап 1 · C₁ · висячий бок · пробы 14–17 · 13–17 м · M 4 · C 0,6 ·
    MC 2,4 — отклонён — C < Сб»."""
    step = e["step"]
    parts = [f"Этап {e['stage']}" if step == ou.STEP_TRIM else STEP_TITLES[step].capitalize()]
    parts += [x for x in (e.get("mark") or mark(step, e["stage"], e["kind"], e["side"], e["seq"]),) if x]
    if e["side"]:
        parts.append(SIDE_TITLES[e["side"]])
    if e["row0"] >= 0:
        parts.append(f"пробы {e['row0'] + 1}–{e['row1'] + 1}" if e["row1"] > e["row0"] else f"проба {e['row0'] + 1}")
    if e["from"] is not None and e["to"] is not None:
        parts.append(f"{_num(e['from'], 2)}–{_num(e['to'], 2)} м")
    parts += [f"{name} {_num(e[key], digits)}" for name, key, digits in (("M", "m", 2), ("C", "c", 3), ("MC", "mc", 2))
              if e[key] is not None]  # fmt: skip
    what = DECISION_TITLES.get(e["decision"], e["decision"])
    cmp = COMPARED_TITLES.get(e["compared"], e["compared"])
    tail = " ".join(x for x in (what, f"({cmp})" if cmp and cmp not in what else "") if x)
    return " · ".join(parts) + (f" — {tail}" if tail else "")


# --- the long form ----------------------------------------------------------------------------------------------

_KIND = np.array(ou.KINDS, object)
_SIDE = np.array([SIDE_TITLES[s] for s in ou.SIDES], object)
_STEP = np.array(STEP_TITLES, object)
_DEC = np.array([DECISION_TITLES[d] for d in ou.DECISIONS], object)
_CMP = np.array([COMPARED_TITLES[ou.COMPARED.get(k, "")] for k in range(max(ou.COMPARED) + 1)], object)


def _or_none(a: np.ndarray) -> list[float | None]:
    return [None if x != x else x for x in a.tolist()]


@dataclass(frozen=True)
class LongBlock:
    """Records of the long form of some holes, by (hole, line, step) — a column per field of `OreEvents`.
    `hole` — the index of the hole in the lines; `no` — the number of the record in its hole (1…);
    `sample0`, `sample1` — numbers of the first and the last samples in the hole (1…; 0 — none)."""

    hole: np.ndarray
    no: np.ndarray
    sample0: np.ndarray
    sample1: np.ndarray
    events: OreEvents

    def __len__(self) -> int:
        return len(self.hole)

    def marks(self) -> list[str]:
        """Обозначение of every record."""
        i = self.events.i
        seq = np.where(i[:, ou.EI_STEP] == ou.STEP_UNIT, i[:, ou.EI_SEQ], 0)
        keys = np.column_stack([i[:, ou.EI_STEP], i[:, ou.EI_STAGE], i[:, ou.EI_KIND], i[:, ou.EI_SIDE], seq])
        uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
        names = np.array([mark(s, st, ou.KINDS[k], ou.SIDES[sd], q) for s, st, k, sd, q in uniq.tolist()], object)
        return names[inverse.ravel()].tolist()  # type: ignore[no-any-return]

    def rows(self, names: Sequence[str] | None = None) -> list[tuple[Any, ...]]:
        """The records as rows under `LONG_HEAD`: the words in Russian, a number that does not apply — None.
        `names` — the names of the holes by their index (the number of the hole, 1…, without them)."""
        i, f = self.events.i, self.events.f
        hole = [names[h] for h in self.hole.tolist()] if names is not None else (self.hole + 1).tolist()
        samples = [[n or None for n in a.tolist()] for a in (self.sample0, self.sample1)]
        return list(
            zip(
                hole, (i[:, ou.EI_LINE] + 1).tolist(), self.no.tolist(), _STEP[i[:, ou.EI_STEP]].tolist(),
                [s or None for s in i[:, ou.EI_STAGE].tolist()], self.marks(), *samples,
                *(_or_none(f[:, k]) for k in (ou.EF_FROM, ou.EF_TO, ou.EF_M, ou.EF_C, ou.EF_MC)),
                _SIDE[i[:, ou.EI_SIDE]].tolist(), _CMP[i[:, ou.EI_CMP]].tolist(), _DEC[i[:, ou.EI_DEC]].tolist(),
                strict=True,
            )
        )  # fmt: skip

    def records(self) -> list[dict[str, Any]]:
        """The records as dicts (`OreEvents.records`): `hole` — of the lines, `row0`, `row1` — from the first
        sample of the hole (0-based; −1 — none), `n`, `mark`, `text`; NaN is None."""
        out = self.events.records()
        marks = self.marks()
        for e, h, n, a, b, name in zip(
            out, self.hole.tolist(), self.no.tolist(), self.sample0.tolist(), self.sample1.tolist(), marks, strict=True
        ):
            e.update(hole=h, n=n, row0=a - 1, row1=b - 1, mark=name)
            for key in ("from", "to", "m", "c", "mc"):
                if e[key] != e[key]:
                    e[key] = None
            e["text"] = event_text(e)
        return out


def _block(events: OreEvents, starts: np.ndarray, h0: int) -> LongBlock:
    """The events of the holes that begin with hole h0 of the set (`starts` — of these holes, from 0)."""
    i = events.i
    hole = i[:, ou.EI_HOLE]
    first = np.searchsorted(hole, hole, side="left")  # the events go by hole
    base = starts[hole]
    row0, row1 = i[:, ou.EI_ROW0], i[:, ou.EI_ROW1]
    return LongBlock(
        hole + h0,
        np.arange(len(hole), dtype=np.int64) - first + 1,
        np.where(row0 >= 0, row0 - base + 1, 0),
        np.where(row1 >= 0, row1 - base + 1, 0),
        events,
    )


def _edited(sub: OreLines, p: OreParams, hole: int, edits: Sequence[Mapping[str, Any]], found: OreEvents) -> Any:
    """Hole `hole` of `sub` after its manual edits (`ore_edit`) and the events with its records of step 6 added
    after those of the hole. Returns (the hole, the events)."""
    from .ore_edit import edit_hole  # the edit takes the words of the protocol from here

    he = edit_hole(sub, p, edits, hole)
    extra = he.events()
    rows = extra.i[:, ou.EI_ROW0 : ou.EI_ROW1 + 1]  # counted from the first sample of the hole
    rows[rows >= 0] += sub.starts[hole]
    ev_i = np.concatenate([found.i, extra.i])
    order = np.argsort(ev_i[:, ou.EI_HOLE], kind="stable")
    return he, OreEvents(ev_i[order], np.concatenate([found.f, extra.f])[order])


def long_form(
    lines: OreLines,
    params: OreParams,
    *,
    holes: Sequence[int] | None = None,
    edits: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    tick: Tick | None = None,
    chunk: int = LONG_CHUNK,
) -> Iterator[LongBlock]:
    """The long form of a set of holes (or of `holes` — indices, taken in ascending order), a block of about
    `chunk` samples at a time: the kernel runs on the holes of a block, nothing else is kept. `edits` — the
    manual edits of the result by the index of the hole (`report.edits`): the intervals they made are the
    records of step 6 of their holes. `tick(fraction)` is called after every block and may raise to cancel."""
    p = params.checked()
    total = len(lines.starts) - 1
    if holes is None:
        cuts = [0]
        while cuts[-1] < total:  # whole holes, at least one, until the block has `chunk` samples
            end = int(np.searchsorted(lines.starts, lines.starts[cuts[-1]] + max(1, chunk), side="left"))
            cuts.append(min(total, max(cuts[-1] + 1, end)))
        todo = list(zip(cuts[:-1], cuts[1:], strict=True))
    else:
        todo = [(h, h + 1) for h in sorted({int(h) for h in holes})]
    for k, (h0, h1) in enumerate(todo):
        sub = lines.holes(h0, h1)
        found = ore_intervals(sub, p, events=True).events
        assert found is not None
        for h in sorted(h for h, mine in (edits or {}).items() if h0 <= h < h1 and mine):
            found = _edited(sub, p, h - h0, (edits or {})[h], found)[1]
        yield _block(found, sub.starts, h0)
        if tick is not None:
            tick((k + 1) / len(todo))


# --- the form И1, сл. 17 ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HoleProtocol:
    """The protocol of a hole: the form И1, сл. 17 (`rows`, `columns`, `cells`), the long form (`events`) and
    the rows of the table of the hole (`intervals`: all classes, the nested ones too, with `manual`); of a hole
    with manual edits — also the `edits` applied and the `violations` of the intervals they made."""

    hole: int
    unit: str
    params: OreParams
    rows: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    cells: list[dict[str, Any]]
    events: list[dict[str, Any]]
    intervals: list[dict[str, Any]]
    stats: dict[str, int]
    edits: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """What the API answers (JSON: no NaN)."""
        return {
            "hole": self.hole, "unit": self.unit, "algo": ALGO, "mode": self.params.mode, "rows": self.rows,
            "columns": self.columns, "cells": self.cells, "events": self.events, "intervals": self.intervals,
            "stats": self.stats, "edits": self.edits, "violations": self.violations,
        }  # fmt: skip

    def grid(self) -> dict[str, Any]:
        """The form as a sheet: `head` — titles of the columns (the sample: №, от, до, l, C, примечание; then
        a column per lane), `body` — texts by line of the form (None — an empty cell or one under a span),
        `tones` — the tone of every cell with a text, `merges` — (first line, last line, column) of the cells
        that span several lines."""
        grade = f"C, {self.unit}" if self.unit else "C"
        head = ["№", "От, м", "До, м", "l, м", grade, "Примечание"]
        fixed = len(head)
        at: dict[tuple[str, int], int] = {}
        for col in self.columns:
            for lane in range(col["lanes"]):
                at[col["key"], lane] = len(head)
                head.append(col["title"])
        body: list[list[str | None]] = []
        for r in self.rows:
            sample = [str(r["sample"]) if r["sample"] else "", _num(r["from"], 2), _num(r["to"], 2), _num(r["l"], 2)]
            body.append([*sample, _num(r["g"], 3), r["note"] or None, *([None] * (len(head) - fixed))])
        tones: list[list[str | None]] = [[None] * len(head) for _ in self.rows]
        merges = []
        for cell in self.cells:
            k = at[cell["col"], cell["lane"]]
            body[cell["row0"]][k] = cell["text"]
            tones[cell["row0"]][k] = cell["tone"]
            if cell["row1"] > cell["row0"]:
                merges.append((cell["row0"], cell["row1"], k))
        return {"head": head, "body": body, "tones": tones, "merges": merges}


def _form_rows(sub: OreLines, p: OreParams, cls: np.ndarray, ore_id: np.ndarray) -> tuple[list[dict[str, Any]], Any]:
    """The lines of the form of a hole and, for the pieces of step 0, the line of each."""
    pc = pieces(sub, p)
    line_of = np.repeat(pc.line_no, np.diff(pc.line_start)).tolist()
    piece_of: dict[int, int] = {}
    before: dict[int, list[int]] = {}  # the stretches without samples that go right above a sample
    pending: list[int] = []
    for k, r in enumerate(pc.row.tolist()):
        if r < 0:
            pending.append(k)
        else:
            piece_of[r] = k
            if pending:
                before[r], pending = pending, []
    rows: list[dict[str, Any]] = []
    where = [0] * len(pc.row)

    def fin(x: float) -> float | None:
        return x if math.isfinite(x) else None

    frm, to, grade, state = sub.frm.tolist(), sub.to.tolist(), sub.grade.tolist(), pc.state.tolist()
    thick = None if sub.thick is None else sub.thick.tolist()
    for r in range(len(frm)):
        for k in before.get(r, ()):
            where[k] = len(rows)
            rows.append({
                "sample": None, "from": float(pc.frm[k]), "to": float(pc.to[k]), "l": float(pc.thick[k]), "g": None,
                "line": line_of[k] + 1, "state": "gap", "cls": None, "ore_id": 0, "note": STATE_NOTES["gap"],
            })  # fmt: skip
        k = piece_of.get(r, -1)
        name = ou.ROW_STATES[state[r]]
        length = fin(to[r] - frm[r]) if name != "bad" else None
        if k >= 0:
            where[k] = len(rows)
            length = float(pc.thick[k])
        elif thick is not None and length is not None and math.isfinite(thick[r]) and thick[r] > 0.0:
            length = thick[r]
        rows.append({
            "sample": r + 1, "from": fin(frm[r]), "to": fin(to[r]), "l": length, "g": fin(grade[r]),
            "line": line_of[k] + 1 if k >= 0 else None, "state": name,
            "cls": ou.CLASSES[cls[r]] if cls[r] >= 0 else None, "ore_id": int(ore_id[r]), "note": STATE_NOTES[name],
        })  # fmt: skip
    return rows, (pc, where)


def _lanes(spans: list[tuple[int, int]]) -> tuple[list[int], int]:
    """A lane for every span so that the spans of a lane do not overlap (first fit, in the order given)."""
    taken: list[list[tuple[int, int]]] = []
    out = []
    for a, b in spans:
        for k, lane in enumerate(taken):
            if all(b < x or a > y for x, y in lane):
                lane.append((a, b))
                out.append(k)
                break
        else:
            taken.append([(a, b)])
            out.append(len(taken) - 1)
    return out, max(1, len(taken))


def _unit_tone(e: dict[str, Any]) -> str:
    return "ore" if e["ore"] else "waste"


def _c_cell(e: dict[str, Any]) -> tuple[str, str]:
    """Text and tone of a cell of a column C: the прирезка against Сб, the waste unit against Мпп."""
    if e["kind"] == "gap":
        return f"M {_ru(e['m'], 1)} > Мпп", "reject"
    ok = e["decision"] in ("pass", "joined")
    return f"{_ru(e['c'], 2)} {'≥' if ok else '<'} Сб", "pass" if ok else "reject"


def _stage_series(
    units: list[dict[str, Any]], changes: list[dict[str, Any]], stage: int
) -> tuple[list[dict[str, Any]], bool]:
    """The series of a line after a stage: every record of `changes` — a unit made at the stage — takes the
    place of the units it covers. Returns the series and whether a unit was made."""
    made = False
    tops = [u["from"] for u in units]
    for e in changes:
        a = bisect_left(tops, e["from"])
        b = a
        while b < len(units) and units[b]["to"] <= e["to"]:
            b += 1
        if a == b:
            continue  # not a unit of this line (never: a unit is made of whole units)
        new = dict(e)
        if e["decision"] == "" and any(u["decision"] == "demoted" and u["made"] == stage for u in units[a:b]):
            new["decision"] = "demoted"  # the waste a некондиционная unit went to
        new["made"] = stage
        units[a:b] = [new]
        tops[a:b] = [new["from"]]
        made = True
    return units, made


def hole_protocol(
    lines: OreLines,
    params: OreParams,
    hole: int = 0,
    *,
    unit: str = "",
    edits: Sequence[Mapping[str, Any]] | None = None,
) -> HoleProtocol:
    """The protocol of hole `hole` of the lines: the kernel runs on its samples with `params`, the events go
    to the form И1, сл. 17 and to the long form. `unit` — the unit of the grade field (LABEL). `edits` — the
    manual edits of the hole (`report.edits`): the result is then the rows after them («Итог», the classes of
    the samples, `intervals`), the intervals they made are the records of step 6."""
    p = params.checked()
    sub = lines.holes(hole)
    res = ore_intervals(sub, p, events=True, nested=True)
    assert res.events is not None
    happened, after = res.events, None
    if edits:
        after, happened = _edited(sub, p, 0, edits, happened)
        after.intervals.iv_i[:, IV_HOLE] = hole
        res = after.intervals
    events = _block(happened, sub.starts, hole).records()
    rows, (pc, where) = _form_rows(sub, p, res.sample_cls, res.sample_ore_id)
    top = {f: where[k] for k, f in enumerate(pc.frm.tolist())}  # the line of the form a piece begins at
    end = {t: where[k] for k, t in enumerate(pc.to.tolist())}
    row_of = {r["sample"]: n for n, r in enumerate(rows) if r["sample"]}

    columns: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []

    def column(key: str, title: str, hint: str, found: list[tuple[dict[str, Any], str, str, int | None]]) -> None:
        """A column of the form from (record, text, tone, index of the record) of its cells."""
        kept = [x for x in found if x[0]["from"] in top and x[0]["to"] in end]  # all: a record is whole pieces
        spans = [(top[e["from"]], end[e["to"]]) for e, _, _, _ in kept]
        lanes, count = _lanes(spans)
        columns.append({"key": key, "title": title, "hint": hint, "lanes": count})
        for (e, text, tone, index), (a, b), lane in zip(kept, spans, lanes, strict=True):
            cells.append({
                "col": key, "lane": lane, "row0": a, "row1": b, "text": text, "tone": tone,
                "from": e["from"], "to": e["to"], "m": e["m"], "c": e["c"], "mc": e["mc"],
                "ore": e.get("ore"), "decision": e.get("decision", ""), "event": index, "manual": e.get("manual", 0),
            })  # fmt: skip

    numbered = [{**e, "index": k, "made": 0} for k, e in enumerate(events)]
    first = [e for e in numbered if e["step"] == ou.STEP_UNIT]
    column("n", "N", "Номер элементарного интервала в линии опробования",
           [(e, str(e["seq"]), _unit_tone(e), e["index"]) for e in first])  # fmt: skip
    column("in0", f"IN{_sub(0)}", "Элементарные интервалы: M/C/MC; рудный — все пробы ≥ Сб, безрудный — < Сб",
           [(e, _mcm(e["m"], e["c"], e["mc"]), _unit_tone(e), e["index"]) for e in first])  # fmt: skip
    series: dict[int, list[dict[str, Any]]] = {}
    for e in first:
        series.setdefault(e["line"], []).append(e)
    trims = [e for e in numbered if e["step"] == ou.STEP_TRIM]
    simple = p.mode == "simple"
    for stage in sorted({e["stage"] for e in trims}):
        mine = [e for e in trims if e["stage"] == stage]
        checks = [e for e in mine if e["kind"] != "unit"]
        if simple:
            column(f"c{stage}", f"C{_sub(stage)}", f"Наращивание, пара {stage}: среднее всего интервала против Сб",
                   [(e, *_c_cell(e), e["index"]) for e in checks])  # fmt: skip
            made_of = [{**e, "decision": "joined"} for e in checks if e["decision"] == "joined"]
        else:
            for side, no, title in (("upper", 2 * stage - 1, "висячего"), ("lower", 2 * stage, "лежачего")):
                if not checks:
                    break  # a stage that only turned некондиционные units into waste
                picked = [e for e in checks if e["side"] == side or (side == "upper" and e["kind"] == "gap")]
                hint = f"Этап {stage}: прирезка со стороны {title} бока против Сб"
                hint += "; безрудный промежуток длиннее Мпп" if side == "upper" else ""
                column(f"c{no}", f"C{_sub(no)}", hint, [(e, *_c_cell(e), e["index"]) for e in picked])
            made_of = [e for e in mine if e["kind"] == "unit"]
        changed = False
        for line, units in series.items():
            series[line], made = _stage_series(units, [e for e in made_of if e["line"] == line], stage)
            changed = changed or made
        if changed:
            column(f"in{stage}", f"IN{_sub(stage)}", f"Единицы после этапа {stage}: M/C/MC",
                   [({**u, "decision": u["decision"] if u["made"] == stage else ""}, _mcm(u["m"], u["c"], u["mc"]),
                     _unit_tone(u), u["index"]) for units in series.values() for u in units])  # fmt: skip
    for key, step, title, hint, good in (
        ("layer", ou.STEP_LAYER, "Прослои", "Шаг 3: фрагменты «порода … порода» с C < Сб и M > Мпп", "interlayer"),
        ("near", ou.STEP_NEAR, "Сближенные", "Шаг 4: цепочки сближенных интервалов", "near_joined"),
    ):
        found = [e for e in numbered if e["step"] == step]
        if not found:
            continue
        tones = {good: "interlayer" if key == "layer" else "pass", "torn": "reject", "rejected_c": "reject"}
        column(key, title, hint, [
            (e, f"{_ru(e['m'], 1)} м @ {_ru(e['c'], 2)} — {DECISION_TITLES[e['decision']]}",
             "waste" if e["kind"] == "gap" else tones.get(e["decision"], "none"), e["index"]) for e in found
        ])  # fmt: skip
    intervals = res.rows()
    manual = [0] * len(intervals) if after is None else after.manual.tolist()
    final: list[tuple[dict[str, Any], str, str, int | None]] = []
    for r, flag in zip(intervals, manual, strict=True):
        r["hole"] = hole
        r["manual"] = flag
        if r["c"] != r["c"]:
            r["c"] = None
        if r["nested"]:
            continue
        text = f"{CLASS_TITLES[r['cls']]} {_mcm(r['m'], r['c'], r['mc'])}"
        if r["cls"] == "ore" and r["c"] is not None:
            text = label(r["m"], r["c"], r["mc"], unit)
        text += " — правка" if flag else ""
        final.append(({**r, "ore": r["cls"] == "ore", "decision": r["basis"]}, text, r["cls"], None))
    column("final", "Итог", "Рудные интервалы: класс, M/C/MC; у кондиционного — подпись", final)

    for e in events:  # why a line ended — in the note of the sample it ended at
        if e["decision"] == "break" and e["row0"] + 1 in row_of:
            r = rows[row_of[e["row0"] + 1]]
            span = f" {_num(e['from'], 2)}–{_num(e['to'], 2)} м" if e["m"] else ""
            why = f"разрыв линии — {COMPARED_TITLES[e['compared']]}{span}"
            r["note"] = why if e["compared"] == "no_value" or not r["note"] else f"{r['note']}; {why}"
    done = ([], []) if after is None else (after.edits, after.violations)
    return HoleProtocol(hole, unit, p, rows, columns, cells, events, intervals, dict(res.stats), *done)
