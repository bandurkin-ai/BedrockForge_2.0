"""The jump from a cell of a resource report to its blocks (подшаг 20.9; ТЗ 7.35 Д-36, ТД36-1; ADR 0027 решения
22–24, 30; docs/progress/e20-api.md §8).

    a = answer(conn, store, parse_drill({...}))                   # {rules, cell {blocks, volume, tonnes}, stale, …}
    p = rows_answer(conn, store, parse_drill_rows({...}))         # the same + {page, size, total, rows, csv}

A cell is «group × cut-off» of a stored report: `group` — the labels of the keys («(пусто)» — no value, as in the
table; None — the total), the cut-off and the kind of the row (`all`, `cutoff`, `balance`, `offbalance`). The tree of
the rules is a function of the parameters of the report and of the cell (the same inputs — the same tree):

    AND( the restrictor of the report (the filter and the quick one),
         for each key of the group: `cond` «field = label» / «(пусто)» → `empty`; a bench → `slab` z by `share` over
         [Z0 + j·H; Z0 + (j+1)·H] (the bench «(пусто)» — NOT the slab over all the benches),
         the cut-off: `expr` «g_б >= c» (balance: «g_б >= c_б», off-balance: «c_з <= g_б < c_б»; «без борта» — none),
         what the pass leaves out: «ρ >= 0» if blocks of the cell have no density, «FRAC не пусто» if they have no
         volume (the report does not count them, so the filter must not show them) )

The cell is counted again by the kernel of the report (`compute.run` with the restrictor of THIS tree: Σ V·FRAC·f and
Σ t·f): the filter of 3D shows the blocks with f > 0 and the table lists the same blocks, so the numbers of the cell
are the ones of the set (ТД36-1, 1e−12). Only a block cut by the edge of a bench by less than 1e−12 of its height is
counted here by `slab` and dropped by the table of the report (the volume and the tonnes differ by that much).
Nothing is written; works in a project «только просмотр». `stale` — the report is out of date in the graph, or its
model has changed since (the set of blocks may differ from the table). Errors — `{code, message, hint}` in Russian: 404
`resource_report_not_found`, `block_model_not_found`; 409 `report_building`; 422 `bad_param {param}`.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.blocks import calc_job, geometry, restrict
from bf_core.blocks.filters import schema as fs
from bf_core.blocks.model import CHUNK, Chunk, Model
from bf_core.storage.blobs import BlobStore

from . import compute as cp
from . import kernels as rk
from . import result as rr
from . import store as st
from .csv_out import cell_text
from .spec import EMPTY_LABEL, Drill, DrillRows, ReportSpec, parse_params

BENCH_KEY = "Уступ"  # the name of the bench among the keys of a row
FIX = "Обновите отчёт (кнопка «Перестроить») или выберите ячейку заново."


@dataclass
class Cell:
    """A cell of the stored report against the model as it is now: the items of the tree (without the ones the pass
    adds), the state in the graph."""

    drill: Drill
    spec: ReportSpec
    model: Model
    items: list[dict[str, Any]]
    stale: bool
    stale_reason: str | None
    info: dict[str, Any]


# --- the tree ---------------------------------------------------------------------------------------------------------


def _bad(param: str, message: str, hint: str = FIX) -> Any:
    return cp.bad_param(param, message, hint)


def _text(node: Mapping[str, Any]) -> dict[str, Any]:
    """A condition (or a group of them) as `{expr}`; the node itself if it is not written as a text."""
    text = fs.to_text(node)
    return dict(node) if text is None else {"expr": text}


def _cond(field: str, op: str, value: float) -> dict[str, Any]:
    return {"cond": {"field": field, "op": op, "value": float(value)}}


def _group_items(spec: ReportSpec, group: tuple[str, ...] | None) -> list[dict[str, Any]]:
    if group is None:
        return []
    if len(group) != len(spec.keys):
        raise _bad("group", f"В группе {len(group)} подписей, а в отчёте ключей {len(spec.keys)}.",
                   "Передайте по одной подписи на каждый ключ отчёта (в его порядке) или null для итога.")  # fmt: skip
    out: list[dict[str, Any]] = []
    for i, (key, label) in enumerate(zip(spec.keys, group, strict=True)):
        if key.kind == "bench":
            b = spec.bench
            assert b is not None and b.z0 is not None and b.j0 is not None and b.n is not None
            first = b.z0 + b.j0 * b.h  # the arithmetic of `result.core_from_spec` and of the kernel
            if label == EMPTY_LABEL:
                out.append({"op": "not", "items": [{"slab": {"axis": "z", "from": first, "to": first + b.n * b.h,
                                                               "by": "share"}}]})  # fmt: skip
                continue
            labels = list(key.labels or ())
            if label not in labels:
                raise _bad(f"group.{i}", f"Уступа «{label[:60]}» нет среди уступов отчёта.")
            j = labels.index(label)
            out.append({"slab": {"axis": "z", "from": first + j * b.h, "to": first + (j + 1) * b.h, "by": "share"}})
            continue
        assert key.field is not None
        if label == EMPTY_LABEL:
            out.append({"cond": {"field": key.field, "op": "empty"}})
        elif label in (key.labels or ()):
            out.append({"cond": {"field": key.field, "op": "=", "value": label}})
        else:
            raise _bad(f"group.{i}", f"Подписи «{label[:60]}» нет среди значений поля «{key.field}».")
    return out


def _cutoff_items(spec: ReportSpec, d: Drill) -> list[dict[str, Any]]:
    g = spec.cutoff_field
    assert g is not None
    if d.kind == "all":
        if None not in spec.cutoffs:
            raise _bad("cutoff", "В отчёте нет строки «без борта».", "Выберите борт из таблицы отчёта.")
        return []
    if d.kind == "cutoff":
        assert d.cutoff is not None
        for c in spec.cutoffs:
            if c is not None and math.isclose(c, d.cutoff, rel_tol=1e-12, abs_tol=1e-12):
                return [_text(_cond(g, ">=", c))]
        raise _bad("cutoff", f"Борта {d.cutoff:g} нет в отчёте.", "Выберите борт из таблицы отчёта.")
    bal = spec.balance
    if bal is None or (d.kind == "offbalance" and bal.off_cutoff is None):
        what = "забаланса" if d.kind == "offbalance" else "баланса"
        raise _bad("kind", f"В отчёте нет строки {what}.", "Задайте балансовый и забалансовый борта в отчёте.")
    if d.kind == "balance":
        return [_text(_cond(g, ">=", bal.cutoff))]
    assert bal.off_cutoff is not None
    return [_text({"op": "and", "items": [_cond(g, ">=", bal.off_cutoff), _cond(g, "<", bal.cutoff)]})]


def join(items: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The tree of the rules from its items (canonical form; None — nothing restricts: every block passes)."""
    if not items:
        return None
    node = dict(items[0]) if len(items) == 1 else {"op": "and", "items": [dict(i) for i in items]}
    return fs.normalize_rules(node)


