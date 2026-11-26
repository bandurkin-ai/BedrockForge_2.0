"""The pass of the resource report over a block model (подшаг 20.2; ТЗ 8.10; ADR 0027 decisions 3–7, 12).

    p = Core(grades=["AU"], cutoff_field="AU", density_value=2.5, edges=..., group_edges=..., keys=[Key("DOM", labels)])
    s = run(model, p, restrictor, chunk=, tick=)          # `Sums`: the accumulators of the total and of the groups
    q, n = kernels.range_sums(s.s0[None], s.c0[None], s.n0[None], lo, hi)     # a row of the table (kernels.py)

`Core` is what the kernels need — plain names, numbers and arrays; `resources/spec.py` (20.1) turns the request of
the user into one. A portion of rows is read by `restrictor.iter` (the fields of the restrictor and `Core.reads()`),
then: volume = `geometry.row_volumes` (the FRAC field of the model is in it) × the share f of the restrictor; a block
with f = 0 is out (№ 65); no volume (an empty FRAC) or no density (NaN or negative: a gap is not a zero) — out, counted
in `Sums.excluded`; tonnes = volume × f × density. A block with no value of the cut-off field is NOT out: it goes to the
bin «нет g_б» and is counted by the cut-off «без борта» only (as BF `cutoff=None`).

Groups: up to three keys, each a category field or the bench (`Bench`); the code is the mixed-radix number of the
digits (kernels.py), so the group row of the accumulators is `code × bins + bin`. The sums are Neumaier's, in the order
of the rows of the file: the same bits with any `chunk` (a portion of 1, 7 or 2²⁰ rows). `tick(share, text)` is called
once a portion (it raises to cancel the job). Errors — {code, message, hint} in Russian: 422 `bad_param {param}`,
`report_too_big`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.model import CHUNK, Model
from bf_core.blocks.restrict import Restrictor

from . import kernels as rk

Tick = Callable[[float | None, str | None], None]
MAX_KEYS, MAX_GROUPS, MAX_CELLS = 3, 20_000, 2_000_000


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def bad_param(param: str, message: str, hint: str = "Проверьте параметры отчёта.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


@dataclass(frozen=True)
class Bench:
    """The benches j = 0…count−1: [first + j·height; first + (j+1)·height] along Z (ADR 0027 decision 7)."""

    first: float
    height: float
    count: int


@dataclass(frozen=True)
class Key:
    """A key of the group: a category field with its `labels` (the radix is labels + 1: the digit 0 is «(пусто)»), or
    — `bench` set, `field` None — the bench (the radix is benches + 1)."""

    field: str | None
    labels: Sequence[str] = ()
    bench: Bench | None = None

    @property
    def radix(self) -> int:
        return (self.bench.count if self.bench is not None else len(self.labels)) + 1


@dataclass(frozen=True)
class Core:
    """What the kernels count. `grades` — the fields whose grade and metal are summed (the cut-off field may be
    among them or not); `cutoff_field` — the field g_б (None: no cut-off, only «без борта» rows); the density — a field
    or a number; `edges` — the bins of the total (the cut-offs of the table, the curve and the balance, sorted, unique),
    `group_edges` — the bins of a group (the table and the balance); `frac` — the name of the FRAC field of the model
    (None — no such field)."""

    grades: Sequence[str]
    cutoff_field: str | None
    density_field: str | None
    density_value: float | None
    edges: np.ndarray
    group_edges: np.ndarray
    keys: Sequence[Key] = ()
    frac: str | None = None

    def reads(self) -> list[str]:
        """The fields to read besides the ones of the restrictor."""
        names = [*self.grades, self.cutoff_field, self.density_field, self.frac, *(k.field for k in self.keys)]
        return list(dict.fromkeys(n for n in names if n is not None))

    def bench_key(self) -> int | None:
        idx = [i for i, k in enumerate(self.keys) if k.bench is not None]
        return idx[0] if idx else None

    def strides(self) -> list[int]:
        """The stride of each key in the code (the last key varies fastest)."""
        out, acc = [0] * len(self.keys), 1
        for i in range(len(self.keys) - 1, -1, -1):
            out[i] = acc
            acc *= self.keys[i].radix
        return out

    def n_groups(self) -> int:
        """The number of combinations of the digits (0 — no keys, only the total)."""
        if not self.keys:
            return 0
        return math.prod(k.radix for k in self.keys)

    def check(self) -> None:
        """422 on a key set the kernels do not take."""
        if len(self.keys) > MAX_KEYS:
            raise bad_param("groups", f"Не больше {MAX_KEYS} ключей группировки.", "Уберите лишние ключи.")
        if sum(k.bench is not None for k in self.keys) > 1:
            raise bad_param("groups", "Уступ можно взять в группировку только один раз.", "Оставьте один ключ «Уступ».")
        for k in self.keys:
            if k.bench is not None and (not (k.bench.height > 0.0) or k.bench.count < 1):
                raise bad_param("bench", "Уступы: высота должна быть больше нуля, число уступов — не меньше 1.",
                                "Например высота 10 м.")  # fmt: skip
            if k.bench is None and k.field is None:
                raise bad_param("groups", "У ключа группировки нет поля.", "Выберите категорийное поле.")
        if self.density_field is None and not (self.density_value is not None and self.density_value > 0.0):
            raise bad_param("density", "Плотность: нужно поле или число больше нуля.", "Например 2,7 или поле SG.")
        n = self.n_groups()
        cells = n * (len(self.group_edges) + 2) * (2 + 3 * len(self.grades))
        if n > MAX_GROUPS or cells > MAX_CELLS:
            raise ApiError(422, "report_too_big", "Слишком много групп, бортов и содержаний для одного отчёта.",
                           "Уберите ключ группировки или часть бортов, или возьмите меньше элементов.")  # fmt: skip


@dataclass
class Sums:
    """The accumulators of a pass. Total: `s0`, `c0` [bins, Q] (sum, carry), `n0` [bins, 1 + grades]; groups: `sg`,
    `cg` [groups × bins, Q], `ng` — the same shape of rows, `group_view()` reshapes them to [groups, bins, …].
    `excluded`: filtered (f = 0), no_volume, no_density (blocks); `no_density_volume` — the volume of the blocks with no
    density (m³ × f; the sum and its carry in `no_density_acc`) — the tonnage of them is unknown."""

    core: Core
    s0: np.ndarray
    c0: np.ndarray
    n0: np.ndarray
    sg: np.ndarray
    cg: np.ndarray
    ng: np.ndarray
    excluded: dict[str, int] = field(default_factory=dict)
    no_density_acc: np.ndarray = field(default_factory=lambda: np.zeros(2))
    warnings: list[str] = field(default_factory=list)

    @property
    def no_density_volume(self) -> float:
        return float(self.no_density_acc[0] + self.no_density_acc[1])

    def total_view(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """s, c [1, bins, Q] and n [1, bins, K] of the total: the shape `kernels.range_sums` takes."""
        return self.s0[None], self.c0[None], self.n0[None]

    def group_view(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """s, c [groups, bins, Q] and n [groups, bins, K] (groups = `core.n_groups()`, 0 — no keys)."""
        g, b = self.core.n_groups(), len(self.core.group_edges) + 2
        if g == 0:
            return (np.zeros((0, b, self.sg.shape[1])), np.zeros((0, b, self.sg.shape[1])),
                    np.zeros((0, b, self.ng.shape[1]), dtype=np.int64))  # fmt: skip
        return (self.sg.reshape(g, b, -1), self.cg.reshape(g, b, -1), self.ng.reshape(g, b, -1))


def new_sums(core: Core) -> Sums:
    q, k = 2 + 3 * len(core.grades), 1 + len(core.grades)
    b0, bg = len(core.edges) + 2, len(core.group_edges) + 2
    rows = max(core.n_groups() * bg, 1)
    return Sums(core, np.zeros((b0, q)), np.zeros((b0, q)), np.zeros((b0, k), dtype=np.int64),
                np.zeros((rows, q)), np.zeros((rows, q)), np.zeros((rows, k), dtype=np.int64),
                {"filtered": 0, "no_volume": 0, "no_density": 0})  # fmt: skip


def default_frac(model: Model) -> str | None:
    """The name of the FRAC field of the model (the role `frac`), None if it has none."""
    f = model.role_field("frac")
    return None if f is None else str(f["name"])


def group_codes(core: Core, cols: dict[str, np.ndarray], n: int) -> tuple[np.ndarray, int]:
    """int64 [n]: the code of the digits of the NON-bench keys (−1 — no keys at all), and the stride of the bench key
    (0 — none). A bench key adds its digit in the kernel."""
    if not core.keys:
        return np.full(n, -1, dtype=np.int64), 0
    code = np.zeros(n, dtype=np.int64)
    bstride = 0
    for key, stride in zip(core.keys, core.strides(), strict=True):
        if key.bench is not None:
            bstride = stride
            continue
        assert key.field is not None
        digit = cols[key.field].astype(np.int64) + 1  # −1 (empty) → 0
        digit[(digit < 0) | (digit >= key.radix)] = 0  # a code beyond the labels: «(пусто)», not a crash
        code += digit * stride
    return code, bstride


def run(
    model: Model, core: Core, r: Restrictor, *, chunk: int = CHUNK, tick: Tick = _noop, sums: Sums | None = None
) -> Sums:
    """The pass over `model`: the accumulators of `core` for the restrictor `r` (module docstring). `sums` — continue
    into given accumulators (the same `core`)."""
    core.check()
    out = sums if sums is not None else new_sums(core)
    bench_i = core.bench_key()
    bench = None if bench_i is None else core.keys[bench_i].bench
    nbg = len(core.group_edges) + 2
    grades_n = len(core.grades)
    for ch, f in r.iter(model, extra=core.reads(), chunk=chunk):
        tick(0.05 + 0.9 * (ch.start + ch.n) / max(model.rows, 1), None)
        cols, n = ch.cols, ch.n
        vol = geometry.row_volumes(model.grid, ch.p, ch.s, ch.w, cols[core.frac] if core.frac else None)
        ok = f > 0.0
        out.excluded["filtered"] += n - int(np.count_nonzero(ok))
        keep = ok & ~np.isnan(vol)
        out.excluded["no_volume"] += int(np.count_nonzero(ok & ~keep))
        veff = np.where(keep, vol * f, 0.0)
        dens = cols[core.density_field] if core.density_field else np.full(n, core.density_value, dtype=np.float64)
        has = np.isfinite(dens) & (dens >= 0.0)
        lost = keep & ~has  # no density: the tonnage is unknown, not 0
        out.excluded["no_density"] += int(np.count_nonzero(lost))
        rk.masked_sum(out.no_density_acc, lost, veff)
        keep &= has
        tonnes = np.where(keep, veff * np.where(keep, dens, 0.0), 0.0)
        cut = cols[core.cutoff_field] if core.cutoff_field else None
        bin_t = np.where(keep, rk.bins_of(core.edges, cut, n), -1).astype(np.int32)
        bin_g = rk.bins_of(core.group_edges, cut, n)
        gcode, bstride = group_codes(core, cols, n)
        grades = (np.ascontiguousarray(np.stack([cols[x] for x in core.grades]), dtype=np.float64)
                  if grades_n else np.zeros((0, n)))  # fmt: skip
        if bench is None:
            rk.accumulate(bin_t, bin_g, gcode, veff, tonnes, grades, out.s0, out.c0, out.n0, out.sg, out.cg, out.ng,
                          nbg)  # fmt: skip
        else:
            zc = geometry.centres(model.grid, ch.p, ch.s, ch.w)[:, 2]
            dz = geometry.row_widths(model.grid, ch.p, ch.s, ch.w)[:, 2]
            rk.accumulate_bench(bin_t, bin_g, gcode, bstride, bench.count, bench.first, bench.height, zc - dz * 0.5,
                                zc + dz * 0.5, veff, tonnes, grades, out.s0, out.c0, out.n0, out.sg, out.cg, out.ng,
                                nbg)  # fmt: skip
    tick(0.97, None)
    return out


def code_of(core: Core, digits: Sequence[int]) -> int:
    """The code of a group from the digit of each key (0 — «(пусто)»; the bench j — j + 1; a label — its index + 1)."""
    return sum(int(d) * s for d, s in zip(digits, core.strides(), strict=True))


def group_label(core: Core, code: int) -> list[str | None]:
    """The labels of the digits of a group code, one per key (None — «(пусто)»); a bench — «Уступ j» by its number
    (the signs of the elevation — `result.py`, 20.3)."""
    out: list[str | None] = []
    for key, stride in zip(core.keys, core.strides(), strict=True):
        d = (code // stride) % key.radix
        if key.bench is not None:
            out.append(None if d == 0 else str(d - 1))
        else:
            out.append(None if d == 0 else str(key.labels[d - 1]))
    return out


def memory_mb(_params: Any = None) -> int:
    """A portion of 2²⁰ rows: the columns read, the volumes, the weights, the bins and the codes."""
    return 640
