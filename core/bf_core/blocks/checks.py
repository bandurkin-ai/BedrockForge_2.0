"""Checks of a block model (ТЗ 7.31, 8.7.2, **Т8.7-5**; ADR 0023 decisions 5 and 7): blocks off the grid, sub-blocks
across the border of the parent, duplicate rows, overlapping blocks (exact or by volume), Σ of the sub-block volumes
of a parent against the volume of the parent, empty and constant fields, the total volume.

The rows come as portions in file order — (P, sz, sy, sx) ascending, repeats allowed (a model being imported is sorted
before it is checked) — so memory stays at a portion plus the last, unfinished parent. `ModelChecker.feed` takes a
portion, `finish` gives a `CheckReport`; `check_cells` reads a stored model (`blocks.check`).

A repeated row (the same P and s) keeps its first copy: the later copies are `duplicates` and take no part in the
other checks. Overlaps are never repaired — the rows stay. Overlap, by decision 5:
- `exact` — the sub-blocks of one parent are tested on the grid of the smallest block (their boxes in sub-cells; a
  parent has at most 128³ of them, below `EXACT_MAX_SUBCELLS`): a pair of boxes meets if the intervals meet on all
  three axes; a parent with many rows is painted cell by cell; the rows that meet are counted;
- `volume` — the sum of the sub-block volumes of a parent against the volume of the parent: every row of a parent
  over its volume counts (an overlap that a gap covers stays unseen). Used when a parent holds more than
  `EXACT_MAX_SUBCELLS` sub-cells, and for a parent too full to paint.
The volumes of the rows are `geometry.row_volumes`; Σ of a parent is compared in whole sub-cells, which is exact
(Т8.7-5 asks 1e−12 relative to the volume of the parent). A gap in a field is never a value: NaN, ±inf, null and
the code −1 of a category are empty.

The report: `findings` — [{code, severity (error|warning|info), message, hint, rows (the first N, the numbers in the
file), count}] (the fields add `fields`); `checks` — the stored form (`meta.checks`, docs/progress/e17-api.md).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np

from . import geometry as geo
from .schema import MAX_EXAMPLES, ROLE, GridV2, bad_model

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

EXACT_MAX_SUBCELLS = 60_000_000  # a parent with more sub-cells is tested by volume (as BF)
PAIR_BUCKETS = (4, 8, 16, 24)  # parents with up to this many rows are tested pair by pair, larger ones painted
PAINT_CELLS = 1 << 22  # sub-cells painted at once
MAX_KEY_P = (1 << 42) - 1  # the sort key shifts P by 21 bits: keep the order of absurd numbers inside uint64
KINDS = ("outside", "crossing", "duplicate", "overlap")  # the kinds of `examples`
METHOD_LABEL = {"exact": "по сетке наименьшего блока", "volume": "по объёму блоков в родительской ячейке"}
ERROR, WARNING, INFO = "error", "warning", "info"


def ru_num(value: float, digits: int = 0) -> str:
    """12 345,6 — a space between thousands, a comma before the decimals (the text for the user)."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


@dataclass
class CheckReport:
    findings: list[dict[str, Any]]
    checks: dict[str, Any]  # `meta.checks`
    rows: int

    def finding(self, code: str) -> dict[str, Any] | None:
        return next((f for f in self.findings if f["code"] == code), None)

    @property
    def ok(self) -> bool:
        """No error in the findings (warnings and notes do not count)."""
        return all(f["severity"] != ERROR for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "findings": self.findings, "checks": self.checks}


@dataclass
class _Count:
    """A counted check: how many, the first row numbers, the examples."""

    n: int = 0
    rows: list[int] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, rows: np.ndarray, limit: int, count: int | None = None) -> None:
        self.n += len(rows) if count is None else count
        if len(self.rows) < limit:
            self.rows.extend(int(r) for r in rows[: limit - len(self.rows)])


class _Rows(NamedTuple):
    """The rows of parents with 2 or more rows: P, the numbers in the file, s and w in sub-cells (int16)."""

    p: np.ndarray
    rows: np.ndarray
    s: np.ndarray
    w: np.ndarray