def open_cell(conn: sa.Connection, store: BlobStore, d: Drill) -> Cell:
    """The report (404), its model as it is now (404), the items of the tree of the cell (422)."""
    info = st.info(conn, d.report_id, store=store)
    if info["state"] == "building":
        raise st.building()
    if not info.get("params"):
        raise st.broken()
    spec = parse_params(dict(info["params"]))
    model = Model.open(conn, store, spec.model_id)
    spec = spec.resolved(model.fields, rr.z_range_of(model))
    tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)
    items: list[dict[str, Any]] = [] if tree is None else [fs.normalize_rules(tree)]
    items += _group_items(spec, d.group) + _cutoff_items(spec, d)
    graph = info["graph"]
    stale, reason = bool(graph["stale"]), graph["stale_reason"]
    seen = (info.get("model") or {}).get("version")
    if seen is not None and int(seen) != model.version:
        stale, reason = True, reason or "Блочная модель изменилась после построения отчёта."
    return Cell(d, spec, model, items, stale, reason, info)


# --- the cell ---------------------------------------------------------------------------------------------------------


def _density(spec: ReportSpec) -> tuple[str | None, float | None]:
    dens = spec.density
    return (dens, None) if isinstance(dens, str) else (None, float(dens or 0.0))


def _slab_of(item: Mapping[str, Any]) -> tuple[dict[str, Any], bool] | None:
    """(the body, negated) of an item that is a slab of Z by share (or its NOT), else None."""
    if "slab" in item:
        return dict(item["slab"]), False
    kids = item.get("items") or []
    if item.get("op") == "not" and len(kids) == 1 and "slab" in kids[0]:
        return dict(kids[0]["slab"]), True
    return None


def _layers(model: Model, body: Mapping[str, Any], conn: sa.Connection, store: BlobStore) -> np.ndarray:
    """The share of a slab of Z for each layer of a regular model: the function of the restrictor itself over one row
    of each layer (the height of a block and its centre in Z depend on the layer only), so the bits are the ones the
    filter of 3D gets."""
    nz = int(model.grid.counts[2])
    nxy = np.uint64(int(model.grid.counts[0]) * int(model.grid.counts[1]))
    fn = restrict.LEAVES["slab"](restrict.Build(model, conn, store), body, "rules.slab")
    rows = Chunk(0, np.arange(nz, dtype=np.uint64) * nxy, None, None, {})
    return np.asarray(fn(rows), dtype=np.float64)


