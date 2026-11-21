"""Selections of samples (ТЗ 8.3, № 237; ADR 0019 §8): the rows of one table a chart picked, kept in the core
behind a token — the layer of segments in 3D and the filter of the «Таблица» panel read them by it.

    sel = make(project, table, ids, source="rows")     # table — `stats.source.Table`; ids — dh_rows.id
        Picked {token, set_id, table_id, table_name, rev, ids (int64, ascending, unique), source, unknown}
        more than MAX_ROWS (200 000) rows — 422 `too_many_rows`
    sel = current(project, token)      # 404 `selection_not_found`; the table edited or gone — 409 `selection_stale`
    known(project, table, ids) -> (ids that are rows of the table, how many are not)
    lines_of(project, sel) -> (Columns of the table, int64 line positions of the rows of `sel`, ascending)
    segments(project, sel, method) -> bytes   BFA1 `selection_segments` (below)
    row_mask(sel, ids) -> bool [n]       the rows of a table snapshot (`drillholes.snapshot`) in the selection
    drop(project, token) -> bool

One selection per window (the window keeps its token), but the core keeps the last KEEP (16) of a process (LRU;
`DELETE` drops one). A selection belongs to one revision of its table: an edit raises `rev` (and a replaced table
has another identity token), after it every use of the token answers 409 `selection_stale` — the window takes
the selection off with a message. The ids are not trusted: those that are not rows of the table are counted
(`unknown`) and left out.

BFA1 `selection_segments` (`viz/binary.py`; the 3D layer `viz/holes/sampleMarks.ts`): header {kind, format,
token, set_id, table_id, rev, method, n (rows of the selection), segments (k drawn), no_position (rows without a
place: the hole has no trajectory), origin [x, y, z] (whole metres, the middle of the extent), bbox [xmin, ymin,
zmin, xmax, ymax, zmax] | null}; arrays `row u4` (dh_rows.id; f8 past 2**32), `hole u4` (dh_holes.id),
`from f8`, `to f8`, `a f4×3`, `b f4×3` — the start and the end of every interval on the path of its hole, from
the origin (as the parts of the paths: float32 for the video card, float64 in the core). The rows go in line
order (hole name, FROM, TO). An event (FROM = TO) is a segment of length 0."""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

MAX_ROWS = 200_000
KEEP = 16
FORMAT = 1
_ID_ORDER = "\0id order"


@dataclass(frozen=True, eq=False)
class Picked:
    token: str
    project: str  # uid
    set_id: int
    table_id: int
    table_name: str
    table_token: str
    rev: int
    ids: np.ndarray  # int64 dh_rows.id, ascending, unique
    source: str  # rows | scatter | box | probplot | table
    unknown: int = 0  # given ids that are not rows of the table
    masks: dict[tuple[Any, ...], np.ndarray] = field(default_factory=dict)  # the snapshot key → its mask

    @property
    def n(self) -> int:
        return int(self.ids.shape[0])

    def info(self) -> dict[str, Any]:
        return {"token": self.token, "set_id": self.set_id, "table_id": self.table_id, "table_name": self.table_name,
                "rev": self.rev, "n": self.n, "unknown": self.unknown, "source": self.source}  # fmt: skip


class Store:
    """The selections of the process: LRU of KEEP by token."""

    def __init__(self, keep: int = KEEP) -> None:
        self.keep = keep
        self._lock = threading.Lock()
        self._items: OrderedDict[str, Picked] = OrderedDict()

    def put(self, sel: Picked) -> None:
        with self._lock:
            self._items[sel.token] = sel
            while len(self._items) > self.keep:
                self._items.popitem(last=False)

    def get(self, token: str) -> Picked | None:
        with self._lock:
            sel = self._items.get(token)
            if sel is not None:
                self._items.move_to_end(token)
            return sel

    def pop(self, token: str) -> Picked | None:
        with self._lock:
            return self._items.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


STORE = Store()


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def too_many(n: int, param: str) -> ApiError:
    return ApiError(422, "too_many_rows", f"Выделено слишком много проб: {_n(n)}, можно не больше {_n(MAX_ROWS)}.",
                    "Выделите меньшую область графика или сузьте фильтр таблицы.", extra={"param": param})  # fmt: skip


def not_found() -> ApiError:
    return ApiError(404, "selection_not_found", "Выделение не найдено.",
                    "Выделение снято или устарело: выделите пробы на графике заново.")  # fmt: skip


def stale(sel: Picked, gone: bool = False) -> ApiError:
    what = "удалена" if gone else "изменилась"
    return ApiError(409, "selection_stale", f"Выделение устарело: таблица «{sel.table_name}» {what}.",
                    "Выделение снято. Выделите пробы на графике заново.", extra={"token": sel.token})  # fmt: skip


def make(project: Any, table: Any, ids: Any, *, source: str, unknown: int = 0, param: str = "rows") -> Picked:
    """A new selection of the rows `ids` (dh_rows.id of `table`) of the current revision of the table."""
    got = np.unique(np.asarray(ids, dtype=np.int64).reshape(-1))
    if got.shape[0] > MAX_ROWS:
        raise too_many(int(got.shape[0]), param)
    got.flags.writeable = False
    sel = Picked(secrets.token_hex(12), project.uid, table.set_id, table.id, table.name, table.token, table.rev, got,
                 source, int(unknown))  # fmt: skip
    STORE.put(sel)
    return sel


