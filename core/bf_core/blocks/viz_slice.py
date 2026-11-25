"""The slice, the point and the polygons of the display of a block model (подшаг 17.27; ADR 0023 decisions 18–19;
e17-api §12). Routes — `viz_routes.py`; the columns, the restrictor, the overtaking — `viz_data.py`.

    slice_bytes(project, id, spec, tok)  → BFA1 `blocks_slice` (the raster of `slice.Slicer` over the cached columns)
    at_answer(project, id, body)         → the JSON of `pick.describe` (the row under a point, the values, f)
    polygons_answer(project, id, body, tok) → {polygons, count, area, truncated} (`slice_poly.Sectioner`)

The header of `blocks_slice` is `Raster.header()` with the model, its version, the field and the labels; the key
`kind` of the contract is taken twice there ("blocks_slice" and the kind of the field), so the second one is
`field_kind` (number | category | null). The rows are read from the cache of columns by pieces of `CHUNK` rows
in the order of the file; for a plane ⟂ Z only the rows of one parent layer are looked at (P ascending: two
bisections of `idx`, as `Slicer.p_range` says), and the restrictor is counted only for them.

The point. With the column `idx` in the cache (the shell of the model was shown, or any swath) the row is found by
bisection in the cache and the values come from the cached columns, the rest of the fields of the row — from one
row group of the file (`pick.fetch_row`); without it the row group of P is read (`pick.find_in_model`, 40–60 ms).
A point in no row — `{found: false}`; `f` and `passed` are those of the restrictor (no restrictor — 1.0, true).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from bf_core.stats import blocks_cache as bc
from bf_core.viz import binary

from . import geometry, pick
from . import schema as bs
from .model import CHUNK, Chunk, Model
from .slice import NUMBER, Plane, Slicer, plane_of
from .slice_poly import Sectioner
from .viz_data import Rows, cache_model, column_key, field_spec, open_model, subs
from .viz_spec import Common, Token, bad, body_of, heavy, parse_common

MAX_PX = 4096


@dataclass(frozen=True)
class SliceSpec:
    common: Common
    origin: Any
    normal: Any
    field: str | None
    hidden: tuple[int, ...]
    max_px: int
    limit: int


def _ints(raw: Any, param: str) -> tuple[int, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(h, int) and not isinstance(h, bool) for h in raw):
        raise bad(param, "Скрытые категории: список кодов.")
    return tuple(sorted(set(raw)))


def parse_slice(raw: Any) -> SliceSpec:
    body = body_of(raw)
    plane = body.get("plane")
    if not isinstance(plane, dict):
        raise bad("plane", "Плоскость среза: нужен объект {origin, normal}.", "Задайте начало и нормаль тремя числами.")
    field = body.get("field")
    if field is not None and (not isinstance(field, str) or not field.strip()):
        raise bad("field", "Поле среза: нужно имя поля.")
    max_px = body.get("max_px", MAX_PX)
    if isinstance(max_px, bool) or not isinstance(max_px, int):
        raise bad("max_px", "Размер растра среза: целое число от 64 до 4096.")
    limit = body.get("limit", 200_000)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise bad("limit", "Число многоугольников: целое число.")
    name = None if field is None else field.strip()
    return SliceSpec(
        parse_common(body),
        plane.get("origin"),
        plane.get("normal"),
        name,
        _ints(body.get("hidden"), "hidden"),
        max_px,
        limit,
    )


def _bisect(idx: np.ndarray, value: int, side: Literal["left", "right"]) -> int:
    """`np.searchsorted` of a whole number in `idx` without turning the array into another type."""
    top = int(np.iinfo(idx.dtype).max)
    if value > top:
        return int(idx.shape[0])
    return int(np.searchsorted(idx, idx.dtype.type(value), side=side))


def _layer(rows: Rows, lo_p: int, hi_p: int) -> tuple[int, int]:
    return _bisect(rows.idx, lo_p, "left"), _bisect(rows.idx, hi_p, "left")


def _field_of(model: Model, name: str | None) -> tuple[str | None, str | None, list[str] | None]:
    """(stored name, kind of the raster, labels) of the field of a slice."""
    if name is None:
        return None, None, None
    spec = field_spec(model, name, "field")
    if spec["kind"] == bs.CATEGORY:
        return str(spec["name"]), bs.CATEGORY, model.labels(str(spec["name"]))
    return str(spec["name"]), NUMBER, None


# --- the slice --------------------------------------------------------------------------------------------------------


def slice_bytes(project: Any, model_id: int, spec: SliceSpec, tok: Token) -> bytes:
    model, r, _tree = open_model(project, model_id, spec.common)
    name, kind, labels = _field_of(model, spec.field)
    plane = plane_of(model.grid, spec.origin, spec.normal)
    with heavy(tok):
        rows = Rows(project, model, r, [] if name is None else [name])
        slicer = Slicer(model.grid, plane, sub=model.sub, max_px=spec.max_px, kind=kind, hidden=spec.hidden)
        lo, hi = 0, model.rows
        rng = slicer.p_range()
        if rng is not None:
            lo, hi = _layer(rows, *rng)
        values = None if name is None else rows.values(name)
        filtered = bool(r.fields) or r._fn is not None
        for a in range(lo, hi, CHUNK):
            tok.check()
            b = min(a + CHUNK, hi)
            ch = rows.chunk(a, b, ())
            slicer.add(ch.p, ch.s, ch.w, rows.passed(a, b) if filtered else None,
                       None if values is None else values[a:b])  # fmt: skip
        raster = slicer.finish()
    head = {"kind": "blocks_slice", "format": 1, "model": model.id, "version": model.version,
            **{k: v for k, v in raster.header().items() if k != "kind"}, "field": name, "field_kind": raster.kind,
            "labels": labels, "warnings": list(r.warnings)}  # fmt: skip
    return binary.encode(head, [("value", raster.value, "<f4"), ("m", raster.m, "<u1")])


# --- the point --------------------------------------------------------------------------------------------------------


def _hit_cached(model: Model, idx: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, pt: np.ndarray) -> Any:
    """The row that holds the point (project axes, already moved inside) in the cached columns, as `pick.row_at`
    but without turning `idx` into another type; None — in no row."""
    grid = model.grid
    valid, qp, qs = geometry.locate(grid, pt[None, :])
    if not bool(valid[0]):
        return None
    lo, hi = _bisect(idx, int(qp[0]), "left"), _bisect(idx, int(qp[0]), "right")
    if hi <= lo:
        return None
    row = lo
    if s is not None and w is not None:
        at = qs[0].astype(np.int64)
        s0, w0 = s[lo:hi].astype(np.int64), w[lo:hi].astype(np.int64)
        inside = ((at >= s0) & (at < s0 + w0)).all(axis=1)
        if not bool(inside.any()):
            return None
        row = lo + int(np.argmax(inside))
    return pick._hit(grid, idx, s, w, row, row, pt, model.sub)


def parse_at(raw: Any) -> tuple[Common, Any, Any, list[str] | None]:
    body = body_of(raw)
    fields = body.get("fields")
    if fields is not None and (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)):
        raise bad("fields", "Поля блока: список имён или null (все поля).")
    if body.get("point") is None:
        raise pick.bad_point()
    return parse_common(body), body["point"], body.get("normal"), fields


def at_answer(project: Any, model_id: int, raw: Any) -> dict[str, Any]:
    common, point, normal, fields = parse_at(raw)
    model, r, _tree = open_model(project, model_id, common)
    names = pick.fields_of(model, fields)
    cm = cache_model(model)
    idx = bc.MODELS.peek(column_key(project, cm, bc.IDX))
    if idx is None:
        hit = pick.find_in_model(model, point, normal)
    else:
        s, w = subs(project, model)
        hit = _hit_cached(model, idx, s, w, pick.shifted(model.grid, point, normal, sub=model.sub))
    if hit is None:
        return {"found": False}
    need = list(dict.fromkeys([*r.fields, *names]))
    have: dict[str, np.ndarray] = {}
    for n in need:
        col = bc.MODELS.peek(column_key(project, cm, n))
        if col is not None:
            have[n] = np.asarray(col[hit.row : hit.row + 1])
    missing = [n for n in need if n not in have]
    if missing:
        have.update(pick.fetch_row(model, hit, missing).cols)
    s1 = w1 = None
    if hit.sub is not None:
        s1, w1 = np.array([hit.sub[:3]], dtype=np.uint8), np.array([hit.sub[3:]], dtype=np.uint8)
    p1 = np.array([hit.p], dtype=np.uint64)
    f = None
    if r.fields or r._fn is not None:
        f = float(r.weights(Chunk(hit.row, p1, s1, w1, {n: have[n] for n in r.fields}))[0])
    return pick.describe(model, hit, Chunk(hit.row, p1, s1, w1, {n: have[n] for n in names}), f)


# --- the polygons -----------------------------------------------------------------------------------------------------


def polygons_answer(project: Any, model_id: int, spec: SliceSpec, tok: Token) -> dict[str, Any]:
    model, r, _tree = open_model(project, model_id, spec.common)
    name, _kind, _labels = _field_of(model, spec.field)
    plane: Plane = plane_of(model.grid, spec.origin, spec.normal)
    sec = Sectioner(model.grid, plane, sub=model.sub, limit=spec.limit)
    with heavy(tok):
        rows = Rows(project, model, r, [] if name is None else [name])
        lo, hi = 0, model.rows
        if sec._empty:
            hi = 0
        elif plane.axis == 2 and sec._pa is not None:
            layer = model.grid.counts[0] * model.grid.counts[1]
            lo, hi = _layer(rows, sec._pa * layer, (sec._pa + 1) * layer)
        filtered = bool(r.fields) or r._fn is not None
        for a in range(lo, hi, CHUNK):
            tok.check()
            b = min(a + CHUNK, hi)
            ch = rows.chunk(a, b, ())
            sec.add(ch.p, ch.s, ch.w, rows.passed(a, b) if filtered else None, first=a)
        poly = sec.finish()
        col = None if name is None else rows.values(name)
    field = None if name is None else model.field(name)
    out: list[dict[str, Any]] = []
    for i in range(len(poly)):
        row = int(poly.row[i])
        value = None if col is None or field is None else pick.value_of(field, col[row])
        out.append({"row": row, "value": value, "ring": poly.vertices(i).tolist()})
    return {"polygons": out, "count": int(poly.count), "area": float(poly.total), "truncated": bool(poly.truncated),
            "warnings": list(r.warnings)}  # fmt: skip
