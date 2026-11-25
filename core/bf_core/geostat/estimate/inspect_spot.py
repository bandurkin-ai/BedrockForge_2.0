"""The block of the inspector (ТЗ 7.35 Д-30; Э18, 18.Д3; e18-api.md §7): which row of the block model the user
pointed at and whether a row of the estimation plan estimates it. Reads the model through the windows of
`blocks/pick.py` (one row group of `cells`, cached) — never the whole model.

    spot = locate(model, {"ijk": [i, j, k]} | {"xyz": [x, y, z]} | {"row": r})    # Spot; 422 `bad_param` in Russian
    d = domain_of(model, spec, restrictor, spot, fields=names)    # Domain: is the block in the domain of the row,
                                                                   # the stored values of `fields` at its row

Reading. A click costs the decoding of the row group of every file it needs (1 048 576 rows a column): the groups of
`cells` come from the windows of `pick` (cached), the fields — from `read_fields`, which keeps the decoded columns of
the last row groups (`COLUMNS_BYTES`; a file never changes in place — a new file is a new SHA-256), so the next click
in the same row group costs nothing.

`Spot` is a row of the file with its parent: `centre` / `size` — the row's (a sub-block), `parent_centre` /
`parent_size` — the parent's; `[first, stop)` — the rows of the parent (ascending P). A row of the plan with `sub:
parent` estimates the parent (the result goes to every row of it, Д-27), with `sub: own` — the row.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry, pick
from bf_core.blocks import schema as bs
from bf_core.blocks.model import Chunk, Model
from bf_core.blocks.model import P as COL_P
from bf_core.blocks.model import S as COL_S
from bf_core.blocks.model import W as COL_W


def _bad(param: str, message: str, hint: str = "Щёлкните блок в 3D.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- the row groups of the files of a model ---------------------------------------------------------------------------

COLUMNS_BYTES = 256 << 20  # the decoded columns kept (8 MB a column of a full row group)
_COLUMNS: OrderedDict[tuple[str, str, int], np.ndarray] = OrderedDict()
_BYTES = [0]
_SIZES: dict[str, list[int]] = {}
_LOCK = threading.Lock()


def forget() -> None:
    """Empties what is kept (tests, a project that is closed)."""
    with _LOCK:
        _COLUMNS.clear()
        _SIZES.clear()
        _BYTES[0] = 0


def group_span(model: Model, row: int) -> tuple[int, int]:
    """The rows [start, stop) of the row group of `cells` that holds `row` (the other files have the same groups)."""
    key = model.shas().get(bs.ROLE, "")
    with _LOCK:
        sizes = _SIZES.get(key)
    if sizes is None:
        sizes = model.reader.group_sizes()
        with _LOCK:
            _SIZES[key] = sizes
            while len(_SIZES) > 8:
                _SIZES.pop(next(iter(_SIZES)))
    start = 0
    for n in sizes:
        if row < start + n:
            return start, start + n
        start += n
    return start, start


def _file_of(model: Model, name: str) -> str:
    return str(model.reader.specs[name].get("file") or bs.ROLE)


def read_fields(model: Model, names: Sequence[str], a: int, b: int) -> dict[str, np.ndarray]:
    """The fields `names` (stored names) of the rows [a, b) in the memory form. A range inside one row group is read
    through the cache of the decoded columns of the group; a range across groups — straight."""
    gs, ge = group_span(model, a)
    if not names:
        return {}
    if b > ge:
        got = model.reader.fill(names, start=a, stop=b)
        return {k: v for k, v in got.items() if not k.startswith("\0")}
    sha = {n: model.shas().get(_file_of(model, n), "") for n in names}
    have: dict[str, np.ndarray] = {}
    with _LOCK:
        for n in names:
            col = _COLUMNS.get((sha[n], n, gs))
            if col is not None and sha[n]:
                _COLUMNS.move_to_end((sha[n], n, gs))
                have[n] = col
    miss = [n for n in names if n not in have]
    if miss:
        got = model.reader.fill(miss, start=gs, stop=ge)
        with _LOCK:
            for n in miss:
                have[n] = got[n]
                if sha[n]:
                    _COLUMNS[(sha[n], n, gs)] = got[n]
                    _BYTES[0] += int(got[n].nbytes)
            while _BYTES[0] > COLUMNS_BYTES and len(_COLUMNS) > len(miss):
                _, old = _COLUMNS.popitem(last=False)
                _BYTES[0] -= int(old.nbytes)
    return {n: have[n][a - gs : b - gs].copy() for n in names}


def read_cells(model: Model, a: int, b: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """P, s, w of the rows [a, b) (the window of `pick` of the row group, cached; a range across groups — straight)."""
    gs, ge = group_span(model, a)
    if b > ge:
        got = model.reader.fill(p=True, sub=True, start=a, stop=b)
        return got[COL_P], got.get(COL_S), got.get(COL_W)
    p, s, w = pick._rows_of(model, gs, ge)  # noqa: SLF001
    return p[a - gs : b - gs], None if s is None else s[a - gs : b - gs], None if w is None else w[a - gs : b - gs]


@dataclass(frozen=True)
class Spot:
    """The row under the pointer and its parent (module docstring)."""

    row: int
    p: int
    ijk: tuple[int, int, int]
    sub: tuple[int, ...] | None  # (sx, sy, sz, wx, wy, wz) of a model with sub-blocks
    centre: np.ndarray  # f8 [3] project axes
    size: np.ndarray  # f8 [3] along the axes of the model
    parent_centre: np.ndarray
    parent_size: np.ndarray
    first: int  # the rows of the parent: [first, stop)
    stop: int

    @property
    def rows(self) -> int:
        return self.stop - self.first


def parent_rows(model: Model, p: int) -> tuple[int, int] | None:
    """The rows [first, stop) of the file whose P is `p` (ascending P); None — the model has none."""
    win = pick._window(model, int(p))  # noqa: SLF001
    if win is None:
        return None
    pa, _, _ = pick._rows_of(model, *win)  # noqa: SLF001
    lo = int(np.searchsorted(pa, np.uint64(p), side="left"))
    hi = int(np.searchsorted(pa, np.uint64(p), side="right"))
    return None if hi <= lo else (win[0] + lo, win[0] + hi)


def _spot(model: Model, row: int, p: int, s: np.ndarray | None, w: np.ndarray | None,
          span: tuple[int, int]) -> Spot:  # fmt: skip
    grid = model.grid
    p1 = np.array([p], dtype=np.uint64)
    s1 = None if s is None else np.asarray(s, dtype=np.uint8).reshape(1, 3)
    w1 = None if w is None else np.asarray(w, dtype=np.uint8).reshape(1, 3)
    i, j, k = (int(a[0]) for a in geometry.row_ijk(grid, p1))
    sub = None if s1 is None or w1 is None else tuple(int(x) for x in (*s1[0], *w1[0]))
    return Spot(row, p, (i, j, k), sub, geometry.centres(grid, p1, s1, w1)[0], geometry.row_widths(grid, p1, s1, w1)[0],
                geometry.centres(grid, p1)[0], geometry.row_widths(grid, p1)[0], span[0], span[1])  # fmt: skip


def locate(model: Model, at: Mapping[str, Any]) -> Spot:
    """The row of `at` (`spec.parse_inspect`: {row} | {ijk} | {xyz}). {ijk} — the parent: the first row of it (a model
    with sub-blocks); {xyz} — the row that holds the point. 422 `bad_param {param: row | block | xyz}`."""
    grid = model.grid
    if "row" in at:
        row = int(at["row"])
        if row >= model.rows:
            raise _bad("row", f"В модели нет блока с номером строки {row}.",
                       f"Номер строки — от 0 до {model.rows - 1}.")  # fmt: skip
        pp, ss, ww = read_cells(model, row, row + 1)
        p = int(pp[0])
        return _spot(model, row, p, ss, ww, parent_rows(model, p) or (row, row + 1))
    if "ijk" in at:
        i, j, k = (int(v) for v in at["ijk"])
        for a, v in enumerate((i, j, k)):
            if v >= grid.counts[a]:
                raise _bad(f"block.{a}", f"Индекс {v} выходит за сетку модели (до {grid.counts[a] - 1}).")
        p = int(geometry.parent_p(grid, np.array([i]), np.array([j]), np.array([k]))[0])
        found = parent_rows(model, p)
        win = pick._window(model, p)  # noqa: SLF001
        if found is None or win is None:
            raise _bad("block", "В модели нет блока с такими индексами.", "Щёлкните блок, который есть в модели.")
        _, sa, wa = pick._rows_of(model, *win)  # noqa: SLF001
        at0 = found[0] - win[0]
        return _spot(model, found[0], p, None if sa is None else sa[at0], None if wa is None else wa[at0], found)
    x, y, z = (float(v) for v in at["xyz"])
    hit = pick.find_in_model(model, (x, y, z))
    if hit is None:
        raise _bad("xyz", "Точка не попадает ни в один блок модели.", "Щёлкните блок, который есть в модели.")
    s = None if hit.sub is None else np.array(hit.sub[:3], dtype=np.uint8)
    w = None if hit.sub is None else np.array(hit.sub[3:], dtype=np.uint8)
    span = parent_rows(model, hit.p) or (hit.row, hit.row + 1)
    return _spot(model, hit.row, hit.p, s, w, span)


# --- the domain of a row of the plan ---------------------------------------------------------------------------------


def domain_mask(model: Model, target: Any, col: np.ndarray) -> np.ndarray:
    """bool [n]: the rows of the values `col` of the domain field in the domain `field = label` (the same rule as
    `kna_blocks._domain`: a category by its label, a number by its value; NaN is never equal)."""
    field = model.field(target.domain_field)
    label = str(target.label)
    if field.get("kind") == bs.CATEGORY:
        labels = model.labels(str(field["name"]))
        return np.asarray(col == labels.index(label)) if label in labels else np.zeros(col.shape[0], dtype=bool)
    try:
        value = float(label.replace(",", "."))
    except ValueError:
        return np.zeros(col.shape[0], dtype=bool)
    return np.asarray(col == value)


@dataclass(frozen=True)
class Domain:
    """What the rows of the block say: `active` — the row of the plan estimates the block (in the domain and passes the
    restrictor; a parent — when any of its rows does), `label` — the domain field of the row under the pointer (None —
    the row has no domain field or the value is empty), `cols` — the stored values at the row `spot.row`."""

    active: bool
    label: str | None
    cols: dict[str, float]


def domain_of(model: Model, spec: Any, restrictor: Any, spot: Spot, fields: Sequence[str] = ()) -> Domain:
    """The domain of the block for the row `spec` and the stored `fields` at the row of the spot, in one reading of
    the rows of the block (`[first, stop)` of a parent, the row itself for `own`)."""
    parent = spec.sub == "parent"
    a, b = (spot.first, spot.stop) if parent else (spot.row, spot.row + 1)
    names = list(dict.fromkeys([*([spec.target.domain_field] if spec.target.domain_field else []), *restrictor.fields,
                                *fields]))  # fmt: skip
    keep = read_fields(model, model.reader.canonical(names), a, b)
    pp, ss, ww = read_cells(model, a, b)
    mask = np.ones(b - a, dtype=bool)
    label = None
    if spec.target.domain_field:
        field = model.field(spec.target.domain_field)
        col = np.asarray(keep[str(field["name"])])
        mask = domain_mask(model, spec.target, col)
        value = pick.value_of(field, col[spot.row - a])
        label = None if value is None else (value if isinstance(value, str) else f"{value:g}")
    chunk = Chunk(a, pp, ss, ww, keep)
    mask = mask & np.asarray(restrictor.mask(chunk))
    active = bool(mask.any()) if parent else bool(mask[0])
    at = spot.row - a
    cols = {k: float(np.asarray(v)[at]) for k, v in keep.items() if k in {str(f) for f in fields}}
    return Domain(active, label, cols)
