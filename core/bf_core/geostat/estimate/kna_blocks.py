"""The blocks of KNA (ТЗ 8.8.5; e18-api.md §8.2; Э18, 18.15): which blocks of a row of the plan are candidates and where
the blocks the user pointed at are. Reads the block model; the analysis itself is `kna.py`.

    bl = Blocks.open(conn, store, spec, cancel=…)       # the model, the domain of the row and its restrictor
    t = bl.targets(limit=TARGET_LIMIT)                   # the blocks the row estimates: Targets (rows, centres, sizes)
    b = bl.place({"row": r} | {"ijk": [i, j, k]} | {"xyz": [x, y, z]})      # BlockAt; 404 / 422 in Russian

A block of the row is a parent cell when `sub: parent` (the centre and the size of the parent, the result goes to
every row of it, Д-27) and a row of the model when `sub: own`. It belongs to the row when its row (any row of the
parent) is in the domain `target.domain_field = target.label` and passes the restrictor (a stored filter or rules, the
share > 0). More than `TARGET_LIMIT` blocks are cut to that many evenly along the file (the same cut every time) —
the quantiles of the auto blocks are taken over them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.model import Model
from bf_core.storage.blobs import BlobStore

TARGET_LIMIT = 200_000
Cancel = Callable[[], None]


def _bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


@dataclass(frozen=True)
class BlockAt:
    """One block under analysis: the row of the model that stands for it, its parent P and (i, j, k), the centre
    (project axes) and the size (along the axes of the model), whether the row estimates it (`active`; no — STATUS 1),
    the label of the auto pick (`много`, `средне`, `мало`) and the number of samples inside the first radius."""

    row: int
    p: int
    ijk: tuple[int, int, int]
    centre: np.ndarray
    size: np.ndarray
    active: bool = True
    auto: str | None = None
    n_r1: int | None = None

    def at(self) -> dict[str, Any]:
        return {"row": self.row, "p": self.p, "ijk": list(self.ijk)}


@dataclass(frozen=True)
class Targets:
    """The blocks of the row, in the order of the file: the stand-in row, P, (i, j, k), centres, sizes; `total` is
    their number before the cut to `TARGET_LIMIT`."""

    row: np.ndarray  # i8 [m]
    p: np.ndarray  # u8 [m]
    ijk: np.ndarray  # i8 [m, 3]
    centre: np.ndarray  # f8 [m, 3]
    size: np.ndarray  # f8 [m, 3]
    total: int

    @property
    def n(self) -> int:
        return int(self.row.shape[0])

    def block(self, i: int, **kw: Any) -> BlockAt:
        return BlockAt(int(self.row[i]), int(self.p[i]), (int(self.ijk[i, 0]), int(self.ijk[i, 1]),
                                                           int(self.ijk[i, 2])), self.centre[i].copy(),
                       self.size[i].copy(), True, **kw)  # fmt: skip


def _domain(model: Model, target: Any) -> np.ndarray:
    """bool [rows]: the rows of the domain `field = label` (every row without a domain field)."""
    mask = np.ones(model.rows, dtype=bool)
    if target.domain_field is None:
        return mask
    field = model.field(target.domain_field)  # 404 `block_field_not_found`
    col = np.asarray(model.columns([str(field["name"])])[str(field["name"])])
    label = str(target.label)
    if field.get("kind") == "category":
        labels = model.labels(str(field["name"]))
        return np.asarray(col == labels.index(label)) if label in labels else np.zeros(model.rows, dtype=bool)
    try:
        value = float(label.replace(",", "."))
    except ValueError:
        return np.zeros(model.rows, dtype=bool)
    return np.asarray(col == value)  # NaN is never equal


class Blocks:
    """The block model of a row with the mask of its blocks (module docstring)."""

    def __init__(self, model: Model, mask: np.ndarray, parent: bool) -> None:
        self.model = model
        self.mask = mask
        self.parent = parent
        self.p = model.p()
        sw = model.subs()
        self.s: np.ndarray | None = None if sw is None else sw[0]
        self.w: np.ndarray | None = None if sw is None else sw[1]

    @classmethod
    def open(cls, conn: sa.Connection, store: BlobStore, spec: Any, *, cancel: Cancel | None = None) -> Blocks:
        from bf_core.blocks import restrict
        from bf_core.blocks.filters import store as fst

        model = Model.open(conn, store, spec.model_id)  # 404 when the model is gone
        mask = _domain(model, spec.target)
        rules = spec.target.rules
        if spec.target.filter_id is not None:
            rules = fst.rules_of(conn, spec.target.filter_id)
        if rules is not None:
            mask &= restrict.evaluate(model, rules, conn=conn, store=store, cancel=cancel) > 0.0
        return cls(model, mask, spec.sub == "parent")

    @property
    def rotation(self) -> float:
        return float(self.model.grid.rotation)

    # --- the geometry of a block ----------------------------------------------------------------------------

    def _geometry(self, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The centres and the sizes of the blocks the rows stand for: the parent (`sub: parent`) or the row."""
        grid = self.model.grid
        p = self.p[rows]
        if self.parent:
            return geometry.centres(grid, p), geometry.row_widths(grid, p)
        s = None if self.s is None else self.s[rows]
        w = None if self.w is None else self.w[rows]
        return geometry.centres(grid, p, s, w), geometry.row_widths(grid, p, s, w)

    def _block(self, row: int, **kw: Any) -> BlockAt:
        rows = np.array([row], dtype=np.intp)
        centre, size = self._geometry(rows)
        i, j, k = geometry.row_ijk(self.model.grid, self.p[rows])
        active = bool(self.mask[row])
        if self.parent:
            active = bool(self.mask[self.p == self.p[row]].any())
        return BlockAt(int(row), int(self.p[row]), (int(i[0]), int(j[0]), int(k[0])), centre[0], size[0], active,
                       **kw)  # fmt: skip

    # --- the candidates --------------------------------------------------------------------------------------

    def targets(self, limit: int = TARGET_LIMIT) -> Targets:
        """The blocks of the row (module docstring). 422 `kna_no_blocks` — the domain has none."""
        rows = np.flatnonzero(self.mask)
        if self.parent and rows.size:
            _, first = np.unique(self.p[rows], return_index=True)
            rows = rows[np.sort(first)]  # the first row of every parent, in the order of the file
        total = int(rows.size)
        if total == 0:
            raise ApiError(422, "kna_no_blocks", "В домене оценки нет блоков.",
                           "Проверьте домен и ограничитель в параметрах оценки.", {"param": "target"})  # fmt: skip
        if total > limit:
            rows = rows[np.unique(np.linspace(0, total - 1, limit).astype(np.int64))]
        centre, size = self._geometry(rows)
        i, j, k = geometry.row_ijk(self.model.grid, self.p[rows])
        return Targets(rows.astype(np.int64), self.p[rows], np.stack([i, j, k], axis=1).astype(np.int64), centre,
                       size, total)  # fmt: skip

    # --- a block the user pointed at -------------------------------------------------------------------------

    def place(self, at: dict[str, Any], param: str = "blocks") -> BlockAt:
        """The block of `at` — `spec.parse_at`: {row} | {ijk} | {xyz}. 422 `kna_block_missing` — no such block."""
        n = self.model.rows
        if "row" in at:
            row = int(at["row"])
            if row >= n:
                raise _bad(f"{param}.row", f"В модели нет блока с номером строки {row}.",
                           f"Номер строки — от 0 до {n - 1}.")  # fmt: skip
            return self._block(row)
        if "ijk" in at:
            i, j, k = (int(v) for v in at["ijk"])
            grid = self.model.grid
            for a, v in enumerate((i, j, k)):
                if v >= grid.counts[a]:
                    raise _bad(f"{param}.ijk.{a}", f"Индекс {v} выходит за сетку модели (до {grid.counts[a] - 1}).",
                               "Щёлкните блок в 3D.")  # fmt: skip
            target = int(geometry.parent_p(grid, np.array([i]), np.array([j]), np.array([k]))[0])
            rows = np.flatnonzero(self.p == np.uint64(target))
            if rows.size == 0:
                raise _bad(f"{param}.ijk", "В модели нет блока с такими индексами.", "Щёлкните блок в 3D.",
                           "kna_block_missing")  # fmt: skip
            inside = rows[self.mask[rows]]
            return self._block(int((inside if inside.size else rows)[0]))
        x, y, z = (float(v) for v in at["xyz"])
        hit = geometry.locate_rows(self.p, self.s, self.w, self.model.grid, np.array([[x, y, z]]))
        if hit[0] < 0:
            raise _bad(f"{param}.xyz", "Точка не попадает ни в один блок модели.", "Щёлкните блок в 3D.",
                       "kna_block_missing")  # fmt: skip
        return self._block(int(hit[0]))