@dataclass
class _Field:
    kind: str
    filled: int = 0
    lo: float = math.inf
    hi: float = -math.inf


def empty_mask(values: Any, kind: str = "number") -> np.ndarray:
    """Which values are empty: not finite (a float, a number kept as an object — None), null, the code −1 of a
    category of a v1 model."""
    a = np.asarray(values)
    if a.dtype.kind == "O":
        a = np.array([math.nan if v is None else v for v in a], dtype=np.float64)
    if a.dtype.kind == "f":
        return ~np.isfinite(a)  # type: ignore[no-any-return]
    if kind == "category" and a.dtype.kind in "iu":
        return a < 0  # type: ignore[no-any-return]
    return np.zeros(len(a), dtype=bool)


def duplicate_mask(p: np.ndarray, s: np.ndarray | None = None, w: np.ndarray | None = None) -> np.ndarray:
    """For rows in any order: True for every row repeating an earlier one (the same P, s and w); the first stays. The
    importer drops these before it writes (decision 7). The same s with another w is an overlap, not a repeat."""
    key = geo.row_keys(np.minimum(np.asarray(p, dtype=np.uint64), np.uint64(MAX_KEY_P)), s)
    if w is None or s is None:
        order = np.argsort(key, kind="stable")
        eq = key[order[1:]] == key[order[:-1]]
    else:
        ww = np.asarray(w).reshape(-1, 3)
        order = np.lexsort((ww[:, 0], ww[:, 1], ww[:, 2], key))
        eq = (key[order[1:]] == key[order[:-1]]) & (ww[order[1:]] == ww[order[:-1]]).all(axis=1)
    same = np.zeros(len(key), dtype=bool)
    same[order[1:]] = eq
    return same


