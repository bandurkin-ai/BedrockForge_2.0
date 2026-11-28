"""What the display of a block model reads and keeps (подшаг 17.27; ADR 0023 §11, decisions 16–19; e17-api §12).
The routes are in `viz_routes.py`, the slice, the point and the polygons in `viz_slice.py`.

    data = shell_bytes(project, model_id, spec, token)     # BFA1 `blocks`; cached, overtaken, in a HEAVY slot

The columns. `stats/blocks_cache.MODELS` holds the columns of the model in the memory of the API process (LRU by
bytes; a model of a million rows or more also leaves them as `.npy` in `cache/blocks`): `idx` (P, uint32 below 2³²
cells), every field a request reads, and here — `s`, `w` (uint8 n×3) of a model with sub-blocks, under the keys
`(project, model, SHA of cells, "\\0s" / "\\0w")` of the same cache. A request reads only what it needs: the fields of
the restrictor and the field of the colour. The restrictor (`restrict.prepare`) works on pieces of the cached
columns, the share f > 0 passes; the colour class of a row comes from the field (`shell_merge.scale_class`,
`break_class`, a category code, 0xFFFF — no value), `lod.view` makes the shell, LOD above the limit and the merge.

The answer. `origin` is a whole number of metres — the rounded centre of the local extent of the faces (the zero of
`c`: float32 from it keeps millimetres at kilometres, CLAUDE.md); `c`, `h` `<f4`, `a` `<u1`, `k` `<u2` in the order
of the shell (a, plane, k, v, u). `classes` — 256 buckets, the number of classes of the breaks, the number of
categories (labels or the highest code + 1), 0; `bins` — `scale_bins` / `break_bins`, null for none / categories;
`labels` — the labels of the categories. `warnings` — the Russian lines of the leaves of the restrictor (a surface
that does not cover the model …), an empty list when there are none. The answer of a request is kept (the last few,
≤ 192 МБ; the key holds the SHA-256 of every file of the model, its version and the whole request without
`channel`/`seq`) unless the restrictor names a wireframe or a surface: a changed object must not give the old picture.

The request, its errors and the overtaking of requests (409 `superseded`) — `viz_spec.py`.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.stats import blocks_cache as bc
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary

from . import restrict
from . import schema as bs
from .filters import schema as fs
from .lod import View, view
from .model import ANY, CHUNK, Chunk, Model, field_not_found
from .model import P as COL_P
from .model import S as COL_S
from .model import W as COL_W
from .shell_merge import NO_CLASS, break_bins, break_class, scale_bins, scale_class
from .viz_spec import Colour, Common, Token, ViewSpec, bad, heavy

CACHE_BYTES = 192 * 1024 * 1024


def field_spec(model: Model, name: str, param: str) -> dict[str, Any]:
    """The field of that name (case aside); 404 `block_field_not_found {param}`."""
    try:
        return model.field(name)
    except ApiError as e:
        if e.code != "block_field_not_found":
            raise
        raise field_not_found(name, param) from e


# --- the model, the columns -------------------------------------------------------------------------------------------


def open_model(
    project: Any, model_id: int, common: Common, layout: Sequence[str] = ("grid",)
) -> tuple[Model, restrict.Restrictor, dict[str, Any] | None]:
    """The model, the restrictor of the filter and the quick restrictor together, and their tree. A list of blocks is
    opened by the shell and the block under a point only (`layout=ANY`): the slice and the polygons need the grid."""
    from .filters import store as filters

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, store, model_id, layout, "Срез" if layout == ("grid",) else None)
        tree = restrict.combine(None if common.filter_id is None else filters.rules_of(conn, common.filter_id),
                                common.restrict)  # fmt: skip
        r = restrict.prepare(model, tree, conn=conn, store=store)
    return model, r, tree


def cache_model(model: Model) -> bc.Model:
    shas = model.shas()
    return bc.Model(model.id, model.name, {"counts": list(model.grid.counts)}, list(model.fields), shas[bs.ROLE],
                    model.rows, shas)  # fmt: skip


def column_key(project: Any, cm: bc.Model, name: str) -> tuple[Any, ...]:
    """The key of a column in `blocks_cache.MODELS` (the form `blocks_cache.columns` uses)."""
    head = (project.uid, cm.id, cm.sha)
    file = bc.CELLS if name == bc.IDX else cm.file_of(name)
    return (*head, name) if file == bc.CELLS else (*head, name, cm.shas.get(file, file))


class Rows:
    """The cached columns of a model for one request: `idx`, `s`, `w`, the fields asked for; pieces of them as the
    `Chunk`s of the restrictor."""

    def __init__(self, project: Any, model: Model, r: restrict.Restrictor, names: Sequence[str] = ()) -> None:
        self.model, self.r = model, r
        want = list(dict.fromkeys([*r.fields, *model.reader.canonical(names)]))
        cm = cache_model(model)
        self.s, self.w = prime(project, model, cm, want)
        self.cols = bc.columns(project, cm, want)
        self.idx = self.cols.idx

    def values(self, name: str) -> np.ndarray:
        """The whole column of a field in the memory form: float64 (NaN — empty) or int32 (−1 — empty)."""
        return self.cols.category[name] if name in self.cols.category else self.cols.numbers[name]

    def chunk(self, lo: int, hi: int, names: Sequence[str]) -> Chunk:
        sl = slice(lo, hi)
        return Chunk(lo, self.idx[sl].astype(np.uint64, copy=False), None if self.s is None else self.s[sl],
                     None if self.w is None else self.w[sl], {n: self.values(n)[sl] for n in names})  # fmt: skip

    def passed(self, lo: int, hi: int) -> np.ndarray:
        """bool [hi − lo]: the blocks that pass the restrictor (f > 0)."""
        if not self.r.fields and self.r._fn is None:
            return np.ones(hi - lo, dtype=np.bool_)
        return self.r.mask(self.chunk(lo, hi, self.r.fields))  # type: ignore[no-any-return]

    def passed_all(self, tok: Token, box: Sequence[float] | None = None) -> np.ndarray:
        """The mask of all the rows; `box` — project (xmin, ymin, zmin, xmax, ymax, zmax): the centres inside only."""
        from . import geometry

        n = self.model.rows
        out = np.empty(n, dtype=np.bool_)
        for lo in range(0, n, CHUNK):
            tok.check()
            hi = min(lo + CHUNK, n)
            out[lo:hi] = self.passed(lo, hi)
            if box is not None and out[lo:hi].any():
                ch = self.chunk(lo, hi, ())
                out[lo:hi] &= geometry.centres_in_box(self.model.grid, ch.p, ch.s, ch.w, list(box))
        return out


def _dtype_of(cm: bc.Model, name: str) -> Any:
    if name == bc.IDX:
        return np.uint32 if bs.cell_count(cm.grid) < bc.U32_CELLS else np.uint64
    f = cm.field(name)
    return np.int32 if f is not None and f["kind"] == bs.CATEGORY else np.float64


def _take(project: Any, cm: bc.Model, name: str, dtype: Any, shape: tuple[int, ...], disk: bool) -> Any:
    """The column from the memory cache, else from the disk cache (then it goes to the memory too); None — neither."""
    key = column_key(project, cm, name)
    hit = bc.MODELS.peek(key)
    if hit is not None:
        return hit
    if not disk:
        return None
    flat = bc._disk_load(project, cm.shas.get(cm.file_of(name), cm.sha), name, int(np.prod(shape)), dtype)
    if flat is None:
        return None
    a = flat.reshape(shape)
    a.flags.writeable = False
    bc.MODELS.put(key, a)
    with bc.MODELS._lock:
        bc.MODELS.loads += 1
    return a


def _keep(project: Any, cm: bc.Model, name: str, value: np.ndarray, disk: bool) -> None:
    value.flags.writeable = False
    bc.MODELS.put(column_key(project, cm, name), value)
    if disk:
        bc._disk_save(project, cm.shas.get(cm.file_of(name), cm.sha), name, value.reshape(-1))


def prime(
    project: Any, model: Model, cm: bc.Model, names: Sequence[str]
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(s, w) of a model with sub-blocks (None, None — without). What the cache lacks of `idx`, the fields `names`,
    `s` and `w` is read in ONE pass over the file and put into the cache (`blocks_cache.columns` then finds the rest
    there: a model with sub-blocks is read once, not twice); `s` and `w` also go to the disk cache like the others."""
    if not model.sub:
        return None, None
    n = model.rows
    disk = n >= bc.DISK_MIN_ROWS
    with bc.MODELS.gate((project.uid, model.id, cm.sha)):
        s = _take(project, cm, "\0s", np.uint8, (n, 3), disk)
        w = _take(project, cm, "\0w", np.uint8, (n, 3), disk)
        if s is not None and w is not None:
            return s, w
        idx = _take(project, cm, bc.IDX, _dtype_of(cm, bc.IDX), (n,), disk)
        miss = [f for f in names if _take(project, cm, f, _dtype_of(cm, f), (n,), disk) is None]
        with bc.MODELS._lock:
            bc.MODELS.reads += 1
            bc.MODELS.misses += len(miss) + 2 + (idx is None)
        got = model.reader.fill(miss, p=idx is None, sub=True, p_dtype=_dtype_of(cm, bc.IDX))
        s, w = got[COL_S], got[COL_W]
        _keep(project, cm, "\0s", s, disk)
        _keep(project, cm, "\0w", w, disk)
        if idx is None:
            _keep(project, cm, bc.IDX, got[COL_P], disk)
        for f in miss:
            _keep(project, cm, f, got[f], disk)
    return s, w


