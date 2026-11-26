"""The economics of the blocks of a model for the pit optimiser (подшаг 21.2; ТЗ 8.11.2; ADR 0028 decisions 3–8, 16).

The table of the nodes, the integers of the solver — `econ_table.py`.

    inp = Inputs(elements=[Element("AU", unit_factor("g/t"), 2000.0, 5.0)], rocks=[Rock("Руда", 20.0, rec=[0.9])],
                 c_m0=2.5, z_ref=top, density_field="SG", default_density=2.6)
    tab = build_table(model, inp, restrictor, chunk=, tick=, threads=)   # the model is read ONCE
    v = tab.values(rf)                      # float64 [nodes]: the value of every node at the factor RF (a pass over
    q = to_int(v)                           #   three numbers per row; no model); q.v int64 [nodes] for the solver
    for rc in iter_rows(model, inp, restrictor):    # the report: per-row quantities of a portion (21.8)
        t = row_terms(rc, inp, 1.0)

`Inputs` is plain names and numbers: the spec of the scenario (`mine/spec.py`, 21.1) is turned into one by the caller,
so this module does not depend on it. The formulas are in `econ_kernels.py`: R = A·RF − B is linear in RF, so a row
keeps (A, D = B + C_p, C_m) and a table is read once for all the shells.

A row of the model is one block (a sub-block is a row of its parent). Per row: the rock r (a category field — each
label is a rock, «(пусто)» is the digit 0 — or the ordered rules, the first with f > 0, else `rock_other`, or one rock),
the share f of the restrictor (`blocks/restrict.py`: the restrictor of the user × the surface leaf `below`, `by: frac`
— f = 0 is «воздух»: out of the graph and not a waste), the volume `geometry.row_volumes` (FRAC in it), the density (the
field, a number, or `default_density` where the block has none — counted in «Контроль»; none of them: 422
`pit_density_missing {blocks}` at the end of the pass), the grades (NaN gives no revenue of that element and goes to
«Контроль», never 0), the centre z of the block (the cost of the depth, C_m).

Nodes are the parents of the model (decision 13): the rows of a parent are consecutive in the file and `Table.seg`
groups them; the value of a node is the sum of the values of its rows (an ore row and a waste row of one parent), its
tonnes and metal are the sums of the rows (`iter_rows`). A parent is «воздух» when all its rows are. Cells absent from
a sparse model (`fill_empty`, set when the scenario has a relief): below the relief a waste of the rock «(пусто)» with
the default density, above it — «воздух»; without a relief they are «воздух» and `fill_empty` is False (decision 7).

Reproducibility (decision 16): the sums are Neumaier's in the order of the rows; the kernels write rows, strips of rows
and of nodes are fixed (`strips`), nothing is summed between them: the same bits with a portion of 1, 7 or 2²⁰ rows and
with 1–8 threads. Errors — {code, message, hint} in Russian: 422 `bad_param {param}`, `variable_grid`,
`pit_density_missing {blocks}`, `pit_too_large`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import schema as bs
from bf_core.blocks.model import CHUNK, Chunk, Model
from bf_core.blocks.restrict import Restrictor
from bf_core.composites import strips

from . import econ_kernels as ek
from .econ_empty import empty_cells

Tick = Callable[[float | None, str | None], None]

TROY_OUNCE_G = 31.1034768  # grams in a troy ounce
ROW_STRIP = 1 << 16  # rows of a strip of the kernels (fixed: the result does not depend on the threads)

# the units of a grade → k_e (the metal of t tonnes of rock is t·g·k_e in the units of the price: ounces or tonnes)
_UNITS = {
    "g/t": 1.0 / TROY_OUNCE_G, "г/т": 1.0 / TROY_OUNCE_G, "ppm": 1.0 / TROY_OUNCE_G, "ppb": 1e-3 / TROY_OUNCE_G,
    "%": 0.01, "pct": 0.01,
}  # fmt: skip


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad_param(param: str, message: str, hint: str = "Проверьте параметры сценария.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def unit_factor(unit: str) -> float:
    """k_e of a grade unit: g/t, ppm — 1/31,1034768 oz per g (the price is per ounce); ppb — 10⁻³ of it; % — 0,01 t/t
    (the price is per tonne of metal)."""
    k = _UNITS.get(str(unit).strip().lower())
    if k is None:
        raise bad_param("elements.unit", f"Единица содержания «{unit}» не поддерживается.",
                        "Допустимы г/т, ppm, ppb и %.")  # fmt: skip
    return k


def grid_top(model: Model) -> float:
    """The default Z_ref: the top of the grid (the corner plus the cells of the column)."""
    grid = model.grid
    d = grid.regular("Оптимизация карьера")
    return float(grid.corner[2] + grid.counts[2] * d[2])


# --- the inputs ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    """An element of the revenue: the grade field, k_e (`unit_factor`), the price P_e and the selling cost S_e per unit
    of metal, the royalty roy_e ∈ [0; 1)."""

    field: str
    k: float
    price: float
    sell: float = 0.0
    royalty: float = 0.0


@dataclass(frozen=True)
class Rock:
    """A type of rock: c_p — the processing cost, c_mp — the surcharge of mining ore ($/t), `rec` — the recovery of each
    element (in the order of `Inputs.elements`); `waste_only` — never ore (R = 0); `air` — out of the graph."""

    name: str
    c_p: float
    c_mp: float = 0.0
    rec: Sequence[float] = ()
    waste_only: bool = False
    air: bool = False


@dataclass(frozen=True)
class Inputs:
    """What the kernels need (module docstring). Rock of a row: `rock_field` + `rock_map` (the rock of the digit
    code + 1: index 0 — «(пусто)», length labels + 1), or `rock_rules` [(rock id, restrictor)] in order with
    `rock_other` for the rest, or neither: every row is the rock `rock_other`. `fill_empty` — see the module
    docstring."""

    elements: Sequence[Element]
    rocks: Sequence[Rock]
    c_m0: float
    z_ref: float
    h_gl: float = 10.0
    c_gl: float = 0.0
    density_field: str | None = None
    density_value: float | None = None
    default_density: float | None = None
    frac: str | None = None
    rock_field: str | None = None
    rock_map: Sequence[int] = ()
    rock_rules: Sequence[tuple[int, Restrictor]] = ()
    rock_other: int = 0
    fill_empty: bool = False

    def check(self) -> None:
        """422 `bad_param` for what the kernels cannot take (the readiness of the scenario is `mine/spec.py`'s)."""
        ne, nr = len(self.elements), len(self.rocks)
        if ne < 1:
            raise bad_param("elements", "Нужен хотя бы один элемент выручки.", "Добавьте элемент, например Au.")
        if len({e.field for e in self.elements}) != ne:
            raise bad_param("elements", "Поле содержания указано у двух элементов.", "Уберите повтор.")
        for i, e in enumerate(self.elements):
            where = f"elements.{i}"
            if not (math.isfinite(e.k) and e.k > 0.0):
                raise bad_param(f"{where}.unit", "Единица содержания должна давать коэффициент больше нуля.")
            if not (math.isfinite(e.price) and math.isfinite(e.sell) and e.sell >= 0.0):
                raise bad_param(f"{where}.price", f"Элемент {e.field}: цена и затраты на сбыт должны быть числами.",
                                "Сбыт — не меньше нуля.")  # fmt: skip
            if not (math.isfinite(e.royalty) and 0.0 <= e.royalty < 1.0):
                raise bad_param(f"{where}.royalty", f"Элемент {e.field}: роялти — от 0 до 1 (не включая 1).",
                                "Например 0,03 — три процента.")  # fmt: skip
        if nr < 1:
            raise bad_param("rocks", "Нужен хотя бы один тип породы.", "Задайте породы.")
        for i, r in enumerate(self.rocks):
            where = f"rocks.{i}"
            if len(r.rec) != ne and not r.air:
                raise bad_param(f"{where}.rec", f"Порода «{r.name}»: извлечение задано не для всех элементов.",
                                "Укажите извлечение каждого элемента.")  # fmt: skip
            if any(not (math.isfinite(x) and 0.0 <= x <= 1.0) for x in r.rec):
                raise bad_param(f"{where}.rec", f"Порода «{r.name}»: извлечение — от 0 до 1.", "Например 0,9.")
            if not (math.isfinite(r.c_p) and r.c_p >= 0.0 and math.isfinite(r.c_mp) and r.c_mp >= 0.0):
                raise bad_param(f"{where}.c_p", f"Порода «{r.name}»: затраты не могут быть отрицательными.",
                                "Затраты на переработку и доплата за добычу руды — числа не меньше нуля.")  # fmt: skip
        if not (math.isfinite(self.c_m0) and self.c_m0 >= 0.0 and math.isfinite(self.c_gl) and self.c_gl >= 0.0):
            raise bad_param("c_m0", "Затраты на добычу не могут быть отрицательными.", "Например 2,5 $/т.")
        if not (math.isfinite(self.h_gl) and self.h_gl > 0.0 and math.isfinite(self.z_ref)):
            raise bad_param("h_gl", "Шаг глубины H_гл должен быть больше нуля, Z_ref — числом.", "Например 10 м.")
        if self.density_field is None and not (self.density_value is not None and self.density_value > 0.0):
            raise bad_param("density", "Плотность: нужно поле или число больше нуля.", "Например 2,7 или поле SG.")
        if self.default_density is not None and not (math.isfinite(self.default_density) and self.default_density > 0):
            raise bad_param("default_density", "Плотность по умолчанию должна быть больше нуля.", "Например 2,6.")
        if self.rock_field is not None and self.rock_rules:
            raise bad_param("rocks", "Тип породы задан и полем, и правилами.", "Оставьте что-то одно.")
        ids = [self.rock_other, *self.rock_map, *(i for i, _ in self.rock_rules)]
        if any(not (0 <= int(i) < nr) for i in ids):
            raise bad_param("rocks", "Номер типа породы выходит за список пород.", "Проверьте соответствие меток.")
        if self.rock_field is not None and not len(self.rock_map):
            raise bad_param("rocks", "Для поля типа породы не задано соответствие меток.", "Задайте тип каждой метки.")

    def check_model(self, model: Model) -> None:
        """422 for a model the optimiser cannot take and for a field that is missing or of the wrong kind."""
        if model.grid.tensors is not None:
            raise ApiError(422, "variable_grid", "Оптимизация карьера требует постоянного шага сетки.",
                           "Регуляризуйте модель: Блочные модели → Регуляризация.")  # fmt: skip
        for i, e in enumerate(self.elements):
            if model.field(e.field)["kind"] != bs.NUMBER:
                raise bad_param(f"elements.{i}.field", f"Поле {e.field} не числовое.", "Выберите поле содержания.")
        if self.density_field is not None and model.field(self.density_field)["kind"] != bs.NUMBER:
            raise bad_param("density", f"Поле плотности {self.density_field} не числовое.", "Выберите числовое поле.")
        if self.frac is not None:
            model.field(self.frac)
        if self.rock_field is not None:
            spec = model.field(self.rock_field)
            if spec["kind"] != bs.CATEGORY:
                raise bad_param("rocks.field", f"Поле {self.rock_field} не категорийное.",
                                "Выберите категорийное поле.")  # fmt: skip
            if len(self.rock_map) != len(spec.get("labels") or []) + 1:
                raise bad_param("rocks.map", "Соответствие меток не совпадает с метками поля.",
                                "Обновите список пород.")  # fmt: skip

    def reads(self) -> list[str]:
        """The fields to read besides the ones of the restrictor of the blocks."""
        names = [*(e.field for e in self.elements), self.density_field, self.frac, self.rock_field]
        for _, r in self.rock_rules:
            names.extend(r.fields)
        return list(dict.fromkeys(n for n in names if n is not None))

    def tables(self) -> Tables:
        """The per-rock and per-element arrays of the kernels."""
        ne = len(self.elements)
        rec = np.zeros((len(self.rocks), ne))
        for i, r in enumerate(self.rocks):
            if len(r.rec) == ne:
                rec[i] = r.rec
        return Tables(
            np.array([r.air for r in self.rocks], dtype=np.bool_),
            np.array([r.waste_only for r in self.rocks], dtype=np.bool_),
            np.array([r.c_p + r.c_mp for r in self.rocks], dtype=np.float64), rec,
            np.array([e.k for e in self.elements], dtype=np.float64),
            np.array([e.price for e in self.elements], dtype=np.float64),
            np.array([e.sell for e in self.elements], dtype=np.float64),
            np.array([e.royalty for e in self.elements], dtype=np.float64),
        )  # fmt: skip


class Tables(NamedTuple):
    """The arrays of rocks [R] and elements [E] (`Inputs.tables`)."""

    air: np.ndarray
    waste: np.ndarray
    cp: np.ndarray
    rec: np.ndarray
    k: np.ndarray
    price: np.ndarray
    sell: np.ndarray
    roy: np.ndarray


def grade_cutoff(inp: Inputs) -> list[list[float | None]]:
    """The cut-off grade of every pair (rock, element) at RF = 1, the other elements 0 and no cost of mining
    (decision 8, № 123): g = (c_p + c_mp)/(k·rec·(P − S)·(1 − roy)), in the unit of the grade. None — there is none:
    P ≤ S, rec = 0, or a rock that is never ore (only waste, air)."""
    out: list[list[float | None]] = []
    for r in inp.rocks:
        row: list[float | None] = []
        for i, e in enumerate(inp.elements):
            rec = r.rec[i] if len(r.rec) == len(inp.elements) else 0.0
            den = e.k * rec * (e.price - e.sell) * (1.0 - e.royalty)
            row.append(None if (r.waste_only or r.air or not den > 0.0) else (r.c_p + r.c_mp) / den)
        out.append(row)
    return out


# --- the economics of arrays -----------------------------------------------------------------------------


def _w(a: Any, dtype: Any = np.float64) -> np.ndarray:
    """`a` as a writable C-contiguous array of `dtype` (a copy only when it is not one): ONE numba specialization."""
    a = np.asarray(a)
    if a.dtype == dtype and a.flags.writeable and a.flags.c_contiguous:
        return a
    return np.array(a, dtype=dtype, order="C")


@dataclass
class BlockEcon:
    """The economics of n rows. `state` (see `econ_kernels`: 1, 2 — in the graph), t = V·FRAC·f·ρ, `a` = A, `b` = B,
    `cp` = C_p, `cm` = C_m, `ore` (uint8) — ore at RF = 1. R(RF) = a·RF − b; v(RF) = max(a·RF − (b + cp); 0) − cm."""

    state: np.ndarray
    t: np.ndarray
    a: np.ndarray
    b: np.ndarray
    cp: np.ndarray
    cm: np.ndarray
    ore: np.ndarray

    @property
    def counted(self) -> np.ndarray:
        return (self.state == ek.ST_OK) | (self.state == ek.ST_DEFAULT)

    def revenue(self, rf: float = 1.0) -> np.ndarray:
        """R(RF) of each row (0 where the row is not counted)."""
        return self.a * rf - self.b

    def value(self, rf: float = 1.0) -> np.ndarray:
        """v(RF) of each row (0 where the row is not counted)."""
        v = np.maximum(self.a * rf - (self.b + self.cp), 0.0) - self.cm
        return np.where(self.counted, v, 0.0)


def block_econ(inp: Inputs, *, f: np.ndarray, vol: np.ndarray, rock: np.ndarray, z: np.ndarray, g: np.ndarray,
               dens: np.ndarray | None = None, threads: int = 1) -> BlockEcon:  # fmt: skip
    """The economics of the rows given as arrays: `f` — the share of the restrictor, `vol` — the volume (FRAC in it;
    NaN — none), `rock` — the rock of each row (int32), `z` — the centre, `g` [E, n] — the grades (NaN — a gap),
    `dens` — the density of each row (None — `inp.density_value`)."""
    return _econ(inp, inp.tables(), f, vol, dens, rock, z, g, threads)


def _econ(inp: Inputs, tb: Tables, f: Any, vol: Any, dens: Any, rock: Any, z: Any, g: Any, threads: int) -> BlockEcon:
    n = len(f)
    f, vol, z, g = _w(f), _w(vol), _w(z), _w(g)
    rock = _w(rock, np.int32)
    d = np.empty(0) if dens is None else _w(dens)
    dens_c = float(inp.density_value) if (dens is None and inp.density_value is not None) else float("nan")
    default = float("nan") if inp.default_density is None else float(inp.default_density)
    state = np.empty(n, dtype=np.int8)
    t, a, b, cp, cm = (np.empty(n) for _ in range(5))
    ore = np.empty(n, dtype=np.uint8)

    def part(lo: int, hi: int) -> None:
        ek.row_econ(lo, hi, f, vol, d, dens_c, default, rock, z, g, tb.air, tb.waste, tb.cp, tb.rec, tb.k, tb.price,
                    tb.sell, tb.roy, float(inp.c_m0), float(inp.c_gl), float(inp.z_ref), float(inp.h_gl), state, t, a,
                    b, cp, cm, ore)  # fmt: skip

    strips.run(part, n, threads=threads, size=ROW_STRIP)
    return BlockEcon(state, t, a, b, cp, cm, ore)


# --- the control -----------------------------------------------------------------------------------------


@dataclass
class Control:
    """«Контроль» of the pass (decisions 6, 7; № 117, 118): counts and tonnes that the numbers hide. `cnt`, `acc` — the
    slots of `econ_kernels`; `rock_cnt` [R, 2] — blocks in the graph and ore blocks per rock; `rock_acc` [2R, 2] — the
    Neumaier tonnes of ore (2r) and waste (2r + 1)."""

    elements: list[str]
    rocks: list[str]
    cnt: np.ndarray
    acc: np.ndarray
    rock_cnt: np.ndarray
    rock_acc: np.ndarray

    @classmethod
    def new(cls, inp: Inputs) -> Control:
        ne, nr = len(inp.elements), len(inp.rocks)
        return cls([e.field for e in inp.elements], [r.name for r in inp.rocks],
                   np.zeros(ek.CNT_GAP0 + ne, dtype=np.int64), np.zeros((ek.ACC_GAP0 + ne, 2)),
                   np.zeros((nr, 2), dtype=np.int64), np.zeros((2 * nr, 2)))  # fmt: skip

    @staticmethod
    def _sum(acc: np.ndarray, row: int) -> float:
        return float(acc[row, 0] + acc[row, 1])

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: counts of blocks and tonnes (float64 sums, Neumaier)."""
        c, s = self.cnt, self._sum
        return {
            "rows": int(c[ek.CNT_ROWS]), "air": int(c[ek.CNT_AIR]), "no_volume": int(c[ek.CNT_NO_VOLUME]),
            "no_density": int(c[ek.CNT_NO_DENSITY]), "tonnes": s(self.acc, ek.ACC_TONNES),
            "default_density": {"blocks": int(c[ek.CNT_DEFAULT]), "volume": s(self.acc, ek.ACC_DEFAULT_VOLUME)},
            "empty_cells": {"blocks": int(c[ek.CNT_EMPTY]), "volume": s(self.acc, ek.ACC_EMPTY_VOLUME)},
            "grade_gaps": {name: {"blocks": int(c[ek.CNT_GAP0 + i]), "tonnes": s(self.acc, ek.ACC_GAP0 + i)}
                           for i, name in enumerate(self.elements)},
            "rocks": [{"name": name, "blocks": int(self.rock_cnt[i, 0]), "ore_blocks": int(self.rock_cnt[i, 1]),
                       "ore_tonnes": s(self.rock_acc, 2 * i), "waste_tonnes": s(self.rock_acc, 2 * i + 1)}
                      for i, name in enumerate(self.rocks)],
        }  # fmt: skip

    def warnings(self) -> list[str]:
        """The Russian lines for the user (nothing is said when there is nothing to say)."""
        d, out = self.to_dict(), []
        if d["no_volume"]:
            out.append(f"У {d['no_volume']} блоков пустой объём (FRAC): они не вошли в расчёт.")
        if d["default_density"]["blocks"]:
            out.append(f"У {d['default_density']['blocks']} блоков нет плотности: взята плотность по умолчанию "
                       f"(объём {d['default_density']['volume']:.0f} м³).")  # fmt: skip
        if d["empty_cells"]["blocks"]:
            out.append(f"Пустых ячеек ниже рельефа: {d['empty_cells']['blocks']} (объём "
                       f"{d['empty_cells']['volume']:.0f} м³) — пустая порода.")  # fmt: skip
        for name, gap in d["grade_gaps"].items():
            if gap["blocks"]:
                out.append(f"У {gap['blocks']} блоков нет содержания {name} ({gap['tonnes']:.0f} т): "
                           "элемент выручки не даёт.")  # fmt: skip
        return out


def density_missing(blocks: int) -> ApiError:
    return ApiError(422, "pit_density_missing", f"У {blocks} блоков нет плотности, а плотность по умолчанию не задана.",
                    "Задайте плотность по умолчанию в экономике сценария.", {"blocks": int(blocks)})  # fmt: skip


# --- the pass over the model -----------------------------------------------------------------------------


@dataclass
class RowChunk:
    """A portion of rows with their economics (`start` — the first file row, −1 for cells absent from the file). `f` —
    the share of the restrictor, `vol` — the volume (FRAC in it; the tonnes of a row are `econ.t`), `g` [E, n] — the
    grades (NaN — a gap), `rock` int32 — the rock of each row; the rest as `BlockEcon`."""

    start: int
    p: np.ndarray
    gap: bool
    f: np.ndarray
    vol: np.ndarray
    rock: np.ndarray
    g: np.ndarray
    econ: BlockEcon

    @property
    def n(self) -> int:
        return int(self.p.shape[0])


def _rocks_of(inp: Inputs, ch: Chunk) -> np.ndarray:
    """int32 [n]: the rock of every row (module docstring)."""
    n = ch.n
    if inp.rock_field is not None:
        idx = np.asarray(ch.cols[inp.rock_field], dtype=np.int64) + 1
        idx[(idx < 0) | (idx >= len(inp.rock_map))] = 0
        return np.asarray(inp.rock_map, dtype=np.int32)[idx]
    rock = np.full(n, int(inp.rock_other), dtype=np.int32)
    for rid, rs in reversed(list(inp.rock_rules)):  # the first rule wins: written last
        rock[rs.weights(ch) > 0.0] = int(rid)
    return rock


def _rows_of(model: Model, inp: Inputs, tb: Tables, ch: Chunk, f: np.ndarray, gap: bool, threads: int) -> RowChunk:
    """The economics of a portion `ch` with the shares `f`."""
    n = ch.n
    frac = None if (gap or inp.frac is None) else ch.cols[inp.frac]
    vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, frac)
    z = model.grid.corner[2] + geometry.row_local_centres(model.grid, ch.p, ch.s, ch.w)[:, 2]
    g = np.empty((len(inp.elements), n))
    for i, e in enumerate(inp.elements):
        g[i] = ch.cols[e.field]
    dens = None if inp.density_field is None else ch.cols[inp.density_field]
    rock = _rocks_of(inp, ch)
    return RowChunk(ch.start, ch.p, gap, f, vol, rock, g, _econ(inp, tb, f, vol, dens, rock, z, g, threads))


def iter_rows(
    model: Model, inp: Inputs, r: Restrictor, *, control: Control | None = None, chunk: int = CHUNK,
    tick: Tick = _noop, threads: int = 1, span: tuple[float, float] = (0.0, 1.0),
) -> Iterator[RowChunk]:  # fmt: skip
    """The portions of rows with their economics: the file in its order, then — with `fill_empty` — the cells absent
    from it. `control` collects «Контроль» (counts, Neumaier sums, in the order of the rows); at the end of the pass 422
    `pit_density_missing` if a block has no density and there is no default. `tick(share, None)` once a portion (it
    raises to cancel). The restrictor `r` gives f of the file rows (and of the absent cells, whose fields are empty)."""
    inp.check()
    inp.check_model(model)
    ctl = control if control is not None else Control.new(inp)
    tb = inp.tables()
    waste = tb.waste
    a0, b0 = span
    first = 0.85 if inp.fill_empty else 1.0  # the share of the span of the file rows

    def emit(rc: RowChunk) -> None:
        e = rc.econ
        ek.control(rc.n, rc.gap, e.state, rc.rock, _w(rc.vol), _w(rc.f), e.t, rc.g, e.ore, waste, ctl.cnt, ctl.acc,
                   ctl.rock_cnt, ctl.rock_acc)  # fmt: skip

    names = list(dict.fromkeys([*r.fields, *inp.reads()]))
    for ch, f in r.iter(model, extra=inp.reads(), chunk=chunk):
        tick(a0 + (b0 - a0) * first * (ch.start + ch.n) / max(model.rows, 1), None)
        rc = _rows_of(model, inp, tb, ch, f, False, threads)
        emit(rc)
        yield rc
    if inp.fill_empty:
        for share, ch in empty_cells(model, names, chunk, getattr(r, "kmax", None)):
            tick(a0 + (b0 - a0) * (first + (1.0 - first) * share), None)
            f = r.weights(ch)
            rc = _rows_of(model, inp, tb, ch, f, True, threads)
            emit(rc)
            yield rc
    if int(ctl.cnt[ek.CNT_NO_DENSITY]):
        raise density_missing(int(ctl.cnt[ek.CNT_NO_DENSITY]))


@dataclass
class Terms:
    """See `row_terms`."""

    m: np.ndarray
    mrec: np.ndarray
    gross: np.ndarray
    selling: np.ndarray
    royalty: np.ndarray


def row_terms(rc: RowChunk, inp: Inputs, rf: float = 1.0, *, threads: int = 1) -> Terms:
    """The revenue terms of a portion at the factor `rf` (`econ_kernels.terms`): per element the metal in the rock `m`
    and the recoverable `mrec` [E, n], per row `gross`, `selling`, `royalty` (R = gross − selling − royalty). A row
    that is not in the graph, or of a rock «только пустая», or an element with no grade gives 0 — the caller takes the
    ore rows by `rc.econ.ore`."""
    tb, ne, n = inp.tables(), len(inp.elements), rc.n
    m, mrec = np.empty((ne, n)), np.empty((ne, n))
    gross, selling, royalty = np.empty(n), np.empty(n), np.empty(n)
    g, rock, e = _w(rc.g), _w(rc.rock, np.int32), rc.econ

    def part(lo: int, hi: int) -> None:
        ek.terms(lo, hi, float(rf), e.state, rock, e.t, g, tb.waste, tb.rec, tb.k, tb.price, tb.sell, tb.roy, m, mrec,
                 gross, selling, royalty)  # fmt: skip

    strips.run(part, n, threads=threads, size=ROW_STRIP)
    return Terms(m, mrec, gross, selling, royalty)