class ModelChecker:
    """Checks a model portion by portion. `fields` — [{name, kind}] of the columns that `feed` gets in `values`;
    `frac` — the name of the FRAC column (None: no such field, the volume of a row is the whole block)."""

    def __init__(
        self,
        grid: GridV2,
        fields: Sequence[Mapping[str, Any]] = (),
        frac: str | None = None,
        limit: int = MAX_EXAMPLES,
    ) -> None:
        self.grid, self.limit, self.frac = grid, max(1, int(limit)), frac
        self.rows = 0
        self.fields = {str(f["name"]): _Field(str(f.get("kind", "number"))) for f in fields}
        self.count = {k: _Count() for k in ("outside", "crossing", "duplicate", "overlap", "overfull")}
        self.volume_parts: list[float] = []
        self.volume_rows = self.volume_empty = 0
        self.approx = False  # some overlap was decided by volume
        self._last: int | None = None  # the key of the last row
        self._run: tuple[np.ndarray, np.ndarray] = (np.empty(0, np.int64), np.empty(0, np.int64))  # of the last key
        self._step: list[np.ndarray] = []  # overlapping rows found in this step
        self._step_ex: list[dict[str, Any]] = []
        self._carry: tuple[np.ndarray, ...] | None = None  # the last parent (p, s, w, row numbers)
        self._sub = grid.tensors is None and grid.sub.sub
        self._subcells = int(np.prod(grid.sub.div, dtype=np.int64))

    # -- one portion -------------------------------------------------------------------------------------

    def feed(
        self,
        p: Any,
        s: Any | None = None,
        w: Any | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> None:
        p = np.asarray(p, dtype=np.uint64).reshape(-1)
        n = len(p)
        if (s is None) != (w is None):
            raise bad_model("Смещение и размер субблоков заданы не вместе.")
        if self.grid.tensors is not None or n == 0 and s is None:
            s = w = None
        if s is not None:
            s, w = np.asarray(s).reshape(-1, 3), np.asarray(w).reshape(-1, 3)
            if len(s) != n or len(w) != n:
                raise bad_model("Число строк субблоков не совпадает с числом блоков.")
        base, self.rows = self.rows, self.rows + n
        if n == 0:
            return
        off = p >= np.uint64(self.grid.cells)
        cross = np.zeros(n, dtype=bool)
        if s is not None:
            cross = self.grid.sub.bad_rows(s, w) & ~off
        bad = off | cross
        s_key = None if s is None else np.minimum(s, 127)  # a crossing row keeps its place in the order
        key = geo.row_keys(np.minimum(p, np.uint64(MAX_KEY_P)), s_key)
        if (key[1:] < key[:-1]).any() or (self._last is not None and int(key[0]) < self._last):
            raise bad_model(
                "Строки модели идут не по порядку номера блока.", "Отсортируйте строки по (номер блока, sz, sy, sx)."
            )
        dup, first = self._duplicates(key, w, base)
        self._note(base, p, s, w, off, "outside")
        self._note(base, p, s, w, cross, "crossing")
        self._note(base, p, s, w, dup, "duplicate", first)
        ok = ~(bad | dup)
        if not ok.all():
            idx = np.flatnonzero(ok)
            po = p[idx]
            so, wo = (None, None) if s is None or w is None else (s[idx], w[idx])
        else:
            idx, po, so, wo = np.arange(n), p, s, w
        if len(po):
            if so is not None and wo is not None:
                self._overlaps(po, so, wo, idx + base, final=False)
            self._volume(po, so, wo, None if values is None or self.frac is None else values.get(self.frac), idx)
        self._fields(values)

    def _duplicates(self, key: np.ndarray, w: np.ndarray | None, base: int) -> tuple[np.ndarray, np.ndarray]:
        """(a row repeats an earlier one — the same P, s and w; the number of that first row). Rows of one key are
        a run (the files go by the key only): inside a run the copies are found by w; the sizes met in the last run
        of a portion go to the next one."""
        n = len(key)
        dup, first = np.zeros(n, dtype=bool), np.zeros(n, dtype=np.int64)
        eq = np.zeros(n, dtype=bool)
        eq[1:] = key[1:] == key[:-1]
        cont = self._last is not None and int(key[0]) == self._last
        eq[0] = cont
        code = np.zeros(n, dtype=np.int64) if w is None else w.astype(np.int64) @ np.array([1, 256, 65536])
        if eq.any():
            inrun = eq.copy()
            inrun[:-1] |= eq[1:]
            at = np.flatnonzero(inrun)
            k, c, rows = key[at], code[at], at.astype(np.int64) + base
            pre = len(self._run[0]) if cont else 0
            if pre:
                k = np.concatenate((np.full(pre, key[0]), k))
                c, rows = np.concatenate((self._run[0], c)), np.concatenate((self._run[1], rows))
            order = np.lexsort((rows, c, k))
            ks, cs, rs = k[order], c[order], rows[order]
            head = np.ones(len(k), dtype=bool)
            head[1:] = (ks[1:] != ks[:-1]) | (cs[1:] != cs[:-1])
            start = np.maximum.accumulate(np.where(head, np.arange(len(k)), 0))
            dsub, fsub = np.zeros(len(k), dtype=bool), np.zeros(len(k), dtype=np.int64)
            dsub[order], fsub[order] = ~head, rs[start]
            dup[at], first[at] = dsub[pre:], fsub[pre:]
        tail = np.flatnonzero(key == key[-1])
        c, rows = code[tail], tail.astype(np.int64) + base
        if cont and key[0] == key[-1]:  # the run goes on through the whole portion
            c, rows = np.concatenate((self._run[0], c)), np.concatenate((self._run[1], rows))
        c, at = np.unique(c, return_index=True)
        self._run = (c, rows[at])
        self._last = int(key[-1])
        return dup, first

    def _note(
        self,
        base: int,
        p: np.ndarray,
        s: np.ndarray | None,
        w: np.ndarray | None,
        mask: np.ndarray,
        kind: str,
        other: np.ndarray | None = None,
    ) -> None:
        if not mask.any():
            return
        rows = np.flatnonzero(mask)
        slot = self.count[kind]
        slot.add(rows + base, self.limit, len(rows))
        for r in rows[: max(0, self.limit - len(slot.examples))]:
            ex: dict[str, Any] = {"check": kind, "row": int(r) + base, "p": int(p[r])}
            ex["sub"] = None if s is None or w is None else [int(v) for v in (*s[r], *w[r])]
            if other is not None:
                ex["other_row"] = int(other[r])
            slot.examples.append(ex)

    # -- overlaps and the volume of a parent -------------------------------------------------------------

    def _overlaps(self, p: np.ndarray, s: np.ndarray, w: np.ndarray, rows: np.ndarray, final: bool) -> None:
        """Adds the rows of this portion to the unfinished parent and tests the finished parents. The rows found
        in one step all come after those of the steps before, so the first N of the whole are kept."""
        self._test_parents(p, s, w, rows, final)
        if self._step:
            slot = self.count["overlap"]
            slot.add(np.sort(np.concatenate(self._step)), self.limit)
            self._step_ex.sort(key=lambda e: e["row"])
            slot.examples.extend(self._step_ex[: max(0, self.limit - len(slot.examples))])
            self._step, self._step_ex = [], []

    def _test_parents(self, p: np.ndarray, s: np.ndarray, w: np.ndarray, rows: np.ndarray, final: bool) -> None:
        if self._carry is not None:
            cp, cs, cw, cr = self._carry
            p, s, w, rows = (np.concatenate(v) for v in ((cp, p), (cs, s), (cw, w), (cr, rows)))
            self._carry = None
        if not len(p):
            return
        if not final:
            cut = int(np.searchsorted(p, p[-1], side="left"))
            self._carry = (p[cut:], s[cut:], w[cut:], rows[cut:])
            p, s, w, rows = p[:cut], s[:cut], w[:cut], rows[:cut]
        if len(p) < 2:
            return
        new = np.empty(len(p), dtype=bool)
        new[0] = True
        new[1:] = p[1:] != p[:-1]
        gid = np.cumsum(new) - 1
        size = np.bincount(gid)[gid]
        keep = np.flatnonzero(size >= 2)  # a lone row cannot meet another nor overfill its parent
        if not len(keep):
            return
        s16, w16 = s[keep].astype(np.int16), w[keep].astype(np.int16)
        r = _Rows(p[keep], rows[keep], s16, w16)
        size = size[keep]
        gid = np.cumsum(np.concatenate(([True], r.p[1:] != r.p[:-1]))) - 1
        cells = np.prod(w16.astype(np.int64), axis=1)
        total = np.bincount(gid, weights=cells.astype(np.float64))  # whole sub-cells: exact below 2^53
        full = total > self._subcells  # Σ of the volumes of the parent > its volume (Т8.7-5)
        first = np.flatnonzero(np.concatenate(([True], gid[1:] != gid[:-1])))
        if full.any():
            self.count["overfull"].add(r.rows[first[full]], self.limit)
        if self._subcells > EXACT_MAX_SUBCELLS:
            self.approx = True
            self._flag(r, np.flatnonzero(full[gid]), None)
            return
        lo = 1
        for hi in (*PAIR_BUCKETS, 1 << 62):
            pick = np.flatnonzero((size > lo) & (size <= hi))
            lo = hi
            if not len(pick):
                continue
            if hi == 1 << 62:
                self._paint(r, pick, gid, full)
            else:
                self._pairs(r, pick, gid, hi)

    def _flag(self, r: _Rows, hit: np.ndarray, pairs: Sequence[tuple[int, int | None]] | None) -> None:
        """Counts the overlapping rows of one test (positions `hit` of `r`) and keeps the first examples."""
        if not len(hit):
            return
        self._step.append(r.rows[hit])
        for a, b in (pairs if pairs is not None else [(int(h), None) for h in hit])[: self.limit]:
            ex: dict[str, Any] = {"check": "overlap", "row": int(r.rows[a]), "p": int(r.p[a]),
                                  "sub": [int(v) for v in (*r.s[a], *r.w[a])]}  # fmt: skip
            if b is not None:
                ex["other_row"] = int(r.rows[b])
            self._step_ex.append(ex)

    def _pairs(self, r: _Rows, pick: np.ndarray, gid: np.ndarray, top: int) -> None:
        """Parents of 2…top rows: the rows are contiguous and ascending, so the pair (r, r + d) of one parent for
        d = 1 … size − 1 covers every pair; a slice, not a gather, per d."""
        g = gid[pick]
        sx, sy, sz = (np.ascontiguousarray(r.s[pick, a]) for a in range(3))
        hx, hy, hz = (sx + r.w[pick, 0], sy + r.w[pick, 1], sz + r.w[pick, 2])
        flag = np.zeros(len(pick), dtype=bool)
        found: list[tuple[int, int]] = []
        for d in range(1, min(top, len(pick))):
            hit = (g[:-d] == g[d:]) & (sx[:-d] < hx[d:]) & (sx[d:] < hx[:-d]) & (sy[:-d] < hy[d:])
            hit &= (sy[d:] < hy[:-d]) & (sz[:-d] < hz[d:]) & (sz[d:] < hz[:-d])
            at = np.flatnonzero(hit)
            if not len(at):
                continue
            flag[at] = flag[at + d] = True
            found.extend((int(pick[a]), int(pick[a + d])) for a in at[: self.limit])
        found.sort(key=lambda ab: int(r.rows[ab[0]]))
        self._flag(r, pick[flag], found)

    def _paint(self, r: _Rows, pick: np.ndarray, gid: np.ndarray, full: np.ndarray) -> None:
        """Parents of many rows: every sub-cell of every row is written as one number, a repeated number is an
        overlap. A parent over its volume cannot be painted cheaply: all its rows count (`volume`)."""
        over = full[gid[pick]]
        if over.any():
            self.approx = True
            self._flag(r, pick[over], None)
        pick = pick[~over]
        div = self.grid.sub.div
        flag = np.zeros(len(r.rows), dtype=bool)
        while len(pick):
            cells = np.prod(r.w[pick].astype(np.int64), axis=1)
            take = max(1, int(np.searchsorted(np.cumsum(cells), PAINT_CELLS, side="right")))
            while take < len(pick) and gid[pick[take]] == gid[pick[take - 1]]:
                take += 1  # a parent goes to one portion whole
            part, pick = pick[:take], pick[take:]
            c = cells[:take]
            owner = np.repeat(np.arange(len(part)), c)
            at = np.arange(len(owner)) - np.repeat(np.cumsum(c) - c, c)
            ww, ss = r.w[part][owner].astype(np.int64), r.s[part][owner].astype(np.int64)
            x, yz = at % ww[:, 0], at // ww[:, 0]
            y, z = yz % ww[:, 1], yz // ww[:, 1]
            lin = ((ss[:, 2] + z) * div[1] + (ss[:, 1] + y)) * div[0] + (ss[:, 0] + x)
            lin += (gid[part][owner] - gid[part[0]]) * self._subcells
            order = np.argsort(lin, kind="stable")
            same = lin[order][1:] == lin[order][:-1]
            hit = np.concatenate((owner[order][1:][same], owner[order][:-1][same]))
            flag[part[np.unique(hit)]] = True
        self._flag(r, np.flatnonzero(flag), None)

    def _volume(self, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, frac: Any, idx: np.ndarray) -> None:
        f = None if frac is None else np.asarray(frac, dtype=np.float64).reshape(-1)[idx]
        vol = geo.row_volumes(self.grid, p, s, w, f)
        total, empty = geo.volume_sum(vol)
        self.volume_parts.append(total)
        self.volume_rows += len(vol) - empty
        self.volume_empty += empty

    # -- fields ------------------------------------------------------------------------------------------

    def _fields(self, values: Mapping[str, Any] | None) -> None:
        for name, st in self.fields.items():
            if values is None or name not in values:
                continue
            a = np.asarray(values[name])
            ok = ~empty_mask(a, st.kind)
            v = a[ok] if not ok.all() else a
            if not len(v):
                continue
            st.filled += len(v)
            st.lo, st.hi = min(st.lo, float(v.min())), max(st.hi, float(v.max()))

    # -- the result --------------------------------------------------------------------------------------

    def finish(self, stats_fields: Iterable[Mapping[str, Any]] = ()) -> CheckReport:
        """The report. `stats_fields` — the fields of other files (`fld:…`): empty and constant by their `stats`."""
        if self._carry is not None:
            cp, cs, cw, cr = self._carry
            self._carry = None
            self._overlaps(cp, cs, cw, cr, final=True)
        empty, constant = [], []
        for name, st in self.fields.items():
            if self.rows and st.filled == 0:
                empty.append(name)
            elif st.filled >= 2 and st.lo == st.hi:
                constant.append(name)
        for f in stats_fields:
            stats = f.get("stats") or {}
            if not self.rows or "count" not in stats:
                continue
            if stats["count"] == 0:
                empty.append(str(f["name"]))
            elif stats["count"] >= 2 and stats.get("min") is not None and stats.get("min") == stats.get("max"):
                constant.append(str(f["name"]))
        volume = math.fsum(self.volume_parts)
        method = "volume" if self.approx else "exact"
        c = self.count
        findings = self._findings(empty, constant, volume, method)
        examples = self._examples()
        checks = {
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duplicates": c["duplicate"].n,
            "overlaps": {"n": c["overlap"].n, "method": method},
            "empty": empty,
            "constant": constant,
            "volume": volume,
            "examples": examples,
        }
        return CheckReport(findings, checks, self.rows)

    def _examples(self) -> list[dict[str, Any]]:
        lists = [self.count[k].examples for k in ("outside", "crossing", "duplicate", "overlap")]
        out: list[dict[str, Any]] = []
        for i in range(self.limit):
            for lst in lists:
                if i < len(lst) and len(out) < MAX_EXAMPLES:
                    ex = dict(lst[i])
                    out.append(ex)
        return out

    def _findings(self, empty: list[str], constant: list[str], volume: float, method: str) -> list[dict[str, Any]]:
        c, out = self.count, []

        def add(code: str, severity: str, message: str, hint: str, count: int, rows: list[int], **extra: Any) -> None:
            out.append({"code": code, "severity": severity, "message": message, "hint": hint,
                        "rows": sorted(rows)[: self.limit], "count": count, **extra})  # fmt: skip

        if c["outside"].n:
            add("outside", ERROR, f"Блоки за пределами сетки: {ru_num(c['outside'].n)}.",
                "Проверьте начало и размеры сетки или столбец номера блока в исходном файле.",
                c["outside"].n, c["outside"].rows)  # fmt: skip
        if c["crossing"].n:
            add("crossing", ERROR, f"Субблоки выходят за границу родительского блока: {ru_num(c['crossing'].n)}.",
                "Субблок должен целиком лежать в своём родителе: проверьте размеры блоков и родителя.",
                c["crossing"].n, c["crossing"].rows)  # fmt: skip
        if c["duplicate"].n:
            add("duplicates", WARNING, f"Повторяющиеся блоки: {ru_num(c['duplicate'].n)}. Оставлена первая строка.",
                "Повторы не меняют модель; проверьте исходный файл, если они не ожидались.",
                c["duplicate"].n, c["duplicate"].rows)  # fmt: skip
        if c["overlap"].n:
            how = METHOD_LABEL[method]
            add("overlaps", ERROR, f"Перекрывающиеся блоки: {ru_num(c['overlap'].n)} ({how}).",
                "Строки не удалены: объём и тоннаж по ним завышены. Исправьте модель в исходной программе.",
                c["overlap"].n, c["overlap"].rows, method=method)  # fmt: skip
        if c["overfull"].n:
            add("parent_overfull", ERROR,
                f"Сумма объёмов субблоков больше объёма родителя: {ru_num(c['overfull'].n)} блоков.",
                "Субблоки перекрываются: проверьте их смещения и размеры.",
                c["overfull"].n, c["overfull"].rows)  # fmt: skip
        if empty:
            add("empty_fields", WARNING, f"Пустые поля (нет ни одного значения): {', '.join(empty)}.",
                "Проверьте код пустого при импорте или удалите эти поля.", len(empty), [], fields=empty)  # fmt: skip
        if constant:
            add("constant_fields", INFO, f"Постоянные поля (одно значение у всех блоков): {', '.join(constant)}.",
                "Такое поле не различает блоки; убедитесь, что столбец прочитан верно.",
                len(constant), [], fields=constant)  # fmt: skip
        note = "Блоки без значения FRAC в объём не входят." if self.frac else "Объём — по размерам блоков."
        add("volume", INFO, f"Суммарный объём блоков: {ru_num(volume)} м³."
            + (f" Без значения FRAC: {ru_num(self.volume_empty)}." if self.volume_empty else ""),
            note, self.volume_empty, [])  # fmt: skip
        return out


def check_arrays(
    grid: GridV2,
    p: Any,
    s: Any | None = None,
    w: Any | None = None,
    values: Mapping[str, Any] | None = None,
    fields: Sequence[Mapping[str, Any]] | None = None,
    frac: str | None = None,
    limit: int = MAX_EXAMPLES,
    portion: int = 1 << 20,
) -> CheckReport:
    """A whole model in memory (tests, small models): rows ascending by (P, sz, sy, sx), repeats allowed."""
    p = np.asarray(p, dtype=np.uint64).reshape(-1)
    names = list(values or {}) if fields is None else None
    spec = fields if fields is not None else [{"name": n, "kind": "number"} for n in names or ()]
    chk = ModelChecker(grid, spec, frac, limit)
    for a in range(0, max(len(p), 1), portion):
        b = a + portion
        chk.feed(p[a:b], None if s is None else np.asarray(s)[a:b], None if w is None else np.asarray(w)[a:b],
                 None if values is None else {k: np.asarray(v)[a:b] for k, v in values.items()})  # fmt: skip
    return chk.finish()


def check_cells(
    store: BlobStore,
    shas: Mapping[str, str] | str,
    meta: Mapping[str, Any],
    limit: int = MAX_EXAMPLES,
    tick: Callable[[float | None, str | None], None] | None = None,
) -> CheckReport:
    """The stored model (`meta` v2, `shas` — {file role: SHA-256} of `Model.shas()`, or the SHA-256 of `cells` alone):
    `blocks.check`. The columns come in the memory form (`Reader`), by the row groups of the files. The fields of other
    files (`fld:…`) are judged by their `stats`; FRAC kept in such a file is read from its own file for the volume.
    `tick(fraction, message)` is called per portion and may raise to cancel."""
    from .model import Reader

    grid = GridV2.of(meta["grid"])
    flds = list(meta.get("fields") or [])
    here = [f for f in flds if (f.get("file") or ROLE) == ROLE]
    other = [f for f in flds if (f.get("file") or ROLE) != ROLE]
    frac = next((str(f["name"]) for f in flds if f.get("role") == "frac"), None)
    sub = bool(meta.get("sub")) and grid.tensors is None
    reader = Reader(store, {ROLE: shas} if isinstance(shas, str) else shas, flds, int(meta.get("rows") or 0),
                    bool(meta.get("sub")))  # fmt: skip
    if "rows" not in meta:
        reader.rows = sum(reader.group_sizes())
    rows = reader.rows
    chk = ModelChecker(grid, [{"name": f["name"], "kind": f["kind"]} for f in here], frac, limit)
    names = [str(f["name"]) for f in here]
    read = names if frac is None or frac in names else [*names, frac]
    for first, _n, p, s, w, cols in reader.pieces(read, p=True, sub=sub):
        if tick is not None:
            tick(min(first / max(rows, 1), 1.0), f"Проверка: {ru_num(first)} из {ru_num(rows)} строк")
        chk.feed(p, s, w, cols)
    if tick is not None:
        tick(1.0, None)
    return chk.finish(other)
