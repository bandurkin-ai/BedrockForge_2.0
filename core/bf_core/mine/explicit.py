"""The mode «explicit arcs» of the pit optimiser: MineLib UPIT sets (`<name>.upit` + `<name>.prec`) → the CSR of the
predecessors, solved by HPF (`hpf.solve_arcs`) or by OR-Tools (`flow_ref.solve_arcs`) (подшаг 21.13; ТЗ 8.11,
Т8.11-4; ADR 0028 decisions 14 and 32, findings 7; question № 114). The mode is NOT shown in the interface: it is for
the tests, the measures and the `solver`/`graph` parameters of the core.

THE FORMAT (Espinoza, Goycoolea, Moreno, Newman 2013, «MineLib»). The sites of MineLib are not reachable from the
cloud and the files are not in the repository, so the parser follows the description of the format and is TOLERANT
where the description leaves room; what it assumes is listed here (and is the thing to check on the first real file):

* `<name>.upit` — a header of `KEY: value` lines (`NAME`, `TYPE: UPIT`, `NBLOCKS`; other keys are ignored), the line
  `OBJECTIVE_FUNCTION:`, then one line `id value` per block, and the optional last line `EOF`. The marker is not
  required: the first line that is not a `KEY: value` starts the table. A line has exactly two tokens. `value` is the
  gain of the block (positive — a profit, negative — a cost; the objective is the SUM of the values of the extracted
  blocks); it may be an integer or a decimal (up to 9 digits after the point: the values are scaled to int64 by
  `10^d`, the smallest `d` that makes every value whole, so the objective is exact: `Σ v′ / 10^d`). The text is read
  as a DECIMAL STRING (`decimal`), never through a float: 15+ significant digits stay exact, a 10th digit after the
  point is an error and not a rounding.
* `<name>.prec` — one line `id n p₁ … pₙ` per block: the block `id` may be extracted only after all of `p₁ … pₙ`
  (its PREDECESSORS: the arcs u → pⱼ of the closure problem, as in `graph.predecessors`); `n = 0` — no predecessors.
  A block with no line has none. An optional last line `EOF`; `KEY: value` lines are ignored.
* Ids are 0-based and are all of 0 … NBLOCKS − 1 (a 1-based file — 1 … NBLOCKS — is read too, the `.prec` is then
  1-based as well); the lines may come in any order. NBLOCKS, if given, must agree with the number of lines.
* Duplicates in the predecessors are kept as they are (harmless); a block that is its own predecessor is dropped.

Errors are `ApiError` 422 `minelib_format` (a message in Russian with the line and a hint); a missing file is 404
`minelib_no_file`. The reading is by portions of lines (a set of 2 million blocks and tens of millions of arcs reads
without a list of all the numbers).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

LINES = 500_000  # lines of `.prec` per portion
MAX_DECIMALS = 9
MAX_ABS_SUM = 2**62  # Σ|v′| must fit the int64 of the solvers

Tick = Callable[[float], None]
Mask = NDArray[np.bool_]
Solver = Literal["hpf", "ortools"]


def _format(path: Path, what: str, line: int | None = None) -> ApiError:
    where = f", строка {line}" if line is not None else ""
    return ApiError(
        422,
        "minelib_format",
        f"Файл MineLib «{path.name}» не разобран{where}: {what}.",
        "Проверьте, что это файл набора MineLib UPIT (.upit — «id значение», .prec — «id n предшественники»).",
        {"file": path.name, "line": line},
    )


def _open_lines(path: Path) -> Any:
    try:
        return open(path, encoding="utf-8", errors="replace")  # noqa: SIM115 — closed by the callers
    except FileNotFoundError:
        raise ApiError(
            404,
            "minelib_no_file",
            f"Файл набора MineLib «{path.name}» не найден.",
            "Положите файлы <имя>.upit и <имя>.prec в одну папку и повторите.",
            {"file": path.name},
        ) from None


@dataclass(frozen=True)
class ExplicitProblem:
    """A closure problem with explicit arcs: `value` (int64 v′ = value·`scale`), the CSR of the predecessors."""

    name: str
    value: NDArray[np.int64]
    scale: int
    indptr: NDArray[np.int64]
    indices: NDArray[np.int32]

    @property
    def n(self) -> int:
        return int(self.value.shape[0])

    @property
    def arcs(self) -> int:
        return int(self.indices.shape[0])

    def objective(self, mined: Mask) -> float:
        """Σ of the values of the blocks of `mined` (exact in int64, divided by the scale at the very end)."""
        return objective_int(self.value, mined) / self.scale


@dataclass(frozen=True)
class Solution:
    mined: Mask
    objective_int: int  # Σ v′ over the pit (the integer of the solver)
    objective: float  # the same in the units of the file
    stats: dict[str, Any]


def objective_int(value: NDArray[np.int64], mined: Mask) -> int:
    return int(np.asarray(value, dtype=np.int64)[np.asarray(mined, dtype=bool)].sum())


# --- `.upit` ---------------------------------------------------------------------------------------------------------


def _header_line(line: str) -> tuple[str, str] | None:
    """('KEY', 'value') of a `KEY: value` line, None for anything else."""
    if ":" not in line:
        return None
    key, _, val = line.partition(":")
    key = key.strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        return None
    return key.upper(), val.strip()


def _mantissa(token: str) -> tuple[int, int] | None:
    """(m, e) with the value of `token` = m·10^e EXACTLY (m has no trailing zeros; zero is (0, 0)); None — not a finite
    number, or one with more than 19 digits before the point (it cannot fit the int64 of the solver anyway)."""
    try:
        d = Decimal(token)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    sign, digits, exp = d.as_tuple()
    assert isinstance(exp, int)  # finite
    m = int("".join(map(str, digits))) if digits else 0
    if m == 0:
        return 0, 0
    while m % 10 == 0:
        m //= 10
        exp += 1
    if len(str(m)) + max(exp, 0) > 19:
        return None
    return (-m if sign else m), exp


def _scaled(path: Path, tokens: list[str], first_line: int) -> tuple[NDArray[np.int64], int]:
    """(int64 values, the scale 10^d): the smallest d ≤ 9 with every value whole. The tokens are read as DECIMAL
    strings, never through a float: a value of 15 or more significant digits stays exact, and a digit beyond the 9th
    after the point is an error, not a rounding (no absolute tolerance)."""
    try:
        whole = np.array(tokens, dtype=np.int64)  # the usual case: integers — exact and fast
    except (ValueError, OverflowError):
        whole = None
    if whole is not None:
        if int(np.abs(whole).astype(object).sum()) >= MAX_ABS_SUM:
            raise _format(path, "сумма модулей значений не помещается в 64 бита")
        return whole, 1
    parts: list[tuple[int, int]] = []
    for t in tokens:
        mantissa = _mantissa(t)
        if mantissa is None:
            raise _format(path, "значение блока не число или не помещается в 64 бита", first_line)
        parts.append(mantissa)
    d = max((-e for _, e in parts if e < 0), default=0)
    if d > MAX_DECIMALS:
        raise _format(path, f"значения блоков не приводятся к целым с точностью до {MAX_DECIMALS} знаков")
    ints = [m * 10 ** (e + d) for m, e in parts]
    if sum(abs(x) for x in ints) >= MAX_ABS_SUM:
        raise _format(path, "сумма модулей значений не помещается в 64 бита")
    return np.array(ints, dtype=np.int64), 10**d


def read_upit(path: str | Path) -> tuple[str, NDArray[np.int64], int, int]:
    """(name, value int64 by block id, scale, base): `base` is 0 or 1 — the first id of the file."""
    p = Path(path)
    name = p.stem
    declared: int | None = None
    ids: list[str] = []
    vals: list[str] = []
    in_table = False
    with _open_lines(p) as fh:
        for no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            if line.upper() == "EOF":
                break
            if not in_table:
                kv = _header_line(line)
                if kv is not None:
                    key, val = kv
                    if key == "NAME" and val:
                        name = val
                    elif key == "NBLOCKS":
                        try:
                            declared = int(val)
                        except ValueError:
                            raise _format(p, "NBLOCKS не целое число", no) from None
                    elif key == "OBJECTIVE_FUNCTION":
                        in_table = True
                    continue
                in_table = True
            parts = line.split()
            if len(parts) != 2:
                raise _format(p, "ожидалась строка «id значение»", no)
            ids.append(parts[0])
            vals.append(parts[1])
    n = len(ids)
    if n == 0:
        raise _format(p, "в файле нет блоков")
    if declared is not None and declared != n:
        raise _format(p, f"NBLOCKS = {declared}, а строк с блоками {n}")
    try:
        idx = np.array(ids, dtype=np.int64)
    except ValueError:
        raise _format(p, "номер блока не целое число") from None
    base = int(idx.min())
    if base not in (0, 1):
        raise _format(p, f"номера блоков начинаются с {base}, ожидалось 0 или 1")
    pos = idx - base
    if int(pos.max()) >= n or len(np.unique(pos)) != n:
        raise _format(p, f"номера блоков должны быть всеми числами от {base} до {base + n - 1} без повторов")
    v, scale = _scaled(p, vals, 1)
    out = np.empty(n, dtype=np.int64)
    out[pos] = v
    return name, out, scale, base


# --- `.prec` ---------------------------------------------------------------------------------------------------------


def _portion(p: Path, rows: list[list[str]], first: int, n: int, base: int, seen: Mask) -> tuple[Any, ...]:
    """(ids, counts, predecessors) of the lines `rows` (the first of them is the line `first` of the file)."""
    lens = np.fromiter(map(len, rows), dtype=np.int64, count=len(rows))
    if bool((lens < 2).any()):
        raise _format(p, "ожидалась строка «id n предшественники»", first + int(np.argmax(lens < 2)))
    try:
        flat = np.array(list(itertools.chain.from_iterable(rows)), dtype=np.int64)
    except ValueError:
        raise _format(p, "в строке не целое число", first) from None
    starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
    ids = flat[starts] - base
    cnt = flat[starts + 1]
    wrong = (cnt < 0) | (cnt + 2 != lens)
    if bool(wrong.any()):
        raise _format(p, "число предшественников не совпадает с числом номеров в строке", first + int(np.argmax(wrong)))
    out = (ids < 0) | (ids >= n)
    if bool(out.any()):
        raise _format(p, "номер блока вне набора", first + int(np.argmax(out)))
    if bool(seen[ids].any()) or len(np.unique(ids)) != len(ids):
        raise _format(p, "блок описан дважды", first)
    seen[ids] = True
    mask = np.ones(flat.shape[0], dtype=bool)
    mask[starts] = False
    mask[starts + 1] = False
    preds = flat[mask] - base
    if preds.size and (int(preds.min()) < 0 or int(preds.max()) >= n):
        raise _format(p, "предшественник вне набора", first)
    own = preds == np.repeat(ids, cnt)  # a block is not its own predecessor
    if bool(own.any()):
        cnt = cnt - np.bincount(np.repeat(np.arange(len(ids)), cnt)[own], minlength=len(ids))
        preds = preds[~own]
    return ids, cnt, preds.astype(np.int32)


def read_prec(path: str | Path, n: int, base: int = 0) -> tuple[NDArray[np.int64], NDArray[np.int32]]:
    """(indptr int64 (n + 1), indices int32): the CSR of the predecessors by block id (`base` — the first id)."""
    p = Path(path)
    if n >= 2**31 - 1:
        raise _format(p, "блоков больше, чем помещается в 32 бита")
    deg = np.zeros(n, dtype=np.int64)
    seen = np.zeros(n, dtype=bool)
    parts: list[tuple[Any, ...]] = []
    line_no = 0
    with _open_lines(p) as fh:
        done = False
        while not done:
            rows: list[list[str]] = []
            first = line_no + 1
            read = 0
            for raw in itertools.islice(fh, LINES):
                read += 1
                line_no += 1
                s = raw.strip()
                if not s or (":" in s and _header_line(s) is not None):
                    continue
                if s.upper() == "EOF":
                    done = True
                    break
                rows.append(s.split())
            done = done or read < LINES
            if rows:
                part = _portion(p, rows, first, n, base, seen)
                deg[part[0]] = part[1]
                parts.append(part)
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(deg, out=indptr[1:])
    if not parts:
        return indptr, np.zeros(0, dtype=np.int32)
    ids = np.concatenate([q[0] for q in parts])
    cnt = np.concatenate([q[1] for q in parts])
    flat = np.concatenate([q[2] for q in parts])
    if len(ids) < 2 or bool((np.diff(ids) > 0).all()):
        return indptr, flat  # in increasing id: the records already lie in the order of the CSR (a skipped id has 0)
    src = np.concatenate([[0], np.cumsum(cnt)[:-1]])  # any order: every record goes to its slot of the CSR
    dst = np.repeat(indptr[ids] - src, cnt) + np.arange(flat.shape[0], dtype=np.int64)
    out = np.empty(flat.shape[0], dtype=np.int32)
    out[dst] = flat
    return indptr, out


# --- the set ---------------------------------------------------------------------------------------------------------


def set_paths(directory: str | Path, name: str) -> tuple[Path, Path]:
    d = Path(directory)
    return d / f"{name}.upit", d / f"{name}.prec"


def read_set(upit: str | Path, prec: str | Path | None = None) -> ExplicitProblem:
    """A MineLib UPIT set; `prec` defaults to the `.prec` beside the `.upit`."""
    up = Path(upit)
    pr = Path(prec) if prec is not None else up.with_suffix(".prec")
    name, value, scale, base = read_upit(up)
    indptr, indices = read_prec(pr, len(value), base)
    return ExplicitProblem(name, value, scale, indptr, indices)


def violations(problem: ExplicitProblem, mined: Mask) -> int:
    """The arcs u → q with u mined and q not (0 — a closure). Independent of both solvers."""
    deg = np.diff(problem.indptr)
    tails = np.repeat(np.arange(problem.n, dtype=np.int64), deg)
    m = np.asarray(mined, dtype=bool)
    return int(np.count_nonzero(m[tails] & ~m[problem.indices]))


def solve(
    problem: ExplicitProblem,
    solver: Solver = "hpf",
    *,
    portion: int | None = None,
    tick: Tick | None = None,
    max_bytes: int | None = None,
) -> Solution:
    """The smallest optimal closure of the set. `hpf` works by portions of `portion` units (None — one call) and
    calls `tick(fraction)` between them (it may raise to cancel); `ortools` is the reference (no tick)."""
    stats: dict[str, Any] = {}
    if solver == "hpf":
        from bf_core.mine import hpf

        kw: dict[str, Any] = {"tick": tick, "max_bytes": max_bytes, "stats": stats}
        if portion is not None:
            kw["portion"] = portion
        mined = hpf.solve_arcs(problem.value, problem.indptr, problem.indices, **kw)
    elif solver == "ortools":
        from bf_core.mine import flow_ref

        mined = flow_ref.solve_arcs(problem.value, problem.indptr, problem.indices)
    else:
        raise ValueError(f"unknown solver: {solver}")
    total = objective_int(problem.value, mined)
    return Solution(mined, total, total / problem.scale, stats)
