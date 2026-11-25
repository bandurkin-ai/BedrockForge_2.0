"""Inference of the grid of a block model from the centres and sizes of its blocks (ТЗ 7.31, 8.7.2; ADR 0023
decisions 5–6; Д-13): the parent block, the step, the corner O, the sub-cells `div` (a power of two ≤ 128), the row
(P, s, w) of every block, the refusal of blocks that cannot be represented, the check of three centres.

A file gives a block as a centre (X, Y, Z) and a size (dx, dy, dz) — or only a centre (a regular model). The grid
of the model is `GridV2`; a hint (`GridHint`) carries what the file or the wizard says — the corner, the parent size,
the counts, the turn φ about Z, `div` — and the rest is inferred:

* the parent size — the hint, else the biggest block; a regular model — the smallest step of the distinct centres;
* `div` per axis — the smallest 2^L (L ≤ 7) at which every size is a whole number of sub-cells;
* the corner — the hint, else the lowest corner of the blocks moved down by the residue of the whole parents (a model
  trimmed to the ore may start with a sub-block in the middle of its parent), `counts` — the hint, else up to the
  highest corner;
* the turn φ is about Z only; the centres are `project` (the axes of the project: q = R_z(−φ)·(p − O)) or `model`
  (written in the unturned frame of the model: q = c − O, the project point is O + R_z(φ)·q).

Two passes over the file, in chunks of the caller (a model of 20 million blocks never sits here whole):
`GridScan.add` for every chunk → `finish()` → `InferredGrid.rows_of` for every chunk (the same arrays).
`Unaligned` collects the refused blocks over the chunks: 422 `blocks_unaligned {count, rows ≤ 20}`; the three centres
(first, middle, last row) are compared with the cells' centres by `InferredGrid.check3`: 422 `rotation_mismatch
{rows, dist}` for a turned model, ≤ 1e−6 м. `infer` does all of it for arrays in memory.

Row numbers (`rows` in the errors, `Check3.rows`) are 0-based numbers of the data rows; the messages show them from 1.
Float64 throughout; an empty centre or size (NaN, size ≤ 0) is never a block — it is marked `INVALID`, not turned to 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import geometry as geo
from .schema import MAX_LEVEL, GridV2, idx_of

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
]

TOL = 1e-6  # m: a block edge off the sub-cell lattice by more than this is not on the grid; the three-centre check
MAX_ROWS = 20  # rows named in an error
OK, OFF_GRID, OFF_SUBCELLS, CROSSES_PARENT, INVALID = 0, 1, 2, 3, 4  # `CellRows.bad`
MAX_DISTINCT = 4_000_000  # distinct centres per axis of a regular model; more — the centres are not on a grid
MAX_WHOLE = 200_000  # distinct corners of the whole parents kept for the residue
MAX_SIZES = 4096  # distinct sizes per axis
_BIG = float(2**52)


@dataclass(frozen=True)
class GridHint:
    """What a file or a wizard says of the grid; None / () — not said. `corner` — the corner of parent (0, 0, 0) (a
    Micromine grid line gives a centre: `blocks_roles.micromine_grid_line` turns it into the corner); `rotation` — φ°
    about Z; `notes` — what to tell the user (shown in the warnings of the import)."""

    corner: tuple[float, float, float] | None = None
    size: tuple[float, float, float] | None = None
    counts: tuple[int, int, int] | None = None
    rotation: float = 0.0
    div: tuple[int, int, int] | None = None
    notes: tuple[str, ...] = ()


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
    `CROSSES_PARENT` — over the border of its parent, `INVALID` — no centre or size. p, s, w of a bad row are 0."""

    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    bad: np.ndarray


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
        self.rows: list[int] = []
        self.by_reason = {OFF_GRID: 0, OFF_SUBCELLS: 0, CROSSES_PARENT: 0}

    def add(self, first_row: int, cells: CellRows) -> None:
        bad = cells.bad
        self.invalid += int((bad == INVALID).sum())
        refused = (bad != OK) & (bad != INVALID)
        if not refused.any():
            return
        self.count += int(refused.sum())
        for reason in self.by_reason:
            self.by_reason[reason] += int((bad == reason).sum())
        if len(self.rows) < MAX_ROWS:
            self.rows.extend(int(first_row + i) for i in np.flatnonzero(refused)[: MAX_ROWS - len(self.rows)])

    def error(self) -> ApiError | None:
        if not self.count:
            return None
        parts = []
        for reason, text in ((OFF_GRID, "за началом или за краем сетки"), (CROSSES_PARENT, "через границу родителя"),
                             (OFF_SUBCELLS, "не по подъячейкам 2^L")):  # fmt: skip
            if self.by_reason[reason]:
                parts.append(f"{text} — {_spaced(self.by_reason[reason])}")
        message = f"Блоков, которые нельзя уложить в сетку модели: {_spaced(self.count)} ({'; '.join(parts)})."
        hint = (
            f"Строки данных (с 1): {_rows_text(self.rows)}"
            + (" и другие" if self.count > len(self.rows) else "")
            + ". Субблоки должны делить родителя по подъячейкам 2^L (L ≤ 7) и не выходить за его границы, а блоки — "
            "лежать в сетке от её начала. Проверьте начало и размер родителя в мастере или пересохраните модель в "
            "исходной программе (субблоки по октодереву)."
        )
        return _err("blocks_unaligned", message, hint, {"count": self.count, "rows": list(self.rows)})

    def raise_if(self) -> None:
        err = self.error()
        if err is not None:
            raise err