def subs(project: Any, model: Model) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(s, w) of a model with sub-blocks from the cache of columns (read once, then kept); (None, None) — without."""
    return prime(project, model, cache_model(model), [])


# --- the colour -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Classes:
    """What the colour makes of the rows: the class of every row and the header of the legend."""

    klass: np.ndarray | None
    values: np.ndarray | None
    klass_of: Callable[[np.ndarray], Any] | None
    kind: str  # of the LOD: mean | mode
    colour: dict[str, Any]
    classes: int
    bins: Sequence[Sequence[float | None]] | None
    labels: list[str] | None
    field: str | None
    hidden: tuple[int, ...] = ()


def _mapped(fn: Callable[[np.ndarray], Any], values: np.ndarray, tok: Token) -> np.ndarray:
    out = np.empty(values.shape[0], dtype=np.uint16)
    for lo in range(0, values.shape[0], CHUNK):
        tok.check()
        out[lo : lo + CHUNK] = fn(values[lo : lo + CHUNK])
    return out


def scale_range(c: Colour, values: np.ndarray, spec: Mapping[str, Any]) -> tuple[float, float]:
    """[from; to] of the scale: as given, else the min and max of the field (its `stats`, else the column)."""
    stats = spec.get("stats") or {}
    lo, hi = c.lo, c.hi
    if lo is None:
        lo = stats.get("min")
    if hi is None:
        hi = stats.get("max")
    if lo is None or hi is None:
        ok = values[np.isfinite(values)]
        if not ok.size:
            raise bad("colour.field", f"В поле «{spec['name']}» нет ни одного значения.", "Выберите другое поле.")
        lo = float(ok.min()) if lo is None else lo
        hi = float(ok.max()) if hi is None else hi
    lo, hi = float(lo), float(hi)
    if c.log and lo <= 0:
        pos = values[values > 0]  # NaN > 0 is false
        if not pos.size:
            raise bad("colour.field", f"В поле «{spec['name']}» нет положительных значений для логарифмической шкалы.",
                      "Выключите логарифмическую шкалу.")  # fmt: skip
        lo = float(pos.min())
    if hi <= lo:
        if c.lo is not None and c.hi is not None:
            raise bad("colour.to", "Граница шкалы «до» должна быть больше границы «от».")
        hi = lo + 1.0  # a constant field: one bucket
    return lo, hi


def classes_of(model: Model, rows: Rows, c: Colour, passed: np.ndarray, tok: Token) -> Classes:
    """The classes of the rows for the colour `c` (the field is checked against the model: 404 / 422). A category
    hidden in `c.hidden` drops out of `passed` (changed in place)."""
    if c.kind == "none":
        return Classes(None, None, None, "mean", {"kind": "none"}, 0, None, None, None)
    spec = field_spec(model, str(c.field), "colour.field")
    name = str(spec["name"])
    is_cat = spec["kind"] == bs.CATEGORY
    if c.kind == "categories":
        if not is_cat:
            raise bad("colour.kind", f"Поле «{name}» — число: раскрасьте шкалой или градациями.",
                      "Выберите «Шкала» или «Градации».")  # fmt: skip
        codes = rows.values(name)
        for h in c.hidden:
            passed &= codes != h
        labels = model.labels(name)
        top = int(codes.max()) + 1 if codes.size else 0
        klass = np.where(codes < 0, NO_CLASS, codes).astype(np.uint16)
        return Classes(klass, None, None, "mode", {"kind": "categories", "hidden": list(c.hidden)},
                       max(len(labels), top), None, labels, name, c.hidden)  # fmt: skip
    if is_cat:
        raise bad("colour.kind", f"Поле «{name}» — категория: раскрасьте категориями.", "Выберите «Категории».")
    values = rows.values(name)
    if c.kind == "classes":
        brk = list(c.breaks)

        def by_breaks(v: np.ndarray) -> Any:
            return break_class(v, brk)

        return Classes(_mapped(by_breaks, values, tok), values, by_breaks, "mean",
                       {"kind": "classes", "breaks": brk}, len(brk) + 1, break_bins(brk), None, name)  # fmt: skip
    lo, hi = scale_range(c, values, spec)
    log = c.log

    def fn(v: np.ndarray) -> Any:
        return scale_class(v, lo, hi, log=log)

    return Classes(_mapped(fn, values, tok), values, fn, "mean", {"kind": "scale", "from": lo, "to": hi, "log": log},
                   256, scale_bins(lo, hi, log=log), None, name)  # fmt: skip


# --- the answer -------------------------------------------------------------------------------------------------------


def local_extent(f: Any) -> tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of the faces in the local axes (the centre of a face is on its plane, ±h along u and v)."""
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for a in range(3):
        sel = (f.a >> 1) == a
        if not sel.any():
            continue
        u, v = (a + 1) % 3, (a + 2) % 3
        for ax, size in ((a, np.zeros(int(sel.sum()))), (u, f.h[sel, 0]), (v, f.h[sel, 1])):
            lo[ax] = min(lo[ax], float((f.c[sel, ax] - size).min()))
            hi[ax] = max(hi[ax], float((f.c[sel, ax] + size).max()))
    return lo, hi


