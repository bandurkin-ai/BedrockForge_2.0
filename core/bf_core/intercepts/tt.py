"""«Истинная мощность»: TT, TT_FLAG, TT_SRC and the angles of intervals, and the summary (ТЗ 8.5.1–8.5.2;
ADR 0021 §3–4, 8, 12, решения 4, 5, 13, 16, 20).

    t = of_table(spec, lines, st, tables={table id: Lines}, mesh=None, bodies=None, threads=…, tick=…) -> TtTable
        the rows of a table snapshot `composites.source.Lines` (the job `intercept.tt`, the preview of a hole);
        `st` — the trajectories of the set (`drillholes.trajectory.SetTrajectories`)
    t = compute(spec, rows, inputs, threads=…, tick=…) -> TtTable
        any intervals `orient.Intervals` (the candidates of the rule 8.5.3) with `orient.Inputs`
        t.tt                  float64 [n]  TT, m; NaN — none (a missing one is never a zero)
        t.flag                uint8 [n]    TT_FLAG — the sum of the bits below
        t.src                 uint8 [n]    `orient.SRC_*`;  t.src_text() — TT_SRC
        t.alpha, t.dipdir     float64 [n]  ALPHA, DIPDIR, degrees; NaN — none
        t.theta, t.azimuth    float64 [n]  HOLE_THETA, HOLE_AZ at the middle of the interval; NaN — no trajectory
        t.summary, t.journal  numbers for `dh_results.report`; lines of the job log
        t.columns(angles) -> [(name, "number" | "text", values)]   the fields written into the table, in order
    counts(src, flag) -> {"src": {TT_SRC: rows}, "flags": {"1": rows, "2": …, "4": …, "8": …}}
    warm()                 compiles every kernel of the true thickness (the step `intercepts`)

`spec` — the parsed parameters (`spec.TtSpec`, `spec.RunSpec`): `orientation` (0–3 sources), `eps`, `step`.
An empty orientation is allowed (8.5.3 without one): TT is empty, TT_SRC — «нет».

The value. The plane of an interval comes from the chain of sources (`orient.resolve`), one per interval; then
TT = Σ_j l_j·|n·d_j| along the hole (`directions.thickness`). An interval with a core measurement inside (one, or
those that were not turned into a world normal) has TT = l·sin α_к (ТЗ 8.5.2).

TT_FLAG:
    1  TT / l < ε_в — the hole runs almost along the body; TT is written as it came out (Т8.5-3: 0 and the flag)
    2  no plane: no source gave one, or the row is out of the calculation (no depth, TO ≤ FROM) — TT is empty,
       TT_SRC — «нет»
    4  a core measurement that was not turned (θ < 1°, no trajectory): TT = l·sin α_к, ALPHA and DIPDIR are empty
    8  the hole has no trajectory (or no direction on a part of the interval): TT is empty — but for an interval
       with a core measurement inside; HOLE_THETA and HOLE_AZ are empty
Every empty TT has the bit 2 or 8; nothing is left out silently — the journal counts such rows.

Summary: `rows`; `src`, `flags` (`counts`); `length` — Σ LENGTH of the rows in the calculation, `tt` — Σ TT,
`with_tt` — rows with a TT; `excluded` — rows out of the calculation; `capped` — intervals of more than
`directions.MAX_PARTS` parts (their step grew); `absent` — sources that were not there; `sources` — per source
of the chain {source, rows, …}; `of_table` adds `skipped` — rows without FROM or TO that the reading left out.
The sums are compensated inside a strip of rows and exact across the strips: the columns and the summary are
the same bit for bit with 1, 2, 4 and 8 threads (ТЗ 8.0.2). `tick` gets the share done and may raise to cancel.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import numpy as np

from bf_core.composites import strips
from bf_core.composites.strips import Tick
from bf_core.drillholes.desurvey import Trajectories
from bf_core.jit import njit

from . import EPS_DEFAULT, STEP_DEFAULT, orient
from . import core_orient as co
from . import directions as dr
from .formulas import FLAG_CORE_LOCAL, FLAG_NO_ORIENTATION, FLAG_NO_PATH, FLAG_PARALLEL

ALGO = "tt-1"  # the version of the rules above — `dh_results.algo`
BITS = (FLAG_PARALLEL, FLAG_NO_ORIENTATION, FLAG_CORE_LOCAL, FLAG_NO_PATH)
N_SRC = len(orient.SRC_LABELS)


@njit(cache=True, nogil=True)
def _sums(frm, to, tt, src, flag, r0, r1):  # type: ignore[no-untyped-def]
    """(Σ LENGTH of the rows in the calculation, Σ TT, rows with a TT, rows of every source, rows of every bit
    of the flag) of r0 ≤ r < r1: compensated sums (Neumaier) in the order of the rows."""
    s_len = c_len = s_tt = c_tt = 0.0
    n_tt = 0
    by_src = np.zeros(N_SRC, dtype=np.int64)
    by_bit = np.zeros(8, dtype=np.int64)
    for r in range(r0, r1):
        by_src[src[r]] += 1
        bits = flag[r]
        for b in range(8):
            by_bit[b] += (bits >> b) & 1
        v = to[r] - frm[r]
        if v > 0.0 and v < np.inf:  # NaN fails
            t = s_len + v
            c_len += (s_len - t) + v if abs(s_len) >= v else (v - t) + s_len
            s_len = t
        v = tt[r]
        if v == v:
            n_tt += 1
            t = s_tt + v
            c_tt += (s_tt - t) + v if abs(s_tt) >= abs(v) else (v - t) + s_tt
            s_tt = t
    return s_len + c_len, s_tt + c_tt, n_tt, by_src, by_bit


@dataclass(frozen=True, eq=False)
class TtTable:
    """The true thickness of intervals — see the module docstring."""

    tt: np.ndarray
    flag: np.ndarray
    src: np.ndarray
    alpha: np.ndarray
    dipdir: np.ndarray
    theta: np.ndarray
    azimuth: np.ndarray
    summary: dict[str, Any]
    journal: list[str]

    def src_text(self) -> np.ndarray:
        """TT_SRC: the source of every row as a text (an object array)."""
        return np.array(orient.SRC_LABELS, dtype=object)[self.src]  # type: ignore[no-any-return]

    def columns(self, angles: bool = True) -> list[tuple[str, str, np.ndarray]]:
        """(name, kind, values) of the fields: TT, TT_FLAG, TT_SRC and, with `angles`, ALPHA, DIPDIR, HOLE_THETA,
        HOLE_AZ — the order of `spec.TtSpec.columns()`."""
        out = [("TT", "number", self.tt), ("TT_FLAG", "number", self.flag), ("TT_SRC", "text", self.src_text())]
        if angles:
            out += [("ALPHA", "number", self.alpha), ("DIPDIR", "number", self.dipdir),
                    ("HOLE_THETA", "number", self.theta), ("HOLE_AZ", "number", self.azimuth)]  # fmt: skip
        return out


def _counts(by_src: Any, by_bit: Any) -> dict[str, dict[str, int]]:
    order = [*range(1, N_SRC), orient.SRC_NONE]
    return {
        "src": {orient.SRC_LABELS[k]: int(by_src[k]) for k in order if by_src[k]},
        "flags": {str(b): int(by_bit[b.bit_length() - 1]) for b in BITS},
    }


def counts(src: Any, flag: Any) -> dict[str, dict[str, int]]:
    """Rows by TT_SRC (the sources met; «нет» last) and by the bits of TT_FLAG."""
    bits = np.asarray(flag, dtype=np.uint8)
    by_bit = [int(np.count_nonzero(bits & (1 << b))) for b in range(8)]
    return _counts(np.bincount(np.asarray(src, dtype=np.uint8), minlength=N_SRC), by_bit)


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def _pathless(traj: Trajectories, dense: np.ndarray) -> np.ndarray:
    """bool: the hole of the index has no trajectory."""
    off = np.asarray(traj.offsets, dtype=np.int64)
    n_holes = off.shape[0] - 1
    known = (dense >= 0) & (dense < n_holes)
    if n_holes > 0:
        at = np.where(known, dense, 0)
        known &= off[at + 1] > off[at]
    return ~known  # type: ignore[no-any-return]


def _journal(o: orient.Orientation, t: dr.Thickness, got: dict[str, dict[str, int]], eps: float) -> list[str]:
    n = orient._count
    out = list(o.journal)
    if t.n_bad:
        out.append(f"Интервалов без глубины или с «до» ≤ «от» — {n(t.n_bad)}: вне расчёта, TT пусто (TT_FLAG 2).")
    none = got["src"].get(orient.SRC_LABELS[orient.SRC_NONE], 0) - t.n_bad  # a row out of the calculation is «нет»
    if none:
        out.append(f"Интервалов без ориентировки — {n(none)}: TT пусто (TT_FLAG 2).")
    lost = got["flags"][str(FLAG_NO_PATH)]
    if lost:
        out.append(f"Интервалов скважин без траектории — {n(lost)}: TT пусто, кроме интервалов с замером керна "
                   f"(TT_FLAG 8).")  # fmt: skip
    along = got["flags"][str(FLAG_PARALLEL)]
    if along:
        limit = f"{eps:g}".replace(".", ",")
        out.append(f"Интервалов, где скважина почти параллельна телу (TT / длина < {limit}), — {n(along)}: TT записана "
                   f"как получилась (TT_FLAG 1).")  # fmt: skip
    if t.n_capped:
        out.append(f"Интервалов длиннее {n(dr.MAX_PARTS)} частей ствола — {n(t.n_capped)}: часть ствола для них "
                   f"увеличена.")  # fmt: skip
    return out


def compute(spec: Any, rows: orient.Intervals, inputs: orient.Inputs, *, threads: int = 1,
            tick: Tick | None = None) -> TtTable:  # fmt: skip
    """The true thickness of the intervals `rows` by the parsed parameters `spec` (module docstring)."""
    o = orient.resolve(spec.orientation, rows, inputs, threads=threads, tick=_part(tick, 0.0, 0.4))
    dense = rows.dense_rows()
    t = dr.thickness(inputs.traj, dense, rows.frm, rows.to, o.normal, step=spec.step, eps=spec.eps, threads=threads,
                     tick=_part(tick, 0.4, 0.95))  # fmt: skip
    tt, flag = t.tt, t.flag
    if o.sin_alpha is not None:  # a core measurement inside the interval: TT = l·sin α_к, whatever the path is
        at = np.flatnonzero(np.isfinite(o.sin_alpha))
        length = rows.to[at] - rows.frm[at]
        value = length * o.sin_alpha[at]
        bits = o.flag[at].copy()
        bits[_pathless(inputs.traj, dense[at])] |= FLAG_NO_PATH
        bits[value < float(spec.eps) * length] |= FLAG_PARALLEL
        tt[at], flag[at] = value, bits
    del dense
    parts = strips.run(lambda lo, hi: _sums(rows.frm, rows.to, tt, o.src, flag, lo, hi), rows.n, threads=threads,
                       size=dr.STRIP_ROWS)  # fmt: skip
    got = _counts(sum((p[3] for p in parts), np.zeros(N_SRC, dtype=np.int64)),
                  sum((p[4] for p in parts), np.zeros(8, dtype=np.int64)))  # fmt: skip
    summary = {
        "rows": rows.n,
        **got,
        "length": strips.total(float(p[0]) for p in parts),
        "tt": strips.total(float(p[1]) for p in parts),
        "with_tt": sum(int(p[2]) for p in parts),
        "excluded": t.n_bad,
        "capped": t.n_capped,
        "absent": list(o.absent),
        "sources": o.report,
    }
    journal = _journal(o, t, got, float(spec.eps))
    if tick is not None:
        tick(1.0)
    return TtTable(tt, flag, o.src, o.alpha, o.dipdir, t.theta, t.azimuth, summary, journal)


def of_table(spec: Any, lines: Any, st: Any, *, tables: Any = None, mesh: orient.MeshSource | None = None,
             bodies: Any = None, threads: int = 1, tick: Tick | None = None) -> TtTable:  # fmt: skip
    """The true thickness of every row of the snapshot `lines` (`composites.source.Lines`), in its line order
    (`lines.row_ids` are the rows written). `tables` — the other tables the orientation reads, by table id;
    `mesh`, `bodies` — `orient.Inputs`."""
    rows = orient.Intervals.of(lines, st)
    t = compute(spec, rows, orient.Inputs(st.traj, lines, tables or {}, mesh, bodies), threads=threads, tick=tick)
    skipped = int(getattr(lines, "skipped", 0) or 0)
    journal = list(t.journal)
    if skipped:
        journal.append(f"Строк без «от» или «до» — {orient._count(skipped)}: в расчёт не вошли, их поля пусты.")
    return replace(t, summary={**t.summary, "skipped": skipped}, journal=journal)


def warm() -> None:
    """Compiles the kernels of the true thickness with the dtypes of the job and of the preview (into
    NUMBA_CACHE_DIR): the directions, the core, the chain of sources, the sums of the summary."""
    from bf_core.drillholes.desurvey import Collars, desurvey

    dr.warm()
    co.warm()
    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [10.0]))
    rows = orient.Intervals.grouped([7], [0], [0, 2], [0.0, 1.0], [1.0, 3.0])
    plane = SimpleNamespace(kind="global", alpha=30.0, dipdir=120.0)
    compute(SimpleNamespace(orientation=[plane], eps=EPS_DEFAULT, step=STEP_DEFAULT), rows, orient.Inputs(traj))
