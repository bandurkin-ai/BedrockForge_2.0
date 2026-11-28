"""A block model that lies in no grid, kept as a list of boxes (ADR 0030 decisions 8–12, Д-45 А6): meta v3 with
`layout: "list"`, `kind = "block_model"`, so the tree, the folder, the filters, `.bfggis`, the data manager take it as
any other model.

The file `cells`: `idx` u64 — the cell of the ENCLOSING grid that holds the centre of the block (the same P =
i + nx·(j + ny·k), the same row groups of 2²⁰ rows), `cx cy cz` f8 — the centre and `dx dy dz` f8 — the size, local axes
of the model (the project point of a centre is `grid.to_project`), then the fields. Rows go by (idx, cz, cy, cx), a
tie keeps the order of the file. The enclosing grid has `div` [1, 1, 1], `sub` false, a cell not smaller than the
biggest block (`size` of the wizard, else the biggest block; larger when the cell count would pass 2·10⁹), so two
overlapping blocks always have their centres in neighbouring cells.

    scan = ListScan(hint, centres="project"); scan.add(c, d) …; frame = scan.finish()    # the enclosing grid
    arr  = arrange(frame.local(c), d)                            # the order, the repeated rows left out
    w    = ListWriter(store, frame.grid, fields); w.put(idx, lc, ld, values); staged = w.finish()
    rep  = ListChecker(fields, frac).…; rep.finish(idx, lc, ld)  # duplicates, overlaps `boxes`, volume, fields

The checks (`meta.checks`): `duplicates` — the same centre and size (the first stays); `overlaps {n, method: "boxes"}` —
the blocks whose common part with another has a volume (kernels: `list_kernels.overlap_pairs`); `volume` — Σ Π d·FRAC.
The helpers `local_centres`, `boxes`, `widths`, `volumes` give a `Chunk` of either layout the same answers, so the
operations that read boxes (report, export, filters, regularization, display) need no branch of their own.
Errors — `{code, message, hint}` in Russian: 422 `block_list_sizes`, `block_model_too_large`, `block_model_invalid`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

from . import geometry as geo
from . import schema as bs
from .checks import ERROR, INFO, WARNING, CheckReport, _Field, empty_mask, ru_num
from .fields_store import GroupSink
from .meta import LAYOUT_LIST, LIST_COLUMNS, META_V3
from .schema import MAX_EXAMPLES, MAX_GRID_V2, GridV2, bad_model
from .store import CellsWriterV2, StagedCells

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

    from .model import Chunk


def _kernels() -> Any:
    """The numba kernels, imported when a list is imported or checked (a module that only reads boxes has no numba)."""
    from . import list_kernels

    return list_kernels


MAX_PAIRS = 4096  # overlapping pairs kept for the examples
METHOD = "boxes"
SNAP_STEP = 1e-3  # the lowest corner of the enclosing grid is a multiple of it (a readable one, never above the data)
MAX_TEST_CELLS = (1 << 21) - 2  # cells of the test grid of the overlaps an axis
Tick = Callable[[float | None, str | None], None]


def sizes_needed() -> ApiError:
    return ApiError(422, "block_list_sizes", "Список блоков нужен с размерами: в файле нет столбцов размеров.",
                    "Укажите столбцы размеров блоков по X, Y и Z; модель без размеров — регулярная, её "
                    "импортируют сеткой.")  # fmt: skip


def _snap_down(v: float) -> float:
    return math.floor(v / SNAP_STEP + 1e-9) * SNAP_STEP if math.isfinite(v) else v


# --- the enclosing grid -----------------------------------------------------------------------------------------------


@dataclass
class ListFrame:
    """What pass 1 found: the enclosing grid, the frame of the file (`pivot`, the turn) and how to put the centres of
    the file into the local axes of the model."""

    grid: GridV2
    pivot: tuple[float, float, float]
    origin: tuple[float, float, float]  # the lowest corner of the boxes in the axes of the pivot
    centres: str
    rows: int
    smin: np.ndarray
    smax: np.ndarray
    lo: np.ndarray  # the lowest and the highest edge of the boxes, local axes of the model
    hi: np.ndarray
    parent_guess: bool
    warnings: list[str] = field(default_factory=list)

    def local(self, c: Any) -> np.ndarray:
        """The centres of the file (n×3) in the local axes of the model: q = R_z(−φ)·(c − pivot) − origin
        («project»), or c − pivot − origin («model»)."""
        c = np.asarray(c, dtype=np.float64).reshape(-1, 3)
        base = GridV2.of({"corner": list(self.pivot), "counts": [1, 1, 1], "size": [1.0, 1.0, 1.0],
                          "rotation": self.grid.rotation})  # fmt: skip
        q = base.to_local(c) if self.centres == "project" else c - np.asarray(self.pivot)
        return q - np.asarray(self.origin)  # type: ignore[no-any-return]


class ListScan:
    """Pass 1: the extent and the sizes of the blocks of every chunk (`add`, as `GridScan.add`), then `finish()`."""

    def __init__(self, hint: Any = None, *, centres: str = "project") -> None:
        if centres not in ("project", "model"):
            raise bad_model(f"Оси центров {centres!r}: нужны «project» или «model».")
        self.hint, self.centres = hint, centres
        self.rows = 0
        self._sized: bool | None = None
        self._frame: GridV2 | None = None
        self._lo = np.full(3, np.inf)
        self._hi = np.full(3, -np.inf)
        self._smax = np.zeros(3)
        self._smin = np.full(3, np.inf)

    def _frame_of(self, first: np.ndarray) -> GridV2:
        turn = float(getattr(self.hint, "rotation", 0.0) or 0.0) if self.centres == "project" else 0.0
        corner = getattr(self.hint, "corner", None)
        pivot = corner if corner is not None else (tuple(float(v) for v in first) if turn else (0.0, 0.0, 0.0))
        return GridV2.of({"corner": list(pivot), "counts": [1, 1, 1], "size": [1.0, 1.0, 1.0], "rotation": turn})

    def add(self, centres: Any, sizes: Any | None = None) -> None:
        c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
        if sizes is None:
            raise sizes_needed()
        d = np.asarray(sizes, dtype=np.float64).reshape(-1, 3)
        if len(d) != len(c):
            raise bad_model("Число размеров блоков не совпадает с числом центров.")
        ok = np.isfinite(c).all(axis=1) & np.isfinite(d).all(axis=1) & (d > 0).all(axis=1)
        if not ok.any():
            return
        if not ok.all():
            c, d = c[ok], d[ok]
        if self._frame is None:
            self._frame = self._frame_of(c[0])
        q = self._frame.to_local(c) if self.centres == "project" else c - np.asarray(self._frame.corner)
        self.rows += len(q)
        lo, hi = q - d / 2, q + d / 2
        self._lo, self._hi = np.minimum(self._lo, lo.min(axis=0)), np.maximum(self._hi, hi.max(axis=0))
        self._smin, self._smax = np.minimum(self._smin, d.min(axis=0)), np.maximum(self._smax, d.max(axis=0))

    def finish(self) -> ListFrame:
        if self._frame is None or not self.rows:
            raise bad_model("В файле нет ни одного блока с центром и размером.",
                            "Проверьте столбцы центров и размеров.")  # fmt: skip
        h, frame = self.hint, self._frame
        warnings = list(getattr(h, "notes", ()) or ())
        hinted = getattr(h, "size", None)
        guess = hinted is None
        size = np.asarray(hinted if hinted is not None else self._smax, dtype=np.float64).copy()
        if (size < self._smax - 1e-9).any():
            raise ApiError(422, "bad_param", "Размер ячейки охватывающей сетки меньше самого большого блока.",
                           "Задайте размер не меньше самого большого блока или оставьте его пустым.",
                           extra={"param": "grid.size"})  # fmt: skip
        origin = np.array([_snap_down(float(v)) for v in self._lo])
        span = self._hi - origin
        scale = 1
        while math.prod(max(1, math.ceil(float(span[a] / (size[a] * scale)))) for a in range(3)) > MAX_GRID_V2:
            scale += 1
            if scale > 1 << 20:
                raise ApiError(422, "block_model_too_large", "Охват модели слишком велик для сетки.",
                               "Проверьте координаты блоков: возможно, часть из них записана с ошибкой.")  # fmt: skip
        size = size * scale
        counts = [max(1, math.ceil(float(span[a] / size[a]))) for a in range(3)]
        if scale > 1:
            warnings.append(f"Ячейка охватывающей сетки увеличена в {scale} раз: блоков слишком много на охват модели.")
        turn = frame.rotation
        pivot = tuple(float(v) for v in frame.corner)
        corner = frame.to_project(origin.reshape(1, 3))[0]
        grid = GridV2.of({"corner": [float(v) for v in corner], "counts": counts, "size": [float(v) for v in size],
                          "rotation": turn, "order": bs.ORDER, "div": [1, 1, 1]})  # fmt: skip
        return ListFrame(grid, (pivot[0], pivot[1], pivot[2]), (float(origin[0]), float(origin[1]), float(origin[2])),
                         self.centres, self.rows, self._smin.copy(), self._smax.copy(), self._lo - origin,
                         self._hi - origin, guess, warnings)  # fmt: skip


def cells_of(grid: GridV2, lc: np.ndarray) -> np.ndarray:
    """P (uint64) of the cell of the enclosing grid that holds each centre `lc` (local axes)."""
    origin = np.zeros(3)
    size = np.asarray(grid.regular(), dtype=np.float64)
    counts = np.asarray(grid.counts, dtype=np.int64)
    return _kernels().cell_index(np.ascontiguousarray(lc, dtype=np.float64), origin, size, counts)  # type: ignore[no-any-return]


# --- the order, the repeated rows -------------------------------------------------------------------------------------


@dataclass
class Arranged:
    """`order` — the rows of the input in the order of the file, the repeated ones left out; `idx` — their cells;
    `dups` — how many were left out, `examples` — the first of them (rows of the input)."""

    order: np.ndarray
    idx: np.ndarray
    dups: int
    examples: list[dict[str, Any]]
    lc: np.ndarray  # the centres and the sizes in the same order (the repeated rows left out): no second gather
    ld: np.ndarray


def arrange(grid: GridV2, lc: np.ndarray, ld: np.ndarray) -> Arranged:
    """The sort of a list of blocks by (idx, cz, cy, cx) — a tie keeps the order of the input — and the repeated rows
    (the same centre AND size) left out, the first stays."""
    idx = cells_of(grid, lc)
    order = np.lexsort((lc[:, 0], lc[:, 1], lc[:, 2], idx))
    idx = idx[order]
    lcs, lds = lc[order], ld[order]
    dup, first = _kernels().mark_duplicates(idx, lcs, lds)
    examples: list[dict[str, Any]] = []
    count = int(dup.sum())
    if count:
        for at in np.flatnonzero(dup)[:MAX_EXAMPLES]:
            examples.append({"check": "duplicate", "row": int(order[at]), "p": int(idx[at]), "sub": None,
                             "other_row": int(order[first[at]])})  # fmt: skip
        keep = ~dup
        order, idx, lcs, lds = order[keep], idx[keep], lcs[keep], lds[keep]
    return Arranged(order, idx, count, examples, lcs, lds)


# --- overlaps ---------------------------------------------------------------------------------------------------------


def overlaps(lc: np.ndarray, ld: np.ndarray) -> tuple[np.ndarray, int, np.ndarray]:
    """(the rows that overlap another, the number of overlapping pairs, the first pairs [k, 2] with a < b). A pair
    whose common part is thinner than 1e−9 m on some axis does not count (a shared face is not an overlap)."""
    n = len(lc)
    flag = np.zeros(n, dtype=np.bool_)
    if n < 2:
        return flag, 0, np.empty((0, 2), dtype=np.int64)
    lc, ld = np.ascontiguousarray(lc), np.ascontiguousarray(ld)  # the kernels take the edges from them: no `lo`, `hi`
    gmin = (lc - ld / 2).min(axis=0)
    extent = (lc + ld / 2).max(axis=0) - gmin
    step = np.maximum(2.0 * np.median(ld, axis=0), extent / MAX_TEST_CELLS)
    step = np.maximum(step, 1e-9)
    while True:  # a giant block entered into the tiny cells would make millions of entries: coarser cells then
        total = int(_kernels().count_entries(lc, ld, gmin, step))
        if total <= 8 * n + 1024:
            break
        step = step * 2.0
    keys = np.empty(total, dtype=np.int64)
    boxes = np.empty(total, dtype=np.int32)
    _kernels().fill_entries(lc, ld, gmin, step, keys, boxes)
    at = np.argsort(keys)  # the order inside a cell does not matter
    keys = keys[at]
    boxes = boxes[at]
    del at
    pairs = np.empty((MAX_PAIRS, 2), dtype=np.int64)
    found, kept = _kernels().overlap_pairs(lc, ld, gmin, step, keys, boxes, flag, pairs, MAX_PAIRS)
    got = pairs[:kept]
    return flag, int(found), got[np.lexsort((got[:, 1], got[:, 0]))] if kept else got


# --- the file ---------------------------------------------------------------------------------------------------------


class ListWriter(CellsWriterV2):
    """The file `cells` of a list of blocks: `put(idx, lc, ld, values)` any number of times, rows by `idx` ascending
    (ties allowed: the caller sorted them), then `finish()` → StagedCells with `v2` {v: 3, layout: "list", parents —
    the occupied cells, sub false, groups}. A context manager drops the temp file unless finished."""

    def __init__(self, store: BlobStore, grid: Any, fields_in: Sequence[Mapping[str, Any]],
                 row_group: int = bs.ROW_GROUP_V2) -> None:  # fmt: skip
        import pyarrow as pa

        super().__init__(store, grid, fields_in, sub=False, row_group=row_group)
        if self.grid.sub.sub or self.grid.tensors is not None:
            raise bad_model("У списка блоков не бывает субблоков и переменного шага сетки.")
        geo_types = [(n, pa.float64()) for n in LIST_COLUMNS]
        self._schema = pa.schema([(bs.IDX, pa.uint64()), *geo_types, *[(c.name, c.type) for c in self.cols]])
        codes = [c.name for c in self.cols if c.dtype == "cat"]
        self._sink.abort()  # nothing was written: the sink of the base class goes, ours has the columns of the boxes
        self._sink = GroupSink(self.path, self._schema, row_group, dictionary=codes, on_group=self._on_group)

    def __enter__(self) -> ListWriter:
        return self

    def put(self, idx: Any, lc: Any, ld: Any, values: Mapping[str, Any]) -> None:
        import pyarrow as pa

        if self._done:
            raise RuntimeError("ListWriter is finished")
        ix = np.asarray(idx)
        if ix.ndim != 1 or (len(ix) and not np.issubdtype(ix.dtype, np.integer)):
            raise bad_model("Номера ячеек списка блоков должны быть целыми числами.")
        if not len(ix):
            return
        if int(ix.min()) < 0 or int(ix.max()) >= self.grid.cells:
            raise bad_model("Номер ячейки списка блоков вне охватывающей сетки.")
        ix = ix.astype(np.uint64, copy=False)
        c = np.asarray(lc, dtype=np.float64).reshape(-1, 3)
        d = np.asarray(ld, dtype=np.float64).reshape(-1, 3)
        if len(c) != len(ix) or len(d) != len(ix):
            raise bad_model("Число центров и размеров не совпадает с числом строк.")
        if not (np.isfinite(c).all() and np.isfinite(d).all() and (d > 0).all()):
            raise bad_model("У блока нет центра или размера: в список попадают только блоки с центром и размером > 0.")
        last = -1 if self._last is None else self._last[0]
        if int(ix[0]) < last or (len(ix) > 1 and bool((ix[1:] < ix[:-1]).any())):
            raise bad_model("Строки списка блоков должны идти по возрастанию номера ячейки.")
        missing = [x.name for x in self.cols if x.name not in values]
        if missing:
            raise bad_model(f"Нет значений поля «{missing[0]}».")
        arrays = [pa.array(ix)] + [pa.array(np.ascontiguousarray(a)) for a in (c[:, 0], c[:, 1], c[:, 2], d[:, 0],
                                                                              d[:, 1], d[:, 2])]  # fmt: skip
        for col in self.cols:
            v = np.asarray(values[col.name])
            if v.shape != ix.shape:
                raise bad_model(f"Поле «{col.name}»: число значений не совпадает с числом строк.")
            arrays.append(col.encode(v))
        new = int((ix[1:] != ix[:-1]).sum()) + (1 if self._last is None or int(ix[0]) != self._last[0] else 0)
        self.parents += new
        self._last = (int(ix[-1]), (0, 0, 0))
        self.rows += len(ix)
        self._sink.push(pa.Table.from_arrays(arrays, schema=self._schema))

    def finish(self) -> StagedCells:
        staged = super().finish()
        staged.v2 = {**(staged.v2 or {}), "v": META_V3, "layout": LAYOUT_LIST}
        return staged


# --- the checks -------------------------------------------------------------------------------------------------------


class ListChecker:
    """The checks of a list of blocks: the fields and the volume portion by portion (`feed`), the duplicates and the
    overlaps over the whole list (`finish`)."""

    def __init__(self, fields: Sequence[Mapping[str, Any]] = (), frac: str | None = None,
                 limit: int = MAX_EXAMPLES) -> None:  # fmt: skip
        self.limit, self.frac = max(1, int(limit)), frac
        self.rows = 0
        self.fields = {str(f["name"]): _Field(str(f.get("kind", "number"))) for f in fields}
        self._volume: list[float] = []
        self.volume_empty = 0

    def feed(self, ld: np.ndarray, values: Mapping[str, Any] | None = None) -> None:
        n = len(ld)
        self.rows += n
        if not n:
            return
        vol = (ld[:, 0] * ld[:, 1]) * ld[:, 2]
        if values is not None and self.frac is not None and self.frac in values:
            vol = vol * np.asarray(values[self.frac], dtype=np.float64).reshape(-1)
        total, empty = geo.volume_sum(vol)
        self._volume.append(total)
        self.volume_empty += empty
        for name, st in self.fields.items():
            if values is None or name not in values:
                continue
            a = np.asarray(values[name])
            ok = ~empty_mask(a, st.kind)
            v = a[ok] if not ok.all() else a
            if len(v):
                st.filled += len(v)
                st.lo, st.hi = min(st.lo, float(v.min())), max(st.hi, float(v.max()))

    def finish(self, idx: np.ndarray, lc: np.ndarray, ld: np.ndarray, *, dups: int = 0,
               dup_examples: Sequence[Mapping[str, Any]] = (), rows_of: Callable[[int], int] = int,
               stats_fields: Sequence[Mapping[str, Any]] = ()) -> CheckReport:  # fmt: skip
        """The report; `rows_of(i)` — the number in the file of row i of the list (the importer's `order`);
        `stats_fields` — the fields of other files (`fld:…`), judged by their `stats`."""
        flag, found, pairs = overlaps(lc, ld)
        n_over = int(flag.sum())
        examples: list[dict[str, Any]] = [dict(e) for e in dup_examples][: self.limit]
        for a, b in pairs[: self.limit]:
            examples.append({"check": "overlap", "row": rows_of(int(a)), "p": int(idx[a]), "sub": None,
                             "other_row": rows_of(int(b))})  # fmt: skip
        rows_over = sorted(rows_of(int(r)) for r in np.flatnonzero(flag)[: self.limit])
        empty = [n for n, st in self.fields.items() if self.rows and st.filled == 0]
        constant = [n for n, st in self.fields.items() if st.filled >= 2 and st.lo == st.hi]
        for f in stats_fields:
            stats = f.get("stats") or {}
            if not self.rows or "count" not in stats:
                continue
            if stats["count"] == 0:
                empty.append(str(f["name"]))
            elif stats["count"] >= 2 and stats.get("min") is not None and stats.get("min") == stats.get("max"):
                constant.append(str(f["name"]))
        volume = math.fsum(self._volume)
        checks = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "duplicates": int(dups),
                  "overlaps": {"n": n_over, "method": METHOD}, "empty": empty, "constant": constant,
                  "volume": volume, "examples": examples[:MAX_EXAMPLES]}  # fmt: skip
        return CheckReport(self._findings(dups, n_over, rows_over, empty, constant, volume), checks, self.rows)

    def _findings(self, dups: int, over: int, rows_over: list[int], empty: list[str], constant: list[str],
                  volume: float) -> list[dict[str, Any]]:  # fmt: skip
        out: list[dict[str, Any]] = []

        def add(code: str, severity: str, message: str, hint: str, count: int, rows: list[int], **extra: Any) -> None:
            out.append({"code": code, "severity": severity, "message": message, "hint": hint,
                        "rows": rows[: self.limit], "count": count, **extra})  # fmt: skip

        if dups:
            add("duplicates", WARNING, f"Повторяющиеся блоки: {ru_num(dups)}. Оставлена первая строка.",
                "Повторы не меняют модель; проверьте исходный файл, если они не ожидались.", dups, [])  # fmt: skip
        if over:
            add("overlaps", ERROR, f"Перекрывающиеся блоки: {ru_num(over)} (по пересечению боксов блоков).",
                "Строки не удалены: объём и тоннаж по ним завышены. Исправьте модель в исходной программе.",
                over, rows_over, method=METHOD)  # fmt: skip
        if empty:
            add("empty_fields", WARNING, f"Пустые поля (нет ни одного значения): {', '.join(empty)}.",
                "Проверьте код пустого при импорте или удалите эти поля.", len(empty), [], fields=empty)  # fmt: skip
        if constant:
            add("constant_fields", INFO, f"Постоянные поля (одно значение у всех блоков): {', '.join(constant)}.",
                "Такое поле не различает блоки; убедитесь, что столбец прочитан верно.",
                len(constant), [], fields=constant)  # fmt: skip
        note = "Блоки без значения FRAC в объём не входят." if self.frac else "Объём — по размерам блоков."
        add("volume", INFO, f"Суммарный объём блоков: {ru_num(volume)} м³."
            + (f" Без значения FRAC: {ru_num(self.volume_empty)}." if self.volume_empty else ""),
            note, self.volume_empty, [])  # fmt: skip
        return out


def check_cells(store: BlobStore, shas: Mapping[str, str] | str, meta: Mapping[str, Any], limit: int = MAX_EXAMPLES,
                tick: Tick | None = None) -> CheckReport:  # fmt: skip
    """`checks.check_cells` of a stored list of blocks: the boxes are read whole (56 bytes a row), the fields by row
    groups; the duplicates are looked for again (a list the importer wrote has none)."""
    from .model import Reader

    flds = list(meta.get("fields") or [])
    here = [f for f in flds if (f.get("file") or bs.ROLE) == bs.ROLE]
    other = [f for f in flds if (f.get("file") or bs.ROLE) != bs.ROLE]
    frac = next((str(f["name"]) for f in flds if f.get("role") == "frac"), None)
    reader = Reader(store, {bs.ROLE: shas} if isinstance(shas, str) else shas, flds, int(meta.get("rows") or 0),
                    False, True)  # fmt: skip
    rows = reader.rows
    chk = ListChecker([{"name": f["name"], "kind": f["kind"]} for f in here], frac, limit)
    names = [str(f["name"]) for f in here]
    read = names if frac is None or frac in names else [*names, frac]
    idx, lc, ld = np.empty(rows, dtype=np.uint64), np.empty((rows, 3)), np.empty((rows, 3))
    for first, n, p, c, d, cols in reader.pieces(read, p=True, geo=True):
        if tick is not None:
            tick(min(first / max(rows, 1), 1.0), f"Проверка: {ru_num(first)} из {ru_num(rows)} строк")
        assert p is not None and c is not None and d is not None
        idx[first : first + n], lc[first : first + n], ld[first : first + n] = p, c, d
        chk.feed(d, cols)
    if tick is not None:
        tick(0.9, "Проверка перекрытий")
    dup, first_of = _kernels().mark_duplicates(idx, lc, ld)
    examples = [{"check": "duplicate", "row": int(r), "p": int(idx[r]), "sub": None, "other_row": int(first_of[r])}
                for r in np.flatnonzero(dup)[:limit]]  # fmt: skip
    keep = ~dup
    kept = np.flatnonzero(keep)

    def row_of(i: int) -> int:
        return int(kept[i])

    report = chk.finish(idx[keep], lc[keep], ld[keep], dups=int(dup.sum()), dup_examples=examples, rows_of=row_of,
                        stats_fields=other)  # fmt: skip
    if tick is not None:
        tick(1.0, None)
    return report


# --- a `Chunk` of either layout ---------------------------------------------------------------------------------


def local_centres(grid: GridV2, ch: Chunk) -> np.ndarray:
    """The centres of the rows of a chunk in the local axes of the model: float64 [n, 3]."""
    return ch.lc if ch.lc is not None else geo.row_local_centres(grid, ch.p, ch.s, ch.w)


def widths(grid: GridV2, ch: Chunk) -> np.ndarray:
    """The sizes of the rows along the axes of the model: float64 [n, 3]."""
    return ch.ld if ch.ld is not None else geo.row_widths(grid, ch.p, ch.s, ch.w)


def boxes(grid: GridV2, ch: Chunk) -> np.ndarray:
    """(lo, hi) of the rows in the local axes: float64 [n, 6]."""
    if ch.lc is None or ch.ld is None:
        return geo.row_boxes(grid, ch.p, ch.s, ch.w)
    return np.concatenate([ch.lc - ch.ld / 2, ch.lc + ch.ld / 2], axis=1)


def centres(grid: GridV2, ch: Chunk) -> np.ndarray:
    """The centres of the rows in the axes of the project: float64 [n, 3]."""
    return grid.to_project(ch.lc) if ch.lc is not None else geo.centres(grid, ch.p, ch.s, ch.w)


def volumes(grid: GridV2, ch: Chunk, frac: np.ndarray | None = None) -> np.ndarray:
    """Π d·FRAC of the rows (NaN FRAC — NaN volume, never 0)."""
    if ch.ld is None:
        return geo.row_volumes(grid, ch.p, ch.s, ch.w, frac)
    vol = (ch.ld[:, 0] * ch.ld[:, 1]) * ch.ld[:, 2]
    return vol if frac is None else vol * np.asarray(frac, dtype=np.float64).reshape(-1)


def overlap_note(model: Any) -> str | None:
    """A warning for the operations that sum the volume of the blocks (the report, the regularization) of a list that
    has overlapping blocks (`meta.checks.overlaps.n`): the common part of two blocks is counted twice."""
    if not getattr(model, "listed", False):
        return None
    n = int(((model.meta.get("checks") or {}).get("overlaps") or {}).get("n") or 0)
    if not n:
        return None
    return (f"В модели-списке {ru_num(n)} перекрывающихся блоков: их общий объём посчитан дважды, объём и тоннаж "
            "завышены. Исправьте модель в исходной программе и загрузите заново.")


def warm() -> dict[str, float]:
    """Compiles (or loads from the cache) the kernels of a list of blocks on a few boxes: the order, the repeats, the
    overlaps, the shared faces (`blocks/warm.py`, the step `list`)."""
    import time

    from . import list_shell

    t0 = time.perf_counter()
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [2.0, 2.0, 2.0], "div": [1, 1, 1]})
    lc = np.array([[0.5, 0.5, 0.5], [1.5, 0.5, 0.5], [1.5, 0.5, 0.5], [3.0, 3.0, 3.0]])
    ld = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
    arr = arrange(grid, lc, ld)
    overlaps(arr.lc, arr.ld)
    ok = np.ones(len(arr.order), dtype=bool)
    list_shell.view(grid, arr.idx, arr.lc, arr.ld, ok, limit=1)
    list_shell.view(grid, arr.idx, arr.lc, arr.ld, ok, limit=1000)
    return {"list": time.perf_counter() - t0}