class GridScan:
    """Pass 1: the statistics of the centres and sizes of every chunk; `finish()` gives the grid.

    `centres` — "project" | "model" (see the module doc). The frame of the statistics is the hint's corner and turn;
    with no corner and a turn — the first centre is the pivot (the corner returned is exact to 1e−9 м)."""

    def __init__(self, hint: GridHint | None = None, *, centres: str = "project") -> None:
        if centres not in ("project", "model"):
            raise _bad_input(f"Оси центров {centres!r}: нужны «project» или «model».")
        self.hint = hint or GridHint()
        self.centres = centres
        self.rows = 0  # valid rows seen
        self._frame: GridV2 | None = None
        self._sized: bool | None = None
        self._lo = np.full(3, np.inf)
        self._hi = np.full(3, -np.inf)
        self._smax = np.zeros(3)
        self._smin = np.full(3, np.inf)
        self._sizes: list[np.ndarray] = [np.empty(0) for _ in range(3)]
        self._whole: list[np.ndarray] = [np.empty(0) for _ in range(3)]
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
        for a in range(3):
            half = d[:, a] / 2 if d is not None else 0.0
            lo, hi = q[:, a] - half, q[:, a] + half
            self._lo[a], self._hi[a] = min(self._lo[a], float(lo.min())), max(self._hi[a], float(hi.max()))
            if d is None:
                if len(self._uniq[a]) <= MAX_DISTINCT:
                    self._uniq[a] = np.unique(np.concatenate([self._uniq[a], np.unique(q[:, a])]))
                continue
            size = d[:, a]
            top = float(size.max())
            self._smin[a] = min(self._smin[a], float(size.min()))
            if top > self._smax[a] + TOL:  # a bigger block: the whole parents seen so far were smaller
                self._smax[a], self._whole[a] = top, np.empty(0)
            if top >= self._smax[a] - TOL and len(self._whole[a]) < MAX_WHOLE:
                found = lo[size >= self._smax[a] - TOL]
                self._whole[a] = np.unique(np.concatenate([self._whole[a], np.unique(found)]))[:MAX_WHOLE]
            if len(self._sizes[a]) < MAX_SIZES:
                self._sizes[a] = np.unique(np.concatenate([self._sizes[a], np.unique(np.round(size, 9))]))[:MAX_SIZES]

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

    def _div_of(self, a: int, parent: float) -> int:
        """The smallest 2^L (L ≤ 7) at which every size of the axis is a whole number of sub-cells (7 — when none)."""
        sizes = self._sizes[a]
        if not len(sizes) or float(sizes.max()) - float(sizes.min()) <= TOL:
            return 1
        start = max(0, math.ceil(math.log2(parent / float(sizes.min())) - 1e-9)) if sizes.min() < parent else 0
        for level in range(min(start, MAX_LEVEL), MAX_LEVEL + 1):
            sub = parent / (1 << level)
            k = sizes / sub
            if bool((np.abs(k - np.rint(k)) * sub <= TOL).all() and (np.rint(k) >= 1).all()):
                return 1 << level
        return 1 << MAX_LEVEL

    def finish(self) -> InferredGrid:
        if self._frame is None or not self.rows:
            raise _bad_input(
                "В файле нет ни одного блока с центром и размером.", "Проверьте столбцы центров и размеров."
            )
        h, sized = self.hint, bool(self._sized)
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
        div = list(h.div) if h.div is not None else [self._div_of(a, parent[a]) if sized else 1 for a in range(3)]
        lo = self._lo.copy()
        if not sized:  # one block per cell: the lowest corner is half a step below the lowest centre
            lo = lo - np.asarray(parent) / 2
        if h.corner is not None:
            origin_q = np.zeros(3)  # the frame is the grid's own: its corner is the hint's
        else:
            origin_q = np.empty(3)
            for a in range(3):
                shift = 0.0
                whole = self._whole[a]
                if sized and len(whole):
                    r = np.mod(whole - lo[a], parent[a])
                    r[r > parent[a] - TOL] = 0.0
                    values, counts = np.unique(np.round(r, 6), return_counts=True)
                    top = float(values[np.argmax(counts)])
                    shift = top - parent[a] if top > TOL else 0.0
                origin_q[a] = _snap(float(lo[a]) + shift)
        counts_n = [
            max(1, int(math.ceil((float(self._hi[a]) - float(origin_q[a])) / parent[a] - TOL / parent[a])))
            for a in range(3)
        ]  # fmt: skip
        if h.counts is not None:
            counts_n = [int(v) for v in h.counts]
        frame = self._frame
        if h.corner is not None:
            corner = tuple(float(v) for v in h.corner)
        elif self.centres == "project":
            corner = tuple(float(v) for v in frame.to_project(origin_q.reshape(1, 3))[0])
        else:
            corner = tuple(float(v) for v in origin_q)
        corner = tuple(_snap(v) for v in corner)  # type: ignore[assignment]
        rotation = h.rotation
        grid = GridV2.of({"corner": list(corner), "counts": counts_n, "size": parent, "rotation": rotation, "div": div})
        return InferredGrid(grid, self.centres, parent_guess, warnings)