def encode_blocks(model: Model, v: View, spec: ViewSpec, cl: Classes, passed: int, warnings: list[str]) -> bytes:
    f = v.faces
    origin = np.zeros(3)
    if len(f):
        lo, hi = getattr(v, "extent", None) or local_extent(f)  # a list of blocks gives its own (list_shell.ListView)
        origin = np.round((lo + hi) / 2)
    g = model.grid
    head = {"kind": "blocks", "format": 1, "model": model.id, "version": model.version, "mode": spec.mode,
            "corner": list(g.corner), "rotation": g.rotation, "origin": origin.tolist(), "faces": len(f),
            "faces_raw": v.faces_raw, "simplified": v.simplified, "lod": v.lod, "rows": model.rows, "passed": passed,
            "empty": model.rows - passed, "field": cl.field, "colour": cl.colour, "classes": cl.classes,
            "bins": cl.bins, "labels": cl.labels, "warnings": warnings}  # fmt: skip
    arrays = [("c", f.c - origin, "<f4"), ("h", f.h, "<f4"), ("a", f.a, "<u1"), ("k", f.k, "<u2")]
    return binary.encode(head, arrays)


class ResponseCache:
    """The last few answers by bytes (module docstring)."""

    def __init__(self, limit: int = CACHE_BYTES) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self.size = 0
        self.hits = 0

    def get(self, key: str) -> bytes | None:
        with self._lock:
            got = self._items.get(key)
            if got is not None:
                self._items.move_to_end(key)
                self.hits += 1
            return got

    def put(self, key: str, data: bytes) -> None:
        if len(data) > self.limit // 2:
            return
        with self._lock:
            if key in self._items:
                self.size -= len(self._items.pop(key))
            self._items[key] = data
            self.size += len(data)
            while self.size > self.limit:
                self.size -= len(self._items.popitem(last=False)[1])

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.size = 0


