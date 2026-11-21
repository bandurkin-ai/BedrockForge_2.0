"""The sections of the pairs of QAQC (ТЗ 8.4.2; ADR 0020 §4, §10): `duplicates` — the duplicates of a type
against their original samples, `external` — the control laboratory against the main one. They register
themselves in `calc.SECTIONS` and `calc.LINES` (`calc` imports this module last); the head of an answer, the
filter and the order of the rows are those of `calc`.

    duplicates   kind — DUP_F (the default), DUP_C, DUP_P:
                 {kind, pairs (records of the type), used, excluded {no_parent, missing, negative, zero, below},
                 hard_limit, share_limit, share10, share20, share30, share_at_limit, passed, stats {mean1,
                 mean2, pearson, rma {a, b}}, ranked {p, hard}, points {row, x1, x2, hard}, th {state (`ok`,
                 `few_pairs`, `no_fit`), group, need, a, b, k, groups {m, med, n}, precision [{c, p, q}]}};
                 max_points=N → thinned {shown, total}: every pair past the limit of HARD and an even choice of
                 the rest; samples=True → samples {row, x1, x2, mean, hard, hrd, ampd}: every pair taken;
                 c=[…] — more values for the precision P(c) of Thompson–Howarth (q — 10, 50, 90 for the
                 percentiles of the means of the pairs, None for the given ones)
    external     the records UMP by (the laboratory of the original sample, the laboratory of the record):
                 {hard_limit (`hard_ump`), share_limit, groups [{lab_main, lab_check, pairs, used, excluded,
                 mean_main, mean_check, bias, t, p, df, pearson, rma {a, b, a_ci, b_ci, n_boot, seed},
                 share10, share20, share30, share_at_limit, passed, notes[]}]} — by the names of the
                 laboratories, an empty one first; x — the main laboratory, y — the control one;
                 group={lab_main, lab_check} → points, ranked (None — no such group now), max_points →
                 thinned; samples=True → samples {group, row, x1, x2, mean, hard, hrd, ampd};
                 intervals=False — without the bootstrap (a_ci, b_ci None, n_boot 0): the points of a group
                 need none
    the lines of the summary: duplicates {DUP_F, DUP_C, DUP_P: {pairs, used, hard_limit, share_at_limit,
                 passed}}; external {groups, pairs, used, passed, failed} — without the bootstrap

x1 is the value of the original sample written into the record at the import (`p:E`), x2 — the value of the
record itself; the DL is that of the laboratory of the record (its own `dl:E`, else the table of the settings).
A record has no parent when the import found none and wrote no value of it (`no_parent`). A number that is not
there is None: t, p and df of a group whose differences are all equal, the intervals of fewer than three
pairs — each with a note of the group. Every group draws its bootstrap from the seed of the settings anew
(`limits.seed`, `limits.n_boot`; n·B ≤ 10⁸ — the realizations made are in `rma.n_boot`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import records as r
from .blanks import effective_dl
from .calc import LINES, NOTE_KEYS, SECTIONS, Context, _dl_table, _find, _lab_names, _opt, _thin
from .duplicates import EXCLUDED, Pairs, Selection, hard_limit, measure, scatter_stats, select
from .external import bias, paired_t, rma_intervals
from .filters import TYPES
from .limits import FAIL, NORM, QaqcError, gt
from .standards import group_index
from .thompson import thompson_howarth

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


def _table(ctx: Context, at: np.ndarray, x1: np.ndarray, x2: np.ndarray, hard: np.ndarray,
           hrd: np.ndarray) -> dict[str, Any]:  # fmt: skip
    """Every pair taken as a row of the sheet of a report."""
    return {"row": ctx.recs.row[at].tolist(), "x1": x1.tolist(), "x2": x2.tolist(), "mean": (0.5 * (x1 + x2)).tolist(),
            "hard": hard.tolist(), "hrd": hrd.tolist(), "ampd": (2.0 * hard).tolist()}  # fmt: skip


def _th(x1: np.ndarray, x2: np.ndarray, group: int, c: Any) -> dict[str, Any]:
    """The block of Thompson–Howarth of the pairs taken."""
    th = thompson_howarth(x1, x2, group, c)
    return {
        "state": th.state, "group": th.group, "need": th.need, "a": _opt(th.a), "b": _opt(th.b), "k": th.k,
        "groups": {"m": th.m.tolist(), "med": th.med.tolist(), "n": th.size.tolist()},
        "precision": [{"c": at, "p": _opt(p), "q": q} for q, at, p in th.precision],
    }  # fmt: skip


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
        "th": _th(x1, x2, ctx.limits.th_group, params.get("c")),
    }  # fmt: skip
    if params.get("samples"):
        out["samples"] = _table(ctx, d.at[d.used], x1, x2, m.hard, m.hrd)
    return out, d.notes


def _duplicates_line(ctx: Context) -> dict[str, Any]:
    out = {}
    for kind in DUP_KINDS:
        d = _pair_data(ctx, kind)
        out[kind] = {"pairs": int(d.at.shape[0]), "used": int(d.used.shape[0]), "hard_limit": d.limit,
                     "share_at_limit": _opt(d.measured.share_at_limit), "passed": d.measured.passed}  # fmt: skip
    return out


# --- external control ----------------------------------------------------------------------------------------------


@dataclass
class _Lab:
    """A pair of laboratories of the external control: its records and the pairs taken, in the order of the file."""

    main: str | None
    check: str | None
    pairs: int
    excluded: dict[str, int]
    used: np.ndarray  # the places of the pairs taken among the records UMP
    measured: Pairs


def _labs(ctx: Context) -> list[_Lab]:
    def make(ctx: Context) -> list[_Lab]:
        d = _pair_data(ctx, "UMP")
        main = ctx.recs.text(r.PARENT_LAB)
        of, (g_main, g_check) = group_index(main.codes[d.at].astype(np.int64) + 1,
                                            ctx.recs.text(r.LAB).codes[d.at].astype(np.int64) + 1)  # fmt: skip
        count = int(g_main.shape[0])
        why = np.bincount(of * (len(EXCLUDED) + 1) + d.sel.reason, minlength=count * (len(EXCLUDED) + 1))
        why = why.reshape(count, len(EXCLUDED) + 1)
        order = np.argsort(of[d.used], kind="stable")  # the pairs taken, group by group, in the order of the file
        bounds = np.concatenate(([0], np.cumsum(why[:, 0])))
        main_names, check_names = [None, *main.labels], _lab_names(ctx)
        out = []
        for g in range(count):
            used = d.used[order[int(bounds[g]) : int(bounds[g + 1])]]
            out.append(_Lab(main_names[int(g_main[g])], check_names[int(g_check[g])], int(why[g].sum()),
                            {name: int(why[g, code]) for code, name in enumerate(EXCLUDED, start=1)}, used,
                            measure(d.x1[used], d.x2[used], d.limit, ctx.limits.hard_share)))  # fmt: skip
        return out

    return ctx.once("external", make)  # type: ignore[no-any-return]


def _external_group(ctx: Context, d: _PairData, lab: _Lab, intervals: bool) -> dict[str, Any]:
    x1, x2, lim, n = d.x1[lab.used], d.x2[lab.used], ctx.limits, int(lab.used.shape[0])
    stats = scatter_stats(x1, x2)
    t, p, df = paired_t(x1, x2)
    ci = rma_intervals(x1, x2, lim.n_boot, lim.seed) if intervals and stats["rma"]["b"] is not None else None
    notes = []
    if n and t != t:
        notes.append("Все разности пар равны: t-критерий не определён." if n >= 2 else
                     "Меньше двух пар: t-критерий не определён.")  # fmt: skip
    if intervals and n and (ci is None or ci.b is None):
        notes.append("Интервалы RMA не определены: меньше трёх пар или нет разброса значений.")
    if ci is not None and 0 < ci.n_boot < lim.n_boot:
        notes.append(f"Реализаций бутстрапа — {ci.n_boot} вместо {lim.n_boot}: пар {n}, предел n·B ≤ 10⁸.")
    return {
        "lab_main": lab.main, "lab_check": lab.check, "pairs": lab.pairs, "used": n, "excluded": lab.excluded,
        "mean_main": stats["mean1"], "mean_check": stats["mean2"],
        "bias": _opt(bias(stats["mean1"], stats["mean2"])), "t": _opt(t), "p": _opt(p),
        "df": None if df != df else int(df), "pearson": stats["pearson"],
        "rma": {**stats["rma"], "a_ci": None if ci is None or ci.a is None else list(ci.a),
                "b_ci": None if ci is None or ci.b is None else list(ci.b),
                "n_boot": 0 if ci is None else ci.n_boot, "seed": lim.seed},
        **_shares(lab.measured), "notes": notes,
    }  # fmt: skip


def _external(ctx: Context, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    d, labs = _pair_data(ctx, "UMP"), _labs(ctx)
    intervals = params.get("intervals", True) is not False
    groups = ctx.once(f"external:{intervals}", lambda c: [_external_group(c, d, lab, intervals) for lab in labs])
    out: dict[str, Any] = {"hard_limit": d.limit, "share_limit": ctx.limits.hard_share, "groups": groups}
    if params.get("group") is not None:
        g = _find(params["group"], [(lab.main, lab.check) for lab in labs], ("lab_main", "lab_check"))
        out["points"], out["ranked"] = None, None
        if g is not None:
            lab = labs[g]
            out.update(_points(ctx, d.at[lab.used], d.x1[lab.used], d.x2[lab.used], lab.measured, d.limit, params))
            out["ranked"] = {"p": lab.measured.ranked_p.tolist(), "hard": lab.measured.ranked_hard.tolist()}
    if params.get("samples"):
        none = np.zeros(0)
        used = np.concatenate([lab.used for lab in labs]) if labs else np.zeros(0, dtype=np.int64)
        hard = np.concatenate([lab.measured.hard for lab in labs]) if labs else none
        hrd = np.concatenate([lab.measured.hrd for lab in labs]) if labs else none
        sizes = [int(lab.used.shape[0]) for lab in labs]
        out["samples"] = {"group": np.repeat(np.arange(len(labs)), sizes).tolist(),
                          **_table(ctx, d.at[used], d.x1[used], d.x2[used], hard, hrd)}  # fmt: skip
    return out, d.notes


def _external_line(ctx: Context) -> dict[str, Any]:
    labs = _labs(ctx)
    return {
        "groups": len(labs), "pairs": sum(lab.pairs for lab in labs),
        "used": sum(int(lab.used.shape[0]) for lab in labs),
        "passed": sum(lab.measured.passed is True for lab in labs),
        "failed": sum(lab.measured.passed is False for lab in labs),
    }  # fmt: skip


SECTIONS.update(duplicates=_duplicates, external=_external)
LINES.update(duplicates=_duplicates_line, external=_external_line)
NOTE_KEYS += [f"pairs:{kind}" for kind in (*DUP_KINDS, "UMP")]