@dataclass
class InferredGrid:
    """The grid of pass 1 and the rows of pass 2. `parent_guess` — the parent size is the biggest block (not said by the
    file); `warnings` — Russian, for the import log; `sub` — the grid has sub-cells (`s`, `w` are returned)."""

    grid: GridV2
    centres: str
    parent_guess: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def sub(self) -> bool:
        return self.grid.sub.sub

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
        bad[(off.max(axis=1) > TOL) | (w < 1).any(axis=1)] = OFF_SUBCELLS
        bad[((par < 0) | (par >= counts)).any(axis=1)] = OFF_GRID
        bad[~ok] = INVALID
        good = bad == OK
        par = np.where(good[:, None], par, 0)
        p = idx_of(par[:, 0], par[:, 1], par[:, 2], grid.counts)
        if not self.sub:
            return CellRows(p, None, None, bad)
        s_out = np.where(good[:, None], s, 0).astype(np.uint8)
        w_out = np.where(good[:, None], w, 0).astype(np.uint8)
        return CellRows(p, s_out, w_out, bad)

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
        check = Check3(worst <= TOL, worst, [int(r) for r in rows])
        if raise_turned and not check.ok and self.grid.rotation != 0.0:
            raise _err(
                "rotation_mismatch",
                f"Центры блоков (строки {_rows_text(check.rows)}) не совпадают с вычисленными: расстояние "
                f"{worst:.6g} м при допуске 1e-6 м.",
                "Проверьте угол поворота вокруг Z, начало модели и оси, в которых записаны центры (проекта или "
                "модели), в мастере импорта.",
                {"rows": list(check.rows), "dist": worst},
            )  # fmt: skip
        return check

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
    centres: Any, sizes: Any | None = None, hint: GridHint | None = None, *, centres_in: str = "project"
) -> Inferred:
    """The grid and the cells of the blocks in memory (`centres` n×3, `sizes` n×3 or None). Blocks that cannot be
    represented → 422 `blocks_unaligned`; a turned model with the wrong convention → 422 `rotation_mismatch`; rows
    without a centre or a size are left out (`valid`, a warning)."""
    c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
    d = None if sizes is None else np.asarray(sizes, dtype=np.float64).reshape(-1, 3)
    scan = GridScan(hint, centres=centres_in)
    scan.add(c, d)
    found = scan.finish()
    warnings = list(found.warnings)
    check_rows = found.three_rows(len(c))
    check = found.check3(c[check_rows], None if d is None else d[check_rows], check_rows)
    cells = found.rows_of(c, d)
    lost = Unaligned()
    lost.add(0, cells)
    lost.raise_if()
    valid = None
    if lost.invalid:
        valid = cells.bad == OK
        warnings.append(f"Строк без центра или с размером ≤ 0: {lost.invalid:,} — пропущены.".replace(",", " "))
    parents = int(np.unique(cells.p if valid is None else cells.p[valid]).size)
    return Inferred(found, cells.p, cells.s, cells.w, valid, check, parents, warnings)