RESPONSES = ResponseCache()


def response_key(project: Any, model: Model, tree: Mapping[str, Any] | None, spec: ViewSpec) -> str | None:
    """None — the answer is not kept (the restrictor names a wireframe or a surface: the object may change)."""
    if tree is not None and fs.object_ids(fs.normalize_rules(tree)):
        return None
    text = json.dumps({"project": project.uid, "model": model.id, "version": model.version, "files": model.shas(),
                       "tree": tree, "colour": spec.colour.__dict__, "mode": spec.mode, "extent": spec.extent,
                       "limit": spec.limit}, sort_keys=True, ensure_ascii=False, default=list)  # fmt: skip
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def threads() -> int:
    from bf_core.composites import strips

    return strips.default_threads(1)  # one request at a time is the usual case: all the cores


def shell_bytes(project: Any, model_id: int, spec: ViewSpec, tok: Token) -> bytes:
    """BFA1 `blocks` of the request (module docstring)."""
    model, r, tree = open_model(project, model_id, spec.common, ANY)
    if spec.colour.kind != "none":
        field_spec(model, str(spec.colour.field), "colour.field")  # 404 block_field_not_found before the slot
    key = response_key(project, model, tree, spec)
    if key is not None:
        hit = RESPONSES.get(key)
        if hit is not None:
            return hit
    with heavy(tok):
        names = [str(spec.colour.field)] if spec.colour.kind != "none" else []
        rows: Rows
        if model.listed:  # no grid of parents: the boxes (list_shell.py)
            from . import list_shell as ls

            rows = ls.ListRows(project, model, r, names)
        else:
            rows = Rows(project, model, r, names)
        tok.check()
        passed = rows.passed_all(tok, spec.extent)
        cl = classes_of(model, rows, spec.colour, passed, tok)
        if model.listed:
            v = ls.view(model.grid, rows.idx, rows.lc, rows.ld, passed, cl.klass, limit=spec.limit,  # type: ignore[attr-defined]
                        shell=spec.mode == "shell", kind=cl.kind, values=cl.values, klass_of=cl.klass_of,
                        threads=threads(), tick=tok.tick)  # fmt: skip
        else:
            v = view(model.grid, rows.idx, rows.s, rows.w, passed, cl.klass, limit=spec.limit,
                     shell=spec.mode == "shell", kind=cl.kind, values=cl.values, klass_of=cl.klass_of,
                     threads=threads(), tick=tok.tick)  # fmt: skip
        out = encode_blocks(model, v, spec, cl, int(np.count_nonzero(passed)), list(r.warnings))
    if key is not None:
        RESPONSES.put(key, out)
    return out


def warm() -> None:
    """Compiles (or loads from the cache) the kernels of the shell, the merge and the LOD on a few blocks."""
    from .schema import GridV2

    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [1.0, 1.0, 1.0], "div": [2, 2, 2],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    p = np.arange(8, dtype=np.uint64)
    s, w = np.zeros((8, 3), dtype=np.uint8), np.full((8, 3), 2, dtype=np.uint8)
    s.setflags(write=False)  # the columns of the cache of a model are read-only: another specialization of the kernels
    w.setflags(write=False)
    ok = np.ones(8, dtype=np.bool_)
    k = scale_class(np.arange(8.0), 0.0, 8.0)
    for shell in (True, False):
        view(grid, p, s, w, ok, k, shell=shell)
        view(grid, p, s, w, ok, k, limit=1, shell=shell, values=np.arange(8.0),
             klass_of=lambda v: scale_class(v, 0.0, 8.0))  # fmt: skip
    view(grid, p, None, None, ok, k, limit=1, kind="mode")