def _table_now(project: Any, sel: Picked) -> Any:
    """The table of the selection as it is now; None — it is not there any more."""
    from bf_core.db import dialect

    from . import source

    with dialect.read_tx(project.engine) as conn:
        try:
            return source.find_table(conn, sel.set_id, sel.table_id)
        except ApiError:
            return None


def current(project: Any, token: str) -> Picked:
    """The selection of `token` while its table is the one it was made in (module docstring)."""
    sel = STORE.get(token)
    if sel is None or sel.project != project.uid:
        raise not_found()
    t = _table_now(project, sel)
    if t is None or t.id != sel.table_id:
        raise stale(sel, gone=True)
    if t.rev != sel.rev or t.token != sel.table_token:
        raise stale(sel)
    return sel


def drop(project: Any, token: str) -> bool:
    sel = STORE.get(token)
    if sel is None or sel.project != project.uid:
        return False
    return STORE.pop(token) is not None


def _id_order(project: Any, cols: Any) -> np.ndarray:
    """The line positions of the rows of the table in the order of their ids (cached with the columns)."""
    from . import source

    t = cols.table

    def build() -> np.ndarray:
        order = np.argsort(cols.row_ids, kind="stable")
        order.flags.writeable = False
        return order

    return source.COLUMNS.get((project.uid, t.id, t.token, t.rev, _ID_ORDER), build)  # type: ignore[no-any-return]


def _positions(project: Any, cols: Any, ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(line positions of the ids that are rows of `cols`, ascending; a mask of `ids` that are)."""
    order = _id_order(project, cols)
    if cols.n == 0 or ids.shape[0] == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(ids.shape[0], dtype=bool)
    sorted_ids = cols.row_ids[order]
    at = np.searchsorted(sorted_ids, ids).clip(0, cols.n - 1)
    ok = sorted_ids[at] == ids
    return np.sort(order[at[ok]]), ok


def known(project: Any, table: Any, ids: Any) -> tuple[np.ndarray, int]:
    """The ids (unique) that are rows of the table, and how many of the given ones are not."""
    from . import source

    got = np.unique(np.asarray(ids, dtype=np.int64).reshape(-1))
    cols = source.read(project, table.set_id, table.id, [])
    _lines, ok = _positions(project, cols, got)
    return got[ok], int(got.shape[0] - ok.sum())


def lines_of(project: Any, sel: Picked) -> tuple[Any, np.ndarray]:
    """The columns of the table (its rows with FROM and TO) and the line positions of the selected rows."""
    from . import source

    cols = source.read(project, sel.set_id, sel.table_id, [])
    if cols.table.rev != sel.rev:
        raise stale(sel)
    lines, _ok = _positions(project, cols, sel.ids)
    return cols, lines


def _id_type(ids: np.ndarray) -> str:
    """u4 for the ids (f8 — exact to 2**53 — in a database whose ids went past 2**32)."""
    return "<u4" if not ids.size or int(ids.max()) < 1 << 32 else "<f8"


def segments(project: Any, sel: Picked, method: str = "minimum_curvature") -> bytes:
    """BFA1 `selection_segments` of the selection (module docstring)."""
    from bf_core.composites.coords import hole_index
    from bf_core.drillholes.trajectory import trajectories
    from bf_core.viz import binary

    cols, lines = lines_of(project, sel)
    st = trajectories(project, sel.set_id, method)
    dense_of_hole = hole_index(st.hole_ids, cols.hole_db_ids)
    hole = cols.hole_of()[lines]
    dense = dense_of_hole[hole]
    frm, to = cols.from_m[lines], cols.to_m[lines]
    on = np.flatnonzero(dense >= 0)  # the holes of the trajectories (a hole without one is skipped there: NaN)
    a, b, _mid = st.traj.interval_positions(dense[on], frm[on], to[on])
    placed = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    keep = on[placed]
    a, b = a[placed], b[placed]
    if keep.size:
        box = np.concatenate((np.minimum(a.min(axis=0), b.min(axis=0)), np.maximum(a.max(axis=0), b.max(axis=0))))
        origin = np.round((box[:3] + box[3:]) * 0.5) + 0.0
        bbox: list[float] | None = [float(v) for v in box]
    else:
        origin, bbox = np.zeros(3), None
    header = {
        "kind": "selection_segments", "format": FORMAT, "token": sel.token, "set_id": sel.set_id,
        "table_id": sel.table_id, "rev": sel.rev, "method": method, "n": sel.n, "segments": int(keep.size),
        "no_position": int(sel.n - keep.size), "origin": [float(v) for v in origin], "bbox": bbox,
    }  # fmt: skip
    rows = lines[keep]
    arrays = [
        ("row", cols.row_ids[rows], _id_type(cols.row_ids)), ("hole", cols.hole_db_ids[hole[keep]], "<u4"),
        ("from", frm[keep], "<f8"), ("to", to[keep], "<f8"),
        ("a", (a - origin).astype(np.float32), "<f4"), ("b", (b - origin).astype(np.float32), "<f4"),
    ]  # fmt: skip
    return binary.encode(header, arrays)


def row_mask(sel: Picked, key: tuple[Any, ...], ids: np.ndarray) -> np.ndarray:
    """The rows of a snapshot (its key and its ids — dh_rows.id) that are in the selection; cached per snapshot."""
    hit = sel.masks.get(key)
    if hit is None:
        hit = np.isin(ids, sel.ids)
        hit.flags.writeable = False
        sel.masks.clear()
        sel.masks[key] = hit
    return hit
