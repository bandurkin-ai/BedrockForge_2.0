"""Regularization of a block model (подшаг 17.25; ТЗ 8.7.5, Т8.7-7; ADR 0023 §9, decision 14;
docs/progress/e17-api.md §9 «регуляризация»): a new regular model in the same axes (the same φ), the cell size
`size`, the origin a multiple of `size` from O in the local axes, no sub-blocks.

    plan  = make_plan(model, size, grades=None, density=None, categories=None)       # 422 / 404, reads only P
    res   = regularize(model, store, size, grades=…, density=…, categories=…, threads=1, tick=None)
    reply = commit(conn, store, model, res, name="…", folder=None, job_id=None)  # {model_id, name, rows, totals…}

Overlaps are exact: the box of a row (a parent or a sub-block, ТЗ 8.7.2) against the box of a new cell, the product
of the three overlaps along the axes (in the units of the new cell; an edge within 1e−9 of a new edge is that edge, so
that a lattice that fits fits exactly). Per new cell, over the rows that overlap it, with the weight
w = overlap · FRAC (volume) or overlap · FRAC · density (tonnes):
  · a number — the mean Σ w·v / Σ w over the rows WITH a value; a gap does not count into the weight, never a zero;
    no row with a value — NaN. A density that is a field is averaged by volume and goes to the result (role kept);
  · a category — the mode by volume (equal volumes — the lower code); rows without a category do not count; and
    `P_<КОД>` (f8) — the share of the volume of the code among the rows with a category (a cell without one — NaN);
  · FRAC (f8, role `frac`) — Σ overlap · FRAC of the rows, i.e. the share of the cell the old model covers (rows with
    no FRAC field count as 1; a row with an empty FRAC adds nothing; no row with a FRAC — NaN).
A new cell exists where at least one row overlaps it. Volume, tonnage and metal are kept (Т8.7-7): `totals` — the
sums over the rows with a value before and over the cells after (metal = tonnes · grade when a density is given, else
volume · grade; of the number fields but the density). With a gap the cell holds the mean over the rest, so the
sums may differ: a gap is not a zero.

Streaming and determinism. The rows come by portions (`Model.iter`); the new cells are accumulated for the window of
the new layers (z) the portion touches, a layer is written (ascending P) as soon as no later row can reach it, so the
memory is a portion and a few layers, not the model. Inside a portion the cells are cut into fixed bands of new
rows (y); a band is one task (`composites.strips`): every cell is summed by one thread, in the order of the file, so
the result is the same bit for bit with 1, 2, 4 and 8 threads (ТЗ 8.0.2). The totals are Neumaier sums per strip of
rows, and `math.fsum` between the strips. The kernels are numba `cache=True, nogil=True` (`regularize_kernels.py`; the
cache — bf_core.paths).

Errors — {code, message, hint} in Russian: 422 `bad_param {param}`, `variable_grid`, `block_model_empty`,
`block_model_too_large`; 404 `block_field_not_found`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import regularize_kernels as rk
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.blocks.model import CHUNK, Chunk, Model
from bf_core.blocks.prototype import bad_param
from bf_core.blocks.schema import GridV2
from bf_core.composites import strips
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

OP = "regularize"
ALGO = "regularize-1"
NAME = "Регуляризованная модель"
FRAC_NAME = "FRAC"
FRAC_NOTE = "Доля объёма ячейки, покрытая исходной моделью"
STRIP_ROWS = 1 << 16  # rows of a strip of the pre-pass (fixed: the result does not depend on the threads)
BANDS = 32  # bands of new rows (y) of the accumulation, at most
MAX_WINDOW_BYTES = 3 << 30
WEIGHTS = ("volume", "tonnes")
BASE = rk.BASE


def _snap_py(x: float) -> float:
    r = float(np.rint(x))
    return r if abs(x - r) < rk.SNAP else x


class Plan:
    """What a call does: the new grid, the fields of the result and how each is made (`make_plan`)."""

    def __init__(self) -> None:
        self.size: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self.grid: GridV2 = None  # type: ignore[assignment]
        self.q0: tuple[float, float, float] = (0.0, 0.0, 0.0)  # the origin of the new grid in the local axes
        self.numbers: list[tuple[str, int]] = []  # (stored name, 0 volume | 1 tonnes)
        self.density: str | float | None = None
        self.cats: list[str] = []
        self.frac: str | None = None
        self.fields: list[dict[str, Any]] = []  # the fields of the file `cells` of the result, in order
        self.cat_off: list[int] = []  # the first accumulator of every category
        self.cat_n: list[int] = []  # its number of codes
        self.pcols: list[tuple[int, int, str]] = []  # (category, code, name) of the fields `P_<КОД>`
        self.nacc = BASE

    def params(self) -> dict[str, Any]:
        return {"size": list(self.size), "grades": [{"field": n, "weight": WEIGHTS[w]} for n, w in self.numbers],
                "density": self.density, "categories": list(self.cats)}  # fmt: skip


def _grade_list(model: Model, grades: Sequence[Any] | None, density: Any) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if grades is None:
        out = [(str(f["name"]), 0) for f in model.fields if f["kind"] == bs.NUMBER and f.get("role") != "frac"]
    else:
        for n, g in enumerate(grades):
            name, weight = (g, "volume") if isinstance(g, str) else (g.get("field"), g.get("weight") or "volume")
            param = f"grades.{n}"
            if weight not in WEIGHTS:
                raise bad_param(f"{param}.weight", f"Вес «{weight}» неизвестен.", "Допустимо: по объёму, по тоннажу.")
            spec = model.field(str(name))
            if spec["kind"] != bs.NUMBER or spec.get("role") == "frac":
                raise bad_param(f"{param}.field", f"Поле «{spec['name']}» нельзя усреднить как число.",
                                "Выберите числовое поле; категории — отдельным списком.")  # fmt: skip
            if any(k == spec["name"] for k, _ in out):
                raise bad_param(f"{param}.field", f"Поле «{spec['name']}» указано дважды.", "Уберите повтор.")
            out.append((str(spec["name"]), 1 if weight == "tonnes" else 0))
    if any(w == 1 for _, w in out) and density is None:
        raise bad_param("density", "Для веса по тоннажу нужна плотность.", "Укажите поле плотности или число.")
    return out


def _extent_cells(model: Model) -> tuple[list[int], list[int]]:
    """The least and the greatest parent index (i, j, k) of the rows: one column read."""
    nx, ny = model.grid.counts[0], model.grid.counts[1]
    lo = [1 << 62] * 3
    hi = [-1] * 3
    for _first, n, pp, *_ in model.reader.pieces((), p=True):
        if not n or pp is None:
            continue
        p = pp.astype(np.int64)
        for a, v in enumerate((p % nx, (p // nx) % ny, p // (nx * ny))):
            lo[a], hi[a] = min(lo[a], int(v.min())), max(hi[a], int(v.max()))
    return lo, hi


def _extent_boxes(model: Model) -> tuple[list[float], list[float]]:
    """A list of blocks: the lowest and the highest edge of the boxes along every local axis (the columns of the boxes
    only)."""
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for _first, n, _p, c, d, _cols in model.reader.pieces((), p=False, geo=True):
        if n and c is not None and d is not None:
            lo, hi = np.minimum(lo, (c - d / 2).min(axis=0)), np.maximum(hi, (c + d / 2).max(axis=0))
    return [float(v) for v in lo], [float(v) for v in hi]


def _density_of(model: Model, density: Any) -> str | float | None:
    if isinstance(density, str):
        spec = model.field(density)
        if spec["kind"] != bs.NUMBER:
            raise bad_param("density", f"Плотность «{spec['name']}» — не число.", "Выберите числовое поле плотности.")
        return str(spec["name"])
    if density is None:
        return None
    if isinstance(density, bool) or not isinstance(density, (int, float)) or not math.isfinite(density) or density <= 0:
        raise bad_param("density", "Плотность: нужно положительное число или имя поля.", "Например 2,7 или SG.")
    return float(density)


def make_plan(model: Model, size: Sequence[float], grades: Sequence[Any] | None = None, density: Any = None,
              categories: Sequence[str] | None = None) -> Plan:  # fmt: skip
    """Checks the request and builds the new grid and the fields (module docstring)."""
    d = model.grid.regular("Регуляризация")
    if model.rows == 0:
        raise ApiError(422, "block_model_empty", "В модели нет блоков.", "Регуляризовать можно модель с блоками.")
    if (
        not isinstance(size, Sequence)
        or isinstance(size, str)
        or len(size) != 3
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in size)
    ):
        raise bad_param("size", "Размер блока: нужны три положительных числа.", "Задайте размер по X, Y, Z в метрах.")
    pl = Plan()
    pl.size = (float(size[0]), float(size[1]), float(size[2]))
    frac = model.role_field("frac")
    pl.frac = None if frac is None else str(frac["name"])
    pl.density = _density_of(model, density)
    pl.numbers = _grade_list(model, grades, pl.density)
    if isinstance(pl.density, str) and all(n != pl.density for n, _ in pl.numbers):
        pl.numbers.append((pl.density, 0))  # the tonnage of the result needs it
    if categories is None:
        pl.cats = [str(f["name"]) for f in model.fields if f["kind"] == bs.CATEGORY]
    else:
        for n, c in enumerate(categories):
            spec = model.field(str(c))
            if spec["kind"] != bs.CATEGORY:
                raise bad_param(f"categories.{n}", f"Поле «{spec['name']}» — не категория.", "Выберите поле-категорию.")
            if spec["name"] not in pl.cats:
                pl.cats.append(str(spec["name"]))
    # the grid: the origin — a multiple of the size from O, the cover — the parents that hold rows (a list of blocks:
    # the boxes of the rows)
    if model.listed:
        elo, ehi = _extent_boxes(model)
    else:
        lo, hi = _extent_cells(model)
        elo = [lo[a] * d[a] for a in range(3)]
        ehi = [(hi[a] + 1) * d[a] for a in range(3)]
    n0, counts = [], []
    for a in range(3):
        n0.append(int(math.floor(_snap_py(elo[a] / pl.size[a]))))
        counts.append(int(math.ceil(_snap_py(ehi[a] / pl.size[a]))) - n0[a])
    pl.q0 = (n0[0] * pl.size[0], n0[1] * pl.size[1], n0[2] * pl.size[2])
    corner = model.grid.to_project(np.asarray([pl.q0]))[0]
    pl.grid = GridV2.of({"corner": [float(v) for v in corner], "counts": counts, "size": list(pl.size),
                         "order": bs.ORDER, "rotation": model.grid.rotation})  # fmt: skip
    _fields(model, pl)
    return pl


def _fields(model: Model, pl: Plan) -> None:
    """`pl.fields` (numbers, categories, `P_<КОД>`, FRAC of the file of the result), the accumulators of categories."""
    taken: set[str] = set()

    def put(spec: dict[str, Any], param: str) -> None:
        key = spec["name"].casefold()
        if key in taken or key == bs.IDX or len(spec["name"]) > bs.MAX_FIELD_NAME:
            raise bad_param(param, f"Имя поля «{spec['name'][:60]}» занято или слишком длинное.",
                            "Переименуйте поле или уберите его из списка.")  # fmt: skip
        taken.add(key)
        pl.fields.append(spec)

    for name, _w in pl.numbers:
        f = model.field(name)
        put({"name": f["name"], "kind": bs.NUMBER, "dtype": "f8", "unit": f.get("unit"),
             "description": f.get("description"), "role": f.get("role")}, "grades")  # fmt: skip
    for name in pl.cats:
        f = model.field(name)
        put({"name": f["name"], "kind": bs.CATEGORY, "dtype": "cat", "unit": None,
             "labels": list(f.get("labels") or []), "description": f.get("description"),
             "role": f.get("role")}, "categories")  # fmt: skip
    off = BASE + 2 * len(pl.numbers)
    for c, name in enumerate(pl.cats):
        f = model.field(name)
        labels = list(f.get("labels") or [])
        pl.cat_off.append(off)
        pl.cat_n.append(len(labels))
        for code, label in enumerate(labels):
            nm = f"P_{label}"
            if nm.casefold() in taken:
                nm = f"P_{f['name']}_{label}"
            put({"name": nm, "kind": bs.NUMBER, "dtype": "f8", "unit": None, "role": None,
                 "description": f"Доля объёма кода «{label}» поля «{f['name']}»"}, "categories")  # fmt: skip
            pl.pcols.append((c, code, nm))
        off += len(labels)
    pl.nacc = off
    put({"name": FRAC_NAME, "kind": bs.NUMBER, "dtype": "f8", "role": "frac", "unit": None,
         "description": FRAC_NOTE}, "grades")  # fmt: skip
    if len(pl.fields) > bs.MAX_FIELDS:
        raise bad_param("categories", "Слишком много полей в результате.", "Уберите категории с большим числом кодов.")


# --- the run ----------------------------------------------------------------------------------------------------


@dataclass
class Result:
    """What `regularize` returns: the staged file (give it to `commit`), the plan, the rows and the totals."""

    staged: bm.StagedCells
    plan: Plan
    rows: int
    totals: dict[str, Any]
    inputs: dict[str, Any]


class _Window:
    """The accumulators of the new layers not written yet: `acc` [nacc, slots, na·nb]; slot s — layer `base` + s."""

    def __init__(self, pl: Plan) -> None:
        self.pl = pl
        self.cells = pl.grid.counts[0] * pl.grid.counts[1]
        self.base = 0
        self.slots = 0
        self.acc = np.zeros((pl.nacc, 0, self.cells))

    def need(self, lo: int, hi: int) -> None:
        """Slots for the layers [lo, hi] (a row below `base` — the rows are not in the order of the file)."""
        if lo < self.base:
            raise bm.broken()
        want = hi - self.base + 1
        if want > self.slots:
            if self.pl.nacc * want * self.cells * 8 > MAX_WINDOW_BYTES:
                raise ApiError(422, "block_model_too_large", "Слой ячеек результата не помещается в память.",
                               "Укрупните блоки или уменьшите число полей.")  # fmt: skip
            grown = np.zeros((self.pl.nacc, want, self.cells))
            grown[:, : self.slots] = self.acc
            self.acc, self.slots = grown, want

    def drop(self, count: int) -> None:
        """The first `count` layers are written: the window moves up by them."""
        keep = max(self.slots - count, 0)
        if keep:
            self.acc[:, :keep] = self.acc[:, count : count + keep].copy()
        self.acc[:, keep:] = 0.0
        self.base += count


def _cells(pl: Plan, acc: np.ndarray, layer: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The cells of a finished layer (accumulators `acc` [nacc, na·nb]): (P ascending, {field: values})."""
    occ = np.flatnonzero(acc[0] > 0)
    p = (occ + pl.grid.counts[0] * pl.grid.counts[1] * layer).astype(np.uint64)
    out: dict[str, np.ndarray] = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        for f, (name, _w) in enumerate(pl.numbers):
            den = acc[BASE + 2 * f + 1, occ]
            out[name] = np.where(den > 0, acc[BASE + 2 * f, occ] / den, np.nan)
        blocks, totals = [], []
        for c, name in enumerate(pl.cats):
            n = pl.cat_n[c]
            block = acc[pl.cat_off[c] : pl.cat_off[c] + n][:, occ]
            tot = block.sum(axis=0) if n else np.zeros(len(occ))
            blocks.append(block)
            totals.append(tot)
            mode = block.argmax(axis=0) if n else np.zeros(len(occ), np.intp)
            out[name] = np.where(tot > 0, mode, -1).astype(np.int32)  # equal volumes — the lower code
        for c, code, name in pl.pcols:
            out[name] = np.where(totals[c] > 0, blocks[c][code] / totals[c], np.nan)
        cov = np.where(acc[2, occ] > 0, np.minimum(acc[1, occ], 1.0), np.nan)
        out[FRAC_NAME] = np.where(np.abs(cov - 1.0) < 1e-12, 1.0, cov)
    return p, out


