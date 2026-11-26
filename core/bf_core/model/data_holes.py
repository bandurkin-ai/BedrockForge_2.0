"""Data of a model along drillholes (ТЗ 8.9.2; ADR 0026 решения 5, 6, 17): contacts and s(t) of a domain, the sides of
a vein, the grades of a shell.

    table = read_code_table(project, spec.data, tick)               # the intervals with the code, the trajectories
    pts = domain_points_of(table, codes, d_max=…, step=…, group=…)  # the group's codes → Points
    pts = domain_points(lines, in_d, traj, dense, d_max=…, step=…, group=…)   # the same on arrays
    hanging, footwall = vein_sides(lines, in_v, traj, dense)
    pts = shell_points(project, spec, group, gid, k, tick)          # grades of composites or of a table of samples

`lines` is `composites.intervals.Lines` (holes ascending, rows by depth, no overlaps), `in_d` the state of every row:
`True` / 1 — in D, `False` / 0 — not in D, −1 — no code (a gap: no points, never «outside D»), `None` — the table
has no code; `traj` — `drillholes.desurvey.Trajectories`, `dense` — the index of every hole of `lines` in `traj`
(−1 — none). The distance of s(t) is along the hole (depth), the coordinates are `Trajectories.positions` (метод 7.11).
Points deeper than the end of the path are dropped and counted; a hole without a trajectory gives no points.

The result is the same bit for bit with any number of threads and any order of the holes or rows of the tables
(the order of the points is canonical, `data.assemble`). numba is imported inside the functions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .data import KIND, WING_NONE, Points, Tick, assemble, check_size
from .spec_parts import POINTS_MAX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .spec import DataSel, Group, ModelSpec

F8 = NDArray[np.float64]
EPS = 1e-6  # m: depths closer than this are one depth (`composites.intervals.DEPTH_EPS`)
BEYOND = 1e-6  # m: a point deeper than the end of the path by more is dropped
STRIP = 4096  # holes of a strip (`composites.strips.STRIP_HOLES`)


def _fraction(tick: Tick | None, lo: float, hi: float, text: str) -> Any:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f, text))


# --- the arrays of the domain -----------------------------------------------------------------------------------


def _known(traj: Any, dense: Any, n_holes: int) -> tuple[NDArray[np.int64], NDArray[np.bool_]]:
    """(the index in `traj` of every hole, which holes have a path)."""
    idx = np.ascontiguousarray(dense, dtype=np.int64)
    if idx.shape[0] != n_holes:
        raise ValueError("dense must be per hole of the lines")
    off = np.asarray(traj.offsets, dtype=np.int64)
    known = (idx >= 0) & (idx < traj.n_holes)
    if traj.n_holes:
        at = np.where(known, idx, 0)
        known &= off[at + 1] > off[at]
    return idx, known


def _states(lines: Any, in_d: Any) -> NDArray[np.int8]:
    st = np.array(in_d, copy=True)
    if st.shape != (lines.n,):
        raise ValueError("in_d must be per row of the lines")
    return st.astype(np.int8)  # bool → 0 / 1; −1 stays


def domain_points(
    lines: Any,
    in_d: Any,
    traj: Any,
    dense: Any,
    *,
    d_max: float,
    step: float,
    group: int,
    names: Sequence[str] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
    limit: int = POINTS_MAX,
) -> Points:
    """Contacts, s(t) and ±D_max of a domain along the holes (module docstring); `names` — the name of every hole
    of `lines` (the source of its points), by default «скважина <id>». 422 `too_many_points` over `limit` points."""
    if in_d is None:
        return Points.empty(notes=["В таблице нет кодов: по скважинам данных нет."])
    from bf_core.composites import strips

    from . import data_kernels as kern

    nh = int(lines.n_holes)
    st = _states(lines, in_d)
    idx, known = _known(traj, dense, nh)
    counts_rows = np.diff(lines.starts)
    lost = ~known & (counts_rows > 0)
    if (~known).any():
        st[np.repeat(~known, counts_rows)] = -1  # no path, no points
    at = np.where(known, idx, 0)
    length = np.where(known, traj.lengths()[at], np.nan) if traj.n_holes else np.full(nh, np.nan)
    starts = np.ascontiguousarray(lines.starts, dtype=np.int64)
    frm = np.ascontiguousarray(lines.frm, dtype=np.float64)
    to = np.ascontiguousarray(lines.to, dtype=np.float64)
    d, s = float(d_max), float(step)
    none = (np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0, np.int8), np.zeros(0, np.int64))
    counts = strips.run(lambda lo, hi: int(kern.domain_strip(lo, hi, starts, frm, to, st, d, s, EPS, False, *none)),
                        nh, threads=threads, tick=_fraction(tick, 0.0, 0.2, "Данные модели: счёт точек"))  # fmt: skip
    total = int(sum(counts))
    check_size(POINTS_MAX - int(limit) + total)

    def fill(lo: int, hi: int) -> tuple[Any, ...]:
        n = counts[lo // STRIP]
        oh, ot, of = np.empty(n, np.int64), np.empty(n), np.empty(n)
        ok, orow = np.empty(n, np.int8), np.empty(n, np.int64)
        kern.domain_strip(lo, hi, starts, frm, to, st, d, s, EPS, True, oh, ot, of, ok, orow)
        inside = ot <= length[oh] + BEYOND
        sel = np.flatnonzero(inside)
        xyz = np.empty((sel.shape[0], 3))
        if sel.shape[0]:
            xyz[:] = traj.positions(idx[oh[sel]], ot[sel])
        return oh[sel], ot[sel], of[sel], ok[sel], orow[sel], xyz, n - sel.shape[0]

    parts = strips.run(fill, nh, threads=threads, tick=_fraction(tick, 0.2, 1.0, "Данные модели: точки по стволам"))
    hole = np.concatenate([p[0] for p in parts]) if parts else np.zeros(0, np.int64)
    depth, f, kind, row, xyz = (
        np.concatenate([p[k] for p in parts]) if parts else np.zeros(0) for k in (1, 2, 3, 4, 5)
    )
    deeper = int(sum(p[6] for p in parts))
    if xyz.size == 0:
        xyz = np.zeros((0, 3))
    row = row.astype(np.int64)
    kinds = np.asarray(kind, dtype=np.int8)
    inner = np.maximum(row, 0)
    span = np.column_stack([np.where(kinds == KIND["contact"], depth, frm[inner] if frm.size else depth),
                            np.where(kinds == KIND["contact"], depth, to[inner] if to.size else depth)])  # fmt: skip
    labels = list(names) if names is not None else [f"скважина {int(h)}" for h in lines.holes]
    used = np.unique(hole)
    notes = []
    if lost.any():
        notes.append(f"Скважин без траектории — {int(lost.sum())}: данных по ним нет. Проверьте координаты устьев.")
    if deeper:
        notes.append(f"Точек глубже конца ствола — {deeper}: отброшены.")
    n = int(f.shape[0])
    raw = Points(xyz.reshape(-1, 3), f.astype(np.float64), kinds, np.full(n, group, np.int32), np.full(n, WING_NONE,
                 np.int8), np.searchsorted(used, hole).astype(np.int32), span.reshape(-1, 2), np.full(n, -1, np.int64),
                 [labels[h] for h in used.tolist()], {"points": n, "no_trajectory": int(lost.sum()),
                                                       "no_depth": deeper}, notes)  # fmt: skip
    return assemble([raw], tick=tick)


# --- the table of codes -----------------------------------------------------------------------------------------


@dataclass
class CodeTable:
    """The intervals of the table of codes of a set for `domain_points` (read once for all the groups)."""

    lines: Any  # composites.intervals.Lines
    row_code: NDArray[np.int32]  # per row of `lines`: an index into `labels`, −1 — no code
    labels: list[str]
    names: list[str]  # hole names, per hole of `lines`
    dense: NDArray[np.int64]  # per hole of `lines`: the index in `traj`
    traj: Any
    notes: list[str]


def _table_id(text: str, param: str) -> int:
    try:
        return int(str(text).strip())
    except ValueError:
        raise ApiError(404, "table_not_found", "Таблица не найдена.", "Выберите таблицу из списка набора.") from None


def _dense_of(hole_ids: Any, wanted: Any) -> NDArray[np.int64]:
    """The dense index of every id of `wanted` among the ascending `hole_ids` of the set; −1 — not there."""
    ids = np.ascontiguousarray(hole_ids, dtype=np.int64)
    want = np.ascontiguousarray(wanted, dtype=np.int64)
    if ids.shape[0] == 0:
        return np.full(want.shape[0], -1, dtype=np.int64)
    at = np.searchsorted(ids, want)
    at[at == ids.shape[0]] = 0
    return np.where(ids[at] == want, at, -1).astype(np.int64)


def _labels_of(src: Any, name: str) -> tuple[NDArray[np.int32], list[str]]:
    """The code of every row as an index into the labels; numbers are written `format(x, ".15g")`."""
    if name in src.codes:
        return np.asarray(src.codes[name], dtype=np.int32), list(src.labels[name])
    vals = np.asarray(src.numbers[name], dtype=np.float64)
    ok = ~np.isnan(vals)
    uniq, inv = np.unique(vals[ok], return_inverse=True)
    out = np.full(vals.shape[0], -1, dtype=np.int32)
    out[ok] = inv
    return out, [format(float(u), ".15g") for u in uniq.tolist()]


def read_code_table(project: Any, sel: DataSel, tick: Tick | None = None) -> CodeTable:
    """The table `sel.table` of the set `sel.set_id` with the field `sel.field`, and the trajectories of the set."""
    from bf_core.composites import intervals as iv
    from bf_core.composites import source

    table_id = _table_id(sel.table, "data.table")
    src = source.read(project.engine, sel.set_id, table_id, [sel.field],
                      cache_dir=Path(project.folder) / "cache" / "tables", tick=tick)  # fmt: skip
    name = next(iter(src.fields))
    row_code, labels = _labels_of(src, name)
    lines = iv.build_lines(src.hole_ids, src.from_m, src.to_m, ids=src.row_ids)
    notes = lines.notes(src.name)
    if src.skipped:
        notes.append(f"Таблица «{src.name}»: строк без глубины — {src.skipped}, в расчёт не вошли.")
    by_id = dict(zip(src.hole_db_ids.tolist(), src.holes, strict=True))
    st = source.trajectories(project, sel.set_id, tick=tick)
    return CodeTable(lines, np.asarray(lines.take(row_code), dtype=np.int32), labels,
                     [by_id[int(h)] for h in lines.holes.tolist()], _dense_of(st.hole_ids, lines.holes), st.traj,
                     list(dict.fromkeys(notes)))  # fmt: skip


def states_of(table: CodeTable, codes: Sequence[str]) -> NDArray[np.int8]:
    """1 — the row has a code of `codes`, 0 — another code, −1 — no code."""
    want = {str(c) for c in codes}
    lut = np.array([lab in want for lab in table.labels] + [False], dtype=np.int8)
    return np.where(table.row_code < 0, np.int8(-1), lut[table.row_code]).astype(np.int8)


def domain_points_of(table: CodeTable, codes: Sequence[str], *, d_max: float, step: float, group: int,
                     limit: int = POINTS_MAX, threads: int = 1, tick: Tick | None = None) -> Points:  # fmt: skip
    got = domain_points(table.lines, states_of(table, codes), table.traj, table.dense, d_max=d_max, step=step,
                        group=group, names=table.names, threads=threads, tick=tick, limit=limit)  # fmt: skip
    if table.notes:
        got = Points(got.xyz, got.f, got.kind, got.group, got.wing, got.src, got.span, got.vertex, got.sources,
                     got.counts, [*table.notes, *got.notes], got.levels)  # fmt: skip
    return got


# --- veins --------------------------------------------------------------------------------------------------------


def vein_sides(lines: Any, in_v: Any, traj: Any, dense: Any, *, group: int | None = None) -> tuple[F8, F8]:
    """The hanging wall and the footwall of the intervals of a vein (решение 17): for every stretch of touching rows in
    the vein — the points of the path at its top and its bottom; of the two the higher one (z) is the hanging wall (at
    equal z — the one at FROM). Two arrays (n, 3), sorted by (x, y, z); holes without a path give nothing. `group` is
    only the id of the vein (the sides are not tagged: the caller keeps them per vein)."""
    nh = int(lines.n_holes)
    st = _states(lines, in_v)
    idx, known = _known(traj, dense, nh)
    hr = np.repeat(np.arange(nh), np.diff(lines.starts))
    is_v = (st == 1) & known[hr]
    prev = np.zeros(lines.n, dtype=bool)
    if lines.n > 1:
        prev[1:] = is_v[1:] & is_v[:-1] & (hr[1:] == hr[:-1]) & (lines.frm[1:] - lines.to[:-1] <= EPS)
    nxt = np.zeros(lines.n, dtype=bool)
    nxt[:-1] = prev[1:]
    top, bottom = is_v & ~prev, is_v & ~nxt
    hole = idx[hr[top]]
    a = np.asarray(traj.positions(hole, lines.frm[top]), dtype=np.float64).reshape(-1, 3)
    b = np.asarray(traj.positions(hole, lines.to[bottom]), dtype=np.float64).reshape(-1, 3)
    upper = a[:, 2] >= b[:, 2]
    hang, foot = np.where(upper[:, None], a, b), np.where(upper[:, None], b, a)
    return _sorted(hang), _sorted(foot)


def _sorted(p: F8) -> F8:
    p = p[np.isfinite(p).all(axis=1)] + 0.0
    return np.ascontiguousarray(p[np.lexsort((p[:, 2], p[:, 1], p[:, 0]))])


def vein_sides_of(table: CodeTable, codes: Sequence[str], *, tick: Tick | None = None) -> tuple[F8, F8, list[str]]:
    hang, foot = vein_sides(table.lines, states_of(table, codes), table.traj, table.dense)
    return hang, foot, list(table.notes)


# --- grades of a shell ---------------------------------------------------------------------------------------------


@dataclass
class GradeRows:
    """The samples of a field of a table: the middle of every interval on its hole and the value."""

    xyz: F8
    g: F8
    hole: NDArray[np.int64]  # per row: the index in `names`
    names: list[str]
    frm: F8
    to: F8
    notes: list[str]


def read_grades(
    project: Any, set_id: int, table_id: int, field: str, param: str, tick: Tick | None = None
) -> GradeRows:
    """The numbers of `field` of a table and the coordinates of the middles of its intervals (ТЗ 7.11). A row without
    a path has NaN coordinates, a missing value is NaN: neither becomes 0."""
    from bf_core.composites import source

    src = source.read(project.engine, set_id, table_id, [field], cache_dir=Path(project.folder) / "cache" / "tables",
                      tick=tick)  # fmt: skip
    name = next(iter(src.fields))
    if name not in src.numbers:
        raise ApiError(422, "bad_param", f"Поле «{name}» не числовое.", "Выберите числовое поле содержания.",
                       {"param": param})  # fmt: skip
    st = source.trajectories(project, set_id, tick=tick)
    per_hole = np.diff(src.starts)
    dense = np.repeat(_dense_of(st.hole_ids, src.hole_db_ids), per_hole)
    mid = 0.5 * (src.from_m + src.to_m)
    xyz = np.asarray(st.traj.positions(dense, mid), dtype=np.float64).reshape(-1, 3)
    notes = [f"Таблица «{src.name}»: строк без глубины — {src.skipped}, в расчёт не вошли."] if src.skipped else []
    g = np.asarray(src.numbers[name], dtype=np.float64)
    hole = np.repeat(np.arange(len(src.holes)), per_hole)
    frm, to = np.asarray(src.from_m, dtype=np.float64), np.asarray(src.to_m, dtype=np.float64)
    return GradeRows(xyz, g, hole, list(src.holes), frm, to, notes)


def _result_table(project: Any, result_id: int, param: str) -> tuple[int, int]:
    """(set id, table id) of a result of sample processing (composites)."""
    from bf_core.db import dialect
    from bf_core.db.schema import dh_results

    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(
            sa.select(dh_results.c.set_id, dh_results.c.table_id).where(dh_results.c.id == result_id)
        ).first()
    if row is None:
        raise ApiError(404, "result_not_found", "Результат обработки проб не найден.",
                       "Обновите раздел «Пробы» и выберите композиты заново.")  # fmt: skip
    if row.table_id is None:
        raise ApiError(422, "bad_param", "У результата нет таблицы композитов.", "Выберите композиты из списка.",
                       {"param": param})  # fmt: skip
    return int(row.set_id), int(row.table_id)


def shell_points(project: Any, spec: ModelSpec, group: Group, gid: int, k: int, tick: Tick | None = None) -> Points:
    """The grades of a shell as points (решение 6): `f` is T(g) of a numeric shell (linear, log, nscore) and the grade
    itself of an indicator shell (its values — `data.indicator` — come from the thresholds); `levels` of the group are
    T(t) for the numeric shell and t for the indicator one. A missing grade is no point."""
    from . import data_manual

    p = group.params
    base = f"groups.{k}.params"
    if p["source"] == "composites":
        set_id, table_id = _result_table(project, int(p["result_id"]), f"{base}.result_id")
    else:
        chosen = p.get("set_id") if p.get("set_id") is not None else (spec.data.set_id if spec.data else None)
        if chosen is None:
            raise ApiError(422, "bad_param", "Для таблицы проб не выбран набор скважин.", "Выберите набор скважин.",
                           {"param": f"{base}.set_id"})  # fmt: skip
        set_id, table_id = int(chosen), _table_id(p["table"], f"{base}.table")
    rows = read_grades(project, set_id, table_id, p["field"], f"{base}.field", tick)
    indicator = p["mode"] == "indicator"
    graded = data_manual.grade_points(rows.xyz, rows.g, transform="linear" if indicator else p["transform"],
                                      thresholds=p["thresholds"])  # fmt: skip
    values = np.asarray(graded.values)
    keep = np.flatnonzero(~np.isnan(values))
    f = np.asarray(rows.g if indicator else values)[keep]
    hole = rows.hole[keep]
    used = np.unique(hole)
    n = int(keep.shape[0])
    levels = graded.levels if graded.levels is not None else np.zeros(0)
    return Points(
        np.ascontiguousarray(rows.xyz[keep]) + 0.0,
        f.astype(np.float64),
        np.full(n, KIND["value"], np.int8),
        np.full(n, gid, np.int32),
        np.full(n, WING_NONE, np.int8),
        np.searchsorted(used, hole).astype(np.int32),
        np.column_stack([rows.frm[keep], rows.to[keep]]).reshape(-1, 2),
        np.full(n, -1, np.int64),
        [rows.names[h] for h in used.tolist()],
        {"points": n},
        [*rows.notes, *graded.notes],
        {gid: [float(x) for x in levels]},
    )
