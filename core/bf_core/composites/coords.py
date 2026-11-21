"""Coordinates of samples (ТЗ 8.2.1, ADR 0018 §6): the begin, the middle and the end of every interval on the
path of its hole — the columns XB, YB, ZB, XM, YM, ZM, XE, YE, ZE of «‹Таблица› — координаты»; and X, Y, Z of
composites (ТЗ 8.2.3), which is the same middle.

    dense = hole_index(st.hole_ids, lines.holes)          # the hole of the trajectories of every hole; −1 — none
    c = line_coords(lines, traj, dense, threads=…, tick=…) -> Coords
        c.begin, c.middle, c.end — float64 [n, 3] in line order; c.columns() — {"XB": …, …, "ZE": …}
        c.no_trajectory — ids of the holes without a trajectory, c.rows_without — their rows; c.journal
    xyz = midpoints(lines, traj, dense, threads=…, tick=…)  # float64 [n, 3]: X, Y, Z of composites
    positions(traj, rows, depths, threads=…, tick=…)        # any depths; `rows` — the hole of every depth

`traj` — `desurvey.Trajectories` of the set (`source.trajectories(project, set_id).traj`); its holes are
indexed densely, in the order of `SetTrajectories.hole_ids` (ascending ids), so `hole_index` is a search.
The points are those of `Trajectories.positions` — bit for bit what the «Таблица» panel, 3D and the sections
show; the middle is the middle by depth, (FROM + TO) / 2 on the path, not the middle of the chord. A depth
below the end of the path is its end. A hole without a trajectory (no collar coordinates, not in the set)
gets NaN — never a zero — and a line of the journal.

Rows are independent: blocks of `BLOCK_ROWS` rows go to threads (the kernel of `positions` is `nogil`), the
result is the same bit for bit with any number of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.drillholes.desurvey import Trajectories

from . import intervals as iv
from . import strips
from .strips import Tick

COLUMNS = ("XB", "YB", "ZB", "XM", "YM", "ZM", "XE", "YE", "ZE")  # begin, middle, end
BLOCK_ROWS = 1 << 16  # rows of a block: a few milliseconds between two ticks


def hole_index(set_hole_ids: Any, holes: Any) -> np.ndarray:
    """For every hole id of `holes` — its dense index in the trajectories of the set, whose holes go by
    ascending id (`SetTrajectories.hole_ids`); −1 — the set does not have the hole."""
    ids = np.ascontiguousarray(set_hole_ids, dtype=np.int64)
    want = np.ascontiguousarray(holes, dtype=np.int64)
    if ids.shape[0] == 0:
        return np.full(want.shape[0], -1, dtype=np.int64)
    at = np.searchsorted(ids, want)
    at[at == ids.shape[0]] = 0
    return np.where(ids[at] == want, at, -1).astype(np.int64)


def positions(traj: Trajectories, rows: Any, depths: Any, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
    """X, Y, Z (n, 3) at measured depths: `rows` — the dense hole index of every depth (−1 — none: NaN)."""
    hole = np.ascontiguousarray(rows, dtype=np.int64)
    depth = np.ascontiguousarray(depths, dtype=np.float64)
    if hole.shape != depth.shape or hole.ndim != 1:
        raise ValueError("rows and depths must be one-dimensional and of one length")
    out = np.empty((hole.shape[0], 3), dtype=np.float64)

    def block(lo: int, hi: int) -> None:
        out[lo:hi] = traj.positions(hole[lo:hi], depth[lo:hi])

    strips.run(block, hole.shape[0], threads=threads, tick=tick, size=BLOCK_ROWS)
    return out


@dataclass(frozen=True)
class Coords:
    begin: np.ndarray  # float64 [n, 3]: X, Y, Z at FROM; NaN — no trajectory
    middle: np.ndarray  # … at (FROM + TO) / 2
    end: np.ndarray  # … at TO
    no_trajectory: np.ndarray  # int64: ids of the holes of the lines without a trajectory
    rows_without: int  # their rows
    journal: list[str]  # lines for the job log

    def columns(self) -> dict[str, np.ndarray]:
        """The nine columns XB … ZE (`COLUMNS`), each float64 [n]."""
        parts = (self.begin, self.middle, self.end)
        return {name: np.ascontiguousarray(parts[k // 3][:, k % 3]) for k, name in enumerate(COLUMNS)}


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def _rows(lines: iv.Lines, traj: Trajectories, dense: Any) -> tuple[np.ndarray, np.ndarray]:
    """(the dense hole index of every row of the lines, the mask of the holes without a trajectory)."""
    idx = np.ascontiguousarray(dense, dtype=np.int64)
    if idx.shape[0] != lines.n_holes:
        raise ValueError("dense must be per hole of the lines")
    known = (idx >= 0) & (idx < traj.n_holes)
    at = np.where(known, idx, 0)
    built = known & (traj.offsets[at + 1] > traj.offsets[at]) if traj.n_holes else known
    return np.repeat(np.where(built, idx, -1), np.diff(lines.starts)), ~built


def line_coords(
    lines: iv.Lines, traj: Trajectories, dense: Any, *, threads: int = 1, tick: Tick | None = None
) -> Coords:
    """The begin, the middle and the end of every row of the lines (module docstring). `dense` — per hole of
    the lines, its index in `traj` (`hole_index`), −1 — none."""
    rows, missing = _rows(lines, traj, dense)
    begin = positions(traj, rows, lines.frm, threads=threads, tick=_part(tick, 0.0, 1.0 / 3.0))
    end = positions(traj, rows, lines.to, threads=threads, tick=_part(tick, 1.0 / 3.0, 2.0 / 3.0))
    middle = positions(traj, rows, 0.5 * (lines.frm + lines.to), threads=threads, tick=_part(tick, 2.0 / 3.0, 1.0))
    left = lines.holes[missing]
    without = int(np.diff(lines.starts)[missing].sum())
    journal = []
    if left.shape[0]:
        journal.append(
            f"Скважин без траектории — {left.shape[0]} (строк — {without}): координаты пусты. "
            "Проверьте координаты устьев."
        )
    return Coords(begin, middle, end, left, without, journal)


def midpoints(
    lines: iv.Lines, traj: Trajectories, dense: Any, *, threads: int = 1, tick: Tick | None = None
) -> np.ndarray:
    """X, Y, Z of every row of the lines — the point of the path at (FROM + TO) / 2; NaN — no trajectory."""
    rows, _missing = _rows(lines, traj, dense)
    return positions(traj, rows, 0.5 * (lines.frm + lines.to), threads=threads, tick=tick)
