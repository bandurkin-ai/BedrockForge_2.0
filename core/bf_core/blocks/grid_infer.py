"""Inference of the grid of a block model from the centres and sizes of its blocks (ТЗ 7.31, 8.7.2; ADR 0023
decisions 5–6, ADR 0030 decisions 1–4; Д-13, Д-45): the parent block, the step, the corner O, the sub-cells `div` (any
whole 1…128 per axis), the row (P, s, w) of every block, the refusal of blocks that cannot be represented, the check of
three centres.

A file gives a block as a centre (X, Y, Z) and a size (dx, dy, dz) — or only a centre (a regular model). The grid
of the model is `GridV2`; a hint (`GridHint`) carries what the file or the wizard says — the corner, the parent size,
the counts, the turn φ about Z, `div` — and the rest is inferred:

* the parent size — the hint, else the biggest block (2×, 3×, 4× of it when the blocks do not lie with it: the
  sub-blocks of a parent twice the biggest block); a regular model — the smallest step of the distinct centres;
* `div` per axis — the lcm of the denominators of the fractions of the parent that the sizes and the offsets of the
  blocks make (10/5 → 5, 10/3 and 10/2 → 6: `grid_lattice`); the tolerance of an axis — the quantum of the columns
  (a text with 2 decimals — 0.005 m), else 1e-6 m, at most ¼ of a sub-cell;
* the corner — the hint, else the lowest corner of the blocks moved down by the residue of the whole parents (a model
  trimmed to the ore may start with a sub-block in the middle of its parent), `counts` — the hint, else up to the
  highest corner;
* the turn φ is about Z only; the centres are `project` (the axes of the project: q = R_z(−φ)·(p − O)) or `model`
  (written in the unturned frame of the model: q = c − O, the project point is O + R_z(φ)·q).

Two passes over the file, in chunks of the caller: `GridScan.add` for every chunk → `finish()` →
`InferredGrid.rows_of` for every chunk (the same arrays). `Unaligned` collects the refused blocks: 422
`blocks_unaligned` (`grid_diag` adds the reason and the choices); the three centres (first, middle, last row) are
compared with the cells' centres by `InferredGrid.check3`: 422 `rotation_mismatch {rows, dist}` for a turned model.
`infer` does all of it for arrays in memory. Row numbers (`rows`, `Check3.rows`) are 0-based, the messages show them
from 1. Float64 throughout; an empty centre or size (NaN, size ≤ 0) is never a block — `INVALID`, not turned to 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import geometry as geo
from . import grid_lattice as gl
from .schema import GridV2, idx_of

__all__ = [
    "CROSSES_PARENT",
    "INVALID",
    "OFF_GRID",
    "OFF_SUBCELLS",
    "TOL",
    "CellRows",
    "Check3",
    "GridHint",
    "GridScan",
    "Inferred",
    "InferredGrid",
    "Unaligned",
    "infer",
    "shifted_note",
]

TOL = 1e-6  # m: a block edge off the sub-cell lattice by more than this is not on the grid; the three-centre check
MAX_ROWS = 20  # rows named in an error
OK, OFF_GRID, OFF_SUBCELLS, CROSSES_PARENT, INVALID = 0, 1, 2, 3, 4  # `CellRows.bad`
MAX_DISTINCT = 4_000_000  # distinct centres per axis of a regular model; more — the centres are not on a grid
_BIG = float(2**52)


@dataclass(frozen=True)
class GridHint:
    """What a file or a wizard says of the grid; None / () — not said. `corner` — the corner of parent (0, 0, 0) (a
    Micromine grid line gives a centre: `blocks_roles.micromine_grid_line` turns it into the corner); `rotation` — φ°
    about Z; `div` — the sub-cells per axis (any whole 1…128); `tol` — the tolerance of the fit, m (the wizard's
    «Допуск»; None — by the type of the columns); `notes` — what to tell the user (the warnings of the import)."""

    corner: tuple[float, float, float] | None = None
    size: tuple[float, float, float] | None = None
    counts: tuple[int, int, int] | None = None
    rotation: float = 0.0
    div: tuple[int, int, int] | None = None
    notes: tuple[str, ...] = ()
    tol: float | None = None


@dataclass
class Check3:
    """The check of three centres (first, middle, last row): `dist` — the biggest distance, м, between the centre in
    the file and the centre of the cell computed from (P, s, w); `rows` — the 0-based rows checked."""

    ok: bool
    dist: float
    rows: list[int]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "dist": self.dist, "rows": list(self.rows)}


@dataclass
class CellRows:
    """The cells of a chunk: `p` uint64 (P), `s`, `w` uint8 n×3 (None — the model has no sub-blocks), `bad` int8:
    0 — fine, `OFF_GRID` — before the corner or beyond the counts, `OFF_SUBCELLS` — an edge off the sub-cells,
    `CROSSES_PARENT` — over the border of its parent, `INVALID` — no centre or size. p, s, w of a bad row are 0.
    `shifted` — the blocks whose edges were moved to the lattice by more than 1e-6 m (within the tolerance),
    `max_shift` — the biggest move, m."""

    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    bad: np.ndarray
    shifted: int = 0
    max_shift: float = 0.0


def _snap(value: float, places: int = 6, eps: float = 1e-8) -> float:
    """A value of a file computed with a float error (23436265.919999998) back to its digits (23436265.92)."""
    r = round(value, places)
    return r if abs(r - value) <= eps else value


def _err(code: str, message: str, hint: str, extra: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, code, message, hint, extra=extra)


def _bad_input(message: str, hint: str = "Проверьте столбцы центров и размеров блоков.") -> ApiError:
    return ApiError(422, "block_model_invalid", message, hint)


def _rows_text(rows: list[int]) -> str:
    return ", ".join(str(r + 1) for r in rows[:MAX_ROWS])


def shifted_note(count: int, biggest: float) -> str:
    """The warning of the blocks whose edges were moved to the lattice (ADR 0030 decision 4)."""
    shift = f"{biggest:.4g}".replace(".", ",")
    return f"Центры уточнены до сетки: {_spaced(count)} блоков, наибольший сдвиг {shift} м."


def _spaced(n: int) -> str:
    """A whole number with a space between thousands (only the digits are touched, not the commas of a text)."""
    return f"{n:,}".replace(",", " ")


class Unaligned:
    """The blocks the grid cannot hold, over the chunks of a file: `add(first_row, cells)` for every chunk, then
    `raise_if()` — 422 `blocks_unaligned {count, rows}` naming up to 20 rows. `invalid` — rows without a centre or a
    size (the reader drops them and says so)."""

    def __init__(self) -> None:
        self.count = 0
        self.invalid = 0
        self.shifted = 0  # blocks moved to the lattice by more than 1e-6 m (within the tolerance)
        self.max_shift = 0.0
        self.rows: list[int] = []
        self.by_reason = {OFF_GRID: 0, OFF_SUBCELLS: 0, CROSSES_PARENT: 0}

    def add(self, first_row: int, cells: CellRows) -> None:
        bad = cells.bad
        self.shifted += cells.shifted
        self.max_shift = max(self.max_shift, cells.max_shift)
        self.invalid += int((bad == INVALID).sum())
        refused = (bad != OK) & (bad != INVALID)
        if not refused.any():
            return
        self.count += int(refused.sum())
        for reason in self.by_reason:
            self.by_reason[reason] += int((bad == reason).sum())
        if len(self.rows) < MAX_ROWS:
            self.rows.extend(int(first_row + i) for i in np.flatnonzero(refused)[: MAX_ROWS - len(self.rows)])

    def error(self, diag: dict[str, Any] | None = None, choices: list[dict[str, Any]] | None = None) -> ApiError | None:
        """422 `blocks_unaligned {count, rows ≤ 20, by_reason, diag?, choices?}` (ADR 0030 decision 6). `diag` and
        `choices` — `grid_diag.diagnose` / `choices_of`: why the blocks do not lie and what the user may do."""
        if not self.count:
            return None
        parts = []
        for reason, text in ((OFF_GRID, "за началом или за краем сетки"), (CROSSES_PARENT, "через границу родителя"),
                             (OFF_SUBCELLS, "не по подъячейкам деления родителя")):  # fmt: skip
            if self.by_reason[reason]:
                parts.append(f"{text} — {_spaced(self.by_reason[reason])}")
        lead = diag["text"][0] if diag and diag.get("text") else ""
        message = f"Блоков, которые нельзя уложить в сетку модели: {_spaced(self.count)} ({'; '.join(parts)}). {lead}"
        hint = (
            f"Строки данных (с 1): {_rows_text(self.rows)}"
            + (" и другие" if self.count > len(self.rows) else "")
            + ". Субблоки должны делить родителя на целое число подъячеек (до 128 по оси) и не выходить за его "
            "границы, а блоки — лежать в сетке от её начала. Задайте родителя и начало сетки вручную или отмените "
            "импорт файла."
        )
        extra: dict[str, Any] = {"count": self.count, "rows": list(self.rows),
                                 "by_reason": {"off_grid": self.by_reason[OFF_GRID],
                                               "crosses_parent": self.by_reason[CROSSES_PARENT],
                                               "off_subcells": self.by_reason[OFF_SUBCELLS]}}  # fmt: skip
        if diag is not None:
            extra["diag"] = diag
        if choices is not None:
            extra["choices"] = choices
        return _err("blocks_unaligned", message.strip(), hint, extra)

    def raise_if(self, diag: dict[str, Any] | None = None, choices: list[dict[str, Any]] | None = None) -> None:
        err = self.error(diag, choices)
        if err is not None:
            raise err


class GridScan:
    """Pass 1: the statistics of the centres and sizes of every chunk; `finish()` gives the grid.

    `centres` — "project" | "model" (see the module doc). The frame of the statistics is the hint's corner and turn;
    with no corner and a turn — the first centre is the pivot (the corner returned is exact to 1e−9 м)."""

    def __init__(
        self,
        hint: GridHint | None = None,
        *,
        centres: str = "project",
        quanta: Sequence[float] | None = None,
        f4: Sequence[bool] | None = None,
    ) -> None:
        if centres not in ("project", "model"):
            raise _bad_input(f"Оси центров {centres!r}: нужны «project» или «model».")
        self.hint = hint or GridHint()
        self.centres = centres
        self.quanta = tuple(float(v) for v in (quanta if quanta is not None else (0.0, 0.0, 0.0)))
        self.f4 = tuple(bool(v) for v in (f4 if f4 is not None else (False, False, False)))  # axes stored as float32
        self._cmax = np.zeros(3)  # the biggest |centre| of the file, for the quantum of a float32 column
        self.rows = 0  # valid rows seen
        self._frame: GridV2 | None = None
        self._sized: bool | None = None
        self._lo = np.full(3, np.inf)
        self._hi = np.full(3, -np.inf)
        self._smax = np.zeros(3)
        self._smin = np.full(3, np.inf)
        self._pairs = [gl.PairSet() for _ in range(3)]  # (low edge, size) → blocks, per axis
        self._uniq: list[np.ndarray] = [np.empty(0) for _ in range(3)]

    def _frame_of(self, first: np.ndarray) -> GridV2:
        h = self.hint
        turn = h.rotation if self.centres == "project" else 0.0
        corner = h.corner if h.corner is not None else (tuple(float(v) for v in first) if turn else (0.0, 0.0, 0.0))
        return GridV2.of({"corner": list(corner), "counts": [1, 1, 1], "size": [1.0, 1.0, 1.0], "rotation": turn})

    def _local(self, c: np.ndarray) -> np.ndarray:
        assert self._frame is not None
        return self._frame.to_local(c) if self.centres == "project" else c - np.asarray(self._frame.corner)

    def add(self, centres: Any, sizes: Any | None = None) -> None:
        """A chunk: centres n×3 (X, Y, Z), sizes n×3 or None (a regular model — one block per cell)."""
        c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
        d = None if sizes is None else np.asarray(sizes, dtype=np.float64).reshape(-1, 3)
        if d is not None and len(d) != len(c):
            raise _bad_input("Число размеров блоков не совпадает с числом центров.")
        if self._sized is None:
            self._sized = d is not None
        elif self._sized != (d is not None):
            raise _bad_input("Размеры блоков заданы не во всех порциях файла.")
        ok = np.isfinite(c).all(axis=1)
        if d is not None:
            ok &= np.isfinite(d).all(axis=1) & (d > 0).all(axis=1)
        if not ok.any():
            return
        if not ok.all():
            c = c[ok]
            d = None if d is None else d[ok]
        if self._frame is None:
            self._frame = self._frame_of(c[0])
        q = self._local(c)
        self.rows += len(q)
        self._cmax = np.maximum(self._cmax, np.abs(c).max(axis=0))
        for a in range(3):
            half = d[:, a] / 2 if d is not None else 0.0
            lo, hi = q[:, a] - half, q[:, a] + half
            self._lo[a], self._hi[a] = min(self._lo[a], float(lo.min())), max(self._hi[a], float(hi.max()))
            if d is None:
                if len(self._uniq[a]) <= MAX_DISTINCT:
                    self._uniq[a] = np.unique(np.concatenate([self._uniq[a], np.unique(q[:, a])]))
                continue
            size = d[:, a]
            self._smin[a] = min(self._smin[a], float(size.min()))
            self._smax[a] = max(self._smax[a], float(size.max()))
            self._pairs[a].add(lo, size)

    def _step(self, a: int, warnings: list[str]) -> float:
        """The step of a regular model along an axis: the smallest gap of the distinct centres."""
        u = self._uniq[a]
        if len(u) > MAX_DISTINCT:
            raise _bad_input(
                f"Центры блоков по {'XYZ'[a]} не лежат на сетке: больше {MAX_DISTINCT:,} различных значений.".replace(
                    ",", " "
                ),
                "Укажите размер блока и начало сетки в мастере или проверьте столбец центров.",
            )
        gaps = np.diff(u)
        gaps = gaps[gaps > TOL]
        if not len(gaps):
            warnings.append(f"Размер блока по {'XYZ'[a]} определить не из чего (одно значение центра): принят 1 м.")
            return 1.0
        return _snap(float(gaps.min()))

    def _tols(self) -> list[float]:
        """The tolerance of the fit per axis, m: the wizard's, else the quantum of the columns of the axis (≥ 1e-6)."""
        t = self.hint.tol
        if t is not None:
            if isinstance(t, bool) or not math.isfinite(t) or t <= 0:
                raise _err("bad_param", "Допуск привязки: нужно число метров больше нуля.",
                           "Задайте допуск, например 0,005 для чисел с двумя знаками после запятой.",
                           {"param": "grid.tol"})  # fmt: skip
            return [float(t)] * 3
        out = [max(gl.FLOOR, q) for q in self.quanta]
        for a in range(3):
            if self.f4[a]:  # float32: half a unit of the last bit of the biggest centre (and size) of the axis
                size = float(self._smax[a]) if self._sized else 0.0
                out[a] = max(out[a], gl.edge_tol(gl.column_tol("F", max_abs=float(self._cmax[a])),
                                                 gl.column_tol("F", max_abs=size) if size else 0.0))  # fmt: skip
        return out

    def _axis(self, a: int, tol: float, parent: float, warnings: list[str]) -> tuple[float, int, float]:
        """(parent, div, corner in the local axes) of a sized axis: the hint, else the pairs of the axis."""
        h = self.hint
        biggest = _snap(float(self._smax[a]))
        pairs = self._pairs[a]
        lo_p, sz_p, n_p = pairs.items()
        if pairs.overflow or not len(lo_p):
            warnings.append(f"По оси {'XYZ'[a]} больше {_spaced(gl.MAX_PAIRS)} различных положений блоков: деление "
                            "родителя не выводится.")  # fmt: skip
            origin = 0.0 if h.corner is not None else _snap(float(self._lo[a]))
            return parent, int(h.div[a]) if h.div is not None else 1, origin
        pick = gl.choose_axis(lo_p, sz_p, n_p, biggest=biggest, tol=tol,
                              hint_parent=parent if h.size is not None else None,
                              hint_origin=0.0 if h.corner is not None else None,
                              div=int(h.div[a]) if h.div is not None else None)  # fmt: skip
        if pick.k > 1:
            warnings.append(f"Размер родителя по {'XYZ'[a]} взят {pick.parent:g} м ({pick.k} × самый большой блок "
                            f"{biggest:g} м): блоки пересекали границу родителя {biggest:g} м.")  # fmt: skip
        return _snap(float(pick.parent)), int(pick.div), float(pick.origin)

    def finish(self) -> InferredGrid:
        if self._frame is None or not self.rows:
            raise _bad_input(
                "В файле нет ни одного блока с центром и размером.", "Проверьте столбцы центров и размеров."
            )
        h, sized = self.hint, bool(self._sized)
        tols = self._tols()
        warnings = list(h.notes)
        if h.size is not None:
            parent = [_snap(float(v)) for v in h.size]
        elif sized:
            parent = [_snap(float(self._smax[a])) for a in range(3)]
        else:
            parent = [self._step(a, warnings) for a in range(3)]
        parent_guess = h.size is None and sized and bool(((self._smax - self._smin) > TOL).any())
        if parent_guess:
            warnings.append(
                "Размер родительского блока взят по самому большому блоку: "
                + " × ".join(f"{v:g}" for v in parent)
                + "."
            )
        div = list(h.div) if h.div is not None else [1, 1, 1]
        origin_q = np.zeros(3)
        if sized:
            for a in range(3):
                parent[a], div[a], origin_q[a] = self._axis(a, tols[a], parent[a], warnings)
        else:  # one block per cell: the lowest corner is half a step below the lowest centre
            lo = self._lo - np.asarray(parent) / 2
            origin_q = np.zeros(3) if h.corner is not None else np.array([_snap(float(v)) for v in lo])
        if h.tol is not None:
            for a in range(3):
                if h.tol > 0.25 * parent[a] / div[a] + 1e-12:
                    raise _err("bad_param", f"Допуск {h.tol:g} м по оси {'XYZ'[a]} больше четверти подъячейки "
                               f"({parent[a] / div[a]:g} м).", "Уменьшите допуск: он не должен быть больше четверти "
                               "подъячейки, иначе блок можно отнести к соседней.", {"param": "grid.tol"})  # fmt: skip
        return self.assemble(parent, div, origin_q.tolist(), tols, warnings=warnings, parent_guess=parent_guess)

    def axis_pairs(self, a: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
        """(low edges, sizes, counts, overflow) of the distinct (low edge, size) pairs of an axis, local axes (for the
        diagnosis of a refusal, `grid_diag`)."""
        lo, sz, n = self._pairs[a].items()
        return lo, sz, n, self._pairs[a].overflow

    def extent(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(lowest, highest edge, biggest, smallest size) per axis in the local axes (sizes 0 for a regular model)."""
        return self._lo.copy(), self._hi.copy(), self._smax.copy(), self._smin.copy()

    def assemble(
        self,
        parent: Sequence[float],
        div: Sequence[int],
        origin_q: Sequence[float],
        tols: Sequence[float],
        *,
        warnings: list[str] | None = None,
        parent_guess: bool = False,
        manual: bool = True,
    ) -> InferredGrid:
        """The grid for a parent, a division, a corner (local axes) and a tolerance of every axis. `manual` — the
        counts and the corner said by the hint stay as they are; False (a candidate of the diagnosis) — they are
        taken from the data."""
        h, frame = self.hint, self._frame
        assert frame is not None
        origin = np.asarray(origin_q, dtype=np.float64)
        counts_n = [
            max(1, int(math.ceil((float(self._hi[a]) - float(origin[a])) / parent[a] - tols[a] / parent[a])))
            for a in range(3)
        ]  # fmt: skip
        if manual and h.counts is not None:
            counts_n = [int(v) for v in h.counts]
        if manual and h.corner is not None:
            corner = tuple(float(v) for v in h.corner)
        elif self.centres == "project":
            corner = tuple(float(v) for v in frame.to_project(origin.reshape(1, 3))[0])
        else:
            corner = tuple(float(v) for v in np.asarray(frame.corner) + origin)
        corner = tuple(_snap(v) for v in corner)  # type: ignore[assignment]
        grid = GridV2.of({"corner": list(corner), "counts": counts_n, "size": list(parent),
                          "rotation": h.rotation, "div": list(div)})  # fmt: skip
        return InferredGrid(grid, self.centres, parent_guess, warnings if warnings is not None else [],
                            (tols[0], tols[1], tols[2]), (float(origin[0]), float(origin[1]), float(origin[2])))


@dataclass
class InferredGrid:
    """The grid of pass 1 and the rows of pass 2. `parent_guess` — the parent size is the biggest block (not said by the
    file); `warnings` — Russian, for the import log; `sub` — the grid has sub-cells (`s`, `w` are returned)."""

    grid: GridV2
    centres: str
    parent_guess: bool = False
    warnings: list[str] = field(default_factory=list)
    tol: tuple[float, float, float] = (TOL, TOL, TOL)  # m per axis: how far an edge may be from its sub-cell line
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)  # the corner in the local axes of pass 1

    @property
    def sub(self) -> bool:
        return self.grid.sub.sub

    def _tol_axes(self) -> np.ndarray:
        """The tolerance per axis, at most ¼ of a sub-cell (a quantum bigger than that never moves a block to the
        neighbouring cell)."""
        sub = np.asarray(self.grid.regular(), dtype=np.float64) / np.asarray(self.grid.sub.div, dtype=np.float64)
        return np.minimum(np.asarray(self.tol, dtype=np.float64), 0.25 * sub)

    def local(self, c: Any) -> np.ndarray:
        c = np.asarray(c, dtype=np.float64).reshape(-1, 3)
        if self.centres == "project":
            return self.grid.to_local(c)
        return c - np.asarray(self.grid.corner)

    def to_project_axes(self, c: Any) -> np.ndarray:
        """Centres of the file in the axes of the project (the same for «project»; O + R_z(φ)·(c − O) for «model»)."""
        c = np.asarray(c, dtype=np.float64).reshape(-1, 3)
        return c if self.centres == "project" else self.grid.to_project(c - np.asarray(self.grid.corner))

    def _lattice(self, c: Any, d: Any | None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(u0, u1, off, ok): edges of the blocks in sub-cells (rounded), the distance of the file's edges from the
        lattice, м (n×3 for both the low and the high edge: the biggest), the rows with a centre and a size."""
        c = np.asarray(c, dtype=np.float64).reshape(-1, 3)
        n = len(c)
        grid = self.grid
        parent, div = np.asarray(grid.regular(), dtype=np.float64), np.asarray(grid.sub.div, dtype=np.float64)
        sub = parent / div
        ok = np.isfinite(c).all(axis=1)
        if d is None:
            size = np.broadcast_to(parent, (n, 3))
        else:
            size = np.asarray(d, dtype=np.float64).reshape(-1, 3)
            ok &= np.isfinite(size).all(axis=1) & (size > 0).all(axis=1)
        c = np.where(ok[:, None], c, 0.0)
        size = np.where(ok[:, None], size, parent)
        q = self.local(c)
        a0, a1 = (q - size / 2) / sub, (q + size / 2) / sub
        a0, a1 = np.clip(a0, -_BIG, _BIG), np.clip(a1, -_BIG, _BIG)
        u0, u1 = np.rint(a0), np.rint(a1)
        off = np.maximum(np.abs(a0 - u0), np.abs(a1 - u1)) * sub
        return u0, u1, off, ok

    def rows_of(self, centres: Any, sizes: Any | None = None) -> CellRows:
        """Pass 2: (P, s, w) of the blocks of a chunk. A block that cannot be represented is marked in `bad` (its
        p, s, w are 0); `Unaligned` counts them."""
        grid = self.grid
        u0, u1, off, ok = self._lattice(centres, sizes)
        div = np.asarray(grid.sub.div, dtype=np.int64)
        counts = np.asarray(grid.counts, dtype=np.int64)
        i0, i1 = u0.astype(np.int64), u1.astype(np.int64)
        par = np.floor_divide(i0, div)
        s, w = i0 - par * div, i1 - i0
        bad = np.zeros(len(i0), dtype=np.int8)
        bad[(s + w > div).any(axis=1)] = CROSSES_PARENT
        bad[(off > self._tol_axes()).any(axis=1) | (w < 1).any(axis=1)] = OFF_SUBCELLS
        bad[((par < 0) | (par >= counts)).any(axis=1)] = OFF_GRID
        bad[~ok] = INVALID
        good = bad == OK
        moved = off.max(axis=1)[good] if good.any() else np.empty(0)
        shifted, max_shift = int((moved > TOL).sum()), float(moved.max()) if len(moved) else 0.0
        par = np.where(good[:, None], par, 0)
        p = idx_of(par[:, 0], par[:, 1], par[:, 2], grid.counts)
        if not self.sub:
            return CellRows(p, None, None, bad, shifted, max_shift)
        s_out = np.where(good[:, None], s, 0).astype(np.uint8)
        w_out = np.where(good[:, None], w, 0).astype(np.uint8)
        return CellRows(p, s_out, w_out, bad, shifted, max_shift)

    def check3(self, centres: Any, sizes: Any | None, rows: list[int], *, raise_turned: bool = True) -> Check3:
        """The check of the convention (ADR 0023 §3): the centres of three rows of the file against the centres of the
        cells computed from their (P, s, w) — `geometry.centres` in the axes of the project; the biggest distance
        ≤ 1e−6 м.
        `centres`, `sizes` — the three rows. A turned model (φ ≠ 0) that fails → 422 `rotation_mismatch {rows, dist}`
        (wrong angle, origin or axes of the centres); an unturned one is left to `Unaligned`."""
        u0, u1, off, ok = self._lattice(centres, sizes)
        cells = self.rows_of(centres, sizes)
        file_centres = self.to_project_axes(centres)
        dist = off.max(axis=1)  # m: the edges off the sub-cell lattice
        good = cells.bad == OK
        if good.any():
            made = geo.centres(self.grid, cells.p[good], None if cells.s is None else cells.s[good],
                               None if cells.w is None else cells.w[good])  # fmt: skip
            gap = np.linalg.norm(made - file_centres[good], axis=1)
            dist[good] = np.maximum(dist[good], gap)
        dist[~ok] = math.inf
        worst = float(dist.max()) if len(dist) else 0.0
        limit = float(self._tol_axes().max())
        check = Check3(worst <= limit, worst, [int(r) for r in rows])
        if raise_turned and not check.ok and self.grid.rotation != 0.0:
            raise _err(
                "rotation_mismatch",
                f"Центры блоков (строки {_rows_text(check.rows)}) не совпадают с вычисленными: расстояние "
                f"{worst:.6g} м при допуске {'1e-6' if limit == TOL else f'{limit:.6g}'} м.",
                "Проверьте угол поворота вокруг Z, начало модели и оси, в которых записаны центры (проекта или "
                "модели), в мастере импорта.",
                {"rows": list(check.rows), "dist": worst, "choices": self._choices()},
            )  # fmt: skip
        return check

    def _choices(self) -> list[dict[str, Any]]:
        """What the user may do after a refusal that names no blocks (`rotation_mismatch`): set the grid by hand — the
        form starts from this grid — or leave the file out (ADR 0030 decision 6)."""
        from . import grid_diag

        return grid_diag.current_choices(self)

    def three_rows(self, n: int) -> list[int]:
        """The rows checked: first, middle, last (0-based)."""
        return sorted({0, n // 2, n - 1}) if n else []


@dataclass
class Inferred:
    """`infer`: the grid and the rows of all blocks (not sorted: `schema.sort_rows` orders them for the files)."""

    inferred: InferredGrid
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    valid: np.ndarray | None  # None — every row is a block
    check3: Check3
    parents: int
    warnings: list[str]

    @property
    def grid(self) -> GridV2:
        return self.inferred.grid

    @property
    def dropped(self) -> int:
        return 0 if self.valid is None else int((~self.valid).sum())


def infer(
    centres: Any,
    sizes: Any | None = None,
    hint: GridHint | None = None,
    *,
    centres_in: str = "project",
    quanta: Sequence[float] | None = None,
) -> Inferred:
    """The grid and the cells of the blocks in memory (`centres` n×3, `sizes` n×3 or None). Blocks that cannot be
    represented → 422 `blocks_unaligned`; a turned model with the wrong convention → 422 `rotation_mismatch`; rows
    without a centre or a size are left out (`valid`, a warning)."""
    c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
    d = None if sizes is None else np.asarray(sizes, dtype=np.float64).reshape(-1, 3)
    scan = GridScan(hint, centres=centres_in, quanta=quanta)
    scan.add(c, d)
    found = scan.finish()
    warnings = list(found.warnings)
    check_rows = found.three_rows(len(c))
    check = found.check3(c[check_rows], None if d is None else d[check_rows], check_rows)
    cells = found.rows_of(c, d)
    lost = Unaligned()
    lost.add(0, cells)
    if lost.count:  # the refusal names its reason and what the user may do (ADR 0030)
        from . import grid_diag

        sample = grid_diag.Sample(len(c))
        sample.add(0, c, d)
        diag = grid_diag.diagnose(scan, found, *sample.arrays(), rows=len(c), lost=lost)
        lost.raise_if(diag, grid_diag.choices_of(diag, found.grid.rotation))
    valid = None
    if lost.invalid:
        valid = cells.bad == OK
        warnings.append(f"Строк без центра или с размером ≤ 0: {lost.invalid:,} — пропущены.".replace(",", " "))
    if lost.shifted:
        warnings.append(shifted_note(lost.shifted, lost.max_shift))
    parents = int(np.unique(cells.p if valid is None else cells.p[valid]).size)
    return Inferred(found, cells.p, cells.s, cells.w, valid, check, parents, warnings)