def _restrictor(cell: Cell, items: Sequence[Mapping[str, Any]], conn: sa.Connection,
                store: BlobStore) -> restrict.Restrictor:  # fmt: skip
    """The restrictor of the tree of `items`. A model of whole blocks on a regular grid takes the slabs of Z from a
    table of its layers (the generic leaf computes the centres and the boxes of every row: 0,4 s per 2 million blocks,
    the most of the jump); any other model — the generic restrictor. The same shares either way."""
    model = cell.model
    slabs = [(i, _slab_of(i)) for i in items]
    if not any(sl for _, sl in slabs) or model.grid.tensors is not None or model.sub:
        return restrict.prepare(model, join(items), conn=conn, store=store)
    base = restrict.prepare(model, join([i for i, sl in slabs if sl is None]), conn=conn, store=store)
    tables = [(_layers(model, sl[0], conn, store), sl[1]) for _, sl in slabs if sl is not None]
    nxy = np.uint64(int(model.grid.counts[0]) * int(model.grid.counts[1]))

    def shares(chunk: Chunk) -> np.ndarray:
        f = base.weights(chunk)
        layer = (chunk.p // nxy).astype(np.intp)
        for table, negated in tables:
            f = f * ((1.0 - table[layer]) if negated else table[layer])
        return f

    return restrict.Restrictor(list(base.fields), base.warnings, shares)


def _count(cell: Cell, items: Sequence[Mapping[str, Any]], conn: sa.Connection, store: BlobStore,
           chunk: int) -> tuple[restrict.Restrictor, cp.Sums, dict[str, Any]]:  # fmt: skip
    """One pass of the kernel of the report over the tree of `items` with no keys and no cut-off: the whole cell is one
    bin."""
    r = _restrictor(cell, items, conn, store)
    field, value = _density(cell.spec)
    core = cp.Core([], None, field, value, np.empty(0), np.empty(0), (), cp.default_frac(cell.model))
    sums = cp.run(cell.model, core, r, chunk=chunk)
    q, n = rk.range_sums(*sums.total_view(), 0, 2)
    return r, sums, {"blocks": int(n[0, 0]), "volume": float(q[0, 0]), "tonnes": float(q[0, 1])}


def _leftovers(cell: Cell, sums: cp.Sums) -> list[dict[str, Any]]:
    """The items for the blocks the pass left out of the cell (no density — a gap is not a zero; no volume): the filter
    of 3D must not show them."""
    out: list[dict[str, Any]] = []
    field, _ = _density(cell.spec)
    if sums.excluded.get("no_density") and field is not None:
        out.append(_text(_cond(field, ">=", 0.0)))
    frac = cp.default_frac(cell.model)
    if sums.excluded.get("no_volume") and frac is not None:
        out.append({"cond": {"field": frac, "op": "notempty"}})
    return out


def _state(cell: Cell, rules: dict[str, Any] | None, count: Mapping[str, Any],
           warnings: Sequence[str]) -> dict[str, Any]:  # fmt: skip
    return {"rules": rules, "cell": dict(count), "stale": cell.stale, "stale_reason": cell.stale_reason,
            "model_id": cell.model.id, "warnings": list(warnings)}  # fmt: skip


def counted(cell: Cell, conn: sa.Connection, store: BlobStore,
            chunk: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """(the final items, the answer of the drill): the pass over the tree, then the items for what it left out (the
    cell does not change: those blocks were not in it)."""
    r, sums, count = _count(cell, cell.items, conn, store, chunk)
    items = [*cell.items, *_leftovers(cell, sums)]
    return items, _state(cell, join(items), count, r.warnings)


def answer(conn: sa.Connection, store: BlobStore, d: Drill, *, chunk: int = CHUNK) -> dict[str, Any]:
    """`POST /resources/reports/{id}/drill`: {rules, cell {blocks, volume, tonnes}, stale, stale_reason, model_id,
    warnings}; `rules` — a tree for `blocks.filter {model_id, restrict}` (null: every block passes)."""
    cell = open_cell(conn, store, d)
    return counted(cell, conn, store, chunk)[1]


# --- the rows ---------------------------------------------------------------------------------------------------------

HEAD = ("i", "j", "k", "X", "Y", "Z", "dX", "dY", "dZ", "Доля", "Объём, м³", "Тонны")


def _labels(codes: np.ndarray, labels: Sequence[str]) -> list[str]:
    n = len(labels)
    return [labels[c] if 0 <= c < n else EMPTY_LABEL for c in codes.tolist()]


def _bench_labels(spec: ReportSpec, zc: np.ndarray) -> list[str]:
    key = next(k for k in spec.keys if k.kind == "bench")
    b = spec.bench
    assert b is not None and b.z0 is not None and b.j0 is not None and b.n is not None
    j = np.floor((zc - (b.z0 + b.j0 * b.h)) / b.h).astype(np.int64)
    labels = list(key.labels or ())
    return [labels[x] if 0 <= x < len(labels) else EMPTY_LABEL for x in j.tolist()]


def _page_rows(cell: Cell, ch: Any, idx: np.ndarray, f: np.ndarray, ve: np.ndarray,
               dens: np.ndarray) -> list[dict[str, Any]]:  # fmt: skip
    """The rows of the blocks `idx` of a portion (the counted ones of it, in the order of the file)."""
    spec, grid = cell.spec, cell.model.grid
    p = ch.p[idx]
    s = None if ch.s is None else ch.s[idx]
    w = None if ch.w is None else ch.w[idx]
    i, j, k = geometry.row_ijk(grid, p)
    xyz = geometry.centres(grid, p, s, w)
    size = geometry.row_widths(grid, p, s, w)
    keys: dict[str, list[str]] = {}
    for key in spec.keys:
        if key.kind == "bench":
            keys[BENCH_KEY] = _bench_labels(spec, xyz[:, 2])
        else:
            assert key.field is not None
            keys[key.field] = _labels(ch.cols[key.field][idx], key.labels or ())
    grades = {e.field: ch.cols[e.field][idx] for e in spec.elements}
    tonnes = ve[idx] * dens[idx]
    out: list[dict[str, Any]] = []
    for a in range(len(idx)):
        out.append({
            "i": int(i[a]), "j": int(j[a]), "k": int(k[a]),
            "x": float(xyz[a, 0]), "y": float(xyz[a, 1]), "z": float(xyz[a, 2]),
            "dx": float(size[a, 0]), "dy": float(size[a, 1]), "dz": float(size[a, 2]),
            "f": float(f[idx[a]]), "volume": float(ve[idx[a]]), "tonnes": float(tonnes[a]),
            "keys": {name: vals[a] for name, vals in keys.items()},
            "elements": {name: (float(v[a]) if math.isfinite(float(v[a])) else None) for name, v in grades.items()},
        })  # fmt: skip
    return out


def _csv(rows: Sequence[Mapping[str, Any]], keys: Sequence[str], elements: Sequence[str]) -> str:
    """The page as a text: «;», the decimal comma, CRLF (as the CSV of the export), the shortest text of a number."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow([*HEAD, *keys, *elements])
    for r in rows:
        w.writerow([cell_text(r[c]) for c in ("i", "j", "k", "x", "y", "z", "dx", "dy", "dz", "f", "volume", "tonnes")]
                   + [cell_text(r["keys"].get(k)) for k in keys]
                   + [cell_text(r["elements"].get(e)) for e in elements])  # fmt: skip
    return buf.getvalue()


def rows_answer(conn: sa.Connection, store: BlobStore, d: DrillRows, *, chunk: int = CHUNK) -> dict[str, Any]:
    """`POST /resources/reports/{id}/drill/rows`: the answer of the drill + {page, size, total, rows, csv}. The rows
    are the blocks with f > 0 of the tree that the table of the report counts, in the order of the file; `total` — their
    number (the `blocks` of the cell); a page beyond the end is empty."""
    cell = open_cell(conn, store, d.cell)
    items, head = counted(cell, conn, store, chunk)
    spec = cell.spec
    lo, hi = (d.page - 1) * d.size, d.page * d.size
    r = _restrictor(cell, items, conn, store)
    dens_field, dens_value = _density(spec)
    frac = cp.default_frac(cell.model)
    names = [e.field for e in spec.elements] + [k.field for k in spec.keys if k.field is not None]
    names += [n for n in (dens_field, frac) if n is not None]
    rows: list[dict[str, Any]] = []
    seen = 0
    if lo < head["cell"]["blocks"]:
        for ch, f in r.iter(cell.model, extra=names, chunk=chunk):
            vol = geometry.row_volumes(cell.model.grid, ch.p, ch.s, ch.w, None if frac is None else ch.cols[frac])
            ve = vol * f
            dens = np.full(ch.n, dens_value) if dens_field is None else ch.cols[dens_field]
            keep = (f > 0.0) & ~np.isnan(ve) & np.isfinite(dens) & (dens >= 0.0)  # what the pass of the report counts
            idx = np.flatnonzero(keep)
            if seen + len(idx) > lo:
                part = idx[max(lo - seen, 0) : max(hi - seen, 0)]
                rows += _page_rows(cell, ch, part, f, ve, dens)
            seen += len(idx)
            if seen >= hi:
                break
    keys = [BENCH_KEY if k.kind == "bench" else str(k.field) for k in spec.keys]
    out = {**head, "page": d.page, "size": d.size, "total": head["cell"]["blocks"], "rows": rows}
    out["csv"] = _csv(rows, keys, [e.field for e in spec.elements]) if d.csv else None
    return out