def _after(pl: Plan, out: Mapping[str, np.ndarray], parts: dict[str, list[float]]) -> None:
    """The partial sums of the totals after, of one layer (summed again with `fsum` at the end)."""
    vol = out[FRAC_NAME] * ((pl.size[0] * pl.size[1]) * pl.size[2])
    ok = ~np.isnan(vol)
    parts["volume"].append(math.fsum(vol[ok]))
    dens: np.ndarray | None = None
    if isinstance(pl.density, str):
        dens = out[pl.density]
    elif pl.density is not None:
        dens = np.full(len(vol), pl.density)
    if dens is not None:
        good = ok & ~np.isnan(dens)
        parts["tonnes"].append(math.fsum(vol[good] * dens[good]))
    for name, _w in pl.numbers:
        v = out[name]
        if dens is None:
            good = ok & ~np.isnan(v)
            parts[name].append(math.fsum(vol[good] * v[good]))
        else:
            good = ok & ~np.isnan(v) & ~np.isnan(dens)
            parts[name].append(math.fsum((vol[good] * dens[good]) * v[good]))


def _band(task: Callable[[int, int], Any], jb: int, nb: int, x: int, y: int) -> None:
    task(x * jb, min(y * jb, nb))


class _Run:
    """The state of one call of `regularize`: the window, the writer, the sums."""

    def __init__(self, model: Model, pl: Plan, writer: bm.CellsWriterV2, threads: int) -> None:
        g = model.grid
        self.model, self.pl, self.writer, self.threads = model, pl, writer, threads
        self.d = np.asarray(g.regular(), dtype=np.float64)
        self.div = np.asarray(g.sub.div, dtype=np.float64)
        self.size = np.asarray(pl.size, dtype=np.float64)
        self.q0 = np.asarray(pl.q0, dtype=np.float64)
        self.na, self.nb = pl.grid.counts[0], pl.grid.counts[1]
        bands = max(1, min(self.nb, BANDS))
        self.jb = -(-self.nb // bands)  # new rows a band holds
        self.bands = -(-self.nb // self.jb)
        self.dens_mode = 0 if pl.density is None else (1 if isinstance(pl.density, str) else 2)
        self.dens_c = float(pl.density) if self.dens_mode == 2 else 0.0  # type: ignore[arg-type]
        self.wts = np.asarray([w for _, w in pl.numbers], dtype=np.int64)
        self.cat_off = np.asarray(pl.cat_off or [0], dtype=np.int64)
        self.win = _Window(pl)
        self.before: list[np.ndarray] = []
        self.after: dict[str, list[float]] = {k: [] for k in ("volume", "tonnes", *(n for n, _ in pl.numbers))}
        self.rows_out = 0

    def flush(self, upto: int) -> None:
        """Writes the layers below `upto` (ascending P) and moves the window up to it."""
        win = self.win
        for layer in range(win.base, min(upto, win.base + win.slots)):
            p, out = _cells(self.pl, win.acc[:, layer - win.base], layer)
            if len(p):
                self.writer.write(p, out)
                _after(self.pl, out, self.after)
                self.rows_out += len(p)
        if upto > win.base:
            win.drop(upto - win.base)

    def chunk(self, ch: Chunk) -> None:
        """A portion of rows: the boxes and the sums before, then the cells, segment by segment (module docstring)."""
        pl, g, n = self.pl, self.model.grid, ch.n
        has_sub = ch.s is not None
        listed = ch.lc is not None and ch.ld is not None
        vals = np.stack([ch.cols[nm] for nm, _ in pl.numbers]) if pl.numbers else np.empty((0, n))
        cats = np.stack([ch.cols[nm] for nm in pl.cats]).astype(np.int32) if pl.cats else np.empty((0, n), np.int32)
        frac = ch.cols[pl.frac] if pl.frac else np.empty(0)
        dens = ch.cols[pl.density] if self.dens_mode == 1 else np.empty(0)  # type: ignore[index]
        s = ch.s if ch.s is not None else np.empty((0, 3), np.uint8)
        w = ch.w if ch.w is not None else np.empty((0, 3), np.uint8)
        tlo, thi = np.empty((n, 3)), np.empty((n, 3))
        clo, chi = np.empty((n, 3), np.int32), np.empty((n, 3), np.int32)
        if listed:  # the boxes as they are: no (P, s, w) to compute them from
            assert ch.lc is not None and ch.ld is not None
            prep = partial(rk.prep_list, ch.lc - ch.ld / 2, ch.lc + ch.ld / 2, ch.ld, self.q0, self.size, frac,
                           pl.frac is not None, dens, self.dens_mode, self.dens_c, vals, tlo, thi, clo,
                           chi)  # fmt: skip
        else:
            prep = partial(
                rk.prep,
                ch.p,
                has_sub,
                s,
                w,
                g.counts[0],
                g.counts[1],
                self.d,
                self.div,
                self.q0,
                self.size,
                frac,
                pl.frac is not None,
                dens,
                self.dens_mode,
                self.dens_c,
                vals,
                tlo,
                thi,
                clo,
                chi,
            )
        self.before.extend(strips.run(prep, n, threads=self.threads, size=STRIP_ROWS))
        last = int(ch.p[-1]) // (g.counts[0] * g.counts[1])  # later rows are not lower than the bottom of this layer
        low = last * self.d[2] - (self.d[2] / 2 if listed else 0.0)  # a block of a list reaches ½ cell below its cell
        safe = int(math.floor(_snap_py((low - self.q0[2]) / self.size[2])))
        lows, highs = clo[:, 2], chi[:, 2]
        floor_ahead = np.minimum.accumulate(lows[::-1])[::-1]  # the lowest new layer of the rows from here on
        cuts = np.flatnonzero(floor_ahead[1:] > np.maximum.accumulate(highs)[:-1]) + 1  # nothing joins the two sides
        for a, b in zip([0, *cuts.tolist()], [*cuts.tolist(), n], strict=True):
            self.flush(min(int(floor_ahead[a]), safe))
            self.win.need(int(lows[a:b].min()), int(highs[a:b].max()))
            acc = self.win.acc.reshape(pl.nacc, -1)
            task = partial(rk.accumulate, a, b, self.na, self.nb, self.win.base, tlo, thi, clo, chi, frac,
                           pl.frac is not None, dens, self.dens_mode, self.dens_c, vals, self.wts, cats, self.cat_off,
                           acc)  # fmt: skip
            strips.run(partial(_band, task, self.jb, self.nb), self.bands, threads=self.threads, size=1)
        self.flush(safe)


def regularize(model: Model, store: BlobStore, size: Sequence[float], *, grades: Sequence[Any] | None = None,
               density: Any = None, categories: Sequence[str] | None = None, threads: int = 1,
               tick: Callable[[float], None] | None = None, chunk: int = CHUNK) -> Result:  # fmt: skip
    """The regularization (module docstring): the staged file of the new model and the totals. `tick(fraction)` is
    called after every portion (it may raise `JobCancelled`); nothing stays on disk after an error or a cancel."""
    pl = make_plan(model, size, grades, density, categories)
    names = [n for n, _ in pl.numbers] + pl.cats + ([pl.frac] if pl.frac else [])
    done = 0
    with bm.CellsWriterV2(store, pl.grid, pl.fields, sub=False) as writer:
        run = _Run(model, pl, writer, threads)
        for ch in model.iter(names, chunk):
            run.chunk(ch)
            done += ch.n
            if tick is not None:
                tick(0.97 * done / model.rows)
        run.flush(run.win.base + run.win.slots)
        staged = writer.finish()
    return Result(
        staged,
        pl,
        run.rows_out,
        _totals(pl, run.before, run.after),
        {"model": [{"id": model.id, "sha": model.shas().get(bs.ROLE, "")}]},
    )


def _totals(pl: Plan, before: Sequence[np.ndarray], after: Mapping[str, list[float]]) -> dict[str, Any]:
    """{before, after: {volume, tonnes, metal: {field: number}}} from the Neumaier pairs per strip and the layers."""
    have = pl.density is not None
    sums = np.stack(before)

    def part(slot: int) -> float:
        return math.fsum(sums[:, slot, :].reshape(-1).tolist())

    b = {"volume": part(0), "tonnes": part(1) if have else None,
         "metal": {n: part(2 + f) for f, (n, _w) in enumerate(pl.numbers) if n != pl.density}}  # fmt: skip
    a = {"volume": math.fsum(after["volume"]), "tonnes": math.fsum(after["tonnes"]) if have else None,
         "metal": {n: math.fsum(after[n]) for n, _w in pl.numbers if n != pl.density}}  # fmt: skip
    return {"before": b, "after": a, "metal_basis": "tonnes" if have else "volume"}


def commit(conn: sa.Connection, store: BlobStore, model: Model, res: Result, *, name: str = NAME,
           folder: str | None = None, job_id: int | None = None,
           author: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Inside the write transaction: the new model (`source {op: regularize, params}`, `inputs.model`, `algo`) with the
    CRS of the source. → the reply of the job: {model_id, name, rows, totals, events}."""
    crs = conn.execute(sa.select(objects.c.crs).where(objects.c.id == model.id)).scalar()
    try:
        source = {"op": OP, "params": res.plan.params()}
        bm_id = bm.commit_block_model(conn, store, res.staged, name=name, source=source, folder=folder, job_id=job_id,
                                      crs=crs, author=author, inputs=res.inputs, algo=ALGO)  # fmt: skip
    finally:
        res.staged.discard()
    final = conn.execute(sa.select(objects.c.name).where(objects.c.id == bm_id)).scalar_one()
    return {"model_id": bm_id, "name": str(final), "rows": res.rows, "totals": res.totals,
            "events": [["objects.changed", {"kind": bs.KIND, "id": bm_id}]]}  # fmt: skip
