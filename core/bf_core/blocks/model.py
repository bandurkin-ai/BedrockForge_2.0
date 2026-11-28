"""A block model for reading (подшаг 17.4; ADR 0023 §2 and §15; docs/progress/e17-api.md §2–3).

    m = Model.open(conn, store, model_id)      # 404 block_model_not_found, 409 block_model_broken
    m.grid, m.rows, m.parents, m.sub, m.full   # GridV2; the counts of the meta (v1 is read as v2)
    m.field("AU"), m.labels("DOM")             # 404 block_field_not_found; the labels of a category
    m.columns(["AU", "DOM"])                   # {name: array}, whole, or rows [start, stop)
    m.p(), m.subs()                            # uint64 [rows]; (s, w) uint8 [rows, 3] or None
    for ch in m.iter(["AU"], chunk=1 << 20):   # Chunk(start, p, s, w, cols): memory ≈ a chunk, not the model
    m.centres(rows), m.local(rows), m.boxes(rows), m.volume(rows)     # float64 (blocks/geometry.py)

The memory form (e17-api §2): a number of any `dtype` is float64, NaN — empty (never 0); a category is int32 codes
of `labels`, −1 — empty (`fields_store.decode`: a v1 category, int32 with −1, and a v2 one, int16 with null, read
alike); P is uint64; s and w are uint8. A v1 file and a v2 file of one model read the same way: the fields of the
files `fld:` are read in step with `cells` (the same groups of rows; another size of the groups — 409
`block_model_broken`). Only the row groups a call needs are read, one at a time; `columns` of a whole model holds
what it returns (8 bytes a number, 4 a category, per row) and nothing else — the row groups go back to the system.
The names of the fields are looked up without case and come out as they are stored.

A list of blocks (meta v3, `layout: "list"`, ADR 0030 decision 8; `blocks/list_layout.py`): `Model.open` refuses it
(422 `block_list_unsupported`, «Регуляризуйте модель») unless the caller names `layout=ANY` — the operations that
read boxes: the report, the export, the filters, the calculator, the density, the categories, the display and the
regularization. For it `p()` is the cell of the centre (`idx`), `subs()` is None, `iter` gives chunks whose `lc`, `ld`
(centres and sizes, local axes, float64) are set, and `centres/local/boxes/volume` come from the columns `cx … dz`.

`Reader` is the same without the grid and the connection: what `stats/blocks_cache.py` needs to fill the cache.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from . import geometry
from . import meta as bmeta
from . import schema as bs
from .fields_store import decode
from .meta import LAYOUT_GRID, LAYOUT_LIST, LIST_COLUMNS
from .schema import SUB_COLUMNS, GridV2
from .store import broken, cells_sha, not_found

CHUNK = 1 << 20
ANY = (LAYOUT_GRID, LAYOUT_LIST)  # `Model.open(layout=ANY)`: the operation reads a list of blocks too


def list_unsupported(name: str, what: str | None = None) -> ApiError:
    """422 `block_list_unsupported`: the operation needs the grid of parents and sub-blocks (ADR 0030 decision 8)."""
    label = f"Операция «{what}»" if what else "Эта операция"
    return ApiError(422, "block_list_unsupported", f"{label} недоступна для модели-списка блоков «{str(name)[:80]}».",
                    "Регуляризуйте модель: Блочные модели → Регуляризация.")  # fmt: skip


def field_not_found(name: str, param: str | None = None) -> ApiError:
    extra = {"param": param} if param else None
    return ApiError(404, "block_field_not_found", f"Поле «{str(name)[:80]}» не найдено.",
                    "Обновите свойства модели.", extra=extra)  # fmt: skip


def unsupported() -> ApiError:
    """422 `blocks_unsupported`: a consumer of Э11/Э13 (swath; OMF export — 17.15) cannot read a model with a turn or
    sub-blocks yet (docs/progress/e17-api.md §2)."""
    return ApiError(422, "blocks_unsupported", "Модель с поворотом или субблоками здесь пока не поддерживается.",
                    "Используйте модель без поворота и без субблоков.")  # fmt: skip


def is_plain(info: Mapping[str, Any]) -> bool:
    """True — no turn and no sub-blocks: `info` is `store.block_model_info` (v1 or v2) or a public meta."""
    grid = info.get("grid") or {}
    return float(grid.get("rotation") or 0.0) == 0.0 and not info.get("sub")


@dataclass(frozen=True)
class Chunk:
    """A portion of rows: `start` — the number of its first row; `p` uint64; `s`, `w` uint8 [n, 3] (None — a model
    without sub-blocks); `cols` — the fields asked for (the memory form)."""

    start: int
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    cols: dict[str, np.ndarray]
    lc: np.ndarray | None = None  # a list of blocks: centres, local axes, float64 [n, 3] (`p` — the cell of the centre)
    ld: np.ndarray | None = None  # … and the sizes along the axes of the model, float64 [n, 3]

    @property
    def n(self) -> int:
        return int(self.p.shape[0])


def empty_of(spec: Mapping[str, Any], n: int) -> np.ndarray:
    """A buffer of `n` rows for a field in the memory form."""
    return np.empty(n, dtype=np.float64 if spec["kind"] == bs.NUMBER else np.int32)


class Reader:
    """The files of one model read by row groups: `shas` {file role: SHA-256}, `fields` of meta v2 (or v1: no
    `file`, every field is in `cells`), `rows`, `sub`. No state beyond that — safe to share between threads."""

    def __init__(self, store: BlobStore, shas: Mapping[str, str], fields: Sequence[Mapping[str, Any]], rows: int,
                 sub: bool = False, listed: bool = False) -> None:  # fmt: skip
        self.store, self.shas, self.rows, self.sub, self.listed = store, dict(shas), int(rows), sub, listed
        self.specs = {str(f["name"]): f for f in fields}

    def spec(self, name: str) -> Mapping[str, Any]:
        spec = self.specs.get(name)
        if spec is None:
            key = str(name).casefold()
            spec = next((f for n, f in self.specs.items() if n.casefold() == key), None)
        if spec is None:
            raise field_not_found(name)
        return spec

    def canonical(self, names: Sequence[str]) -> list[str]:
        """The stored names of the fields `names`, each once, in the order asked (404 for an unknown one)."""
        return list(dict.fromkeys(str(self.spec(n)["name"]) for n in names))

    def group_sizes(self) -> list[int]:
        """The rows of every row group of `cells` (the groups of the other files are the same)."""
        import pyarrow.parquet as pq

        meta = pq.ParquetFile(self._path(bs.ROLE)).metadata
        return [meta.row_group(g).num_rows for g in range(meta.num_row_groups)]

    def _path(self, role: str) -> Any:
        sha = self.shas.get(role)
        path = self.store.path_of(sha) if sha else None
        if path is None or not path.is_file():
            raise broken()
        return path

    def pieces(self, names: Sequence[str] = (), *, p: bool = True, sub: bool = False, start: int = 0,
               stop: int | None = None, geo: bool = False) -> Iterator[tuple[int, int, np.ndarray | None,
                                                                             np.ndarray | None, np.ndarray | None,
                                                                             dict[str, np.ndarray]]]:  # fmt: skip
        """(first row, rows, P, s, w, {field: values}) of every row group that meets rows [start, stop), cut to them.
        `p` — read P; `sub` — read s and w too (a model without sub-blocks gives None). `geo` — a list of blocks:
        `s` and `w` are then the centres and the sizes (`cx … dz`, float64 n×3), not the sub-blocks."""
        import pyarrow.parquet as pq

        stop = self.rows if stop is None else min(int(stop), self.rows)
        want_sub = sub and self.sub
        want_geo = geo and self.listed
        by_file: dict[str, list[Mapping[str, Any]]] = {}
        for n in self.canonical(names):
            spec = self.specs[n]
            by_file.setdefault(str(spec.get("file") or bs.ROLE), []).append(spec)
        with ExitStack() as stack:
            files = {role: pq.ParquetFile(stack.enter_context(open(self._path(role), "rb")))
                     for role in {bs.ROLE, *by_file}}  # fmt: skip
            sizes = [files[bs.ROLE].metadata.row_group(g).num_rows for g in range(files[bs.ROLE].num_row_groups)]
            if sum(sizes) != self.rows:
                raise broken()
            for f in files.values():
                if [f.metadata.row_group(g).num_rows for g in range(f.num_row_groups)] != sizes:
                    raise broken()
            off = 0
            for g, size in enumerate(sizes):
                lo, hi = max(start - off, 0), min(stop - off, size)
                if off >= stop:
                    break
                if hi <= lo:
                    off += size
                    continue
                cut = slice(lo, hi) if (lo, hi) != (0, size) else slice(None)
                head = [bs.IDX] if p else []
                if want_sub:
                    head += list(SUB_COLUMNS)
                if want_geo:
                    head += list(LIST_COLUMNS)
                cols: dict[str, np.ndarray] = {}
                got: dict[str, Any] = {}
                for role, specs in {bs.ROLE: [], **by_file}.items():
                    want = [str(s["name"]) for s in specs]
                    cells = head if role == bs.ROLE else []
                    if not (want or cells):
                        continue
                    table = files[role].read_row_group(g, columns=[*cells, *want])
                    got.update({n: table.column(n) for n in table.schema.names})
                    for s in specs:
                        cols[str(s["name"])] = decode(got.pop(str(s["name"])), s)[cut]
                pp = None
                if p:
                    pp = np.asarray(got[bs.IDX].to_numpy(zero_copy_only=False)).astype(np.uint64, copy=False)[cut]
                ss = ww = None
                if want_sub:
                    flat = [np.asarray(got[c].to_numpy(zero_copy_only=False)).astype(np.uint8, copy=False)[cut]
                            for c in SUB_COLUMNS]  # fmt: skip
                    ss, ww = np.column_stack(flat[:3]), np.column_stack(flat[3:])
                if want_geo:
                    boxes = [np.asarray(got[c].to_numpy(zero_copy_only=False)).astype(np.float64, copy=False)[cut]
                             for c in LIST_COLUMNS]  # fmt: skip
                    ss, ww = np.column_stack(boxes[:3]), np.column_stack(boxes[3:])
                yield off + lo, hi - lo, pp, ss, ww, cols
                off += size

    def fill(self, names: Sequence[str] = (), *, p: bool = False, sub: bool = False, start: int = 0,
             stop: int | None = None, p_dtype: Any = np.uint64, geo: bool = False) -> dict[str, Any]:  # fmt: skip
        """Rows [start, stop) into arrays of that size straight from the row groups: `{field: values}`, `"\\0p"` —
        P (of `p_dtype`), `"\\0s"`, `"\\0w"` — s and w (no list of parts, no concatenation); `geo` of a list of
        blocks — `"\\0c"`, `"\\0d"`: the centres and the sizes, float64 n×3."""
        stop = self.rows if stop is None else min(int(stop), self.rows)
        start = min(max(int(start), 0), stop)
        n = stop - start
        out: dict[str, Any] = {}
        for name in self.canonical(names):
            out[name] = empty_of(self.specs[name], n)
        if p:
            out[P] = np.empty(n, dtype=p_dtype)
        if sub and self.sub:
            out[S] = np.empty((n, 3), dtype=np.uint8)
            out[W] = np.empty((n, 3), dtype=np.uint8)
        if geo and self.listed:
            out[LC] = np.empty((n, 3), dtype=np.float64)
            out[LD] = np.empty((n, 3), dtype=np.float64)
        done = 0
        for first, k, pp, ss, ww, cols in self.pieces(names, p=p, sub=sub, start=start, stop=stop, geo=geo):
            m = first - start
            if pp is not None:
                out[P][m : m + k] = pp
            if ss is not None and geo and self.listed:
                out[LC][m : m + k] = ss
                out[LD][m : m + k] = ww
            elif ss is not None:
                out[S][m : m + k] = ss
                out[W][m : m + k] = ww
            for name, values in cols.items():
                out[name][m : m + k] = values
            done += k
        if done != n:
            raise broken()
        import pyarrow as pa

        pa.default_memory_pool().release_unused()  # what the pool keeps of the row groups
        return out


P, S, W = "\0p", "\0s", "\0w"
LC, LD = "\0c", "\0d"  # a list of blocks: the centres and the sizes


class Model:
    """A block model of the project, opened for reading (module docstring)."""

    def __init__(self, id: int, name: str, version: int, meta: dict[str, Any], shas: Mapping[str, str],
                 store: BlobStore) -> None:  # fmt: skip
        self.id, self.name, self.version, self.meta = int(id), str(name), int(version), meta
        self.grid: GridV2 = bmeta.grid_of(meta)
        self.rows, self.parents = int(meta["rows"]), int(meta["parents"])
        self.sub, self.full = bool(meta["sub"]), bool(meta["full"])
        self.layout: str = bmeta.layout_of(meta)
        self.listed = self.layout == LAYOUT_LIST  # a list of blocks (module docstring)
        self.fields: list[dict[str, Any]] = meta["fields"]
        self.store = store
        self._shas = dict(shas)
        self.reader = Reader(store, self._shas, self.fields, self.rows, self.sub, self.listed)

    @classmethod
    def open(cls, conn: sa.Connection, store: BlobStore, model_id: int, layout: Sequence[str] = (LAYOUT_GRID,),
             what: str | None = None) -> Model:  # fmt: skip
        """The model `model_id` (meta v1, v2 or v3): its row of `objects` and the SHA-256 of every file of it. A list
        of blocks is opened only by a caller that says `layout=ANY`; otherwise 422 `block_list_unsupported` (`what` —
        the name of the operation for the message)."""
        row = conn.execute(
            sa.select(objects.c.name, objects.c.version, objects.c.meta).where(
                objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready"
            )
        ).first()
        if row is None:
            raise not_found()
        meta = bmeta.upgrade(row.meta)
        if bmeta.layout_of(meta) not in layout:
            raise list_unsupported(row.name, what)
        shas = {role: cells_sha(conn, model_id, role) for role in bmeta.files_of(meta)}
        return cls(model_id, row.name, int(row.version), meta, shas, store)

    def require_grid(self, what: str | None = None) -> None:
        """422 `block_list_unsupported` when the model is a list of blocks (an operation opened with ANY that does
        not read boxes in one of its parts)."""
        if self.listed:
            raise list_unsupported(self.name, what)

    # --- the fields ---------------------------------------------------------------------------------------

    def field(self, name: str) -> dict[str, Any]:
        """The field of that name (case aside); 404 `block_field_not_found`."""
        return dict(self.reader.spec(name))

    def labels(self, name: str) -> list[str]:
        return [str(x) for x in self.field(name).get("labels") or []]

    def role_field(self, role: str) -> dict[str, Any] | None:
        """The field with that role (`frac`, `density`, `grade`, `code`, `class`) — the first, or None."""
        return next((dict(f) for f in self.fields if f.get("role") == role), None)

    def shas(self) -> dict[str, str]:
        """{file role: SHA-256}: `cells` and the files `fld:`."""
        return dict(self._shas)

    # --- reading ------------------------------------------------------------------------------------------

    def p(self, start: int = 0, stop: int | None = None) -> np.ndarray:
        """P of rows [start, stop): uint64."""
        out: np.ndarray = self.reader.fill(p=True, start=start, stop=stop)[P]
        return out

    def geo(self, start: int = 0, stop: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """A list of blocks: (centres, sizes) float64 [n, 3] of rows [start, stop), local axes."""
        if not self.listed:
            raise RuntimeError("only a list of blocks has the columns cx … dz")
        got = self.reader.fill(geo=True, start=start, stop=stop)
        return got[LC], got[LD]

    def subs(self, start: int = 0, stop: int | None = None) -> tuple[np.ndarray, np.ndarray] | None:
        """(s, w) uint8 [n, 3] of rows [start, stop); None — a model without sub-blocks (s = 0, w = div)."""
        if not self.sub:
            return None
        got = self.reader.fill(sub=True, start=start, stop=stop)
        return got[S], got[W]

    def columns(self, names: Sequence[str], start: int = 0, stop: int | None = None) -> dict[str, np.ndarray]:
        """The fields `names` of rows [start, stop) in the memory form: `{stored name: array}`."""
        got = self.reader.fill(names, start=start, stop=stop)
        return {k: v for k, v in got.items() if not k.startswith("\0")}

    def iter(self, names: Sequence[str] = (), chunk: int = CHUNK) -> Iterator[Chunk]:
        """The rows by portions of `chunk` rows (the last is shorter), in the order of the file: P, s, w and the
        fields `names`. A portion is never bigger than `chunk`; what is kept besides it is one row group."""
        if int(chunk) < 1:
            raise ValueError("chunk must be at least 1")
        chunk = int(chunk)
        pend: list[tuple[np.ndarray, np.ndarray | None, np.ndarray | None, dict[str, np.ndarray]]] = []
        have, first = 0, 0
        listed = self.listed  # `ss`, `ww` of a piece are then the centres and the sizes of a list of blocks

        def merged() -> Chunk:
            if len(pend) == 1:
                pp, ss, ww, cc = pend[0]
            else:
                pp = np.concatenate([x[0] for x in pend])
                ss = None if pend[0][1] is None else np.concatenate([x[1] for x in pend])  # type: ignore[misc]
                ww = None if pend[0][2] is None else np.concatenate([x[2] for x in pend])  # type: ignore[misc]
                cc = {k: np.concatenate([x[3][k] for x in pend]) for k in pend[0][3]}
            return Chunk(first, pp, None, None, cc, ss, ww) if listed else Chunk(first, pp, ss, ww, cc)

        for _off, m, pp, ss, ww, cols in self.reader.pieces(names, p=True, sub=True, geo=True):
            assert pp is not None
            pos = 0
            while pos < m:
                take = min(chunk - have, m - pos)
                cut = slice(pos, pos + take)
                pend.append((pp[cut], None if ss is None else ss[cut], None if ww is None else ww[cut],
                             {k: v[cut] for k, v in cols.items()}))  # fmt: skip
                have += take
                pos += take
                if have == chunk:
                    out = merged()
                    first += have
                    pend, have = [], 0
                    yield out
        if have:
            yield merged()

    # --- geometry (blocks/geometry.py) ----------------------------------------------------------------------

    def _rows(self, rows: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        p = self.p()
        sw = self.subs()
        s, w = (None, None) if sw is None else sw
        if rows is None:
            return p, s, w
        r = np.asarray(rows, dtype=np.intp)
        return p[r], None if s is None else s[r], None if w is None else w[r]

    def _geo(self, rows: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
        lc, ld = self.geo()
        if rows is None:
            return lc, ld
        r = np.asarray(rows, dtype=np.intp)
        return lc[r], ld[r]

    def centres(self, rows: np.ndarray | None = None) -> np.ndarray:
        """Centres of the rows (all, or the indices `rows`) in the axes of the project: float64 [n, 3]."""
        if self.listed:
            return self.grid.to_project(self._geo(rows)[0])
        return geometry.centres(self.grid, *self._rows(rows))

    def local(self, rows: np.ndarray | None = None) -> np.ndarray:
        """Centres in the local axes of the model (before the turn and the corner): float64 [n, 3]."""
        if self.listed:
            return self._geo(rows)[0]
        return geometry.row_local_centres(self.grid, *self._rows(rows))

    def boxes(self, rows: np.ndarray | None = None) -> np.ndarray:
        """(lo, hi) of the rows in the local axes: float64 [n, 6]."""
        if self.listed:
            lc, ld = self._geo(rows)
            return np.concatenate([lc - ld / 2, lc + ld / 2], axis=1)
        return geometry.row_boxes(self.grid, *self._rows(rows))

    def volume(self, rows: np.ndarray | None = None) -> np.ndarray:
        """Volumes of the rows: Π(w·d/div)·FRAC when the model has a FRAC field (NaN FRAC — NaN volume, not 0)."""
        if self.listed:
            frac = self.role_field("frac")
            _lc, ld = self._geo(rows)
            vol = (ld[:, 0] * ld[:, 1]) * ld[:, 2]
            if frac is None:
                return vol
            vals = self.columns([str(frac["name"])])[str(frac["name"])]
            return vol * (vals if rows is None else vals[np.asarray(rows, dtype=np.intp)])
        p, s, w = self._rows(rows)
        frac = self.role_field("frac")
        values = None
        if frac is not None:
            values = self.columns([str(frac["name"])])[str(frac["name"])]
            if rows is not None:
                values = values[np.asarray(rows, dtype=np.intp)]
        return geometry.row_volumes(self.grid, p, s, w, values)
