"""The sections of the pairs of QAQC (ТЗ 8.4.2; ADR 0020 §4, §10): `duplicates` — the duplicates of a type
against their original samples. They register themselves in `calc.SECTIONS` and `calc.LINES` (`calc` imports
this module last); the head of an answer, the filter and the order of the rows are those of `calc`.

    duplicates   kind — DUP_F (the default), DUP_C, DUP_P:
                 {kind, pairs (records of the type), used, excluded {no_parent, missing, negative, zero, below},
                 hard_limit, share_limit, share10, share20, share30, share_at_limit, passed, stats {mean1,
                 mean2, pearson, rma {a, b}}, ranked {p, hard}, points {row, x1, x2, hard}}; max_points=N →
                 thinned {shown, total}: every pair past the limit of HARD and an even choice of the rest;
                 samples=True → samples {row, x1, x2, mean, hard, hrd, ampd}: every pair taken
    the line of the summary: {DUP_F, DUP_C, DUP_P: {pairs, used, hard_limit, share_at_limit, passed}}

x1 is the value of the original sample written into the record at the import (`p:E`), x2 — the value of the
record itself; the DL is that of the laboratory of the record (its own `dl:E`, else the table of the settings).
A record has no parent when the import found none and wrote no value of it (`no_parent`). A number that is not
there is None.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import records as r
from .blanks import effective_dl
from .calc import LINES, NOTE_KEYS, SECTIONS, Context, _dl_table, _opt, _thin
from .duplicates import Pairs, Selection, hard_limit, measure, scatter_stats, select
from .filters import TYPES
from .limits import FAIL, NORM, QaqcError, gt

DUP_KINDS = ("DUP_F", "DUP_C", "DUP_P")
_TITLES = {"DUP_F": "полевые дубликаты", "DUP_C": "дроблёные дубликаты", "DUP_P": "пульповые дубликаты",
           "UMP": "внешний контроль"}  # fmt: skip


@dataclass
class _PairData:
    """The records of a type of pairs under the filter: both values, which pairs are taken and their HARD."""

    kind: str
    at: np.ndarray  # the rows of the records in `recs`
    x1: np.ndarray
    x2: np.ndarray
    sel: Selection
    limit: float
    used: np.ndarray  # the places of the pairs taken among the records of the type
    measured: Pairs  # of the pairs taken
    notes: list[str]


def _pair_data(ctx: Context, kind: str) -> _PairData:
    def make(ctx: Context) -> _PairData:
        recs, name = ctx.recs, ctx.element.name
        sub, at = ctx.of_type(TYPES.index(kind))
        x1, x2 = recs.number(r.p(name))[at], ctx.values[sub]
        lab = recs.text(r.LAB).codes[at].astype(np.int64) + 1
        own = recs.number(r.dl(name))[at] if ctx.element.has_dl else None
        dl = effective_dl(lab, ctx.once("dl_table", _dl_table)[0], own)
        orphan = (recs.index(r.PARENT_ROW)[at] < 0) & np.isnan(x1)
        sel = select(x1, x2, dl, ctx.limits.k_dup, no_parent=orphan)
        limit = hard_limit(kind, ctx.limits)
        used = np.flatnonzero(sel.used)
        notes = []
        if sel.no_dl and ctx.limits.k_dup > 0:
            notes.append(f"Пар без DL элемента {name}: {sel.no_dl} ({_TITLES[kind]}) — отсев по k_д·DL к ним не "
                         "применён. Задайте DL в «Настройка → DL».")  # fmt: skip
        return _PairData(kind, at, x1, x2, sel, limit, used,
                         measure(x1[used], x2[used], limit, ctx.limits.hard_share), notes)  # fmt: skip

    return ctx.once(f"pairs:{kind}", make)  # type: ignore[no-any-return]


def _shares(m: Pairs) -> dict[str, Any]:
    return {"share10": _opt(m.share10), "share20": _opt(m.share20), "share30": _opt(m.share30),
            "share_at_limit": _opt(m.share_at_limit), "passed": m.passed}  # fmt: skip


def _points(ctx: Context, at: np.ndarray, x1: np.ndarray, x2: np.ndarray, m: Pairs, limit: float,
            params: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    """The points of the scatter of the pairs taken (`at` — their rows in `recs`), thinned for a chart: every
    pair past the limit of HARD stays."""
    keep, thinned = _thin(np.where(gt(m.hard, limit), FAIL, NORM), params)
    pick = slice(None) if keep is None else keep
    out: dict[str, Any] = {"points": {"row": ctx.recs.row[at][pick].tolist(), "x1": x1[pick].tolist(),
                                      "x2": x2[pick].tolist(), "hard": m.hard[pick].tolist()}}  # fmt: skip
    if thinned is not None:
        out["thinned"] = thinned
    return out


def _duplicates(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    kind = params.get("kind") or DUP_KINDS[0]
    if kind not in DUP_KINDS:
        raise QaqcError("bad_param", f"Нет типа дубликатов «{kind}».", f"Типы: {', '.join(DUP_KINDS)}.", param="kind")
    d = _pair_data(ctx, kind)
    m, x1, x2 = d.measured, d.x1[d.used], d.x2[d.used]
    out: dict[str, Any] = {
        "kind": kind, "pairs": int(d.at.shape[0]), "used": int(d.used.shape[0]), "excluded": dict(d.sel.excluded),
        "hard_limit": d.limit, "share_limit": ctx.limits.hard_share, **_shares(m), "stats": scatter_stats(x1, x2),
        "ranked": {"p": m.ranked_p.tolist(), "hard": m.ranked_hard.tolist()},
        **_points(ctx, d.at[d.used], x1, x2, m, d.limit, params),
    }  # fmt: skip
    if params.get("samples"):
        out["samples"] = {"row": ctx.recs.row[d.at[d.used]].tolist(), "x1": x1.tolist(), "x2": x2.tolist(),
                          "mean": (0.5 * (x1 + x2)).tolist(), "hard": m.hard.tolist(), "hrd": m.hrd.tolist(),
                          "ampd": (2.0 * m.hard).tolist()}  # fmt: skip
    return out, d.notes


def _duplicates_line(ctx: Context) -> dict[str, Any]:
    out = {}
    for kind in DUP_KINDS:
        d = _pair_data(ctx, kind)
        out[kind] = {"pairs": int(d.at.shape[0]), "used": int(d.used.shape[0]), "hard_limit": d.limit,
                     "share_at_limit": _opt(d.measured.share_at_limit), "passed": d.measured.passed}  # fmt: skip
    return out


SECTIONS["duplicates"] = _duplicates
LINES["duplicates"] = _duplicates_line
NOTE_KEYS += [f"pairs:{kind}" for kind in DUP_KINDS]
